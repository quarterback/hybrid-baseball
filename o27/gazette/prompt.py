"""o27.gazette.prompt — compose a ready-to-paste prompt from a payload.

This layer is intentionally data-agnostic: `build_prompt` takes a payload
dict (the structured slate from `o27.gazette.serialize`, OR one you dumped
to a file and hand-edited) plus a chosen `voice`, and returns the full
prompt — shared sport brief + that writer's persona + the slate JSON.

Because the data and the voice are both inputs, you can steer the paper:
swap writers, tweak the JSON, or point at your own voices file, all without
changing this module.
"""
from __future__ import annotations

import json

from .voices import Voice, get_voice


def build_prompt(payload: dict, *, voice: str | Voice | None = None) -> str:
    """The full ready-to-paste prompt for one slate, in a chosen voice.

    `voice` may be a voice id (str), a `Voice`, or None for the default.
    Drop the result into any LLM (Claude, GPT, ...) to print the edition.
    """
    v = voice if isinstance(voice, Voice) else get_voice(voice)
    slate_date = payload.get("edition_date", "")
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return (
        f"{v.system_prompt()}\n\n"
        f"--- TODAY'S SLATE ({slate_date}) ---\n\n"
        f"```json\n{body}\n```\n"
    )
