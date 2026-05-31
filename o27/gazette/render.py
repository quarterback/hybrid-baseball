"""o27.gazette.render — turn a slate payload into finished prose via Claude.

This is the optional live-generation layer. `prompt.build_prompt` gives you a
paste-ready prompt; this module actually calls Claude with it and returns the
article, caching the result per (edition, voice) in the active save's DB so a
given edition only generates once.

Credentials/config:
  - ANTHROPIC_API_KEY  — required (set it as a Fly secret). Absent ⇒ the page
    falls back to the copyable prompt; `generate()` raises GazetteNotConfigured.
  - O27_GAZETTE_MODEL  — optional model override (default: claude-opus-4-8).

Uses the official `anthropic` SDK. The cache table is created on first use, so
this carries no schema migration of its own.
"""
from __future__ import annotations

import json
import os

from o27v2 import db
from .voices import Voice, get_voice


DEFAULT_MODEL = "claude-opus-4-8"

# A slate recap is short; this is plenty of headroom for ~400 words and keeps
# the request well under the SDK's non-streaming timeout guard.
_MAX_TOKENS = 2000


class GazetteNotConfigured(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is absent — live render unavailable."""


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def model_id() -> str:
    return os.environ.get("O27_GAZETTE_MODEL") or DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Per-(edition, voice) cache — lives in the active save's DB.
# ---------------------------------------------------------------------------

def _ensure_table() -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS gazette_articles (
               slate_date  TEXT NOT NULL,
               voice_id    TEXT NOT NULL,
               model       TEXT,
               article     TEXT NOT NULL,
               created_at  TEXT DEFAULT (datetime('now')),
               PRIMARY KEY (slate_date, voice_id)
           )"""
    )


def get_cached(slate_date: str, voice_id: str) -> dict | None:
    _ensure_table()
    return db.fetchone(
        "SELECT * FROM gazette_articles WHERE slate_date = ? AND voice_id = ?",
        (slate_date, voice_id),
    )


def _save(slate_date: str, voice_id: str, model: str, article: str) -> None:
    _ensure_table()
    db.execute(
        """INSERT INTO gazette_articles (slate_date, voice_id, model, article, created_at)
           VALUES (?, ?, ?, ?, datetime('now'))
           ON CONFLICT(slate_date, voice_id)
           DO UPDATE SET article = excluded.article,
                         model = excluded.model,
                         created_at = excluded.created_at""",
        (slate_date, voice_id, model, article),
    )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate(payload: dict, voice: str | Voice | None = None, *,
             save: bool = True) -> dict:
    """Generate the edition's prose with Claude and (optionally) cache it.

    Returns {"article", "model", "voice_id"}. Raises GazetteNotConfigured if
    no API key is set."""
    if not is_configured():
        raise GazetteNotConfigured("ANTHROPIC_API_KEY is not set")

    import anthropic  # imported lazily so the app runs without the SDK installed

    v = voice if isinstance(voice, Voice) else get_voice(voice)
    slate_date = payload.get("edition_date", "")

    # Thinking is left off for a snappy page render; the explicit
    # final-answer-only instruction keeps Opus 4.8 from leaking reasoning into
    # the article body (per the model's thinking-disabled behavior).
    system = (
        v.system_prompt()
        + "\n\nOutput ONLY the finished article text — no preamble, no notes "
          "about your process, no code fences, no JSON."
    )
    user = (
        f"Here is today's slate ({slate_date}). Write the edition.\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    )

    mid = model_id()
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=mid,
        max_tokens=_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(
        b.text for b in message.content if getattr(b, "type", None) == "text"
    ).strip()

    if save and text:
        _save(slate_date, v.id, mid, text)
    return {"article": text, "model": mid, "voice_id": v.id}
