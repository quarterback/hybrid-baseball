"""Flask blueprint — the in-app, phone-friendly audio UI.

A "🎧 Listen" button on the game page links here. The page generates the
broadcast on demand (Claude + OpenAI TTS run on a background thread so the
request never blocks) and shows an ``<audio>`` player when it's ready.

Registered in o27v2/web/app.py alongside the gazette/almanac blueprints.
Kept self-contained: its own routes, its own template string, its own sidecar
manifest — nothing here touches the sim or the core schema.
"""
from __future__ import annotations

import os
import threading

from flask import (
    Blueprint, abort, jsonify, render_template_string, request, send_file,
)

from . import config, manifest, pipeline

audio_bp = Blueprint("audio", __name__, url_prefix="/audio")

# In-flight guard so a double-tap doesn't launch two renders for one game.
_inflight: set[str] = set()
_lock = threading.Lock()


def _ref(game_id: int) -> tuple[str, str]:
    save_key = pipeline.current_save_key()
    return save_key, f"{save_key}:{game_id}"


def _worker(game, save_key: str, stub: bool) -> None:
    ref = f"{save_key}:{game.game_id}"
    try:
        pipeline.produce(game, save_key=save_key, stub_script=stub, stub_tts=stub)
    except Exception:
        # produce() already recorded the failure in the manifest.
        pass
    finally:
        with _lock:
            _inflight.discard(ref)


@audio_bp.post("/game/<int:game_id>/generate")
def generate(game_id: int):
    """Kick off (or no-op if already done/running) a render. Returns JSON."""
    save_key, ref = _ref(game_id)
    existing = manifest.get("game", ref)
    if existing and existing["status"] == "ok":
        return jsonify({"status": "ok"})

    # Gather synchronously (fast SELECTs) so we validate the game and pin the
    # data before the active save can change under us.
    try:
        game = pipeline.gather(game_id)
    except ValueError as e:
        return jsonify({"status": "error", "error": str(e)}), 404

    stub = request.args.get("stub") in ("1", "true", "yes")
    with _lock:
        if ref in _inflight:
            return jsonify({"status": "generating"})
        _inflight.add(ref)
    manifest.begin("game", ref, league=save_key,
                   model="stub" if stub else config.SCRIPT_MODEL)
    threading.Thread(
        target=_worker, args=(game, save_key, stub),
        name=f"audio-{ref}", daemon=True,
    ).start()
    return jsonify({"status": "generating"})


@audio_bp.get("/game/<int:game_id>/status")
def status(game_id: int):
    _, ref = _ref(game_id)
    row = manifest.get("game", ref)
    if not row:
        return jsonify({"status": "none"})
    return jsonify({
        "status": row["status"],
        "error": row.get("error"),
        "duration_s": row.get("duration_s"),
        "n_turns": row.get("n_turns"),
        "est_cost_usd": row.get("est_cost_usd"),
        "model": row.get("model"),
        "audio_url": (f"/audio/game/{game_id}/audio"
                      if row["status"] == "ok" else None),
    })


@audio_bp.get("/game/<int:game_id>/audio")
def audio(game_id: int):
    _, ref = _ref(game_id)
    row = manifest.get("game", ref)
    if not row or row["status"] != "ok":
        abort(404)
    path = row.get("mp3_path") or row.get("wav_path")
    if not path or not os.path.exists(path):
        abort(404)
    # Defence in depth: only serve from inside our output dir.
    if not os.path.abspath(path).startswith(os.path.abspath(config.OUT_DIR)):
        abort(403)
    mimetype = "audio/mpeg" if path.endswith(".mp3") else "audio/wav"
    return send_file(path, mimetype=mimetype, conditional=True,
                     download_name=os.path.basename(path))


@audio_bp.get("/game/<int:game_id>")
def player(game_id: int):
    """Self-contained mobile player page."""
    try:
        game = pipeline.gather(game_id)
        title = (f"{game.away['city']} {game.away['name']} {game.away_score} "
                 f"@ {game.home['city']} {game.home['name']} {game.home_score}")
        subtitle = game.game_date
    except ValueError as e:
        title, subtitle = "Game not available", str(e)
    _, ref = _ref(game_id)
    row = manifest.get("game", ref)
    initial = row["status"] if row else "none"
    return render_template_string(
        _PLAYER_HTML, game_id=game_id, title=title, subtitle=subtitle,
        initial=initial,
    )


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
  <div class="sub">O27 Game of the Week · {{ subtitle }}</div>

  <button id="gen">Generate broadcast</button>
  <div id="player"></div>
  <div class="msg" id="msg"></div>
  <a class="back" href="/game/{{ game_id }}">&larr; Back to box score</a>

<script>
const GID = {{ game_id }};
const btn = document.getElementById('gen');
const msg = document.getElementById('msg');
const player = document.getElementById('player');

function showPlayer(url) {
  player.innerHTML =
    '<audio controls autoplay preload="auto" src="' + url + '"></audio>';
  btn.style.display = 'none';
  msg.textContent = '';
}
function showError(t) { msg.className = 'msg err'; msg.textContent = t || 'Something went wrong.'; btn.disabled = false; btn.textContent = 'Try again'; }

async function poll() {
  const r = await fetch('/audio/game/' + GID + '/status');
  const d = await r.json();
  if (d.status === 'ok') { showPlayer(d.audio_url); return; }
  if (d.status === 'error') { showError(d.error); return; }
  if (d.status === 'generating') {
    msg.className = 'msg';
    msg.textContent = 'Calling the game… this takes a minute.';
    setTimeout(poll, 2500); return;
  }
  // 'none' — nothing yet
}

btn.addEventListener('click', async () => {
  btn.disabled = true; btn.textContent = 'Working…';
  msg.className = 'msg'; msg.textContent = 'Warming up the booth…';
  const r = await fetch('/audio/game/' + GID + '/generate', {method: 'POST'});
  const d = await r.json();
  if (d.status === 'error') { showError(d.error); return; }
  poll();
});

// If a clip already exists (or is mid-render), reflect that on load.
const INITIAL = {{ initial | tojson }};
if (INITIAL === 'ok') { poll(); }
else if (INITIAL === 'generating') { btn.disabled = true; btn.textContent = 'Working…'; poll(); }
</script>
</body></html>"""
