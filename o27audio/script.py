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
from .sources import GameData, RoundupData

Turn = dict[str, str]

_SYSTEM = """You are the broadcast booth for an O27 baseball game — two working \
announcers calling a real game in a real league for an audio recap.

O27 is regular baseball with a few of its own features: one continuous 27-out half per \
side; the Second Chance (a batter can re-take an at-bat); the Walk-Back (a bonus runner \
after a home run); Super-Innings (extra rounds when tied). These are normal, familiar \
parts of the game. Mention them only when they bear on the story, the way a seasoned \
announcer refers to anything routine — never stop to explain the rules, and never treat \
them as novelties.

VOICE — you are professionals, not fans reacting to a box score:
  - Authoritative, economical, matter-of-fact. You've called a thousand games. Cut
    adjectives before facts; one strong verb beats three adjectives.
  - Tell the game's STORY — who took control and when — not every at-bat. Select the
    turning points and the damage; skip the rest.
  - Earn your excitement. Save intensity for the genuinely decisive moments; the biggest
    moments get FEWER words, not more. Don't run hot the whole way.

THE SCORE IS NEVER THE STORY — THE BASEBALL IS. This is the most important rule:
  - O27 games are often high-scoring. A 19-9 or a 32-29 final is just the score. Cover it
    EXACTLY as you'd cover a 4-3 game. Lead on the play that mattered, not the number.
    Anchor a rout to the inning it broke open ("scored seven in the fourth and never
    looked back"). Name who did the damage — and who on the other side couldn't stop it.
  - NEVER marvel at, joke about, or call attention to the size of a score or the format.
    No "wild one," "that's a lot of runs," "you don't see that every day," no winking at
    the audience. The listener can hear the score; your job is the baseball behind it.

STRUCTURE (roughly):
  1. Open on the headline beat — who won, the score as a plain fact, and HOW it was won,
     in a line.
  2. The decisive sequence — the inning or rally where the game turned and stayed turned.
  3. Key performances — two or three names with the telling line (homered twice, drove in
     six), including who got chased or couldn't hold it on the losing side.
  4. A clean close — a forward look or a quiet, resonant detail. Not a punchline.

TWO HOSTS — distinct lanes, not two play-by-play voices:
  - "pbp" carries the action and the sequence: what happened.
  - "color" comes in between the action with the why: pitch selection, a defensive
    miscue, how an inning got away, what it means in the standings. Color supplements; it
    does NOT re-describe what pbp just said.
  - On the decisive moment, color stays out of the way and lets pbp land it. They don't
    step on each other, and they are not a comedy duo — wit is dry and rare.

NEVER REPEAT YOURSELF:
  - Every line adds new information or advances the story. Say each run, stat, record, and
    storyline ONCE. Vary sentence structure and vocabulary; no recycled catchphrases or
    filler ("folks," "wow," "what a game").

AUDIO: spoken words only — no stage directions, no asterisks, no "[laughs]". Short,
natural turns (1-3 sentences) that clearly alternate. Aim ~28-40 turns for a competitive
game; fewer if it was one-sided."""


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


def _with_style(base: str) -> str:
    """Append the user-editable house style / lexicon to a base system prompt."""
    style = config.load_style()
    if not style:
        return base
    return base + "\n\n===== HOUSE STYLE & LEXICON (authoritative — obey) =====\n" + style


def build_messages(game: GameData, max_pbp_chars: int = 60000) -> tuple[str, str]:
    """Return ``(system, user_text)`` for the script-generation call."""
    return _with_style(_SYSTEM), _header(game, max_pbp_chars)


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


# ---------------------------------------------------------------------------
# League roundup ("sports radio") — Stage 2
# ---------------------------------------------------------------------------

_ROUNDUP_SYSTEM = """You host an O27 league roundup — a daily "around the league" radio \
segment recapping the day's games and where the league stands. Two working broadcasters \
covering a real league, not fans.

O27 is regular baseball with a few of its own features (the Second Chance, the Walk-Back, \
Super-Innings); treat them as normal and familiar, never as novelties, and don't explain \
them.

VOICE: authoritative, economical, professional — an anchor desk telling the day's story,
not reading a list. Cut adjectives before facts.

THE SCORE IS NEVER THE STORY: O27 games are often high-scoring. Report every score as a
plain fact and cover the baseball — who won, how, who did the damage. NEVER marvel at or
joke about the size of any score or the format. No "wild one," "lot of runs," no winking,
no novelty. Cover a 30-run game exactly as you'd cover a 4-3 game.

STRUCTURE:
  - Open on the day's headline result — the most consequential game or storyline — stated
    cleanly.
  - Run the slate efficiently: each game gets a sentence or two — who won, how it was
    decided, the key name or the turning inning. VARY how you frame each one; don't reuse
    the same sentence shape every time.
  - Standings check: who's leading, who's moving, the real races.
  - League leaders: home runs, RBI, strikeouts — name names, no awe.
  - Transactions wire: the notable moves and what they mean.
  - Close with a clean forward look to the next slate. Not a punchline.

TWO HOSTS: the lead anchor drives the rundown; the analyst adds context and insight, not a
re-read of the score. Restraint over shtick — they are not a comedy duo.

NEVER REPEAT: say each result, name, and number ONCE. Vary sentence structure; no recurring
catchphrases or filler.

AUDIO: spoken words only, short alternating turns (1-3 sentences). Aim ~22-36 turns."""


