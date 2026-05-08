"""
League definition and player generation for O27v2.

Supports configurable team counts (8–36) via league config JSON files.
Player names are drawn from regional pools with weighted sampling:
  USA 50% | Latin 30% | Japan/Korea 10% | Other 10%

Phase 10 roster (per team, 19 players total):
  - 8 position players (CF, SS, 2B, 3B, RF, LF, 1B, C — all is_pitcher=0)
  - 4 starting pitchers (rotation; one bats #9 each game, all is_pitcher=1)
  - 4 relievers (bullpen-only; never bat in regulation; all is_pitcher=1)
  - 3 jokers (1 per archetype: power, speed, contact)

The "committee" role from Phase 8 is gone: CF/SS/2B no longer pitch.
Starters cycle through the rotation game-by-game (see sim.py).
"""
from __future__ import annotations
import json
import os
import random
from typing import Any

from o27v2 import config as v2cfg
from o27v2 import scout as _scout

_DATA_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_NAMES_DIR    = os.path.join(_DATA_DIR, "names")
_CONFIGS_DIR  = os.path.join(_DATA_DIR, "league_configs")
_TEAMS_DB     = os.path.join(_DATA_DIR, "teams_database.json")

# ---------------------------------------------------------------------------
# Data loaders (cached at module level)
# ---------------------------------------------------------------------------

_name_pools: dict[str, dict] | None = None
_regions_meta: dict | None = None
_teams_db: list[dict] | None = None


def _load_name_pools() -> dict[str, dict]:
    """Load the viperball-derived raw name pools.

    Returns a dict shaped like:
        {
          "male_first":   {bucket_key: [name, ...], ...},   # 40 buckets
          "female_first": {bucket_key: [name, ...], ...},   # 40 buckets
          "surnames":     {bucket_key: [name, ...], ...},   # 39 buckets
        }
    The bucket keys come from the source JSON files unchanged. The
    `regions.json` meta file maps high-level world-regions onto a list
    of these raw bucket keys.
    """
    global _name_pools
    if _name_pools is None:
        _name_pools = {}
        for kind in ("male_first", "female_first", "surnames"):
            path = os.path.join(_NAMES_DIR, f"{kind}.json")
            with open(path, encoding="utf-8") as fh:
                _name_pools[kind] = json.load(fh)
    return _name_pools


def _load_regions_meta() -> dict:
    """Load the world-region metadata: super-region groupings + named
    presets the league-creation form exposes."""
    global _regions_meta
    if _regions_meta is None:
        with open(os.path.join(_NAMES_DIR, "regions.json"), encoding="utf-8") as fh:
            _regions_meta = json.load(fh)
    return _regions_meta


def get_name_region_presets() -> dict[str, dict]:
    """Return preset id -> {label, weights} for the new-league form."""
    return _load_regions_meta().get("presets", {})


def get_name_regions() -> dict[str, dict]:
    """Return region id -> {label, first_keys, surname_keys}."""
    return _load_regions_meta().get("regions", {})


def _load_teams_db() -> list[dict]:
    global _teams_db
    if _teams_db is None:
        with open(_TEAMS_DB, encoding="utf-8") as fh:
            _teams_db = json.load(fh)
    return _teams_db


def get_league_configs() -> dict[str, dict]:
    """Return all preset league configs keyed by config id."""
    configs: dict[str, dict] = {}
    for fname in sorted(os.listdir(_CONFIGS_DIR)):
        if fname.endswith(".json"):
            with open(os.path.join(_CONFIGS_DIR, fname), encoding="utf-8") as fh:
                cfg = json.load(fh)
                configs[cfg["id"]] = cfg
    return configs


def get_config(config_id: str) -> dict:
    """Load a single league config by id."""
    path = os.path.join(_CONFIGS_DIR, f"{config_id}.json")
    if not os.path.exists(path):
        raise ValueError(f"Unknown league config: {config_id!r}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_custom_config(
    *,
    team_count: int,
    leagues_count: int = 2,
    divisions_per_league: int = 3,
    games_per_team: int = 162,
    season_days: int = 186,
    intra_division_weight: float = 0.46,
    inter_division_weight: float = 0.54,
    season_year: int = 2026,
    season_start_month: int = 4,
    season_start_day: int = 1,
    all_star_break_month: int = 7,
    all_star_break_day: int = 13,
    all_star_break_days: int = 4,
    weekly_off_dows: list[int] | None = None,
    max_consecutive_game_days: int = 20,
    target_stand_length: int = 3,
    level: str = "MLB",
    label: str | None = None,
    gender: str = "male",
    name_region_preset: str | None = None,
    name_region_weights: dict[str, float] | None = None,
) -> dict:
    """Build a league-config dict from raw inputs (the parametric form path).

    Validates the team-count math: must be even and divisible by
    `leagues_count * divisions_per_league` so divisions can be even-sized.
    Weights are normalised to sum to 1.0 before being stored.

    Raises `ValueError` with a human-readable message on invalid input
    so the form handler can surface it to the user.
    """
    if team_count < 2:
        raise ValueError("Team count must be at least 2.")
    if team_count % 2 != 0:
        raise ValueError(
            f"Team count must be even (got {team_count}) — "
            f"a balanced schedule needs every team to have a partner each day."
        )
    if leagues_count < 1:
        raise ValueError("Leagues count must be at least 1.")
    if divisions_per_league < 1:
        raise ValueError("Divisions per league must be at least 1.")
    total_divs = leagues_count * divisions_per_league
    if team_count % total_divs != 0:
        raise ValueError(
            f"Team count ({team_count}) must divide evenly across "
            f"{leagues_count} leagues × {divisions_per_league} divisions "
            f"({total_divs} total). Try {((team_count // total_divs) + 1) * total_divs} "
            f"or {(team_count // total_divs) * total_divs} teams."
        )
    if games_per_team < 1:
        raise ValueError("Games per team must be at least 1.")
    if intra_division_weight < 0 or inter_division_weight < 0:
        raise ValueError("Division weights must be non-negative.")

    # Normalise weights so 0.4 / 0.6 and 40 / 60 both work.
    total_w = intra_division_weight + inter_division_weight
    if total_w <= 0:
        # No division weighting — equal-opponent schedule.
        intra_norm, inter_norm = 0.0, 0.0
    else:
        intra_norm = intra_division_weight / total_w
        inter_norm = inter_division_weight / total_w

    teams_per_division = team_count // total_divs

    # Resolve name distribution. Explicit weights win; otherwise look up
    # the named preset; otherwise fall back to the americas_pro defaults.
    presets = get_name_region_presets()
    if name_region_weights is None:
        if name_region_preset and name_region_preset in presets:
            resolved_weights = dict(presets[name_region_preset]["weights"])
        else:
            resolved_weights = _default_region_weights()
    else:
        resolved_weights = _normalise_weights(name_region_weights)

    g = (gender or "male").lower()
    if g not in ("male", "female", "mixed"):
        raise ValueError(f"Gender must be 'male', 'female', or 'mixed' (got {gender!r}).")

    # Pick league names: AL/NL for 2, otherwise League 1..N.
    if leagues_count == 2:
        leagues = ["AL", "NL"]
    elif leagues_count == 1:
        leagues = ["MLB"]
    else:
        leagues = [f"L{i + 1}" for i in range(leagues_count)]

    return {
        "id":                     "custom",
        "label":                  label or f"Custom — {team_count} teams",
        "team_count":             team_count,
        "level":                  level,
        "games_per_team":         games_per_team,
        "season_days":            season_days,
        "leagues":                leagues,
        "divisions_per_league":   divisions_per_league,
        "teams_per_division":     teams_per_division,
        "intra_division_weight":  intra_norm,
        "inter_division_weight":  inter_norm,
        "season_year":            season_year,
        "season_start_month":     season_start_month,
        "season_start_day":       season_start_day,
        "all_star_break_month":   all_star_break_month,
        "all_star_break_day":     all_star_break_day,
        "all_star_break_days":    all_star_break_days,
        "weekly_off_dows":        list(weekly_off_dows or []),
        "max_consecutive_game_days": int(max_consecutive_game_days),
        "target_stand_length":       int(target_stand_length),
        "gender":                    g,
        "name_region_preset":        name_region_preset,
        "name_region_weights":       resolved_weights,
    }


