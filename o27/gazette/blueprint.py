"""o27.gazette.blueprint — Flask blueprint serving The O27 Gazette.

Mounted at /gazette on the o27v2 web app. Reads through `o27v2.db`, so it
tracks whichever save the host app has active. The page renders the day's
structured slate, lets you pick a writer voice, and — when an API key is
configured — generates and caches the finished article in-page. The .txt /
.json endpoints expose the prompt and raw payload.

Routes:
  /gazette                  → the news desk (HTML); ?date= and ?voice= steer it
  /gazette/generate (POST)  → generate (or regenerate) the article via Claude
  /gazette/export.txt       → full ready-to-paste prompt (text/plain)
  /gazette/export.json      → raw structured Game Context payload
"""
from __future__ import annotations

import json
import os

from flask import (
    Blueprint, Response, abort, flash, jsonify, redirect, render_template,
    request, url_for,
)

from . import serialize, prompt as _prompt, render as _render
from .voices import all_voices, get_voice, DEFAULT_VOICE_ID


_HERE = os.path.dirname(os.path.abspath(__file__))

gazette_bp = Blueprint(
    "gazette",
    __name__,
    url_prefix="/gazette",
    template_folder=os.path.join(_HERE, "templates"),
)


def _resolve_date() -> str | None:
    """?date= override, else the most recent slate with played games."""
    return request.args.get("date") or serialize.latest_slate_date()


def _resolve_voice_id() -> str:
    return request.args.get("voice") or DEFAULT_VOICE_ID


@gazette_bp.route("/")
def view():
    """The news desk: structured slate + voice picker + the article."""
    slate_date = _resolve_date()
    voice = get_voice(_resolve_voice_id())
    voices = all_voices()
    common = {
        "voices": voices,
        "voice": voice,
        "api_configured": _render.is_configured(),
        "model_name": _render.model_id(),
    }
    if not slate_date:
        return render_template(
            "gazette.html", slate_date=None, payload=None, payload_json=None,
            prev_date=None, next_date=None, prompt_chars=0, article=None,
            **common)

    payload = serialize.build_daily_payload(slate_date)
    prev_date, next_date = serialize.adjacent_slate_dates(slate_date)
    prompt_chars = (
        len(_prompt.build_prompt(payload, voice=voice)) if payload["games"] else 0
    )
    cached = _render.get_cached(slate_date, voice.id) if payload["games"] else None
    return render_template(
        "gazette.html",
        slate_date=slate_date,
        payload=payload,
        payload_json=json.dumps(payload, indent=2, ensure_ascii=False),
        prev_date=prev_date,
        next_date=next_date,
        prompt_chars=prompt_chars,
        article=cached,
        **common,
    )


@gazette_bp.route("/generate", methods=["POST"])
def generate():
    """Generate (or regenerate) the article for a slate+voice via Claude."""
    slate_date = request.form.get("date") or serialize.latest_slate_date()
    voice_id = request.form.get("voice") or DEFAULT_VOICE_ID
    back = url_for("gazette.view", date=slate_date, voice=voice_id)
    if not slate_date:
        return redirect(back)

    payload = serialize.build_daily_payload(slate_date)
    if not payload["games"]:
        flash("No finished games on that date — nothing to write.")
        return redirect(back)
    try:
        _render.generate(payload, voice_id)
    except _render.GazetteNotConfigured:
        flash("Live generation needs ANTHROPIC_API_KEY set on the server. "
              "Until then, use the copyable prompt below.")
    except Exception as e:  # surface API/network errors without 500ing the page
        flash(f"The presses jammed: {e}")
    return redirect(back)


@gazette_bp.route("/export.txt")
def export_txt():
    """The full ready-to-paste prompt (system instructions + slate JSON)."""
    slate_date = _resolve_date()
    if not slate_date:
        abort(404)
    payload = serialize.build_daily_payload(slate_date)
    text = _prompt.build_prompt(payload, voice=_resolve_voice_id())
    return Response(text, mimetype="text/plain; charset=utf-8")


@gazette_bp.route("/export.json")
def export_json():
    """The raw structured Game Context payload for the slate."""
    slate_date = _resolve_date()
    if not slate_date:
        abort(404)
    return jsonify(serialize.build_daily_payload(slate_date))