def _roundup_header(r: RoundupData) -> str:
    lines = [f"O27 LEAGUE ROUNDUP — season {r.season}, game-day {r.date}", ""]
    lines.append(f"TODAY'S SLATE ({len(r.slate)} games):")
    for g in r.slate:
        si = "  (Super-Innings!)" if g["super_inning"] else ""
        lines.append(
            f"  {g['away']} {g['away_score']} @ {g['home']} {g['home_score']}"
            f"  — {g['winner'] or 'tie'} win{si}"
        )

    # Division leaders only, to keep the digest tight.
    lines.append("\nSTANDINGS (division leaders):")
    seen: set[tuple[str, str]] = set()
    for t in r.standings:
        key = (t["league"], t["division"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"  {t['division']}: {t['city']} {t['name']} "
            f"({t['wins']}-{t['losses']})"
        )

    if r.hr_leaders:
        lines.append("\nHOME RUN LEADERS:")
        for x in r.hr_leaders:
            lines.append(f"  {x['name']} ({x['team']}): {x['hr']} HR")
    if r.rbi_leaders:
        lines.append("\nRBI LEADERS:")
        for x in r.rbi_leaders:
            lines.append(f"  {x['name']} ({x['team']}): {x['rbi']} RBI")
    if r.k_leaders:
        lines.append("\nSTRIKEOUT LEADERS (pitchers):")
        for x in r.k_leaders:
            lines.append(f"  {x['name']} ({x['team']}): {x['k']} K")
    if r.transactions:
        lines.append("\nTRANSACTIONS WIRE (most recent):")
        for tx in r.transactions:
            who = f" — {tx['player']}" if tx.get("player") else ""
            team = f" [{tx['team']}]" if tx.get("team") else ""
            lines.append(f"  {tx['game_date']} {tx['event_type']}{team}{who}: {tx['detail']}")
    return "\n".join(lines)


def build_roundup_messages(r: RoundupData) -> tuple[str, str]:
    return _with_style(_ROUNDUP_SYSTEM), _roundup_header(r)


def generate_roundup_script(
    r: RoundupData, model: str | None = None,
) -> tuple[list[Turn], dict[str, Any]]:
    """Call Claude for a roundup show. Raises if no key/SDK."""
    key = config.anthropic_key()
    if not key:
        raise RuntimeError(
            "No Anthropic key (set ANTHROPIC_API_KEY). "
            "Use stub mode to test the pipeline offline."
        )
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed — run "
            "`pip install -r o27audio/requirements.txt`."
        ) from e

    model = model or config.SCRIPT_MODEL
    system, user_text = build_roundup_messages(r)
    client = anthropic.Anthropic(api_key=key)
    with client.messages.stream(
        model=model,
        max_tokens=12000,
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


def stub_roundup_script(r: RoundupData) -> list[Turn]:
    """Deterministic roundup from the data alone — no API."""
    turns: list[Turn] = [
        {"speaker": "pbp", "text": (
            f"Welcome to the O27 League Roundup for game-day {r.date}! "
            f"We've got {len(r.slate)} games to get to.")},
        {"speaker": "color", "text": "Plenty to chew on. Let's run the slate."},
    ]
    for g in r.slate[:10]:
        si = " — and it went to Super-Innings!" if g["super_inning"] else ""
        turns.append({"speaker": "pbp", "text": (
            f"{g['away']} {g['away_score']}, {g['home']} {g['home_score']}. "
            f"{g['winner'] or 'A tie'} on top{si}.")})
    if r.hr_leaders:
        top = r.hr_leaders[0]
        turns.append({"speaker": "color", "text": (
            f"Around the league, {top['name']} still leads in home runs with "
            f"{top['hr']}. That bat is carrying {top['team']}.")})
    turns.append({"speaker": "pbp", "text": (
        "That's your roundup. We'll see you for the next slate. So long!")})
    return turns


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