# ---------------------------------------------------------------------------
# Division assignment helpers
# ---------------------------------------------------------------------------

_LEAGUE_NAMES = ["AL", "NL"]
_DIV_SUFFIXES = ["East", "Central", "West"]


def _div_suffixes_geo(divs_per_league: int) -> list[str]:
    # Ordered west-to-east so the westmost cluster gets index 0.
    if divs_per_league == 2:
        return ["West", "East"]
    if divs_per_league == 3:
        return ["West", "Central", "East"]
    if divs_per_league == 4:
        return ["West", "Mountain", "Central", "East"]
    return [f"Div {i + 1}" for i in range(divs_per_league)]


def _assign_tiered_divisions(
    selected: list[dict], config: dict, rng: random.Random
) -> list[tuple[str, str]]:
    """Build a (league, division) assignment for tiered configs.

    The four tiers come from `config["tier_order"]` (top → bottom). Teams
    are shuffled and dealt round-robin into the tiers — talent-blind
    initial placement, since the user explicitly opted out of tiered
    talent stratification for the demo. The `league` and `division`
    columns are both set to the tier name; a tiered config has no
    sub-divisions inside a tier.
    """
    tier_order = list(config.get("tier_order") or config.get("leagues") or [])
    teams_per_tier = int(config["teams_per_division"])
    if not tier_order or len(tier_order) * teams_per_tier != len(selected):
        raise ValueError(
            f"Tiered config requires len(tier_order) × teams_per_division "
            f"== team_count (got {len(tier_order)} × {teams_per_tier} != "
            f"{len(selected)})."
        )

    indices = list(range(len(selected)))
    rng.shuffle(indices)

    assignments: list[tuple[str, str]] = [("", "")] * len(selected)
    for slot, orig_idx in enumerate(indices):
        tier = tier_order[slot // teams_per_tier]
        assignments[orig_idx] = (tier, tier)
    return assignments


def _assign_geographic_divisions(
    selected: list[dict], config: dict
) -> list[tuple[str, str]]:
    """
    Build a (league, division) assignment for each team in ``selected``,
    bucketing by longitude so divisions are geographically coherent.

    Walks west→east, alternating leagues, then chunks each league's
    longitude-ordered teams into West / Central / East slices. Teams
    without lat/lon fall back to a Kansas-ish midpoint so missing
    coords don't distort the partition.
    """
    leagues          = config.get("leagues", ["AL", "NL"])
    divs_per_league  = config["divisions_per_league"]
    teams_per_div    = config["teams_per_division"]
    suffixes         = _div_suffixes_geo(divs_per_league)

    indexed = list(enumerate(selected))
    indexed.sort(key=lambda item: (
        item[1].get("lon", -95.0),
        -item[1].get("lat", 39.0),
    ))

    league_buckets: dict[str, list[int]] = {lg: [] for lg in leagues}
    for pos, (orig_idx, _td) in enumerate(indexed):
        lg = leagues[pos % len(leagues)]
        league_buckets[lg].append(orig_idx)

    assignments: list[tuple[str, str]] = [("", "")] * len(selected)
    for lg, ordered_indices in league_buckets.items():
        for div_idx, suf in enumerate(suffixes):
            chunk = ordered_indices[
                div_idx * teams_per_div : (div_idx + 1) * teams_per_div
            ]
            for orig_idx in chunk:
                assignments[orig_idx] = (lg, f"{lg} {suf}")
    return assignments


# ---------------------------------------------------------------------------
# Position constants
# ---------------------------------------------------------------------------

POSITIONS = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C", "P"]

# Phase 10: position players only — pitchers are generated separately as
# a dedicated rotation + bullpen (see generate_players()).
FIELDER_POSITIONS = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]

_JOKER_NAMES = [
    "The Ace", "The Blaze", "The Clutch", "The Dart", "The Edge",
    "The Flame", "The Ghost", "The Hawk", "The Ice", "The Joker",
    "The King", "The Legend", "The Maverick", "The Nail", "The Oracle",
    "The Phantom", "The Quick", "The Rock", "The Storm", "The Titan",
    "The Ultra", "The Viper", "The Wild", "The X-Factor", "The Yankee",
    "The Zenith", "The Arrow", "The Baron", "The Cobra", "The Dagger",
]

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# Default region distribution — preserves legacy "Americas-pro / MLB-like"
# behavior so any code path that doesn't explicitly set a name config
# (tests, smoke-test, legacy presets) still gets the same shape it
# always did. Looks up the canonical preset from regions.json so the
# defaults stay one source of truth.
def _default_region_weights() -> dict[str, float]:
    presets = get_name_region_presets()
    if "americas_pro" in presets:
        return dict(presets["americas_pro"]["weights"])
    # Fallback if regions.json gets stripped down: 100% USA.
    return {"us": 1.0}


def _normalise_weights(weights: dict[str, float]) -> dict[str, float]:
    """Clamp negative weights to 0, then renormalise so the values
    sum to 1.0. Empty / all-zero input falls back to the default."""
    cleaned = {k: max(0.0, float(v)) for k, v in (weights or {}).items()}
    total = sum(cleaned.values())
    if total <= 0:
        return _default_region_weights()
    return {k: v / total for k, v in cleaned.items() if v > 0}


def _pick_weighted_key(rng: random.Random, weights: dict[str, float]) -> str:
    """Sample one key from a probability dict. Caller is responsible for
    making sure the values sum to ~1.0. Distinct from `_weighted_pick`
    which takes (label, weight) tuples — used by bats / throws / etc."""
    r = rng.random()
    cumulative = 0.0
    last_key = None
    for k, w in weights.items():
        cumulative += w
        last_key = k
        if r < cumulative:
            return k
    return last_key or next(iter(weights))


