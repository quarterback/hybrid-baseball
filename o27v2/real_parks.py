"""Real affiliated-baseball stadiums as an O27 data source.

`data/real_parks.json` is a normalized mirror of the owner's stadium
spreadsheet — every MLB park plus the full minor-league/complex/Dominican
pyramid (see scripts/build_real_parks.py). This module turns those raw records
into the two things the sim actually wants:

  1. **Real geometry to play in.** `park_to_dimensions()` maps a record's seven
     measured outfield distances + per-zone wall heights onto the engine's
     five fence-control points (lf/lcf/cf/rcf/rf) and a per-angle `walls` dict,
     so the park-effects hook reshapes batted balls against Fenway's actual
     37-ft Monster, Coors' alleys, Yankee's short porch, etc. Park factors map
     to the existing per-team park_hr / park_hits multipliers.

  2. **Realistic-but-varied generated parks.** `realistic_park_dimensions()`
     samples a real park of a given tier and jitters it a few feet per zone,
     producing fresh fields that still live inside the real distribution
     instead of the deliberately-exotic global generator.

The raw spreadsheet stores seven zones; the engine interpolates a fence from
five. We use the five that line up with the engine's spray-angle control
points: the two foul lines, the two power alleys, and dead center. The two
"Left/Right Field" gap readings are kept in the dataset but not fed to the
five-point model.

This module imports nothing from o27v2.league (league imports this), keeping
the dependency one-directional.
"""
from __future__ import annotations

import json
import os
import random
from functools import lru_cache
from typing import Optional

_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "real_parks.json")

# Spreadsheet seven-zone keys -> engine five-point keys. The engine's
# _FENCE_ANGLES are (-45, -22.5, 0, 22.5, 45): foul lines, alleys, center.
_ZONE_TO_ENGINE = {
    "lf": "left_line",
    "lcf": "left_center",
    "cf": "center",
    "rcf": "right_center",
    "rf": "right_line",
}

# Park-data MLB abbreviations differ from teams_database.json in a handful of
# cases. Normalizing both sides lets a real-MLB league pair each club with its
# own stadium (Brewers -> American Family Field) before falling back to
# positional assignment for anything unmatched.
_ABBREV_ALIASES = {
    "AZ": "ARI", "TB": "TBR", "KC": "KCR", "SD": "SDP", "SF": "SFG",
    "CWS": "CHW", "WSH": "WSN", "ATH": "OAK",
}


def normalize_abbrev(abbrev: Optional[str]) -> str:
    """Canonicalize a team abbreviation for cross-source matching."""
    a = (abbrev or "").strip().upper()
    return _ABBREV_ALIASES.get(a, a)


@lru_cache(maxsize=1)
def load_real_parks() -> list[dict]:
    """All real-park records (cached). Treat the returned list as read-only."""
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def parks_for_tier(tier: str) -> list[dict]:
    """Records whose coarse tier matches (MLB / AAA / AA / A+ / A / R)."""
    t = (tier or "").strip().upper()
    return [p for p in load_real_parks() if (p.get("tier") or "").upper() == t]


def find_by_abbrev(abbrev: str) -> Optional[dict]:
    """The MLB record for a team abbreviation, or None. Alias-aware."""
    want = normalize_abbrev(abbrev)
    if not want:
        return None
    for p in load_real_parks():
        if p.get("team") and normalize_abbrev(p["team"]) == want:
            return p
    return None


def _zone(rec: dict, kind: str, engine_key: str, default: float) -> float:
    """Fetch a single zone value (kind = 'dist' | 'wall'), tolerating gaps."""
    raw = (rec.get(kind) or {}).get(_ZONE_TO_ENGINE[engine_key])
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def classify_shape(dims: dict) -> str:
    """Map real five-point dimensions onto the nearest engine archetype key
    (for the UI label/blurb + quirk gating). Most real parks are 'balanced';
    the classifier just flags the obvious exotics."""
    lf, lcf = dims["lf"], dims["lcf"]
    cf = dims["cf"]
    rcf, rf = dims["rcf"], dims["rf"]
    lines = (lf + rf) / 2.0
    alleys = (lcf + rcf) / 2.0

    if lines < 305 and cf >= 430:
        return "bathtub"
    if cf < 378 and lines < 318:
        return "bandbox"
    if alleys >= 412 and cf >= 430:
        return "cavernous"
    if rf <= 318 and (lf - rf) >= 28:
        return "short_porch_rf"
    if lf <= 318 and (rf - lf) >= 28:
        return "short_porch_lf"
    if cf - alleys >= 38:
        return "triangle"
    return "balanced"


