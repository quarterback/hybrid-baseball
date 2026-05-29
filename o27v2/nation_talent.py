"""Per-nation talent-generation metrics.

Two 0-100 ratings per country (default 50 = league-neutral):
  * investment — top-end funding (academies, pro pathways). Drives the
    elite-talent spike: the chance a generated player is world-class.
  * grassroots — breadth of development. Drives the average-quality lift
    applied to every player from that nation.

Effects at player generation (see `league._make_hitter` / `_make_pitcher`):
  * Elite spike: `elite_probability` scales from 1/1000 at the low end to
    1/100 at the top, HARD-CAPPED at 1/100. When a player's elite roll
    hits, the maker floors their marquee grades into the world-class band.
  * Average lift: `talent_shift` is an additive scout-grade shift in
    [-LIFT_CAP, +LIFT_CAP] applied to every tier roll, so strong programmes
    skew their whole pool up a bit and weak ones down. Neutral (50) = 0,
    so a default nation reproduces the league's prior behaviour exactly.

Investment leans on the elite spike (stars); grassroots leans on the lift
(depth) — so a poor-but-grassroots nation makes solid pools while a rich
one makes the occasional world-beater.

Values live in data/nation_talent.json so they can ebb and flow between
seasons; unlisted nations default to neutral (50/50). The dice still roll
exactly as before — these ratings only nudge the inputs.
"""
from __future__ import annotations

import json
import os

_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "nation_talent.json")

NEUTRAL = 50

# Elite spike: probability a generated player is world-class, as a function
# of the (investment-weighted) talent index. 1/1000 floor, 1/100 ceiling.
ELITE_MIN_P = 0.001     # 1 in 1000 — the weakest programmes
ELITE_MAX_P = 0.010     # 1 in 100  — never higher, by design

# Average lift: a tier-roll shift of (index-50) * LIFT_K, clamped to ±LIFT_CAP.
LIFT_K   = 0.16         # ≈ ±8 grade points across the 0-100 range
LIFT_CAP = 8

# When the elite roll hits, marquee grades are floored into these bands so
# the player reads as genuinely world-class at seed time. Elite+ (81-95)
# stays earned via development — the seed ceiling is still 80.
ELITE_HEADLINE = (74, 80)   # primary rating (skill / pitcher_skill)
ELITE_SUPPORT  = (68, 80)   # supporting ratings

_cache: dict[str, dict[str, int]] | None = None


def _load() -> dict[str, dict[str, int]]:
    global _cache
    if _cache is None:
        try:
            with open(_DATA_PATH, encoding="utf-8") as fh:
                _cache = json.load(fh).get("ratings", {}) or {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def ratings(country_code: str) -> tuple[int, int]:
    """(investment, grassroots) for a country, defaulting to neutral."""
    row = _load().get((country_code or "").upper())
    if not row:
        return NEUTRAL, NEUTRAL
    return (int(row.get("investment", NEUTRAL)),
            int(row.get("grassroots", NEUTRAL)))


def _elite_index(country_code: str) -> float:
    inv, grass = ratings(country_code)
    return 0.7 * inv + 0.3 * grass


def _lift_index(country_code: str) -> float:
    inv, grass = ratings(country_code)
    return 0.4 * inv + 0.6 * grass


def elite_probability(country_code: str) -> float:
    """Chance a single generated player from this nation is world-class."""
    idx = _elite_index(country_code)
    p = ELITE_MIN_P + (idx / 100.0) * (ELITE_MAX_P - ELITE_MIN_P)
    return max(ELITE_MIN_P, min(ELITE_MAX_P, p))


def talent_shift(country_code: str) -> int:
    """Additive scout-grade shift applied to every tier roll for this
    nation's players. 0 for a neutral (50/50) nation."""
    shift = round((_lift_index(country_code) - NEUTRAL) * LIFT_K)
    return max(-LIFT_CAP, min(LIFT_CAP, shift))


def roll_elite(country_code: str, rng) -> bool:
    """True if a freshly generated player from this nation rolls elite."""
    return rng.random() < elite_probability(country_code)


def reset_cache() -> None:
    """Drop the cached ratings (call after editing the data file)."""
    global _cache
    _cache = None