def make_name_picker(
    rng: random.Random,
    *,
    gender: str = "male",
    region_weights: dict[str, float] | None = None,
):
    """Return a callable `_name() -> (str, str)` that draws a unique
    full name + ISO 3166-1 alpha-2 country code using the configured
    world-region distribution and gender.

    `gender` values:
      - "male"    → uses male_first.json
      - "female"  → uses female_first.json
      - "mixed"   → 50/50 split per draw

    `region_weights` is a {region_id: weight} dict (region ids defined
    in regions.json). Falls back to the americas_pro preset when None
    is passed. Weights are auto-normalised.

    Sub-region cohesion: when a region has a `subregions` list, each
    draw picks ONE subregion by weight and BOTH the first name and the
    surname come from that subregion's keys. This keeps culturally
    distinct first/surname pairs from being mixed (e.g. preventing
    'Babar Iyer' from a flat south-asia pool — instead you get
    'Babar Iqbal' or 'Aarav Sharma').
    """
    pools         = _load_name_pools()
    regions_meta  = get_name_regions()
    weights       = _normalise_weights(region_weights)
    used: set[str] = set()
    g_lower = (gender or "male").lower()

    def _first_pool_kind() -> str:
        if g_lower == "male":
            return "male_first"
        if g_lower == "female":
            return "female_first"
        return "male_first" if rng.random() < 0.5 else "female_first"

    def _gather(bucket_kind: str, keys: list[str]) -> list[str]:
        bucket = pools.get(bucket_kind, {})
        out: list[str] = []
        for k in keys:
            v = bucket.get(k)
            if isinstance(v, list):
                out.extend(v)
        return out

    def _resolve_country(node: dict) -> str:
        """Country code resolution. Direct `country` wins; otherwise sample
        from `country_weights` if present; else empty string."""
        c = node.get("country")
        if isinstance(c, str) and c:
            return c
        cw = node.get("country_weights")
        if isinstance(cw, dict) and cw:
            return _pick_weighted_key(rng, _normalise_weights(cw))
        return ""

    def _draw_from_region(region_id: str) -> tuple[str | None, str | None, str]:
        """Return (first, last, country) for one region draw. Either name
        may be None if the bucket is empty (caller should retry)."""
        region = regions_meta.get(region_id)
        if region is None:
            return None, None, ""
        subregions = region.get("subregions")
        if isinstance(subregions, list) and subregions:
            sr_weights = _normalise_weights(
                {str(i): float(sr.get("weight", 0.0)) for i, sr in enumerate(subregions)}
            )
            idx = int(_pick_weighted_key(rng, sr_weights))
            sr = subregions[idx]
            first_candidates = _gather(_first_pool_kind(), sr.get("first_keys", []))
            last_candidates  = _gather("surnames",          sr.get("surname_keys", []))
            country = _resolve_country(sr) or _resolve_country(region)
            if not first_candidates or not last_candidates:
                return None, None, country
            return rng.choice(first_candidates), rng.choice(last_candidates), country
        # Flat region: independent first/surname draws (legacy shape).
        first_candidates = _gather(_first_pool_kind(), region.get("first_keys") or [])
        last_candidates  = _gather("surnames",          region.get("surname_keys") or [])
        country = _resolve_country(region)
        if not first_candidates or not last_candidates:
            return None, None, country
        return rng.choice(first_candidates), rng.choice(last_candidates), country

    def _name() -> tuple[str, str]:
        for _ in range(500):
            region_id = _pick_weighted_key(rng, weights)
            first, last, country = _draw_from_region(region_id)
            if not first or not last:
                continue
            full = f"{first} {last}"
            if full not in used:
                used.add(full)
                return full, country
        return f"Player {rng.randint(100, 999)}", ""

    return _name


def progression_weights(season_index: int) -> dict[str, float]:
    """Return the O27 thesis-shaped region weights for a given league
    season index (1 = inaugural season).

    Linearly interpolates between the named presets:
        season  1  → o27_year_1
        season  5  → o27_year_5
        season 10+ → o27_year_10

    Custom league configs that want the cricket-conversion arc to play
    out automatically can call this once per season and store the result
    in `name_region_weights` before generating that season's free-agent
    pool / draftees. Existing leagues keep their static preset.
    """
    presets = get_name_region_presets()
    y1  = dict((presets.get("o27_year_1")  or {}).get("weights", {}))
    y5  = dict((presets.get("o27_year_5")  or {}).get("weights", {}))
    y10 = dict((presets.get("o27_year_10") or {}).get("weights", {}))
    if not (y1 and y5 and y10):
        # If the presets aren't loadable, fall back to americas_pro.
        return _default_region_weights()

    def _lerp(a: dict[str, float], b: dict[str, float], t: float) -> dict[str, float]:
        keys = set(a) | set(b)
        return {k: a.get(k, 0.0) * (1 - t) + b.get(k, 0.0) * t for k in keys}

    s = max(1, int(season_index))
    if s <= 1:
        out = y1
    elif s >= 10:
        out = y10
    elif s <= 5:
        out = _lerp(y1, y5, (s - 1) / 4.0)
    else:
        out = _lerp(y5, y10, (s - 5) / 5.0)
    return _normalise_weights(out)


# Archetype profiles, PA modifiers, and committee positions are defined in
# o27v2/config.py and imported here so that a single-file edit re-tunes the
# full v2 pipeline.
_JOKER_ARCHETYPES    = v2cfg.ARCHETYPE_PROFILES
_JOKER_PA_MODIFIERS  = v2cfg.ARCHETYPE_PA_MODIFIERS
_COMMITTEE_POSITIONS = v2cfg.COMMITTEE_POSITIONS


def _player_age(rng: random.Random) -> int:
    """
    Draw a player age from a realistic bell curve peaking at 27-30.
    Range: 22-38, mu=28, sigma=3.2 (clamped).
    """
    age = round(rng.gauss(28, 3.2))
    return max(22, min(38, age))


