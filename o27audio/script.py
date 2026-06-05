"""Stage 2 — Script. Claude turns a game's data into a two-host booth call.

Output is a list of speaker-tagged turns: ``{"speaker": "pbp"|"color", "text": ...}``.
Uses structured outputs so there is nothing to parse out of prose.

``stub_script`` produces a deterministic script straight from the data with no
network/keys — used to exercise the TTS + stitch stages offline.
"""
from __future__ import annotations

import json
from typing import Any

from . import config
from .sources import GameData

Turn = dict[str, str]

_SYSTEM = """You are the writers' room for a baseball radio broadcast of O27 — a \
baseball variant where each side bats one continuous 27-out half (no innings as \
you know them), with quirks like the "Second Chance" (a batter can re-take an at-bat), \
the "Walk-Back" bonus runner after a home run, and "Super-Innings" extra rounds when \
tied. Treat these as normal, exciting parts of the game — call them naturally, don't \
explain the rulebook.

Write a lively TWO-HOST booth call:
  - "pbp"  = the play-by-play announcer: drives the action, calls the big plays.
  - "color" = the color commentator (ex-player): reacts, adds insight and humor.

Voice and pacing:
  - Open with a quick cold open (who, where, the headline), then move chronologically
    through the game's turning points using the play-by-play log and scoring events.
  - Hit the dramatic beats: lead changes, home runs, big strikeouts, the Walk-Back,
    any Super-Innings, and the final out. Name real players and teams.
  - Hand the mic back and forth — short, natural turns (1-4 sentences each). The two
    voices should clearly alternate and react to each other.
  - Close with a sign-off naming the final score and the standout performer(s).
  - This is AUDIO: no stage directions, no asterisks, no "[laughs]". Just spoken words.
  - Keep the whole thing tight — a highlights call, not every pitch. Aim ~30-50 turns."""


def _header(game: GameData, max_pbp_chars: int) -> str:
    """A compact, model-friendly digest placed before the raw play-by-play."""
    lines: list[str] = []
    tag = "PLAYOFF " if game.is_playoff else ""
    lines.append(
        f"{tag}GAME {game.game_id} — season {game.season}, {game.game_date}"
    )
    lines.append(
        f"Final: {game.away['city']} {game.away['name']} {game.away_score} "
        f"@ {game.home['city']} {game.home['name']} {game.home_score}"
        + ("  (decided in Super-Innings)" if game.super_inning else "")
    )
    win = game.winner
    if win:
        lines.append(f"Winner: {win['city']} {win['name']} (by {game.margin})")
    lines.append(f"Ballpark: {game.home.get('park_name') or game.home['city']}")

    if game.batting_stars:
        lines.append("\nBatting stars:")
        for s in game.batting_stars:
            lines.append(
                f"  {s['name']} ({s['team']}): {s['h']}-for-{s['ab']}, "
                f"{s['hr']} HR, {s['rbi']} RBI, {s['r']} R"
                + (f", {s['sb']} SB" if s.get("sb") else "")
            )
    if game.pitching_lines:
        lines.append("\nPitching:")
        for p in game.pitching_lines:
            ip = f"{p['outs'] // 3}.{p['outs'] % 3}"
            lines.append(
                f"  {p['name']} ({p['team']}): {ip} IP, {p['k']} K, {p['bb']} BB, "
                f"{p['h']} H, {p['r']} R ({p['er']} ER), {p['hr']} HR"
            )
    if game.scoring_events:
        lines.append("\nScoring sequence (half | batter drove / runner scored | score):")
        for e in game.scoring_events:
            lines.append(
                f"  {e['half']:>13} | {e['batter']} → {e['runner']} | "
                f"{e['visitors_score']}-{e['home_score']}"
            )

    pbp = game.pbp_text
    if len(pbp) > max_pbp_chars:
        pbp = pbp[:max_pbp_chars] + "\n…(play-by-play truncated)…"
    lines.append("\n===== FULL PLAY-BY-PLAY LOG =====\n")
    lines.append(pbp)
    return "\n".join(lines)


def build_messages(game: GameData, max_pbp_chars: int = 60000) -> tuple[str, str]:
    """Return ``(system, user_text)`` for the script-generation call."""
    return _SYSTEM, _header(game, max_pbp_chars)


_SCHEMA = {
    "type": "object",
    "properties": {
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string", "enum": ["pbp", "color"]},
                    "text": {"type": "string"},
                },
                "required": ["speaker", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["turns"],
    "additionalProperties": False,
}


def generate_script(
    game: GameData,
    model: str | None = None,
    max_pbp_chars: int = 60000,
) -> tuple[list[Turn], dict[str, Any]]:
    """Call Claude and return ``(turns, usage)``. Raises if no key/SDK."""
    key = config.anthropic_key()
    if not key:
        raise RuntimeError(
            "No Anthropic key (set ANTHROPIC_API_KEY). "
            "Use --stub-script to test the pipeline offline."
        )
    try:
        import anthropic  # lazy — keeps the module import-safe without the SDK
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed — run "
            "`pip install -r o27audio/requirements.txt` "
            "(or use --stub-script for an offline test)."
        ) from e

    model = model or config.SCRIPT_MODEL
    system, user_text = build_messages(game, max_pbp_chars)
    client = anthropic.Anthropic(api_key=key)

    # Stream (output can be long) + structured outputs (clean speaker turns).
    with client.messages.stream(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _SCHEMA}},
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_text}],
    ) as stream:
        msg = stream.get_final_message()

    raw = next((b.text for b in msg.content if b.type == "text"), "")
    data = json.loads(raw)
    turns = [
        {"speaker": t["speaker"], "text": t["text"].strip()}
        for t in data.get("turns", [])
        if t.get("text", "").strip()
    ]
    usage = {
        "input_tokens": getattr(msg.usage, "input_tokens", 0),
        "output_tokens": getattr(msg.usage, "output_tokens", 0),
        "cache_read_input_tokens": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
    }
    return turns, usage


def stub_script(game: GameData) -> list[Turn]:
    """Deterministic two-host script from the data alone — no API. Lets the
    TTS + stitch stages be exercised offline."""
    a, h = game.away, game.home
    turns: list[Turn] = [
        {"speaker": "pbp", "text": (
            f"Welcome to O27 Game of the Week! It's the {a['city']} {a['name']} "
            f"visiting the {h['city']} {h['name']} on {game.game_date}.")},
        {"speaker": "color", "text": (
            "Should be a good one. Both these clubs can swing it, and you know "
            "anything can happen once we get rolling.")},
    ]
    for e in game.scoring_events[:8]:
        turns.append({"speaker": "pbp", "text": (
            f"{e['batter']} delivers — {e['runner']} comes around to score! "
            f"It's {e['visitors_score']} to {e['home_score']}.")})
        turns.append({"speaker": "color", "text": (
            "That's how you manufacture a run. Big swing in this one.")})
    win = game.winner
    star = game.batting_stars[0]["name"] if game.batting_stars else "the home side"
    turns.append({"speaker": "pbp", "text": (
        f"And that's the ballgame! Final score: {a['name']} {game.away_score}, "
        f"{h['name']} {game.home_score}."
        + (f" The {win['name']} take it." if win else ""))})
    turns.append({"speaker": "color", "text": (
        f"Player of the game has to be {star}. What a performance. "
        "We'll see you next time on O27 radio.")})
    return turns
