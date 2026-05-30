"""o27.gazette — The O27 Gazette: a steerable, LLM-ready news desk.

A self-contained tool (like the almanac) that turns a day's finished games
into a structured "Game Context" payload and pairs it with a swappable
writer voice to produce a ready-to-paste newspaper prompt.

The two layers are decoupled on purpose:
  - `serialize.build_daily_payload(date)` is the data feed — plain dicts you
    can dump, hand-edit, and feed back in.
  - `prompt.build_prompt(payload, voice=...)` is the prose layer — it takes
    any payload plus a chosen `voice` from the roster in `voices`.

Run standalone with `python -m o27.gazette` (see `cli`), or mount the Flask
blueprint from `o27.gazette.blueprint` at /gazette.
"""
from __future__ import annotations

from .serialize import (
    build_daily_payload,
    latest_slate_date,
    adjacent_slate_dates,
)
from .prompt import build_prompt
from .voices import Voice, all_voices, get_voice, DEFAULT_VOICE_ID

__all__ = [
    "build_daily_payload",
    "latest_slate_date",
    "adjacent_slate_dates",
    "build_prompt",
    "Voice",
    "all_voices",
    "get_voice",
    "DEFAULT_VOICE_ID",
]
