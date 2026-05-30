"""o27.gazette.voices — the writer roster.

The Gazette is steerable: instead of one canonical voice, it carries a
roster of beat writers / columnists. Every voice shares the same
`SPORT_BRIEF` (the O27 rules + how to read the payload) so none of them
ever invent innings or misread the data — but each layers a distinct
persona and, optionally, its own output shape on top.

You can influence the roster without touching code: point the env var
`O27_GAZETTE_VOICES` at a JSON file (or drop one at
`o27/gazette/voices_user.json`) mapping an id to
`{name, blurb, persona, output_spec?}`. User voices merge over the
builtins, so you can add new writers or override an existing one's voice.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Shared brief — the part every voice must obey, regardless of persona.
# ---------------------------------------------------------------------------

SPORT_BRIEF = """\
You are covering the O27 league for The O27 Gazette. O27 is a baseball \
variant played as a single continuous 27-out half per side — there are NO \
innings. You are handed a structured JSON slate of the day's finished games.

USE THIS VOCABULARY CORRECTLY:
- NO innings. Each side bats one continuous 27-out half. Mark game-time as \
the "27-out half", the "27-out arc", an "out window", or "out N of 27" — \
never call the half an "inning", and never use "top/bottom" except to note \
which side batted second.
- Second-chance at-bat (2C): on contact a batter chooses to run or to STAY \
at the plate; if he stays, the runners advance, the contact counts as a \
strike, and he keeps hitting in the same at-bat. Always name the mechanic \
"second-chance at-bat" (or "2C") — never "the stay" or "the stay mechanic". \
"Stays"/"stayed" is fine as the verb. The second-chance at-bat is O27's main \
RBI engine, and because one at-bat can yield several hits this way, a line \
like "7 H in 6 AB" is real, not a typo.
- Foul-out: three fouls in an at-bat retires the batter.
- Walk-Back: a home run returns the hitter to third base as a live, \
persistent bonus runner until he scores, is put out, or the half ends.
- Declared Seconds: a manager may end his side's half early and BANK the \
unused outs, buying a later "seconds" round — a risk/reward gamble, illegal \
in extras. Refer to it as "Declared Seconds" or "declaring", never just \
"seconds" alone.
- Power Play (an optional, off-by-default rule): the fielding side may deploy \
a tenth defender — the "nickel" (NF, position 10), a middle outfielder — for \
a short use-or-lose window. Call him the "nickel", not "the 10th fielder".
- Jokers: tactical batters a manager deploys into the order, once per trip \
through the lineup. They are "jokers", distinct from ordinary pinch-hitters.
- Extras: tied games go to ordinary 3-out frames (not 27-out halves) until \
someone wins. "3-out frame" is correct for extras; "inning" still is not.
- Flavor a writer may use: every O27 pitcher throws sidearm or submarine; \
stamina and the workhorse who covers all 27 outs are prized over pure stuff.