def park_to_dimensions(rec: dict) -> dict:
    """Build an engine park_dimensions dict from a real-park record.

    Returns {lf, lcf, cf, rcf, rf, wall_h, walls, shape} — the same shape the
    procedural generator emits, plus a `walls` map of per-angle fence heights
    that o27.engine.park_effects interpolates (the Green Monster only matters
    if its height rides the spray angle). `wall_h` is the rounded mean of the
    five, a sane scalar for any consumer that ignores `walls`.
    """
    dims = {k: int(round(_zone(rec, "dist", k, d)))
            for k, d in (("lf", 330), ("lcf", 385), ("cf", 400),
                         ("rcf", 385), ("rf", 330))}
    walls = {k: int(round(_zone(rec, "wall", k, 8)))
             for k in ("lf", "lcf", "cf", "rcf", "rf")}
    dims["walls"] = walls
    dims["wall_h"] = int(round(sum(walls.values()) / len(walls)))
    dims["shape"] = classify_shape(dims)
    return dims


# The raw empirical HR factor cannot be used as park_hr directly: the engine's
# hr_bar = fence / park_hr lever amplifies it ~5-13x (a 0.79 factor nearly
# erases HRs). Calibrated values in the per-park `engine` block are solved so
# the live resolver reproduces the listed factor (scripts/calibrate_real_parks.py).
# For any record lacking a calibration we compress the HR residual toward 1.
_HR_FALLBACK_COMPRESSION = 0.15


def park_factors(rec: dict) -> tuple[float, float]:
    """(park_hr, park_hits) engine multipliers for a record.

    Prefers the calibrated `engine` block — solved so the live resolver
    reproduces this park's listed factors. Falls back to a gently compressed
    mapping (HR pulled toward neutral, AVG ~linear) when uncalibrated, and to
    neutral 1.0 when a park has no factors at all.
    """
    eng = rec.get("engine") or {}
    if eng.get("park_hr") and eng.get("park_hits"):
        return float(eng["park_hr"]), float(eng["park_hits"])
    pf = rec.get("park_factors") or {}
    hr = pf.get("hr")
    avg = pf.get("avg")
    park_hr = round(1.0 + (float(hr) - 1.0) * _HR_FALLBACK_COMPRESSION, 4) if hr else 1.0
    park_hits = round(float(avg), 4) if avg else 1.0
    return park_hr, park_hits


def realistic_park_dimensions(rng: random.Random, tier: str = "MLB") -> dict:
    """A fresh, realistic-but-varied park: seed from a random real park of
    `tier` and jitter each measurement a few feet so the result lives inside
    the real distribution rather than the deliberately-exotic global
    generator. Returns the same dict shape as `park_to_dimensions`.
    """
    pool = parks_for_tier(tier) or parks_for_tier("MLB") or load_real_parks()
    base = park_to_dimensions(rng.choice(pool))

    # Jitter the five fence points; a shared corner/alley skew keeps the park
    # internally coherent (corners move together, not independently).
    skew = rng.uniform(-8, 8)
    jittered = {}
    for k in ("lf", "lcf", "cf", "rcf", "rf"):
        j = rng.gauss(0, 6) + (skew if k in ("lf", "rf") else skew * 0.4)
        jittered[k] = int(round(base[k] + j))
    walls = {k: max(2, int(round(base["walls"][k] + rng.gauss(0, 2))))
             for k in ("lf", "lcf", "cf", "rcf", "rf")}

    # Honor the engine's physical floors so the jitter can't produce an
    # impossible field (mirrors _roll_park_dimensions' clamps).
    out = {
        "lf": max(250, jittered["lf"]),
        "lcf": max(300, jittered["lcf"]),
        "cf": max(355, jittered["cf"]),
        "rcf": max(300, jittered["rcf"]),
        "rf": max(250, jittered["rf"]),
        "walls": walls,
    }
    out["wall_h"] = int(round(sum(walls.values()) / len(walls)))
    out["shape"] = classify_shape(out)
    return out
