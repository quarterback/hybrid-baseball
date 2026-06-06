"""Flask blueprint — the in-app, phone-friendly audio UI.

Two surfaces, one mechanism:
  - Game of the Week: "🎧 Listen" on a game page → ``/audio/game/<id>``.
  - League roundup:    "📻 Radio" in the nav      → ``/audio/roundup``.

Both generate on a background thread (the request never blocks), expose a
status the page polls, and serve the finished clip with range support so phones
can scrub. Self-contained: own routes, own player template, own sidecar
manifest — nothing here touches the sim or the core schema.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

from flask import (
    Blueprint, abort, jsonify, render_template_string, request, send_file,
)

from . import config, manifest, pipeline, sources

audio_bp = Blueprint("audio", __name__, url_prefix="/audio")

# In-flight guard so a double-tap doesn't launch two renders for one ref.
_inflight: set[str] = set()
_lock = threading.Lock()


def _save_key() -> str:
    return pipeline.current_save_key()


def _stub_requested() -> bool:
    return request.args.get("stub") in ("1", "true", "yes")


def _launch(kind: str, ref: str, league: str, produce: Callable[[bool], None]):
    """Shared generate path. ``produce(stub)`` runs the slow pipeline (it does
    its own manifest begin/record/fail)."""
    existing = manifest.get(kind, ref)
    if existing and existing["status"] == "ok":
        return jsonify({"status": "ok"})
    stub = _stub_requested()
    with _lock:
        if ref in _inflight:
            return jsonify({"status": "generating"})
        _inflight.add(ref)
    manifest.begin(kind, ref, league=league,
                   model="stub" if stub else config.SCRIPT_MODEL)

    def _bg():
        try:
            produce(stub)
        except Exception:
            pass  # produce() already recorded the failure
        finally:
            with _lock:
                _inflight.discard(ref)

    threading.Thread(target=_bg, name=f"audio-{ref}", daemon=True).start()
    return jsonify({"status": "generating"})


def _status_payload(kind: str, ref: str, audio_url: str):
    row = manifest.get(kind, ref)
    if not row:
        return jsonify({"status": "none"})
    return jsonify({
        "status": row["status"],
        "error": row.get("error"),
        "duration_s": row.get("duration_s"),
        "n_turns": row.get("n_turns"),
        "est_cost_usd": row.get("est_cost_usd"),
        "model": row.get("model"),
        "audio_url": audio_url if row["status"] == "ok" else None,
    })


def _serve_audio(kind: str, ref: str):
    row = manifest.get(kind, ref)
    if not row or row["status"] != "ok":
        abort(404)
    path = row.get("mp3_path") or row.get("wav_path")
    if not path or not os.path.exists(path):
        abort(404)
    if not os.path.abspath(path).startswith(os.path.abspath(config.OUT_DIR)):
        abort(403)
    mimetype = "audio/mpeg" if path.endswith(".mp3") else "audio/wav"
    return send_file(path, mimetype=mimetype, conditional=True,
                     download_name=os.path.basename(path))


# --- Game of the Week ------------------------------------------------------

def _game_ref(game_id: int) -> tuple[str, str]:
    sk = _save_key()
    return sk, f"{sk}:{game_id}"


@audio_bp.post("/game/<int:game_id>/generate")
def game_generate(game_id: int):
    sk, ref = _game_ref(game_id)
    try:
        game = pipeline.gather(game_id)
    except ValueError as e:
        return jsonify({"status": "error", "error": str(e)}), 404
    return _launch("game", ref, sk,
                   lambda stub: pipeline.produce(
                       game, save_key=sk, stub_script=stub, stub_tts=stub))


@audio_bp.get("/game/<int:game_id>/status")
def game_status(game_id: int):
    _, ref = _game_ref(game_id)
    return _status_payload("game", ref, f"/audio/game/{game_id}/audio")


@audio_bp.get("/game/<int:game_id>/audio")
def game_audio(game_id: int):
    _, ref = _game_ref(game_id)
    return _serve_audio("game", ref)


@audio_bp.get("/game/<int:game_id>")
def player(game_id: int):
    try:
        g = pipeline.gather(game_id)
        title = (f"{g.away['city']} {g.away['name']} {g.away_score} "
                 f"@ {g.home['city']} {g.home['name']} {g.home_score}")
        subtitle = f"O27 Game Recap · {g.game_date}"
    except ValueError as e:
        title, subtitle = "Game not available", str(e)
    _, ref = _game_ref(game_id)
    row = manifest.get("game", ref)
    return render_template_string(
        _PLAYER_HTML, title=title, subtitle=subtitle,
        generate_url=f"/audio/game/{game_id}/generate",
        status_url=f"/audio/game/{game_id}/status",
        back_url=f"/game/{game_id}", back_label="Back to box score",
        initial=row["status"] if row else "none",
        cta="Generate broadcast",
    )


# --- League roundup --------------------------------------------------------

def _roundup_ref(date: str) -> tuple[str, str]:
    sk = _save_key()
    return sk, f"{sk}:{date}"


def _resolve_date() -> str | None:
    return request.args.get("date") or sources.latest_played_date()


@audio_bp.post("/roundup/generate")
def roundup_generate():
    date = _resolve_date()
    if not date:
        return jsonify({"status": "error", "error": "no played games yet"}), 404
    sk, ref = _roundup_ref(date)
    try:
        rd = pipeline.gather_roundup(date)
    except ValueError as e:
        return jsonify({"status": "error", "error": str(e)}), 404
    return _launch("roundup", ref, sk,
                   lambda stub: pipeline.produce_roundup(
                       rd, save_key=sk, stub_script=stub, stub_tts=stub))


@audio_bp.get("/roundup/status")
def roundup_status():
    date = _resolve_date()
    if not date:
        return jsonify({"status": "none"})
    _, ref = _roundup_ref(date)
    return _status_payload("roundup", ref, f"/audio/roundup/audio?date={date}")


@audio_bp.get("/roundup/audio")
def roundup_audio():
    date = _resolve_date()
    if not date:
        abort(404)
    _, ref = _roundup_ref(date)
    return _serve_audio("roundup", ref)


@audio_bp.get("/roundup")
def roundup_player():
    date = _resolve_date()
    if not date:
        return render_template_string(
            _PLAYER_HTML, title="No games yet",
            subtitle="Sim some games, then come back for the roundup.",
            generate_url="", status_url="", back_url="/",
            back_label="Back to scores", initial="none", cta="Generate roundup")
    _, ref = _roundup_ref(date)
    row = manifest.get("roundup", ref)
    return render_template_string(
        _PLAYER_HTML, title=f"League Roundup — {date}",
        subtitle="O27 around the league",
        generate_url=f"/audio/roundup/generate?date={date}",
        status_url=f"/audio/roundup/status?date={date}",
        back_url="/", back_label="Back to scores",
        initial=row["status"] if row else "none", cta="Generate roundup")


# --- House style / lexicon editor -----------------------------------------

@audio_bp.route("/style", methods=["GET", "POST"])
def style_editor():
    saved = False
    if request.method == "POST":
        config.save_style(request.form.get("style", ""))
        saved = True
    return render_template_string(
        _STYLE_HTML, text=config.load_style(), saved=saved)


_STYLE_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Broadcast style</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0;
         padding: 1.25rem; max-width: 720px; }
  h1 { font-size: 1.15rem; margin: 0 0 .25rem; }
  p.sub { color: #888; font-size: .9rem; margin: 0 0 1rem; }
  textarea { width: 100%; min-height: 60vh; font: .9rem/1.4 ui-monospace, monospace;
             padding: .7rem; border-radius: .5rem; border: 1px solid #999;
             box-sizing: border-box; }
  button { font-size: 1.05rem; padding: .7rem 1.2rem; border: 0; border-radius: .6rem;
           background: #1d6f42; color: #fff; margin-top: .8rem; cursor: pointer; }
  .ok { color: #1d6f42; font-weight: 600; }
  a.back { display: inline-block; margin-top: 1rem; color: #1d6f42; }
</style></head>
<body>
  <h1>🎙️ Broadcast style &amp; lexicon</h1>
  <p class="sub">This text is fed to the announcers on every recap. Changes take effect
    on the next render. {% if saved %}<span class="ok">Saved ✓</span>{% endif %}</p>
  <form method="post">
    <textarea name="style" spellcheck="false">{{ text }}</textarea><br>
    <button type="submit">Save</button>
  </form>
  <a class="back" href="/audio/roundup">&larr; Back to Radio</a>
</body></html>"""