# ---------------------------------------------------------------------------
# Task #65: talent-tier attribute roller
# ---------------------------------------------------------------------------
# Each tier has a probability mass and a 20-80 scout grade range. Each
# attribute on each player is rolled INDEPENDENTLY against this table, so
# a player can be elite Power but average Eye, etc. — producing the spiky
# archetypes the league needs.
_TALENT_TIERS: list[tuple[float, int, int]] = [
    # (probability, lo_grade, hi_grade)
    #
    # Re-tuned 2026 (pass 2): prior table still had a too-fat middle
    # (Good-to-Average ≈ 25%) which, after ~47 independent rolls per
    # team, produced very tight team-to-team parity by Law of Large
    # Numbers — every roster regressed to league mean. The new shape
    # is bimodal on PURPOSE: fatter top tail (more genuine stars),
    # hollow middle (fewer "league average" filler players), and a
    # very long replacement-level tail. Combined with the per-team
    # org_shift in generate_players(), this produces:
    #
    #   - clearly identifiable star talent (top 5% of grades)
    #   - real depth charts where bench guys are visibly worse than
    #     starters (instead of clones at grade ~50)
    #   - team-level talent gaps (good orgs roll above the curve on
    #     every player; bad orgs roll below)
    #
    # Approximate shape:
    #   Elite+/Elite combined   =  7%   (was 2%)
    #   Excellent               = 12%   (was 8%)
    #   Very Good → Average      = 21%   (was 35% — hollowed)
    #   Below-Avg → Sub-Repl    = 60%   (was 53% — slightly fatter)
    #
    # O27 is more offensively dynamic than MLB by design (27-out single
    # innings, 3-foul cap, 2C rule), so a wide-spread talent distribution
    # rewards offensive archetypes — elite contact hitters carve up the
    # below-replacement long tail, producing the monster lines and
    # blowout games the format is built for. Do NOT compress this back
    # toward MLB's tighter bell.
    #
    # Elite+ stays as a transcendent grade-81+ slice — beyond the 20-80
    # canonical scale by design, so the .01% players exist without being
    # capped by the scout-grade ceiling.
    (0.020, 81, 95),  # Elite+ (transcendent) — was 0.5%
    (0.050, 75, 80),  # Elite                 — combined top = 7%
    (0.120, 65, 74),  # Excellent             — was 8%
    (0.090, 60, 64),  # Very Good             ┐
    (0.060, 55, 59),  # Good                  ├─ middle band = 21% (was 35%)
    (0.040, 50, 54),  # Above Average         │
    (0.020, 45, 49),  # Average               ┘
    (0.150, 40, 44),  # Below Average
    (0.220, 30, 39),  # Replacement
    (0.230, 20, 29),  # Sub-Replacement       — long tail
]


def _roll_tier_grade(rng: random.Random, team_shift: int = 0) -> int:
    """Roll one attribute against the 9-tier league talent distribution.

    Returns an integer 20-80 scout grade at seed time. The Elite+ tier
    (81-95) is reserved for talent earned via multi-season development
    (see `o27v2/development.py`) — capping the initial roll at 80 means
    every league starts in true parity, then dynasties emerge as good
    orgs grow their players past the standard scout ceiling.

    `team_shift` defaults to 0 — the league is seeded via a snake draft
    over a team-blind pool, so per-team shifting is no longer applied
    at draft time. Org_strength still drives talent growth between
    seasons via the development pass.

    Older callers that pass a non-zero shift still get the additive
    behaviour clamped to [20, 80].
    """
    r = rng.random()
    cumulative = 0.0
    for prob, lo, hi in _TALENT_TIERS:
        cumulative += prob
        if r < cumulative:
            # Clamp BOTH ends of the range to 80 so an Elite+ tier roll
            # (lo=81, hi=95) collapses to exactly 80 — the league's top
            # legitimate scout grade. Higher grades are earned via
            # development, not handed out at seed time.
            seed_lo = min(lo, 80)
            seed_hi = min(hi, 80)
            return max(20, min(80, rng.randint(seed_lo, seed_hi) + team_shift))
    # Floating-point safety net (probabilities sum to 1.0).
    lo, hi = _TALENT_TIERS[-1][1], _TALENT_TIERS[-1][2]
    return max(20, min(80, rng.randint(min(lo, 80), min(hi, 80)) + team_shift))


def _tier_unit(rng: random.Random, team_shift: int = 0) -> float:
    """Tier-rolled grade converted to the [0,1] unit float the engine uses."""
    return _scout.to_unit(_roll_tier_grade(rng, team_shift))


def _roll_org_grade(rng: random.Random) -> int:
    """Roll an org_strength against the full 9-tier ladder, NOT capped
    at 80. Org_strength influences multi-season development rate, so a
    handful of teams legitimately start in the Elite/Elite+ band — they
    just can't translate that into per-pitch outcomes (which is where
    the old team_shift abuse lived). Player attributes are still capped
    at 80 at seed time via `_roll_tier_grade`."""
    r = rng.random()
    cumulative = 0.0
    for prob, lo, hi in _TALENT_TIERS:
        cumulative += prob
        if r < cumulative:
            return max(20, min(95, rng.randint(lo, hi)))
    lo, hi = _TALENT_TIERS[-1][1], _TALENT_TIERS[-1][2]
    return max(20, min(95, rng.randint(lo, hi)))


# Per-team org-strength: a 20-95 scout-grade team attribute, rolled
# from the same _TALENT_TIERS distribution as individual player
# attributes and then PERSISTED on the teams row (see seed_league()).
# `team_shift` is derived as `org_strength - 50`, so:
#
#   org_strength == 50 → no shift (league-mean org)
#   org_strength == 80 → +30 shift  (Elite org → all rolls +30)
#   org_strength == 25 → -25 shift  (Sub-Repl org → all rolls -25)
#
# An Elite+ org (81-95) compresses its tier rolls hard against the
# grade-95 ceiling, producing rosters where almost every player is
# 80+ and the team-mean lands in the upper 70s / low 80s. The inverse
# happens at the cellar. This produces a real "MLB vs AAA" spread
# between best and worst orgs — substantially wider than the prior
# Gaussian-shift approach, and now visible / sortable on the team page.
def _org_strength_to_shift(org_strength: int) -> int:
    """Convert a team's org_strength (20-95 grade) to its tier-roll shift."""
    return org_strength - 50


# ---------------------------------------------------------------------------
# Realism layer — handedness + park factor rolls
# ---------------------------------------------------------------------------
# Realistic 1990s-2000s ratios for MLB-shaped lineups. Lefties are slightly
# over-represented vs population because they get pulled into the game.

_BATS_WEIGHTS  = [("R", 0.55), ("L", 0.33), ("S", 0.12)]
_THROWS_WEIGHTS_HITTER  = [("R", 0.78), ("L", 0.22)]
# Pitchers skew slightly more left than the position-player population.
_THROWS_WEIGHTS_PITCHER = [("R", 0.70), ("L", 0.30)]


def _weighted_pick(rng: random.Random, weights: list[tuple[str, float]]) -> str:
    """Pick from (label, weight) tuples; weights need not sum to 1.0."""
    total = sum(w for _, w in weights)
    r = rng.random() * total
    cumulative = 0.0
    for label, w in weights:
        cumulative += w
        if r < cumulative:
            return label
    return weights[-1][0]


def _roll_bats(rng: random.Random) -> str:
    return _weighted_pick(rng, _BATS_WEIGHTS)


def _roll_throws(rng: random.Random, is_pitcher: bool) -> str:
    return _weighted_pick(
        rng, _THROWS_WEIGHTS_PITCHER if is_pitcher else _THROWS_WEIGHTS_HITTER
    )


def _roll_park_factors(rng: random.Random) -> tuple[float, float]:
    """Per-team park HR and hits multipliers.

    Most parks are roughly neutral; a handful land at the extremes
    (Coors-likes, pitcher's parks). HR variance is wider than overall hits.
    """
    hr   = round(max(0.85, min(1.20, rng.gauss(1.00, 0.07))), 3)
    hits = round(max(0.93, min(1.08, rng.gauss(1.00, 0.04))), 3)
    return hr, hits