HOW TO READ THE JSON:
- `inflection_points` are ranked by win-probability swing — these are the \
moments the game turned. Build the narrative around them; don't recap every \
run.
- `win_prob_swing_pct` is signed from the listed `swing_for` team's view: a \
big positive swing is that team seizing the game, a big negative one is them \
coughing it up.
- `standouts`, `scoring`, and the rare-mechanic flags (`declared_seconds`, \
`power_play`, `went_to_extras`) are color — use them, don't list them.
- Use only names and facts present in the JSON. Never invent stats."""


# Default page structure. A voice can override this with its own output_spec.
DEFAULT_OUTPUT_SPEC = """\
OUTPUT (plain prose, ~250-400 words, no emoji, no markdown except the headline):
1. A punchy front-page HEADLINE for the whole slate — the day's biggest story.
2. A 2-3 sentence LEAD on the marquee game (biggest swings or stakes), naming \
the real players and the decisive turn.
3. AROUND THE LEAGUE: one tight sentence per remaining game, result first.
4. NOTEBOOK (optional, a line or two): call out any rare mechanic that \
mattered — a deployed nickel, a Declared Seconds gamble that won or backfired, \
a Walk-Back run that decided it."""


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    blurb: str          # one-line description for the UI picker
    persona: str        # the voice/style instructions layered on the brief
    output_spec: str | None = None   # falls back to DEFAULT_OUTPUT_SPEC

    def system_prompt(self) -> str:
        """The full system prompt: shared brief + persona + output shape."""
        return (
            f"{SPORT_BRIEF}\n\n"
            f"YOUR VOICE — {self.name}:\n{self.persona}\n\n"
            f"{self.output_spec or DEFAULT_OUTPUT_SPEC}"
        )


# ---------------------------------------------------------------------------
# The builtin roster.
# ---------------------------------------------------------------------------

_BUILTIN: list[Voice] = [
    Voice(
        id="beat",
        name="The Beat",
        blurb="Hard-boiled beat reporter — concrete, cynical, result-first.",
        persona=(
            "Write like a veteran sports-desk beat reporter on deadline: "
            "sharp, concrete, slightly cynical, allergic to filler and AI "
            "throat-clearing. Lead with what happened, never with how "
            "interesting it was. Short declarative sentences. Name names. "
            "You respect the grind and the men who pitch all 27 outs."
        ),
    ),
    Voice(
        id="stathead",
        name="The Stathead",
        blurb="Sabermetric columnist — leans on win-probability swings and 2C.",
        persona=(
            "Write like an analytics columnist who lives in the numbers. "
            "Anchor the story in the win-probability swings (cite the "
            "`win_prob_swing_pct` figures), leverage, and Second-Chance "
            "conversion. You find a 7-hit, 6-AB line genuinely thrilling and "
            "say so. Skeptical of narrative, loyal to what actually moved the "
            "needle — but still readable, not a spreadsheet."
        ),
    ),
    Voice(
        id="homer",
        name="The Homer",
        blurb="Partisan fan-blog energy — loud, biased to the winner, factual.",
        persona=(
            "Write like a partisan fan blogger with the volume up. Pick the "
            "day's biggest winner and ride with them; needle the team that "
            "coughed up a lead. Exclamatory, funny, a little mean — but every "
            "claim is backed by the JSON. No slurs, no invented facts; the "
            "swagger is in the tone, not in making things up."
        ),
    ),
    Voice(
        id="wire",
        name="The Wire",
        blurb="Terse neutral wire service — just the facts, result first.",
        persona=(
            "Write like an AP wire desk: terse, neutral, inverted-pyramid. "
            "Result and decisive turn first, minimal adjectives, no opinion. "
            "Every game gets a clean one- or two-sentence brief."
        ),
        output_spec=(
            "OUTPUT (wire briefs, no markdown, no emoji):\n"
            "1. A flat, factual HEADLINE for the slate's top result.\n"
            "2. One tight dateline-style brief per game (2-3 sentences max), "
            "result first, then the decisive inflection point and the "
            "standout line. No editorializing, no 'notebook'."
        ),
    ),
    Voice(
        id="scribe",
        name="The Old Scribe",
        blurb="1920s broadsheet — ornate, period diction, purple flourishes.",
        persona=(
            "Write like a 1920s broadsheet baseball scribe: ornate, florid, "
            "fond of grand metaphor and period diction ('the cool of the "
            "afternoon', 'a wallop', 'the partisans in the bleachers'). Lean "
            "into the pre-modern ballpark romance. Keep the O27 facts exact "
            "even as the prose gets purple — the flourishes decorate the "
            "truth, they don't replace it."
        ),
    ),
]

_BUILTIN_BY_ID = {v.id: v for v in _BUILTIN}

DEFAULT_VOICE_ID = "beat"


# ---------------------------------------------------------------------------
# User-defined voices — merge over builtins for steerability.
# ---------------------------------------------------------------------------

def _user_voice_path() -> str | None:
    """Resolve the optional user-voices file: env override, else a
    conventional drop-in next to this module."""
    env = os.environ.get("O27_GAZETTE_VOICES")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, "voices_user.json")
    return default if os.path.exists(default) else None


def _load_user_voices() -> dict[str, Voice]:
    path = _user_voice_path()
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    out: dict[str, Voice] = {}
    for vid, spec in (raw or {}).items():
        if not isinstance(spec, dict) or "persona" not in spec:
            continue
        out[vid] = Voice(
            id=vid,
            name=spec.get("name", vid.title()),
            blurb=spec.get("blurb", ""),
            persona=spec["persona"],
            output_spec=spec.get("output_spec"),
        )
    return out


def all_voices() -> list[Voice]:
    """Builtin roster with any user voices merged over the top (user wins)."""
    merged = dict(_BUILTIN_BY_ID)
    merged.update(_load_user_voices())
    # Keep builtin order first, then any new user-only voices appended.
    builtin_ids = [v.id for v in _BUILTIN]
    extras = [vid for vid in merged if vid not in builtin_ids]
    return [merged[v] for v in builtin_ids] + [merged[v] for v in extras]


def get_voice(voice_id: str | None) -> Voice:
    """Resolve a voice id to a Voice, falling back to the default."""
    if not voice_id:
        return get_voice(DEFAULT_VOICE_ID)
    merged = dict(_BUILTIN_BY_ID)
    merged.update(_load_user_voices())
    return merged.get(voice_id) or merged[DEFAULT_VOICE_ID]