_PLAYER_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🎧 {{ title }}</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, -apple-system, sans-serif; margin: 0;
         padding: 1.25rem; max-width: 640px; }
  h1 { font-size: 1.15rem; margin: 0 0 .2rem; }
  .sub { color: #888; font-size: .9rem; margin-bottom: 1.25rem; }
  button { font-size: 1.05rem; padding: .8rem 1.2rem; border-radius: .6rem;
           border: 0; background: #1d6f42; color: #fff; width: 100%;
           cursor: pointer; }
  button:disabled { opacity: .6; }
  audio { width: 100%; margin-top: 1rem; }
  .msg { margin-top: 1rem; color: #888; font-size: .95rem; min-height: 1.2em; }
  .err { color: #c0392b; }
  a.back { display: inline-block; margin-top: 1.5rem; color: #1d6f42; }
</style></head>
<body>
  <h1>🎧 {{ title }}</h1>
  <div class="sub">{{ subtitle }}</div>

  {% if generate_url %}<button id="gen">{{ cta }}</button>{% endif %}
  <div id="player"></div>
  <div class="msg" id="msg"></div>
  <a class="back" href="{{ back_url }}">&larr; {{ back_label }}</a>
  <a class="back" href="/audio/style" style="margin-left:1rem">🎙️ Edit style</a>

<script>
const GEN = {{ generate_url | tojson }};
const STAT = {{ status_url | tojson }};
const btn = document.getElementById('gen');
const msg = document.getElementById('msg');
const player = document.getElementById('player');

function showPlayer(url) {
  player.innerHTML =
    '<audio controls autoplay preload="auto" src="' + url + '"></audio>';
  if (btn) btn.style.display = 'none';
  msg.textContent = '';
}
function showError(t) {
  msg.className = 'msg err'; msg.textContent = t || 'Something went wrong.';
  if (btn) { btn.disabled = false; btn.textContent = 'Try again'; }
}
async function poll() {
  const r = await fetch(STAT); const d = await r.json();
  if (d.status === 'ok') { showPlayer(d.audio_url); return; }
  if (d.status === 'error') { showError(d.error); return; }
  if (d.status === 'generating') {
    msg.className = 'msg';
    msg.textContent = 'On the air… this takes a minute.';
    setTimeout(poll, 2500); return;
  }
}
if (btn) btn.addEventListener('click', async () => {
  btn.disabled = true; btn.textContent = 'Working…';
  msg.className = 'msg'; msg.textContent = 'Warming up the booth…';
  const r = await fetch(GEN, {method: 'POST'}); const d = await r.json();
  if (d.status === 'error') { showError(d.error); return; }
  poll();
});

const INITIAL = {{ initial | tojson }};
if (STAT && INITIAL === 'ok') { poll(); }
else if (STAT && INITIAL === 'generating') {
  if (btn) { btn.disabled = true; btn.textContent = 'Working…'; }
  poll();
}
</script>
</body></html>"""