# Roster shape — Task #65.
ACTIVE_FIELDERS  = 12   # 8 starting positions + 4 bench
ACTIVE_DH        = 3    # 3 DH/utility bats (matches the 3-DH batting lineup)
ACTIVE_PITCHERS  = 19   # full active pitching staff (rotation + bullpen, no roles)
RESERVE_HITTERS  = 8    # reserve position-player pool (covers IL fill-ins)
RESERVE_PITCHERS = 5    # reserve arms (top up the active pitching staff on IL)
# Active = 12 + 3 + 19 = 34. Total = 34 + 8 + 5 = 47 players/team.
ACTIVE_POSITION_TOTAL = ACTIVE_FIELDERS + ACTIVE_DH  # 15 — fill target on IL


def _make_hitter(
    rng: random.Random,
    pos: str,
    is_active: int,
    name: str,
    team_shift: int = 0,
    country: str = "",
) -> dict:
    """Build one position-player dict with every attribute rolled
    independently against the talent-tier distribution (Task #65).

    `skill` is the engine's overall hitter rating; `speed` is its own
    independent roll. Both come from the same 9-tier ladder so genuine
    elite bats and burners exist alongside replacement-level players.

    `team_shift` is added to every tier roll so all players on a strong
    org skew higher and all players on a weak org skew lower. Set by
    `generate_players` once per team.
    """
    def roll() -> int:
        return _roll_tier_grade(rng, team_shift)

    skill_g  = roll()
    speed_g  = roll()
    # Realism layer — independently tier-rolled so a hitter can be elite
    # power but average eye, etc. Drives distinct stat-line shapes.
    contact_g  = roll()
    power_g    = roll()
    eye_g      = roll()
    # Defense layer — general glove + arm independently tier-rolled.
    # A great-glove no-bat archetype (low skill, elite defense) is a
    # real type in this sport.
    defense_g  = roll()
    arm_g      = roll()

    # Per-position sub-ratings. Strategy:
    # - Roll one "primary specialty" group at full tier
    # - Roll the other two groups at attenuated rolls (mean ~ general
    #   defense - 5, with variance), so most players are visibly weaker
    #   outside their group
    # - With ~10% probability roll all three at full tier → Ben
    #   Zobrist-style utility archetype. UT-as-a-position was removed;
    #   bench players carry canonical positions now (the utility
    #   archetype is just a secondary trait that can show up at any
    #   spot on the diamond).
    is_utility = rng.random() < 0.10
    if is_utility:
        if_g  = roll()
        of_g  = roll()
        cat_g = roll()
    else:
        # Pick a primary specialty group based on the canonical position.
        primary = "if"
        if pos in ("LF", "CF", "RF"):
            primary = "of"
        elif pos == "C":
            primary = "cat"
        # Specialist: the primary group gets a full roll; others get a
        # lower clamped roll (average grade 35-40, replacement-ish).
        spec_high = roll()
        spec_low_a = max(20, roll() // 2 + 10)
        spec_low_b = max(20, roll() // 2 + 10)
        if primary == "if":
            if_g, of_g, cat_g = spec_high, spec_low_a, spec_low_b
        elif primary == "of":
            if_g, of_g, cat_g = spec_low_a, spec_high, spec_low_b
        else:  # cat
            if_g, of_g, cat_g = spec_low_a, spec_low_b, spec_high
    # Pitcher_skill on a position player is only used in emergencies.
    pskill_g = roll() // 2 + 10  # cap fielder-pitching at low grades
    return {
        "name": name,
        "country": country,
        "position": pos,
        "is_pitcher": 0,
        "is_joker": 0,
        "skill": skill_g,
        "speed": speed_g,
        "pitcher_skill": max(20, min(45, pskill_g)),
        # Tuned upward 2025: prior values (gauss(0.10, 0.05) /
        # gauss(0.28, 0.06)) produced a league 2C-attempt rate of ~1.6%
        # of PAs — the second-chance mechanic was a rounding error
        # instead of the load-bearing tactic it's supposed to be.
        # New means target a 4-8% league rate by both relaxing the
        # contact-quality gate and bumping aggressiveness.
        "stay_aggressiveness": round(_clamp(rng.gauss(0.30, 0.10)), 3),
        "contact_quality_threshold": round(_clamp(rng.gauss(0.50, 0.10)), 3),
        "archetype": "",
        "pitcher_role": "",
        "hard_contact_delta": 0.0,
        "hr_weight_bonus":    0.0,
        "age": _player_age(rng),
        "stamina":   roll() // 2 + 10,  # irrelevant for hitters
        "is_active": is_active,
        # Realism layer
        "contact":  contact_g,
        "power":    power_g,
        "eye":      eye_g,
        "command":  50,   # pitcher-only attr; neutral on hitters
        "movement": 50,   # pitcher-only attr; neutral on hitters
        "bats":     _roll_bats(rng),
        "throws":   _roll_throws(rng, is_pitcher=False),
        "defense":  defense_g,
        "arm":      arm_g,
        "defense_infield":  if_g,
        "defense_outfield": of_g,
        "defense_catcher":  cat_g,
        # Baserunning skill + aggressiveness, independent rolls. A smart
        # average-speed runner (high baserunning, mid speed) is just as
        # useful on the bases as a pure burner.
        "baserunning":        roll(),
        "run_aggressiveness": roll(),
        # Phase 5e — work-ethic / work-habits. Both rolled from the same
        # 9-tier ladder (capped at 80 like every other seed-time
        # attribute). habit_cup starts at 0.5 (neutral).
        "work_ethic":  roll(),
        "work_habits": roll(),
        "habit_cup":   0.5,
    }


def _make_pitcher(
    rng: random.Random,
    is_active: int,
    name: str,
    team_shift: int = 0,
    country: str = "",
) -> dict:
    """Build one pitcher dict with Stuff (`pitcher_skill`) and Stamina
    rolled INDEPENDENTLY against the tier ladder.

    No pitcher_role is set — the manager AI derives today's role at game
    time from the live attribute values, so an aging arm with decayed
    Stamina automatically slides from rotation into middle relief without
    any persisted re-tagging.
    """
    def roll() -> int:
        return _roll_tier_grade(rng, team_shift)

    stuff_g   = roll()
    stamina_g = roll()
    # Realism layer — pitcher Command + Movement rolled INDEPENDENTLY of
    # Stuff. Drives the Maddux-vs-Ryan stat-shape spectrum: high Command
    # = low BB regardless of Stuff; high Movement = ground-ball pitcher.
    command_g  = roll()
    movement_g = roll()
    # Pitchers also get defense/arm — they field comebackers and bunts,
    # and high-arm pitchers help suppress steals. Capped lower than
    # position players since pitcher fielding matters less in O27.
    defense_g  = max(20, roll() // 2 + 15)
    arm_g      = max(20, roll() // 2 + 20)
    throws = _roll_throws(rng, is_pitcher=True)
    return {
        "name": name,
        "country": country,
        "position": "P",
        "is_pitcher": 1,
        "is_joker": 0,
        "skill":  max(20, roll() // 2 + 10),  # weak bat
        "speed":  max(20, roll() // 2 + 15),
        "pitcher_skill": stuff_g,
        # Pitchers as hitters — 2C still rarer than position players,
        # but lifted from 0.05 → 0.20 in step with the position-player
        # bump so pitcher PAs aren't structurally locked out of the
        # second-chance mechanic.
        "stay_aggressiveness": round(_clamp(rng.gauss(0.20, 0.06)), 3),
        "contact_quality_threshold": round(_clamp(rng.gauss(0.40, 0.08)), 3),
        "archetype": "",
        "pitcher_role": "",   # Task #65: live derivation only — never stored.
        "hard_contact_delta": 0.0,
        "hr_weight_bonus":    0.0,
        "age": _player_age(rng),
        "stamina":   stamina_g,
        "is_active": is_active,
        # Realism layer
        "contact":  50,   # hitter-only attr; neutral on pitchers' weak bats
        "power":    50,
        "eye":      50,
        "command":  command_g,
        "movement": movement_g,
        "bats":     throws,   # pitchers historically bat from their throwing side
        "throws":   throws,
        "defense":  defense_g,
        "arm":      arm_g,
        "defense_infield":  50,   # pitchers field their own mound; sub-groups neutral
        "defense_outfield": 50,
        "defense_catcher":  50,
        # Pitchers don't bat in O27 → baserunning is academic. Neutral.
        "baserunning":        50,
        "run_aggressiveness": 50,
        # Phase 5e — work-ethic / work-habits. Same shape as hitters.
        "work_ethic":  roll(),
        "work_habits": roll(),
        "habit_cup":   0.5,
    }


def generate_players(
    team_idx: int,
    rng: random.Random,
    home_bonus: float = 0.0,
    org_strength: int = 50,
    name_config: dict | None = None,
) -> list[dict]:
    """Generate ~47 players for a single team (legacy helper).

    Composition (active = 34, reserve = 13, total = 47):
      - 12 active position players (8 starters at canonical positions
        CF/SS/2B/3B/RF/LF/1B/C plus 4 utility bench)
      -  3 active DH/utility bats
      - 19 active pitchers
      -  8 reserve position players (is_active=0)
      -  5 reserve pitchers (is_active=0)

    Every attribute is rolled independently against the talent-tier
    distribution (`_TALENT_TIERS`).

    `org_strength`, `team_idx`, and `home_bonus` are accepted for
    backward compatibility but no longer bias the rolls — the league
    is seeded via a snake draft over a flat, team-blind player pool
    (see `_run_snake_draft`), which is what produces realistic
    parity. This helper is kept for non-league callers (smoke tests,
    one-off batch sims).
    """
    cfg = name_config or {}
    _name = make_name_picker(
        rng,
        gender         = cfg.get("gender", "male"),
        region_weights = cfg.get("region_weights"),
    )

    players: list[dict] = []

    def _hitter(pos: str, is_active: int) -> dict:
        nm, country = _name()
        return _make_hitter(rng, pos, is_active=is_active, name=nm, country=country)

    def _pitcher(is_active: int) -> dict:
        nm, country = _name()
        return _make_pitcher(rng, is_active=is_active, name=nm, country=country)

    # ---- Active position players: 8 starting positions + 4 bench ----
    for pos in FIELDER_POSITIONS:
        players.append(_hitter(pos, is_active=1))
    # Active bench: 4 backups at high-rotation positions (catchers rest
    # a lot, middle infield rotates, CF backup is near-everyday).
    # No "UT" position — every bench guy carries a canonical position.
    for pos in ("CF", "SS", "2B", "C"):
        players.append(_hitter(pos, is_active=1))

    # ---- Active DH/utility bats ----
    for _ in range(ACTIVE_DH):
        players.append(_hitter("DH", is_active=1))

    # ---- Active pitching staff (no role buckets) ----
    for _ in range(ACTIVE_PITCHERS):
        players.append(_pitcher(is_active=1))

    # ---- Reserve pool: bench-level depth, promoted on injury ----
    # Round-robin one reserve at each canonical fielding position, then
    # cycle if RESERVE_HITTERS exceeds 8. No "UT" — every reserve guy
    # carries a canonical position.
    _RESERVE_POSITIONS = ("CF", "SS", "2B", "3B", "RF", "LF", "1B", "C")
    for i in range(RESERVE_HITTERS):
        pos = _RESERVE_POSITIONS[i % len(_RESERVE_POSITIONS)]
        players.append(_hitter(pos, is_active=0))
    for _ in range(RESERVE_PITCHERS):
        players.append(_pitcher(is_active=0))

    return players


# ---------------------------------------------------------------------------
# Snake-draft seeding (replaces per-team team_shift talent generation)
# ---------------------------------------------------------------------------
#
# Old model: each team rolled its own org_strength (20-95), and every player
# attribute on the roster got an additive shift of `org_strength - 50`. With
# a 9-tier distribution that hands out a +30 shift to ~7% of teams and a
# -25 shift to ~23%, the inevitable result was 155-7 vs 7-155 standings.
#
# New model:
#   1. Generate a flat, team-blind player pool (1.4× the roster slots league-
#      wide) from the same 9-tier distribution. No team bias.
#   2. Snake-draft per slot type (CF, SS, ..., UT, DH, P) so the elite-level
#      talent at each position is forced to disperse across teams.
#   3. Surplus players land in the free-agent pool (team_id = NULL) — used
#      by the weekly Sunday match-day waiver sweep (Phase 2).
#
# Snake direction alternates across the entire draft (not per slot type),
# so a team that drafts first in round 1 drafts last in round 2 regardless
# of slot type. This keeps cumulative draft-position equity tight.

# Draft slot definition: (position_string, n_active_per_team, n_reserve_per_team)
# UT was removed — every bench / reserve hitter carries a canonical
# position now, so when one shows up in a box score or on the FA page
# it reads as e.g. "backup CF" instead of "UT". Active backups go to
# the high-rotation positions (CF, SS, 2B, C) where real teams need
# day-to-day coverage; reserve depth is one body per canonical
# position so injury fill-ins are position-typed.
_DRAFT_SLOTS: list[tuple[str, int, int]] = [
    # 8 canonical starters (1 active each).
    ("CF", 1, 0), ("SS", 1, 0), ("2B", 1, 0), ("3B", 1, 0),
    ("RF", 1, 0), ("LF", 1, 0), ("1B", 1, 0), ("C",  1, 0),
    # DH (3 active).
    ("DH", 3, 0),
    # Active backups at high-rotation positions: catchers rest a lot,
    # middle infield rotates, CF backup is a near-everyday role.
    # Each entry adds 1 active + 1 reserve at the given position.
    ("CF", 1, 1), ("SS", 1, 1), ("2B", 1, 1), ("C",  1, 1),
    # Reserve depth at corners + outfield (less rotation needed): 1
    # reserve body per position so injury fill-ins are position-typed.
    ("3B", 0, 1), ("1B", 0, 1), ("LF", 0, 1), ("RF", 0, 1),
    # Pitchers (19 active + 5 reserve).
    ("P", 19, 5),
]

_DRAFT_OVERSAMPLE = 1.4   # generate 40% more players than rosters need
                          # → the surplus is the initial free-agent pool


def _player_overall(p: dict) -> int:
    """Composite rating used to sort the draft pool. Hitters: skill +
    contact + power + eye averaged; pitchers: pitcher_skill + command +
    movement averaged. Keeps ranking aligned with what the engine
    actually rewards per-PA."""
    if p.get("is_pitcher"):
        return (int(p.get("pitcher_skill", 50))
              + int(p.get("command", 50))
              + int(p.get("movement", 50))) // 3
    return (int(p.get("skill", 50))
          + int(p.get("contact", 50))
          + int(p.get("power", 50))
          + int(p.get("eye", 50))) // 4


def _generate_draft_pool(
    n_teams: int,
    rng: random.Random,
    name_picker,
) -> dict[str, list[dict]]:
    """Build the league-wide player pool, keyed by slot position.

    `_DRAFT_SLOTS` may list the same position multiple times (e.g. CF
    appears once as a starter and again as an active+reserve backup),
    so we aggregate slots per position before sizing the pool. The
    pool is unsorted at this point — the draft sorts on demand so a
    fresh rng draw determines tie-break order.
    """
    slots_per_pos: dict[str, int] = {}
    for pos, n_active, n_reserve in _DRAFT_SLOTS:
        slots_per_pos[pos] = slots_per_pos.get(pos, 0) + n_active + n_reserve

    pool: dict[str, list[dict]] = {}
    for pos, total_slots in slots_per_pos.items():
        target = int(round(n_teams * total_slots * _DRAFT_OVERSAMPLE))
        bucket: list[dict] = []
        for _ in range(target):
            nm, country = name_picker()
            if pos == "P":
                bucket.append(_make_pitcher(rng, is_active=0, name=nm, country=country))
            else:
                bucket.append(_make_hitter(rng, pos, is_active=0, name=nm, country=country))
        pool[pos] = bucket
    return pool


_DRAFT_SORT_NOISE = 6   # ± grade points of jitter on the draft-rank sort.
                        # Without it, the top-N picks at every position go
                        # to teams in strict overall order and the FA pool
                        # ends up uniformly worse than every roster — so
                        # the match-day waiver sweep can never find a
                        # positive-improvement claim. With ±6 of jitter the
                        # FA pool overlaps team-roster talent enough that
                        # legitimate upgrades exist on day 1, while still
                        # preserving the parity property at the team-mean
                        # level (the noise cancels out across ~55 picks).


def _run_snake_draft(
    team_ids: list[int],
    pool: dict[str, list[dict]],
    rng: random.Random,
) -> tuple[dict[int, list[dict]], list[dict]]:
    """Snake-draft players from `pool` onto teams. Returns
    (assignments, free_agents).

    For each slot type, runs (n_active + n_reserve) rounds where every
    team picks the best remaining player at that position. Snake
    direction alternates per round across the entire draft so first-
    pick equity stays balanced.

    The first `n_active` picks per team at each position are flagged
    is_active=1 (active roster); the remainder are is_active=0
    (reserve depth, promoted on injury). Surplus players keep
    is_active=0 and are returned as free agents.
    """
    assignments: dict[int, list[dict]] = {tid: [] for tid in team_ids}
    order = list(team_ids)
    rng.shuffle(order)

    global_round = 0
    for pos, n_active, n_reserve in _DRAFT_SLOTS:
        # Jittered sort: each player's draft-rank gets ± _DRAFT_SORT_NOISE
        # grade points of random nudge. The on-roster `skill`/`pitcher_skill`
        # values are unchanged — this only affects pick order, so the
        # FA pool ends up overlapping team talent instead of being a
        # strict bottom-N slice.
        bucket = sorted(pool.get(pos, []),
                        key=lambda p: _player_overall(p) + rng.uniform(
                            -_DRAFT_SORT_NOISE, _DRAFT_SORT_NOISE),
                        reverse=True)
        per_team = n_active + n_reserve
        # Picks 0..n_active-1 → active; picks n_active..per_team-1 → reserve.
        for slot_idx in range(per_team):
            is_active = 1 if slot_idx < n_active else 0
            round_order = order if (global_round % 2 == 0) else list(reversed(order))
            for tid in round_order:
                if not bucket:
                    break
                pick = bucket.pop(0)
                pick["is_active"] = is_active
                assignments[tid].append(pick)
            global_round += 1
        # Whatever's left in this position bucket → free agents.
        # is_active stays 0 (default for FAs).
        pool[pos] = bucket  # pool now holds only the surplus

    free_agents: list[dict] = []
    for leftovers in pool.values():
        for fa in leftovers:
            fa["is_active"] = 0
            free_agents.append(fa)
    return assignments, free_agents


def _team_org_strength_from_roster(players: list[dict]) -> int:
    """Recompute a team's org_strength as the mean composite rating of
    its active roster, clamped to the 20-95 grade range. The persisted
    org_strength is now a *reflection* of actual talent (so the team
    page sort still works), not a hidden multiplier biasing rolls."""
    actives = [p for p in players if p.get("is_active")]
    if not actives:
        return 50
    return max(20, min(95, round(sum(_player_overall(p) for p in actives) / len(actives))))


def seed_league(rng_seed: int = 42, config_id: str = "30teams",
                config: dict | None = None) -> None:
    """
    Insert teams and their players into the database.
    Safe to call only once (checks for existing data first).

    Pass either `config_id` (loads from `data/league_configs/<id>.json`)
    or a fully-built `config` dict — the dict wins when both are given.
    Custom configs from the `/new-league` form take this path.

    Team selection strategy:
      1. Take ALL available teams at the config's declared level.
      2. If still short, fill the remainder from adjacent levels (AAA before AA, etc.).
      3. Shuffle at each stage to ensure variety when multiple runs with different seeds.

    This guarantees a 36-team MLB config gets all 36 MLB entries and does not
    silently fall back to randomly mixing in MiLB teams.
    """
    from o27v2 import db

    existing = db.fetchone("SELECT COUNT(*) as n FROM teams")
    if existing and existing["n"] > 0:
        return

    config  = config if config is not None else get_config(config_id)
    level   = config.get("level", "MLB")
    n_teams = config["team_count"]

    all_teams  = _load_teams_db()
    rng        = random.Random(rng_seed)

    # Stage 1: All teams at the target level (shuffled for variety)
    primary = [t for t in all_teams if t["level"] == level]
    rng.shuffle(primary)
    selected: list[dict] = list(primary[:n_teams])

    # Stage 2: Fill the shortfall from adjacent levels in priority order
    if len(selected) < n_teams:
        level_order = ["MLB", "AAA", "AA", "A"]
        used_levels = {level}
        for fill_level in level_order:
            if fill_level in used_levels:
                continue
            if len(selected) >= n_teams:
                break
            extras = [t for t in all_teams if t["level"] == fill_level]
            rng.shuffle(extras)
            needed = n_teams - len(selected)
            selected += extras[:needed]
            used_levels.add(fill_level)

    # Stage 3: Final safety net (should never be needed with the current DB)
    if len(selected) < n_teams:
        remaining = [t for t in all_teams if t not in selected]
        rng.shuffle(remaining)
        selected += remaining[: n_teams - len(selected)]

    if config.get("schedule_mode") == "tiered":
        div_map = _assign_tiered_divisions(selected, config, rng)
    else:
        div_map = _assign_geographic_divisions(selected, config)

    # Name-distribution config: gender + region weights flow through to
    # every player's name draw. Pulled from the league config so a
    # "Nordic" preset produces Nordic names, etc.
    name_config = {
        "gender":         config.get("gender", "male"),
        "region_weights": config.get("name_region_weights"),
    }

    rng2 = random.Random(rng_seed)
    from o27v2.managers import roll_manager

    # Phase 1: insert all teams with their rolled org_strength. The
    # value is rolled from the same 9-tier ladder players use (uncapped
    # at 95 here — orgs CAN be Elite+ at seed; only player attributes
    # are capped at 80). Org_strength drives multi-season player
    # development (see o27v2/development.py): high-org teams grow
    # talent faster between seasons, building dynasties organically.
    # It does NOT bias per-pitch outcomes — that role is gone.
    team_ids: list[int] = []
    for idx, (team_def, (league_name, division)) in enumerate(zip(selected, div_map)):
        abbrev = team_def.get("abbreviation") or team_def.get("abbrev", "???")
        city   = team_def.get("city", "")
        name   = team_def.get("name", "Team")

        park_hr, park_hits = _roll_park_factors(rng2)
        mgr = roll_manager(rng2)
        # Org_strength rolled on the full 9-tier ladder (uncapped at 95) —
        # ~7% of teams genuinely start with Elite+/Elite development orgs,
        # ~12% Excellent, etc. Drives multi-season player growth without
        # biasing per-pitch outcomes.
        org_strength = _roll_org_grade(rng2)
        team_id = db.execute(
            "INSERT INTO teams (name, abbrev, city, division, league, "
            "park_hr, park_hits, manager_archetype, mgr_quick_hook, "
            "mgr_bullpen_aggression, mgr_leverage_aware, mgr_joker_aggression, "
            "mgr_pinch_hit_aggression, mgr_platoon_aggression, mgr_run_game, "
            "mgr_bench_usage, org_strength)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, abbrev, city, division, league_name,
             park_hr, park_hits,
             mgr["manager_archetype"], mgr["mgr_quick_hook"],
             mgr["mgr_bullpen_aggression"], mgr["mgr_leverage_aware"],
             mgr["mgr_joker_aggression"], mgr["mgr_pinch_hit_aggression"],
             mgr["mgr_platoon_aggression"], mgr["mgr_run_game"],
             mgr["mgr_bench_usage"], org_strength),
        )
        team_ids.append(team_id)

    # Phase 2: generate the league-wide draft pool and snake-draft it.
    # No team bias — every player is rolled from the same 9-tier
    # distribution. Surplus players become free agents (team_id NULL),
    # picked up by the weekly Sunday match-day waiver sweep.
    name_picker = make_name_picker(
        rng2,
        gender         = name_config.get("gender", "male"),
        region_weights = name_config.get("region_weights"),
    )
    pool = _generate_draft_pool(len(team_ids), rng2, name_picker)
    assignments, free_agents = _run_snake_draft(team_ids, pool, rng2)

    # Phase 3: persist drafted rosters + free-agent pool, and recompute
    # each team's org_strength from its actual roster.
    insert_sql = """INSERT INTO players
        (team_id, name, country, position, is_pitcher, skill, speed,
         pitcher_skill, stay_aggressiveness, contact_quality_threshold,
         archetype, pitcher_role, hard_contact_delta, hr_weight_bonus,
         age, stamina, is_active,
         contact, power, eye, command, movement, bats, throws,
         defense, arm,
         defense_infield, defense_outfield, defense_catcher,
         baserunning, run_aggressiveness,
         work_ethic, work_habits, habit_cup, salary)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

    # Salary is computed at insert time so the persisted ledger is the
    # canonical source of truth for the rest of the app. Free agents
    # use the default tier cap (no league context).
    from o27v2.valuation import estimate_player_value

    def _row(team_id_or_none, p: dict, league_name: str | None) -> tuple:
        salary = estimate_player_value(p, league_name=league_name)
        return (team_id_or_none, p["name"], p.get("country", ""),
                p["position"], p["is_pitcher"],
                p["skill"], p["speed"], p["pitcher_skill"],
                p["stay_aggressiveness"], p["contact_quality_threshold"],
                p.get("archetype", ""), p.get("pitcher_role", ""),
                p.get("hard_contact_delta", 0.0), p.get("hr_weight_bonus", 0.0),
                p.get("age", 27),
                p.get("stamina", p.get("pitcher_skill", 50)),
                p.get("is_active", 1),
                p.get("contact", 50), p.get("power", 50), p.get("eye", 50),
                p.get("command", 50), p.get("movement", 50),
                p.get("bats", "R"), p.get("throws", "R"),
                p.get("defense", 50), p.get("arm", 50),
                p.get("defense_infield", 50),
                p.get("defense_outfield", 50),
                p.get("defense_catcher", 50),
                p.get("baserunning", 50),
                p.get("run_aggressiveness", 50),
                p.get("work_ethic", 50), p.get("work_habits", 50),
                p.get("habit_cup", 0.5),
                salary)

    # Cache team-id → league name so each player's salary uses the
    # right tier cap.
    team_league = {
        row["id"]: row["league"]
        for row in db.fetchall("SELECT id, league FROM teams")
    }

    for team_id in team_ids:
        roster = assignments.get(team_id, [])
        if roster:
            league_name = team_league.get(team_id)
            db.executemany(
                insert_sql,
                [_row(team_id, p, league_name) for p in roster],
            )
        # `teams.org_strength` was rolled at INSERT time above and is
        # NOT recomputed from the drafted roster — it represents the
        # team's development infrastructure, not its current talent.

    if free_agents:
        db.executemany(insert_sql, [_row(None, p, None) for p in free_agents])

    # Auto-attach the O27 Youth League. Default-on; opt out by setting
    # `attach_youth_league: false` on the league config. Failure here is
    # logged but non-fatal — pro-side seeding has already succeeded and
    # the youth tables can be back-filled with a separate call later.
    if config.get("attach_youth_league", True):
        try:
            from o27v2 import youth
            youth.seed_youth_league(rng_seed=rng_seed, seed_year=1)
        except Exception as e:
            import sys
            print(f"[seed_league] youth-league attach failed: {e}", file=sys.stderr)
