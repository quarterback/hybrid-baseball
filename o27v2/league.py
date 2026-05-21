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
from o27v2.archetypes import (
    classify_position_player,
    classify_roster_slot,
    is_hit_capable,
    is_run_capable,
    is_two_way,
    encode_field_positions,
)
from o27 import config as _engine_cfg

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


def build_universe_config(
    *,
    universe_id: str,
    label: str | None,
    leagues: list[dict],
    games_per_team: int = 90,
    season_days: int = 150,
    season_year: int = 2026,
    season_start_month: int = 4,
    season_start_day: int = 1,
    all_star_break_month: int = 7,
    all_star_break_day: int = 13,
    all_star_break_days: int = 4,
    gender: str = "male",
    level: str = "MLB",
) -> dict:
    """Build a peer-universe config: several co-equal, fully-independent
    major leagues in one world, each with its own size, locale and playing
    style. Players move between them off the field (transfers / offseason),
    never via interleague games.

    `leagues` is an ordered list of dicts:
        {name, teams, divisions=1, style="", locale=""}
      * style  — a key from _STYLE_PROFILES (or "" for balanced)
      * locale — a region/preset id from data/names/regions.json (or "")

    Validates each league's team math and that style/locale ids exist.
    Raises ValueError with a human-readable message on bad input.
    """
    if not leagues:
        raise ValueError("A universe needs at least one league.")
    seen_names: set[str] = set()
    league_specs: list[dict] = []
    style_profiles: dict[str, str] = {}
    name_regions: dict[str, str] = {}
    valid_regions = set(get_name_regions().keys()) | set(get_name_region_presets().keys())

    for i, lg in enumerate(leagues):
        name = (lg.get("name") or "").strip()
        if not name:
            raise ValueError(f"League #{i + 1} needs a name.")
        if name in seen_names:
            raise ValueError(f"Duplicate league name: {name!r}.")
        seen_names.add(name)
        teams = int(lg.get("teams", 0) or 0)
        ndiv  = max(1, int(lg.get("divisions", 1) or 1))
        if teams < 2:
            raise ValueError(f"League {name!r} needs at least 2 teams.")
        if teams % 2 != 0:
            raise ValueError(
                f"League {name!r} has {teams} teams — each independent league "
                f"needs an EVEN team count for a balanced schedule."
            )
        if teams % ndiv != 0:
            raise ValueError(
                f"League {name!r}: {teams} teams don't divide evenly into "
                f"{ndiv} divisions."
            )
        style  = (lg.get("style") or "").strip()
        locale = (lg.get("locale") or "").strip()
        if style and style not in _STYLE_PROFILES:
            raise ValueError(f"League {name!r}: unknown style {style!r}.")
        if locale and locale not in valid_regions:
            raise ValueError(f"League {name!r}: unknown locale {locale!r}.")
        league_specs.append({"name": name, "teams": teams, "divisions": ndiv})
        if style:
            style_profiles[name] = style
        if locale:
            name_regions[name] = locale

    team_count = sum(s["teams"] for s in league_specs)
    g = (gender or "male").lower()
    if g not in ("male", "female", "mixed"):
        raise ValueError(f"Gender must be male/female/mixed (got {gender!r}).")

    return {
        "id":                     universe_id,
        "label":                  label or f"Universe — {len(league_specs)} leagues",
        "team_count":             team_count,
        "level":                  level,
        "games_per_team":         int(games_per_team),
        "season_days":            int(season_days),
        "leagues":                [s["name"] for s in league_specs],
        "schedule_mode":          "independent",
        "league_specs":           league_specs,
        "style_profiles":         style_profiles,
        "name_regions":           name_regions,
        # Within a league, split games evenly between same-division and
        # cross-division opponents.
        "intra_division_weight":  0.5,
        "inter_division_weight":  0.5,
        "season_year":            int(season_year),
        "season_start_month":     int(season_start_month),
        "season_start_day":       int(season_start_day),
        "all_star_break_month":   int(all_star_break_month),
        "all_star_break_day":     int(all_star_break_day),
        "all_star_break_days":    int(all_star_break_days),
        "gender":                 g,
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


def _assign_universe_divisions(
    selected: list[dict], league_specs: list[dict], rng: random.Random
) -> list[tuple[str, str]]:
    """Build a (league, division) assignment for a peer-universe config.

    `league_specs` is an ordered list of {name, teams, divisions}. Teams are
    shuffled once (variety across seeds) then dealt into each league in turn
    up to that league's `teams` count, and round-robin'd across its
    divisions. Leagues may be DIFFERENT sizes — that's the whole point of a
    peer universe (an O27-MLB of 24 beside an O27-KBO of 10). Division names
    are '<League> A/B/C…' (or just the league name when a league has one
    division).
    """
    total = sum(int(s["teams"]) for s in league_specs)
    if total != len(selected):
        raise ValueError(
            f"Universe league_specs sum to {total} teams but {len(selected)} "
            f"were selected — these must match (team_count)."
        )
    indices = list(range(len(selected)))
    rng.shuffle(indices)

    assignments: list[tuple[str, str]] = [("", "")] * len(selected)
    cursor = 0
    for spec in league_specs:
        name = spec["name"]
        n    = int(spec["teams"])
        ndiv = max(1, int(spec.get("divisions", 1)))
        for j in range(n):
            orig_idx = indices[cursor + j]
            if ndiv > 1:
                div = f"{name} {chr(65 + (j % ndiv))}"
            else:
                div = name
            assignments[orig_idx] = (name, div)
        cursor += n
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


def make_country_pinned_picker(rng: random.Random, region_id: str,
                                country_code: str, gender: str = "male"):
    """Like `make_name_picker` but pinned to a single country within a
    region's subregion list. Used by the youth league, where each team
    represents one specific country and should never draw cross-country
    names — Team Japan should get Japanese names, not the broader
    east_asia 45/32/15/8 JP/KR/TW/CN mix. Falls back to the regular
    weighted picker if the region has no subregions or no subregion
    matches the country code.

    When a country has multiple cultural-variant subregions in the same
    region (e.g. Brazil's 5 in south_america, or Fiji's native + Indo-
    Fijian pair in pacific_islands), the relative weights between those
    variants are preserved.
    """
    region = get_name_regions().get(region_id) or {}
    subregions = region.get("subregions") or []
    cc = (country_code or "").upper()
    matching = [sr for sr in subregions if str(sr.get("country", "")).upper() == cc]
    if not matching:
        return make_name_picker(rng, gender=gender,
                                region_weights={region_id: 1.0})

    pools = _load_name_pools()
    g_lower = (gender or "male").lower()
    used: set[str] = set()
    sr_weights = _normalise_weights(
        {str(i): float(sr.get("weight", 0.0) or 0.0) for i, sr in enumerate(matching)}
    )

    def _gather(bucket_kind: str, keys: list[str]) -> list[str]:
        bucket = pools.get(bucket_kind, {})
        out: list[str] = []
        for k in keys:
            v = bucket.get(k)
            if isinstance(v, list):
                out.extend(v)
        return out

    def _first_kind() -> str:
        if g_lower == "male":   return "male_first"
        if g_lower == "female": return "female_first"
        return "male_first" if rng.random() < 0.5 else "female_first"

    def _name() -> tuple[str, str]:
        for _ in range(500):
            idx = int(_pick_weighted_key(rng, sr_weights))
            sr  = matching[idx]
            firsts = _gather(_first_kind(), sr.get("first_keys", []))
            lasts  = _gather("surnames",    sr.get("surname_keys", []))
            if not firsts or not lasts:
                continue
            full = f"{rng.choice(firsts)} {rng.choice(lasts)}"
            if full not in used:
                used.add(full)
                return full, cc
        return f"Player {rng.randint(100, 999)}", cc

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


# ---------------------------------------------------------------------------
# Playing-style profiles (mechanical league diversity).
#
# A style profile is a per-attribute additive bias (in 20-95 scout-grade
# points) applied on TOP of org/team_shift at seed time. It rides the same
# clamped `team_shift` path through `_roll_tier_grade`, so a contact-leaning
# league genuinely generates higher-contact / lower-power players — the
# engine and the fast-sim both read these grades, so the statistical
# environment actually differs (not just names/parks).
#
# Biases are deliberately small (|b| <= 10) and roughly zero-sum per profile
# so a styled league keeps internal parity — it skews the *shape* of play,
# not the overall talent level. Keys map to the attribute names used by
# `_make_hitter` / `_make_pitcher`. Selected per-league via a config's
# optional `style_profiles` block; persisted on each teams row.
# ---------------------------------------------------------------------------
# Real-sport-inspired flavours. Each bundle carries BOTH hitter keys
# (contact/power/eye/speed/baserunning/run_aggressiveness/defense/arm) and
# pitcher keys (pitcher_skill≈stuff/velocity, command≈control, movement≈
# groundball/HR-suppression) — `_make_hitter` and `_make_pitcher` each read
# only the keys relevant to them. Because EVERY team in a league shares the
# same profile, intra-league competitive parity is automatic regardless of
# the bias — so the bundles are tuned to push the league's NET run
# environment (the hitter-vs-pitcher interaction) in the intended direction,
# not to be attribute-zero-sum. Validated by per-league HR/K/BB/SB divergence.
_STYLE_PROFILES: dict[str, dict[str, int]] = {
    # Nippon (NPB): contact-first hitters + command artists with lower
    # velocity. NET → highest AVG, lowest K (both sides cut Ks), low HR.
    "npb": {
        "contact": 9, "eye": 4, "power": -12, "speed": -2,        # hitters
        "command": 9, "pitcher_skill": -8, "movement": 5,         # pitchers
    },
    # Dominican: free-swinging sluggers vs. hard, fly-ball-prone arms.
    # NET → most HR, most K, fewest walks. Three-true-outcome ball.
    "dominican": {
        "power": 13, "contact": -5, "eye": -11,                   # hitters
        "pitcher_skill": 9, "command": -6, "movement": -9,        # pitchers
    },
    # European: patience + finesse. NET → highest BB/OBP, low HR, doubles
    # over homers, low K, strong defense. Low-scoring, work-the-count ball.
    # Pitching is command-and-deception (low stuff) so it doesn't rack up Ks.
    "european": {
        "eye": 13, "contact": 4, "power": -13, "defense": 5,      # hitters
        "command": 10, "pitcher_skill": 0, "movement": 4,         # pitchers
    },
    # Caribbean / West Indies: athletic, action-oriented. NET → most SB &
    # triples, high BABIP, contact over power.
    "caribbean": {
        "speed": 10, "baserunning": 10, "run_aggressiveness": 7,  # hitters
        "contact": 5, "power": -5, "defense": 7, "arm": 5,
        "pitcher_skill": -3, "movement": -3,                      # pitchers
    },
    # Athletic / academy: toolsy, high-ceiling, raw. Big power and speed
    # and live arms, but undeveloped contact/discipline/command — boom-or-
    # bust talent. NET → loud tools, lots of HR, lots of K, lots of SB.
    # The "talent-discovery" flavour — raw ability outrunning polish.
    "athletic": {
        "power": 9, "speed": 9, "arm": 8, "baserunning": 7,       # hitters
        "run_aggressiveness": 6, "contact": -5, "eye": -6,
        "pitcher_skill": 8, "command": -7, "movement": -3,        # pitchers
    },
    # Balanced — explicit no-op so a config can name it without special-casing.
    "balanced": {},
}
# Generic aliases (kept for backward-compat with earlier configs/saves).
_STYLE_PROFILES["contact"]       = _STYLE_PROFILES["npb"]
_STYLE_PROFILES["power"]         = _STYLE_PROFILES["dominican"]
_STYLE_PROFILES["speed_defense"] = _STYLE_PROFILES["caribbean"]


def resolve_name_region_weights(spec) -> dict | None:
    """Resolve a per-league locale spec to region weights, reusing the
    EXISTING name data. `spec` may be:
      * a dict of {region_id: weight} (used as-is),
      * a preset id from regions.json `presets`,
      * a single region id from regions.json `regions` (pinned 100%).
    Returns None when the spec is empty/unknown so callers can fall back."""
    if not spec:
        return None
    if isinstance(spec, dict):
        return spec
    presets = get_name_region_presets()
    if spec in presets:
        return dict(presets[spec].get("weights", {}))
    regions = get_name_regions()
    if spec in regions:
        return {spec: 1.0}
    return None


def style_profile_label(name: str | None) -> str:
    """Human-readable label for a style profile key (UI badge)."""
    return {
        "npb":           "Nippon (contact / command)",
        "dominican":     "Dominican (power / TTO)",
        "european":      "European (discipline / OBP)",
        "caribbean":     "Caribbean (speed / BABIP)",
        "athletic":      "Academy (toolsy / high-ceiling)",
        "contact":       "Contact / finesse",
        "power":         "Power / TTO",
        "speed_defense": "Speed & defense",
        "balanced":      "Balanced",
        "":              "",
    }.get(name or "", (name or "").replace("_", " ").title())


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


# Ballpark name generator. Each park gets a distinctive name so the
# scoreboard / box score / team page render as "Final at The Oval"
# rather than the generic city name. Weighted across templates so most
# parks land in the surname-and-place tradition, with a sprinkle of
# cricket-ground evocation (The Oval, The Crucible, etc.) — fitting
# O27's super-inning / sidearm DNA borrowed from cricket.
_PARK_ADJECTIVES = (
    "Crescent", "Beacon", "Iron", "Crystal", "Lighthouse", "Tide",
    "Bridge", "Tower", "Harbor", "Heron", "Cedar", "Granite", "Maple",
    "Silver", "Copper", "Twilight", "Sunset", "Ember", "North",
    "Whitestone", "Bayview", "Highland", "Riverside", "Meadow",
    "Hollow", "Stoneford", "Brookside", "Foxgrove",
    "Gaslight", "Tannery", "Coffin", "Spite", "Crooked", "Wedge",
)
_PARK_NOUNS = (
    "Field", "Stadium", "Park", "Ground", "Yards", "Grounds", "Bowl",
    "Coliseum",
)
_CRICKET_SINGLETONS = (
    "The Oval", "The Crucible", "The Pavilion", "The Citadel",
    "The Bullring", "The Cauldron", "The Pitch", "The Cathedral",
    "The Icebox", "The Funnel", "The Keyhole", "The Wedge",
)


def _roll_ballpark_name(
    rng: random.Random,
    city: str,
    surname_pool: list[str],
    used: set[str],
) -> str:
    """Produce a distinctive ballpark name. Reads a surname pool so each
    park can be tied to a (fictional) founder/owner family. `used` is the
    set of names already taken in this league seed — we reroll up to a
    handful of times to avoid duplicates, falling back to a numbered
    suffix when collisions are stubborn.
    """
    def _attempt() -> str:
        bucket = rng.random()
        if bucket < 0.45 and surname_pool:
            # "[Surname] Field" / "Stadium" / "Park" / "Yards"
            surname = rng.choice(surname_pool)
            noun = rng.choice(_PARK_NOUNS)
            return f"{surname} {noun}"
        if bucket < 0.68:
            # "[Adjective] Park"
            adj  = rng.choice(_PARK_ADJECTIVES)
            noun = rng.choice(_PARK_NOUNS)
            return f"{adj} {noun}"
        if bucket < 0.82 and city:
            # "[City] Coliseum" / "[City] Yards"
            noun = rng.choice(("Coliseum", "Yards", "Dome", "Bowl", "Park"))
            return f"{city} {noun}"
        if bucket < 0.94:
            # Cricket-evoking singleton.
            return rng.choice(_CRICKET_SINGLETONS)
        # Compound — "[Adjective] [Surname] Field"
        adj  = rng.choice(_PARK_ADJECTIVES)
        surname = rng.choice(surname_pool) if surname_pool else "Marlow"
        noun = rng.choice(_PARK_NOUNS)
        return f"{adj} {surname} {noun}"

    for _ in range(8):
        name = _attempt()
        if name not in used:
            used.add(name)
            return name
    # Last-resort: append a Roman-style numeral so it's still flavorful.
    base = _attempt()
    return f"{base} II"


# Park shape archetypes — O27's fiat: the cookie-cutter era never
# happened. Every park is a pre-modern revival or a cricket-ground
# import, so the dimension distribution is much wider than MLB's
# 1990s-2000s norm.
#
# Each archetype has a label (UI) and a blurb (flavor tooltip).
_PARK_SHAPES = (
    ("balanced",        "Balanced",
     "Symmetric pre-modern park — wedge outfield, even alleys"),
    ("short_porch_rf",  "Short Porch (Right)",
     "Foul-line jut: RF dives in under 300 ft, deep CF behind it"),
    ("short_porch_lf",  "Short Porch (Left)",
     "Mirror of Yankee Stadium-style RF jut — LF cheats in"),
    ("cavernous",       "Cavernous",
     "Forbes Field / old Cleveland Stadium territory — death-valley alleys"),
    ("bathtub",         "Bathtub",
     "Polo Grounds shape: 275-ft lines, 480-ft dead-CF chasm"),
    ("triangle",        "Center-Field Triangle",
     "Center-field corner juts back another 30 ft from the alleys"),
    ("oval",            "Oval — Cricket-Ground Revival",
     "MCG-style elliptical boundary — pull HRs vanish, gappers feast"),
    # ── Wild & exotic — geometry that could never pass a modern siting
    # review. Rare (low weights) so a league still feels mostly grounded,
    # but every few teams you stumble onto something deranged.
    ("bandbox",         "The Bandbox",
     "Baker-Bowl tiny — 285-ft lines, 360-ft CF, a spite fence to cope"),
    ("crescent",        "The Crescent — Inverted",
     "Concave boundary — CF caves IN shorter than the alleys; the only park where you pull it to center"),
    ("hourglass",       "The Hourglass",
     "Alleys pinch to 335 ft while lines and CF balloon — gappers die, pull shots and CF moonshots feast"),
    ("coffin_corner",   "Coffin Corner",
     "One foul line dives to 265 ft then the adjacent alley cliffs back to 445 — the other half plays cavernous"),
    ("sawtooth_wedge",  "The Wedge",
     "Monotonic ramp — a 275-ft short porch in left climbing to a 415-ft death valley in right"),
)
_PARK_SHAPE_WEIGHTS = (
    0.40, 0.10, 0.07, 0.13, 0.08, 0.10, 0.12,   # original 7
    0.04, 0.03, 0.04, 0.03, 0.03,               # bandbox, crescent, hourglass, coffin, wedge
)


_QUIRK_CATALOG: tuple[dict, ...] = (
    {"key": "tals_hill",          "label": "Tal's Hill",
     "blurb": "30-degree incline rises into deep center field — flag pole in play, outfielders climb the slope to make the catch",
     "weight": 0.07, "shapes": None},
    {"key": "the_porch",          "label": "The Porch",
     "blurb": "second-deck overhang juts out over the short-side foul line — pop flies clear the wall",
     "weight": 0.08, "shapes": ("short_porch_rf", "short_porch_lf")},
    {"key": "ivy_wall",           "label": "Ivy Wall",
     "blurb": "no padding — outfielders read the carom off red brick and ivy; balls lodged in the vines are ground-rule doubles",
     "weight": 0.10, "shapes": None},
    {"key": "hand_scoreboard",    "label": "Hand-Operated Scoreboard",
     "blurb": "scoreboard operators climb in and out the back of the LF wall between innings",
     "weight": 0.15, "shapes": None},
    {"key": "crows_nest",         "label": "Crow's Nest",
     "blurb": "press box perched three decks above the grandstand — typewriters echo all the way to the bullpen",
     "weight": 0.12, "shapes": None},
    {"key": "the_triangle",       "label": "The Triangle",
     "blurb": "deep CF jogs back 30 feet from the alleys — line drives off the corner ricochet at unpredictable angles",
     "weight": 0.10, "shapes": ("triangle", "cavernous")},
    {"key": "abe_lincoln",        "label": "Lincoln Statue",
     "blurb": "ten-foot statue of Abraham Lincoln stands in deep CF, fair-territory landmark",
     "weight": 0.03, "shapes": None},
    {"key": "trolley_shed",       "label": "Trolley Shed",
     "blurb": "abandoned commuter-rail spur cuts across deep CF — ground-rule double if struck on the fly",
     "weight": 0.03, "shapes": None},
    {"key": "flagpole_play",      "label": "Flag Pole in Play",
     "blurb": "60-foot pole stands in fair territory near the CF wall — drives can carom off the standard",
     "weight": 0.06, "shapes": None},
    {"key": "bullpens_play",      "label": "Bullpens in Play",
     "blurb": "visitors' bullpen sits along the LF foul line — relievers warm up in fair territory",
     "weight": 0.07, "shapes": None},
    {"key": "knothole_gates",     "label": "Knothole Gates",
     "blurb": "narrow wooden slats in the LF wall let neighborhood kids press their faces in to watch for free",
     "weight": 0.05, "shapes": None},
    {"key": "crescent_grandstand","label": "Crescent Grandstand",
     "blurb": "fan-shaped wooden grandstand wraps over both foul lines",
     "weight": 0.07, "shapes": None},
    {"key": "concrete_crater",    "label": "The Crater",
     "blurb": "playing field sits 20 ft below street level — every drive looks like a moonshot from the upper deck",
     "weight": 0.04, "shapes": None},
    {"key": "wire_basket",        "label": "Wire Basket",
     "blurb": "wire basket protrudes from the top of the LF wall — robs would-be HRs into doubles",
     "weight": 0.05, "shapes": None},
    {"key": "death_valley",       "label": "Death Valley",
     "blurb": "left-center alley plays 440+ feet from the plate — flyball pitchers thrive, gap doubles die",
     "weight": 0.08, "shapes": ("cavernous", "bathtub")},
    {"key": "lima_bean",          "label": "The Lima Bean",
     "blurb": "asymmetric grandstand wraps the field at odd angles — sun fields shift inning to inning",
     "weight": 0.03, "shapes": None},
    {"key": "round_bowl",         "label": "Round Bowl",
     "blurb": "no foul-line pocket — grandstand wraps the playing field as a uniform curve",
     "weight": 0.18, "shapes": ("oval",)},
    {"key": "low_picket",         "label": "Low Picket Fence",
     "blurb": "cricket-ground tin fence — barely four feet high, fielders vault to rob HRs",
     "weight": 0.20, "shapes": ("oval",)},
    {"key": "members_pavilion",   "label": "Members' Pavilion",
     "blurb": "wood-clad pavilion looms over the LF boundary, hot-tin roof reflects the sun into the batter's eye",
     "weight": 0.15, "shapes": ("oval",)},
    {"key": "scoreboard_clock",   "label": "Scoreboard Clock",
     "blurb": "20-foot clock in deep CF — the only timepiece in the league; ground-rule single on a hit",
     "weight": 0.05, "shapes": None},
    {"key": "spite_fence",        "label": "The Spite Fence",
     "blurb": "60-foot tin-and-timber wall slapped up to choke off the cheap pull HRs the tiny footprint hands out — pop flies clang off it for doubles",
     "weight": 0.30, "shapes": ("bandbox",)},
    {"key": "the_coffin",         "label": "The Coffin",
     "blurb": "the short foul-line pocket cliffs back to the alley at a near-right angle — caroms ricochet sideways and balls wedge in the corner for ground-rule doubles",
     "weight": 0.30, "shapes": ("coffin_corner",)},
    {"key": "pinch_alleys",       "label": "Pinched Alleys",
     "blurb": "the power alleys cave in 50 feet shorter than the lines — outfielders cheat to the gaps and gappers go to die",
     "weight": 0.25, "shapes": ("hourglass",)},
    {"key": "inverted_wall",      "label": "The Inverted Wall",
     "blurb": "dead-center boundary bows IN toward the plate — the scoreboard looms close enough to read from the box",
     "weight": 0.25, "shapes": ("crescent",)},
    {"key": "the_ramp",           "label": "The Ramp",
     "blurb": "the wall climbs steadily from a short porch to a death valley — the same fly ball is a souvenir on one line and a long out on the other",
     "weight": 0.25, "shapes": ("sawtooth_wedge",)},
)


def _roll_park_dimensions(rng: random.Random) -> dict:
    """Generate distinctive outfield dimensions + a shape archetype.

    O27's fiat is a pre-modern / cricket-revival ballpark world — no
    cookie-cutter era ever happened. Each park rolls a shape first
    (balanced / short_porch / cavernous / bathtub / triangle / oval),
    then dimensions are drawn from that shape's joint distribution.

    Returns: {lf, lcf, cf, rcf, rf, wall_h, shape}.
    Distances in feet. Asymmetric corners are real — a Polo Grounds
    bathtub produces 270-ft lines + 480-ft CF.
    """
    shape = rng.choices(
        [s[0] for s in _PARK_SHAPES],
        weights=_PARK_SHAPE_WEIGHTS,
    )[0]

    if shape == "balanced":
        skew = rng.uniform(-10, 10)
        lf  = int(round(rng.gauss(338, 12) - skew))
        rf  = int(round(rng.gauss(338, 12) + skew))
        lcf = int(round(rng.gauss(388, 12)))
        rcf = int(round(rng.gauss(388, 12)))
        cf  = int(round(rng.gauss(412, 14)))
    elif shape == "short_porch_rf":
        # Yankee-Stadium-style short RF.
        lf  = int(round(rng.gauss(348, 12)))
        rf  = int(round(rng.gauss(298, 14)))
        lcf = int(round(rng.gauss(400, 14)))
        rcf = int(round(rng.gauss(365, 14)))
        cf  = int(round(rng.gauss(420, 15)))
    elif shape == "short_porch_lf":
        lf  = int(round(rng.gauss(298, 14)))
        rf  = int(round(rng.gauss(348, 12)))
        lcf = int(round(rng.gauss(365, 14)))
        rcf = int(round(rng.gauss(400, 14)))
        cf  = int(round(rng.gauss(420, 15)))
    elif shape == "cavernous":
        # Forbes Field / old Cleveland Stadium.
        skew = rng.uniform(-12, 12)
        lf  = int(round(rng.gauss(360, 15) - skew))
        rf  = int(round(rng.gauss(360, 15) + skew))
        lcf = int(round(rng.gauss(425, 18)))
        rcf = int(round(rng.gauss(425, 18)))
        cf  = int(round(rng.gauss(458, 18)))
    elif shape == "bathtub":
        # Polo Grounds — super-short lines, super-deep alleys + CF.
        skew = rng.uniform(-8, 8)
        lf  = int(round(rng.gauss(280, 12) - skew))
        rf  = int(round(rng.gauss(280, 12) + skew))
        lcf = int(round(rng.gauss(430, 18)))
        rcf = int(round(rng.gauss(430, 18)))
        cf  = int(round(rng.gauss(478, 16)))
    elif shape == "triangle":
        # Fenway-ish CF triangle — alleys normal, dead CF juts.
        skew = rng.uniform(-10, 10)
        lf  = int(round(rng.gauss(335, 12) - skew))
        rf  = int(round(rng.gauss(335, 12) + skew))
        lcf = int(round(rng.gauss(385, 12)))
        rcf = int(round(rng.gauss(385, 12)))
        cf  = int(round(rng.gauss(445, 16)))
    elif shape == "oval":   # cricket-ground revival
        # Boundary nearly uniform around the whole playing field.
        # Pull HRs become rare, gappers and Stay-mechanic 2C events
        # become much more valuable.
        skew = rng.uniform(-6, 6)
        lf  = int(round(rng.gauss(380, 10) - skew))
        rf  = int(round(rng.gauss(380, 10) + skew))
        lcf = int(round(rng.gauss(398, 9)))
        rcf = int(round(rng.gauss(398, 9)))
        cf  = int(round(rng.gauss(418, 10)))
    elif shape == "bandbox":
        # Baker Bowl — tiny everywhere. HR factory at every angle, which
        # is exactly why these parks always sprouted a freakishly tall
        # spite fence (handled in the wall-height roll below).
        skew = rng.uniform(-8, 8)
        lf  = int(round(rng.gauss(285, 10) - skew))
        rf  = int(round(rng.gauss(285, 10) + skew))
        lcf = int(round(rng.gauss(330, 12)))
        rcf = int(round(rng.gauss(330, 12)))
        cf  = int(round(rng.gauss(360, 12)))
    elif shape == "crescent":
        # Inverted boundary — dead CF caves IN shorter than the power
        # alleys. The only park in the league where straightaway center
        # is the cheap HR and the gaps are where drives go to die.
        skew = rng.uniform(-8, 8)
        lf  = int(round(rng.gauss(350, 12) - skew))
        rf  = int(round(rng.gauss(350, 12) + skew))
        lcf = int(round(rng.gauss(420, 14)))
        rcf = int(round(rng.gauss(420, 14)))
        cf  = int(round(rng.gauss(365, 12)))
    elif shape == "hourglass":
        # Pinched power alleys with deep lines and a deep CF chasm. Pull
        # the foul-line shot or hit it dead center; anything in the gap
        # dies in the pinch.
        skew = rng.uniform(-10, 10)
        lf  = int(round(rng.gauss(365, 12) - skew))
        rf  = int(round(rng.gauss(365, 12) + skew))
        lcf = int(round(rng.gauss(335, 12)))
        rcf = int(round(rng.gauss(335, 12)))
        cf  = int(round(rng.gauss(445, 16)))
    elif shape == "coffin_corner":
        # Extreme one-sided notch: one foul line dives in, its adjacent
        # alley cliffs back, and the far half plays cavernous. Pick the
        # short side at random so coffins land in both LF and RF.
        if rng.random() < 0.5:
            # Short-left coffin.
            lf  = int(round(rng.gauss(265, 10)))
            lcf = int(round(rng.gauss(445, 16)))
            rcf = int(round(rng.gauss(425, 16)))
            rf  = int(round(rng.gauss(390, 14)))
        else:
            # Short-right coffin (mirror).
            rf  = int(round(rng.gauss(265, 10)))
            rcf = int(round(rng.gauss(445, 16)))
            lcf = int(round(rng.gauss(425, 16)))
            lf  = int(round(rng.gauss(390, 14)))
        cf  = int(round(rng.gauss(430, 16)))
    elif shape == "sawtooth_wedge":
        # Monotonic ramp from a short porch in left to a death valley in
        # right (orientation flips half the time).
        if rng.random() < 0.5:
            lf  = int(round(rng.gauss(275, 10)))
            lcf = int(round(rng.gauss(335, 14)))
            cf  = int(round(rng.gauss(395, 14)))
            rcf = int(round(rng.gauss(395, 14)))
            rf  = int(round(rng.gauss(415, 14)))
        else:
            rf  = int(round(rng.gauss(275, 10)))
            rcf = int(round(rng.gauss(335, 14)))
            cf  = int(round(rng.gauss(395, 14)))
            lcf = int(round(rng.gauss(395, 14)))
            lf  = int(round(rng.gauss(415, 14)))
    else:   # defensive fallback — treat unknown shapes as balanced
        skew = rng.uniform(-10, 10)
        lf  = int(round(rng.gauss(338, 12) - skew))
        rf  = int(round(rng.gauss(338, 12) + skew))
        lcf = int(round(rng.gauss(388, 12)))
        rcf = int(round(rng.gauss(388, 12)))
        cf  = int(round(rng.gauss(412, 14)))

    # Wall height: long tail. Bathtub / short-porch parks get the
    # tallest walls (Ebbets / Polo Grounds were both 35-40 ft RF). The
    # bandbox almost always sprouts a freakish spite fence to claw back
    # the cheap HRs its tiny footprint hands out; coffin / wedge parks
    # often slap an extreme wall on the short side.
    wall_roll = rng.random()
    if shape == "bandbox" and wall_roll < 0.70:
        wall_h = int(round(rng.uniform(40, 60)))   # Baker Bowl spite fence
    elif shape in ("coffin_corner", "sawtooth_wedge") and wall_roll < 0.40:
        wall_h = int(round(rng.uniform(30, 55)))
    elif shape in ("bathtub", "short_porch_rf", "short_porch_lf") and wall_roll < 0.45:
        wall_h = int(round(rng.uniform(28, 50)))
    elif wall_roll < 0.08:
        wall_h = int(round(rng.uniform(28, 42)))   # Green Monster class
    elif wall_roll < 0.22:
        wall_h = int(round(rng.uniform(15, 26)))
    elif shape == "oval" and wall_roll < 0.60:
        # Cricket-ground tin fence — very low.
        wall_h = int(round(rng.uniform(4, 8)))
    else:
        wall_h = int(round(rng.uniform(8, 14)))

    # Physical floors only — exotic shapes (bandbox short CF, sub-285
    # coffin lines) must survive. The original archetypes' Gaussians sit
    # well above these, so for them the clamp is a no-op.
    return {
        "lf":     max(250, lf),
        "lcf":    max(300, lcf),
        "cf":     max(355, cf),
        "rcf":    max(300, rcf),
        "rf":     max(250, rf),
        "wall_h": wall_h,
        "shape":  shape,
    }


def _roll_park_quirks(rng: random.Random, shape: str) -> list[dict]:
    """Roll 0-3 architectural quirks from _QUIRK_CATALOG. Some quirks are
    shape-gated (e.g. Round Bowl only fires on oval parks, The Porch
    only on short-porch shapes). Each park's quirks are drawn without
    replacement.
    """
    n_quirks = rng.choices((0, 1, 2, 3), weights=(0.38, 0.40, 0.17, 0.05))[0]
    if n_quirks == 0:
        return []
    eligible = [
        q for q in _QUIRK_CATALOG
        if q["shapes"] is None or shape in q["shapes"]
    ]
    if not eligible:
        return []
    picked: list[dict] = []
    pool = list(eligible)
    for _ in range(min(n_quirks, len(pool))):
        weights = [q["weight"] for q in pool]
        idx = rng.choices(range(len(pool)), weights=weights)[0]
        picked.append({
            "key":   pool[idx]["key"],
            "label": pool[idx]["label"],
            "blurb": pool[idx]["blurb"],
        })
        pool.pop(idx)
    return picked


def _park_shape_meta(shape_key: str) -> dict:
    """Return {label, blurb} for a shape key — UI lookup."""
    for k, label, blurb in _PARK_SHAPES:
        if k == shape_key:
            return {"label": label, "blurb": blurb}
    return {"label": "", "blurb": ""}


# --- Public park helpers for the live ballpark editor (web/app.py) -------

def get_park_shapes() -> list[dict]:
    """All park shape archetypes as [{key, label, blurb}] for the editor
    shape picker."""
    return [{"key": k, "label": label, "blurb": blurb}
            for k, label, blurb in _PARK_SHAPES]


def get_quirk_catalog() -> list[dict]:
    """The full architectural-quirk catalog as
    [{key, label, blurb, shapes}] — `shapes` is None (any) or a list of
    shape keys the quirk is gated to."""
    return [{"key": q["key"], "label": q["label"], "blurb": q["blurb"],
             "shapes": list(q["shapes"]) if q["shapes"] else None}
            for q in _QUIRK_CATALOG]


def get_park_shape_meta(shape_key: str) -> dict:
    """Public alias of _park_shape_meta — {label, blurb} for a shape."""
    return _park_shape_meta(shape_key)


def quirk_meta(key: str) -> dict:
    """Return {key, label, blurb} for a quirk key (empty label if
    unknown) — lets the editor reconstruct a quirk chosen by key."""
    for q in _QUIRK_CATALOG:
        if q["key"] == key:
            return {"key": q["key"], "label": q["label"], "blurb": q["blurb"]}
    return {"key": key, "label": key, "blurb": ""}


def roll_park(rng: random.Random) -> tuple[dict, str, list[dict]]:
    """Roll a fresh park: returns (dimensions_dict, shape_key, quirks_list).
    `dimensions_dict` is {lf, lcf, cf, rcf, rf, wall_h} (shape stripped
    out into its own return value)."""
    dims = _roll_park_dimensions(rng)
    shape = dims.pop("shape", "")
    quirks = _roll_park_quirks(rng, shape)
    return dims, shape, quirks


def _park_surname_pool(rng: random.Random, count: int = 60) -> list[str]:
    """Pull a small pool of surnames from the existing name data to feed
    the ballpark generator. Kept short to keep generation cheap; the
    pool is consumed via random.choice, not exhausted."""
    pools = _load_name_pools().get("surnames", {})
    flat: list[str] = []
    for bucket in pools.values():
        flat.extend(bucket)
    if not flat:
        return ["Marlow", "Hadley", "Wendt", "Pellegrini", "Okonkwo"]
    return rng.sample(flat, k=min(count, len(flat)))


# Roster shape — substitution-economy 42-player baseline (Item 2).
#
# Per the operator's clarification: in O27, the 3 jokers ARE the DH
# role (analogous to MLB's 1 DH, just with 3 of them). There is NO
# separate DH player class. The lineup is 8 fielders + 3 jokers = 11
# batters; the pitcher does NOT bat (jokers replace pitcher batting,
# same way MLB's DH does). Jokers are FIXED in the lineup pre-game and
# CANNOT be substituted out.
#
#   -  8 fielders (canonical starters, 1 each at C/1B/2B/3B/SS/LF/CF/RF)
#   - 11 fielder backups (depth at every position for PH/PR/DEF subs)
#   -  3 jokers (drafted explicitly as elite-bat / no-glove; the DH role)
#   -  3 situational specialists (1 PR + 2 PH for bench leverage)
#   - 17 pitchers (bulk + leverage + emergency)
# Total active: 42.
#
# The bat_first / glove_first / two_way classifier mix falls out of
# the 19 fielders (8 starters + 11 backups). Jokers and specialists
# are drafted explicitly so every team is guaranteed dedicated
# situational weapons.
ACTIVE_FIELDERS    = 19   # 8 canonical starters + 11 fielder backups
ACTIVE_JOKERS      = 3    # the DH role — fixed in lineup, not subbable
ACTIVE_SPECIALISTS = 3    # 1 PR + 2 PH, drafted explicitly
ACTIVE_PITCHERS    = 17
RESERVE_HITTERS    = 3
RESERVE_PITCHERS   = 3
# Active = 19 + 3 + 3 + 17 = 42. Total = 42 + 3 + 3 = 48 players/team.
ACTIVE_POSITION_TOTAL = ACTIVE_FIELDERS + ACTIVE_JOKERS + ACTIVE_SPECIALISTS  # 25


def _make_hitter(
    rng: random.Random,
    pos: str,
    is_active: int,
    name: str,
    team_shift: int = 0,
    country: str = "",
    style: dict[str, int] | None = None,
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
    def roll(attr: str | None = None) -> int:
        bias = style.get(attr, 0) if (style and attr) else 0
        return _roll_tier_grade(rng, team_shift + bias)

    skill_g  = roll("skill")
    speed_g  = roll("speed")
    # Realism layer — independently tier-rolled so a hitter can be elite
    # power but average eye, etc. Drives distinct stat-line shapes.
    contact_g  = roll("contact")
    power_g    = roll("power")
    eye_g      = roll("eye")
    bats_roll  = _roll_bats(rng)
    # Spray tendency: base N(0.5, 0.12), nudged toward pull by power
    # (sluggers turn on the ball) and by LHB tendency. Clamped [0.05, 0.95].
    _power_dev = (power_g - 50) / 100.0            # ~±0.30 at extremes
    _bats_nudge = 0.04 if bats_roll == "L" else 0.0
    pull_pct_g = _clamp(
        rng.gauss(0.5, 0.12) + _power_dev * 0.30 + _bats_nudge,
        0.05, 0.95,
    )
    # Adaptability — independent tier roll; uncorrelated with other ratings
    # so a slugger and a slap hitter can both land high or low on it.
    adaptability_g = roll()
    # Leadership — independent tier roll, decoupled from hard skills.
    # Stacks with grit in the RISP-pressure bonus, so a bench-tier
    # hitter who lands high on BOTH leadership and grit becomes a real
    # clutch threat (the joker archetype). A star hitter who lands low
    # on leadership stays flat in big moments — hard skills alone don't
    # carry pressure events.
    leadership_g = roll()
    # Hitter grit — same scale as pitcher grit (0.25-0.75 in roster gen).
    # On hitters grit reads as "doesn't flinch with the bases loaded"
    # rather than pitcher fatigue resistance. Stacks with leadership in
    # the RISP-pressure bonus. Identity at 0.50.
    hitter_grit = round(0.25 + rng.random() * 0.50, 3)
    # Defense layer — general glove + arm independently tier-rolled.
    # A great-glove no-bat archetype (low skill, elite defense) is a
    # real type in this sport.
    defense_g  = roll("defense")
    arm_g      = roll("arm")

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
    elif pos == "DH":
        # DH = pure bat slot; no defensive primary. All three position
        # groups roll low (replacement-ish). Without this branch a DH
        # would default to "primary=if" and get a full infield roll,
        # silently making DHs field-capable and undermining the
        # bat_first / two_way classification balance.
        if_g  = max(20, roll() // 2 + 10)
        of_g  = max(20, roll() // 2 + 10)
        cat_g = max(20, roll() // 2 + 10)
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
    result = {
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
        # Spray tendency (pull_pct): base N(0.5, 0.12), nudged by power
        # (sluggers tend pull-heavy) and handedness (LHB pull slightly
        # more on average — easier to turn on inside pitching from a
        # natural-stride swing). Clamped [0.05, 0.95].
        "pull_pct": round(pull_pct_g, 3),
        "adaptability": adaptability_g,
        "leadership": leadership_g,
        "grit": hitter_grit,
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
        "bats":     bats_roll,
        "throws":   _roll_throws(rng, is_pitcher=False),
        "defense":  defense_g,
        "arm":      arm_g,
        "defense_infield":  if_g,
        "defense_outfield": of_g,
        "defense_catcher":  cat_g,
        # Baserunning skill + aggressiveness, independent rolls. A smart
        # average-speed runner (high baserunning, mid speed) is just as
        # useful on the bases as a pure burner.
        "baserunning":        roll("baserunning"),
        "run_aggressiveness": roll("run_aggressiveness"),
        # Phase 5e — work-ethic / work-habits. Both rolled from the same
        # 9-tier ladder (capped at 80 like every other seed-time
        # attribute). habit_cup starts at 0.5 (neutral).
        "work_ethic":  roll(),
        "work_habits": roll(),
        "habit_cup":   0.5,
    }
    result["archetype"] = classify_position_player(result)
    # Substitution-economy role tags (Item 1). Derived from the same
    # grades that drive the archetype; written to the DB so the
    # substitution candidate-pickers can filter cheaply in SQL.
    result["role_hit"]       = 1 if is_hit_capable(result) else 0
    result["role_run"]       = 1 if is_run_capable(result) else 0
    result["role_two_way"]   = 1 if is_two_way(result) else 0
    result["role_field_pos"] = encode_field_positions(result)
    result["roster_slot"]    = classify_roster_slot(result)
    return result


# Pseudo-position labels for the explicit specialist draft slots
# (substitution-economy Item 4 follow-up #6). They appear only in the
# draft pool keying — the resulting player rows carry canonical
# positions ("CF" for PR specialists, "DH" for PH specialists, "DH"
# for jokers) so the engine and box-score renderers don't need new
# vocabulary.
SPEC_PR    = "PR_SPEC"
SPEC_PH    = "PH_SPEC"
SPEC_JOKER = "JOKER"


def _make_specialist(
    rng: random.Random,
    kind: str,
    name: str,
    team_shift: int = 0,
    country: str = "",
) -> dict:
    """Build a single-tool specialist player.

    `kind` is "pr_specialist" (pure-speed pinch runner), "ph_specialist"
    (loud-bat pinch hitter), or "joker" (best-of-the-best bat with no
    defensive role, fixed in the lineup for the whole game). The result
    is shaped so classify_roster_slot lands it on the intended slot —
    strong in the specialist's dimension, intentionally weak elsewhere
    so it doesn't leak into bat_first / glove_first / two_way.

    PR specialists carry canonical position "CF"; PH specialists and
    jokers carry "DH". Neither has a defensive role — the field
    thresholds in o27v2/archetypes._FIELD_THRESHOLDS are deliberately
    not cleared.
    """
    def low_roll() -> int:
        # Replacement to slightly-below-average. Tight band so the
        # specialist's weak dimensions stay weak.
        return rng.randint(25, 42)

    def high_roll() -> int:
        # Forced high — elite for the specialist's tool.
        return rng.randint(65, 80)

    def elite_roll() -> int:
        # Joker-tier — best-of-the-best bat. Tighter, higher than the
        # PH specialist roll because jokers are the team's *best* bats.
        return rng.randint(70, 85)

    if kind == "pr_specialist":
        position   = "CF"
        speed_g    = high_roll()
        baserunning_g = high_roll()
        contact_g  = low_roll()
        power_g    = low_roll()
        eye_g      = low_roll()
        defense_g  = low_roll()
        arm_g      = low_roll()
        if_g       = low_roll()
        of_g       = low_roll()
        cat_g      = low_roll()
        skill_g    = max(30, low_roll() + 5)
        ra_g       = high_roll()
    elif kind == "joker":
        # Joker = pure bat, no glove. Stronger than ph_specialist on
        # average because jokers are fixed in the lineup and need to
        # carry their slot every PA, not just situational appearances.
        position   = "DH"
        power_g    = elite_roll()
        contact_g  = elite_roll()
        eye_g      = rng.randint(55, 75)
        speed_g    = low_roll()
        baserunning_g = low_roll()
        defense_g  = low_roll()
        arm_g      = low_roll()
        if_g       = low_roll()
        of_g       = low_roll()
        cat_g      = low_roll()
        skill_g    = rng.randint(65, 82)
        ra_g       = rng.randint(40, 60)
    else:  # ph_specialist
        position   = "DH"
        power_g    = high_roll()
        contact_g  = rng.randint(48, 65)
        eye_g      = rng.randint(45, 65)
        speed_g    = low_roll()
        baserunning_g = low_roll()
        defense_g  = low_roll()
        arm_g      = low_roll()
        if_g       = low_roll()
        of_g       = low_roll()
        cat_g      = low_roll()
        skill_g    = rng.randint(58, 75)
        ra_g       = rng.randint(40, 60)

    bats_roll = _roll_bats(rng)
    _power_dev = (power_g - 50) / 100.0
    _bats_nudge = 0.04 if bats_roll == "L" else 0.0
    pull_pct_g = _clamp(
        rng.gauss(0.5, 0.12) + _power_dev * 0.30 + _bats_nudge,
        0.05, 0.95,
    )

    result = {
        "name": name,
        "country": country,
        "position": position,
        "is_pitcher": 0,
        "is_joker": 0,
        "skill": skill_g,
        "speed": speed_g,
        "pitcher_skill": max(20, min(35, low_roll())),
        "stay_aggressiveness": round(_clamp(rng.gauss(0.30, 0.10)), 3),
        "contact_quality_threshold": round(_clamp(rng.gauss(0.50, 0.10)), 3),
        "pull_pct": round(pull_pct_g, 3),
        "adaptability": rng.randint(40, 60),
        "leadership": rng.randint(40, 65),
        "grit": round(0.25 + rng.random() * 0.50, 3),
        "archetype": "",
        "pitcher_role": "",
        "hard_contact_delta": 0.0,
        "hr_weight_bonus":    0.0,
        "age": _player_age(rng),
        "stamina": low_roll(),
        "is_active": 1,
        "contact": contact_g,
        "power":   power_g,
        "eye":     eye_g,
        "command": 50,
        "movement": 50,
        "bats": bats_roll,
        "throws": _roll_throws(rng, is_pitcher=False),
        "defense": defense_g,
        "arm":     arm_g,
        "defense_infield":  if_g,
        "defense_outfield": of_g,
        "defense_catcher":  cat_g,
        "baserunning":        baserunning_g,
        "run_aggressiveness": ra_g,
        "work_ethic":  rng.randint(40, 70),
        "work_habits": rng.randint(40, 70),
        "habit_cup":   0.5,
    }
    result["archetype"]      = classify_position_player(result)
    result["role_hit"]       = 1 if is_hit_capable(result) else 0
    result["role_run"]       = 1 if is_run_capable(result) else 0
    result["role_two_way"]   = 1 if is_two_way(result) else 0
    result["role_field_pos"] = encode_field_positions(result)
    result["roster_slot"]    = classify_roster_slot(result)
    # Force-tag the intent. The draft slot expresses *intent*; the role
    # flags should reflect that even if a near-miss random profile would
    # land it elsewhere by the generic classifier.
    if kind == "pr_specialist":
        result["roster_slot"] = "pr_specialist"
        result["role_hit"]    = 0
        result["role_run"]    = 1
    elif kind == "joker":
        result["roster_slot"] = "joker"
        result["is_joker"]    = 1   # legacy flag for back-compat
        result["role_hit"]    = 1
        result["role_run"]    = 0
    else:
        result["roster_slot"] = "ph_specialist"
        result["role_hit"]    = 1
        result["role_run"]    = 0
    result["role_two_way"]   = 0
    result["role_field_pos"] = ""
    return result


# ---------------------------------------------------------------------------
# Pitch-type repertoire generation
# ---------------------------------------------------------------------------

_FASTBALL_KEYS = ("four_seam", "sinker", "cutter")


def _roll_release_angle(rng: random.Random) -> float:
    """Roll a pitcher's release angle (0=submarine, 0.5=sidearm, 1.0=3q).

    Distribution biased toward the sidearm spectrum that the O27 setting
    centers on. ~70% land in [0.30, 0.70] (sidearm-ish); the rest split
    between submarine specialists and three-quarter outliers.
    """
    bucket = rng.random()
    if bucket < 0.12:
        return round(rng.uniform(0.05, 0.25), 3)   # submarine
    if bucket < 0.82:
        return round(rng.uniform(0.30, 0.70), 3)   # sidearm
    return round(rng.uniform(0.72, 0.95), 3)        # three-quarter


def _pitch_release_fit(release_angle: float, pitch_meta: dict) -> float:
    """Compatibility score for a pitch given the pitcher's release angle.

    Returns 0.0 if the pitch's `max_release` rules it out, else a weight
    in (0, 1] that peaks when `release_angle` equals `release_optimal`
    and decays with distance scaled by `release_window`.
    """
    max_release = pitch_meta.get("max_release")
    if max_release is not None and release_angle > max_release:
        return 0.0
    optimal = pitch_meta.get("release_optimal", 0.5)
    window = max(0.05, pitch_meta.get("release_window", 0.3))
    distance = abs(release_angle - optimal)
    # Linear falloff inside the window, exponential outside.
    if distance <= window:
        return 1.0 - 0.4 * (distance / window)
    return max(0.05, 0.6 * (window / max(distance, 1e-6)))


def _build_repertoire(
    rng: random.Random,
    release_angle: float,
    team_shift: int,
) -> list[dict]:
    """Sample a 3-5 pitch repertoire from PITCH_CATALOG.

    Composition:
      * exactly one primary fastball (four_seam / sinker / cutter), picked
        by release-angle fit
      * 2-4 secondary pitches sampled from the remainder, weighted by
        release-angle fit
      * quality is rolled on the same tier ladder as Stuff (20-80 scout
        grade) and stored as a unit float in [0.2, 0.95]
      * usage_weight totals roughly to 1.0; primary fastball gets the
        largest slice
    """
    catalog: dict = _engine_cfg.PITCH_CATALOG

    fastball_weights: list[tuple[str, float]] = []
    for fb in _FASTBALL_KEYS:
        meta = catalog[fb]
        fit = _pitch_release_fit(release_angle, meta)
        if fit > 0:
            fastball_weights.append((fb, fit))
    if not fastball_weights:
        fastball_weights = [(_FASTBALL_KEYS[0], 1.0)]
    primary = _weighted_pick(rng, fastball_weights)

    secondary_count = rng.choices((2, 3, 4), weights=(0.25, 0.55, 0.20))[0]
    secondary_pool: list[tuple[str, float]] = []
    for key, meta in catalog.items():
        if key == primary or key in _FASTBALL_KEYS:
            # Skip the primary and rule out duplicate fastball types as
            # secondaries — a pitcher carries ONE fastball variant.
            if key != primary and key in _FASTBALL_KEYS:
                # Allow a second fastball variant occasionally (e.g. SP with
                # a 4S + cutter pairing). Low rate so it doesn't flatten
                # the catalog.
                fit = _pitch_release_fit(release_angle, meta)
                if fit > 0 and rng.random() < 0.18:
                    secondary_pool.append((key, fit * 0.5))
            continue
        fit = _pitch_release_fit(release_angle, meta)
        if fit > 0:
            secondary_pool.append((key, fit))

    secondaries: list[str] = []
    available = list(secondary_pool)
    for _ in range(secondary_count):
        if not available:
            break
        pick = _weighted_pick(rng, available)
        secondaries.append(pick)
        available = [(k, w) for (k, w) in available if k != pick]

    entries: list[dict] = []
    primary_quality = _quality_unit(_roll_tier_grade(rng, team_shift))
    entries.append({
        "pitch_type":   primary,
        "quality":      primary_quality,
        "usage_weight": round(rng.uniform(0.40, 0.55), 3),
    })
    remaining_mass = 1.0 - entries[0]["usage_weight"]
    secondary_qualities = [
        _quality_unit(_roll_tier_grade(rng, team_shift))
        for _ in secondaries
    ]
    if secondaries:
        raw_weights = [
            max(0.05, q + rng.uniform(-0.05, 0.10))
            for q in secondary_qualities
        ]
        total = sum(raw_weights) or 1.0
        for sec, q, rw in zip(secondaries, secondary_qualities, raw_weights):
            entries.append({
                "pitch_type":   sec,
                "quality":      q,
                "usage_weight": round(remaining_mass * (rw / total), 3),
            })
    return entries


def _weighted_pick(rng: random.Random, weighted: list[tuple[str, float]]) -> str:
    keys = [k for (k, _) in weighted]
    weights = [w for (_, w) in weighted]
    return rng.choices(keys, weights=weights)[0]


def _quality_unit(grade_20_80: int) -> float:
    """Map a 20-80 scout grade to a 0.20-0.95 unit float for pitch quality."""
    return round(0.20 + (max(20, min(80, grade_20_80)) - 20) / 80.0 * 0.75, 3)


def _make_pitcher(
    rng: random.Random,
    is_active: int,
    name: str,
    team_shift: int = 0,
    country: str = "",
    style: dict[str, int] | None = None,
) -> dict:
    """Build one pitcher dict with Stuff (`pitcher_skill`) and Stamina
    rolled INDEPENDENTLY against the tier ladder.

    No pitcher_role is set — the manager AI derives today's role at game
    time from the live attribute values, so an aging arm with decayed
    Stamina automatically slides from rotation into middle relief without
    any persisted re-tagging.
    """
    def roll(attr: str | None = None) -> int:
        bias = style.get(attr, 0) if (style and attr) else 0
        return _roll_tier_grade(rng, team_shift + bias)

    stuff_g   = roll("pitcher_skill")
    stamina_g = roll("stamina")
    # Realism layer — pitcher Command + Movement rolled INDEPENDENTLY of
    # Stuff. Drives the Maddux-vs-Ryan stat-shape spectrum: high Command
    # = low BB regardless of Stuff; high Movement = ground-ball pitcher.
    command_g  = roll("command")
    movement_g = roll("movement")
    # Pitchers also get defense/arm — they field comebackers and bunts,
    # and high-arm pitchers help suppress steals. Capped lower than
    # position players since pitcher fielding matters less in O27.
    defense_g  = max(20, roll() // 2 + 15)
    arm_g      = max(20, roll() // 2 + 20)
    throws = _roll_throws(rng, is_pitcher=True)
    # Pitch-type activation: release_angle drives which pitches a pitcher
    # can throw well (see o27/config.py:PITCH_CATALOG). Repertoire is
    # stored as JSON; the engine loads it back into Player.repertoire.
    release_angle = _roll_release_angle(rng)
    repertoire = _build_repertoire(rng, release_angle, team_shift)
    # Pitch variance: high = max-effort frayed mechanics (boom/bust pitch
    # quality); low = consistent. Damped by grit on the per-game form roll.
    pitch_variance = round(rng.uniform(0.02, 0.14), 3)
    # Grit: fatigue resistance + per-game form stability. Bounded 0.25-0.75.
    grit = round(0.25 + rng.random() * 0.50, 3)
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
        "pull_pct": 0.5,   # pitchers bat too rarely for spray to matter
        "adaptability": 50,   # neutral; pitchers don't see enough ABs to adapt
        "leadership": 50,     # neutral; pitchers don't use the batter-side pressure roll
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
        # Pitch-type activation (see _build_repertoire above).
        "release_angle":  release_angle,
        "pitch_variance": pitch_variance,
        "grit":           grit,
        "repertoire":     json.dumps(repertoire),
        # Substitution-economy role tags. Pitchers always land on the
        # pitcher slot; the other role flags are False for pitchers
        # (they don't sub in as bats, runners, or fielders).
        "roster_slot":    "pitcher",
        "role_hit":       0,
        "role_run":       0,
        "role_two_way":   0,
        "role_field_pos": "",
    }


def generate_players(
    team_idx: int,
    rng: random.Random,
    home_bonus: float = 0.0,
    org_strength: int = 50,
    name_config: dict | None = None,
) -> list[dict]:
    """Generate ~48 players for a single team (legacy helper).

    Composition (substitution-economy 42-baseline, jokers-as-DH):
      - 19 active fielders (8 canonical starters + 11 backups)
      -  3 active jokers (drafted explicitly; the DH role in O27 —
         fixed in lineup, not subbable)
      -  1 active PR specialist
      -  2 active PH specialists
      - 17 active pitchers
      -  3 reserve position players
      -  3 reserve pitchers

    The pitcher does NOT bat; jokers fill the DH role (slots #9-11 in
    the 11-batter lineup).

    `org_strength`, `team_idx`, and `home_bonus` are accepted for
    backward compatibility but no longer bias the rolls.
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

    def _spec(kind: str) -> dict:
        nm, country = _name()
        return _make_specialist(rng, kind, name=nm, country=country)

    # ---- Active position players: 8 canonical starters + 11 fielder backups ----
    for pos in FIELDER_POSITIONS:
        players.append(_hitter(pos, is_active=1))
    # High-rotation backups (each gets a starter-equivalent body).
    for pos in ("CF", "SS", "2B", "C"):
        players.append(_hitter(pos, is_active=1))
    # Corner backups.
    for pos in ("3B", "1B", "LF"):
        players.append(_hitter(pos, is_active=1))
    # Extra-depth backups (for PH/PR/DEF substitution pool).
    for pos in ("RF", "CF", "SS", "2B"):
        players.append(_hitter(pos, is_active=1))

    # ---- Active jokers (the DH role; 3 drafted explicitly) ----
    for _ in range(ACTIVE_JOKERS):
        players.append(_spec("joker"))

    # ---- Active situational specialists ----
    players.append(_spec("pr_specialist"))
    players.append(_spec("ph_specialist"))
    players.append(_spec("ph_specialist"))

    # ---- Active pitching staff ----
    for _ in range(ACTIVE_PITCHERS):
        players.append(_pitcher(is_active=1))

    # ---- Reserve pool ----
    _RESERVE_POSITIONS = ("RF", "CF", "SS")
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
    # 3 jokers — the O27 analog of MLB's DH. Drafted as explicit
    # bat-only slots with elite contact/power and no defensive role.
    # They are FIXED in the batting lineup for the whole game (slots
    # #9-11) and CANNOT be substituted out. They can still be inserted
    # as batter_override on top of any other slot for extra leverage PAs.
    (SPEC_JOKER, 3, 0),
    # Active backups at high-rotation positions: catchers rest a lot,
    # middle infield rotates, CF backup is a near-everyday role. The
    # role classifier sorts these into glove-first or two-way based on
    # the bat/glove profile that landed.
    ("CF", 1, 0), ("SS", 1, 0), ("2B", 1, 0), ("C",  1, 0),
    # Corner-IF / corner-OF fielder backups — 1 active body per position,
    # so the substitution candidate-pool has a glove at every spot. The
    # classifier sorts these into glove-first / two-way / bat-first.
    ("3B", 1, 0), ("1B", 1, 0), ("LF", 1, 0),
    # Extra depth (4 more backups across high-rotation + outfield) so
    # the substitution candidate pool has bodies to spend on PH/PR/DEF
    # without leaving the team a defensive replacement short.
    ("RF", 1, 0), ("CF", 1, 0), ("SS", 1, 0), ("2B", 1, 0),
    # Situational specialists drafted explicitly (Item 4 follow-up #6):
    # 1 PR specialist + 2 PH specialists per team = 3 specialists
    # guaranteed in every roster. Built by _make_specialist with tight
    # role-tag enforcement so they actually land as pr_specialist /
    # ph_specialist rather than spilling into the bat_first pool.
    (SPEC_PR, 1, 0),
    (SPEC_PH, 2, 0),
    # Reserve depth (slim — active is 42).
    ("RF", 0, 1), ("CF", 0, 1), ("SS", 0, 1),
    # Pitchers (17 active + 3 reserve).
    ("P", 17, 3),
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
    style: dict[str, int] | None = None,
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
                bucket.append(_make_pitcher(rng, is_active=0, name=nm, country=country, style=style))
            elif pos == SPEC_PR:
                bucket.append(_make_specialist(rng, "pr_specialist", name=nm, country=country))
            elif pos == SPEC_PH:
                bucket.append(_make_specialist(rng, "ph_specialist", name=nm, country=country))
            elif pos == SPEC_JOKER:
                bucket.append(_make_specialist(rng, "joker", name=nm, country=country))
            else:
                bucket.append(_make_hitter(rng, pos, is_active=0, name=nm, country=country, style=style))
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


_ARCHETYPE_EXTRA_ACTIVE: dict[str, int] = {
    # Per-archetype roster-shape tilt within the 42-45 band (Item 4
    # follow-up). Promotes N reserves into the active roster so a
    # platoon manager's bench has actually-more specialists, not just
    # more-aggressive deployment of the same 42-active baseline.
    "platoon_manager": 3,    # 42 → 45
    "special_teams":   2,    # 42 → 44
}


def _promotion_score(p: dict) -> int:
    """Score reserves for archetype-tilt promotion. Specialists rank
    highest (they're the slot a platoon/special-teams manager values
    most), then bat_first, then glove_first; everyone else falls back
    to player overall."""
    slot = p.get("roster_slot", "")
    if slot in ("ph_specialist", "pr_specialist"):
        bonus = 30
    elif slot == "bat_first":
        bonus = 15
    elif slot == "glove_first":
        bonus = 10
    else:
        bonus = 0
    return bonus + _player_overall(p)


def apply_archetype_roster_tilt(roster: list[dict], manager_archetype: str) -> int:
    """Promote reserves to active based on the team's manager archetype.

    Operates in place on the roster list. Returns the count of players
    promoted (0 for archetypes that don't tilt).

    `platoon_manager` and `special_teams` skippers run deeper benches —
    they want more specialists available for situational deployment. A
    `platoon_manager` team lands at 45 active; `special_teams` at 44.
    Every other archetype stays at the 42 baseline. Promoted reserves
    are picked by specialist value (PH/PR specialists first, then
    bat_first, then glove_first) — this is what makes a Platoon
    Manager's roster *look* different at the slot-mix level rather than
    just play differently through the substitution trigger.
    """
    extra = _ARCHETYPE_EXTRA_ACTIVE.get(manager_archetype, 0)
    if extra <= 0:
        return 0
    reserves = sorted(
        (p for p in roster if not p.get("is_active")),
        key=_promotion_score,
        reverse=True,
    )
    promoted = 0
    for p in reserves[:extra]:
        p["is_active"] = 1
        promoted += 1
    return promoted


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
    # Optional mechanical style diversity: {league_name: profile_key}. When
    # present, each league's talent pool is generated with that profile's
    # per-attribute bias so leagues play differently. Absent → neutral.
    style_profiles_cfg: dict[str, str] = config.get("style_profiles") or {}
    # Optional per-league locale: {league_name: region_id_or_preset}. Each
    # value names an EXISTING region/preset in data/names/regions.json — so a
    # league can be located anywhere we have name data (e.g. east_asia,
    # latin_america, nordic), independent of its playing style. Reuses the
    # existing name infrastructure; no new name data.
    name_regions_cfg: dict[str, str] = config.get("name_regions") or {}

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

    if config.get("league_specs"):
        div_map = _assign_universe_divisions(selected, config["league_specs"], rng)
    elif config.get("schedule_mode") == "tiered":
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
    from o27v2.front_office import roll_fo

    # Generator scaffolds pulled up once so we can name parks and
    # managers in the same Phase-1 loop. The manager name picker uses
    # the league's regional weights so a "Nordic" preset gets Nordic
    # managers, etc.
    surname_pool = _park_surname_pool(rng2)
    used_park_names: set[str] = set()
    mgr_name_picker = make_name_picker(
        random.Random(rng_seed ^ 0xA17C0DE),
        gender         = name_config.get("gender", "male"),
        region_weights = name_config.get("region_weights"),
    )

    # Per-league name pickers, built from `name_regions` against the EXISTING
    # name data. A league with a configured locale draws its players' (and
    # managers') names from that region; otherwise it falls back to the
    # league-wide name_config. Cached per (league, salt) so generation is
    # deterministic. crc32 (not python hash) keeps seeding stable across runs.
    import zlib as _zlib
    _lg_picker_cache: dict[tuple[str, int], object] = {}

    def _league_name_picker(league_name: str, salt: int):
        key = (league_name or "", salt)
        if key not in _lg_picker_cache:
            weights = (resolve_name_region_weights(name_regions_cfg.get(league_name))
                       or name_config.get("region_weights"))
            seed = (rng_seed ^ salt ^ _zlib.crc32((league_name or "").encode())) & 0x7FFFFFFF
            _lg_picker_cache[key] = make_name_picker(
                random.Random(seed),
                gender         = name_config.get("gender", "male"),
                region_weights = weights,
            )
        return _lg_picker_cache[key]

    # Phase 1: insert all teams with their rolled org_strength. The
    # value is rolled from the same 9-tier ladder players use (uncapped
    # at 95 here — orgs CAN be Elite+ at seed; only player attributes
    # are capped at 80). Org_strength drives multi-season player
    # development (see o27v2/development.py): high-org teams grow
    # talent faster between seasons, building dynasties organically.
    # It does NOT bias per-pitch outcomes — that role is gone.
    team_ids: list[int] = []
    team_leagues: list[str] = []
    for idx, (team_def, (league_name, division)) in enumerate(zip(selected, div_map)):
        abbrev = team_def.get("abbreviation") or team_def.get("abbrev", "???")
        city   = team_def.get("city", "")
        name   = team_def.get("name", "Team")
        lat    = team_def.get("lat")
        lon    = team_def.get("lon")

        park_hr, park_hits = _roll_park_factors(rng2)
        park_name = _roll_ballpark_name(rng2, city, surname_pool, used_park_names)
        _dim_dict   = _roll_park_dimensions(rng2)
        park_shape  = _dim_dict.pop("shape", "")
        park_dims   = json.dumps(_dim_dict)
        park_quirks = json.dumps(_roll_park_quirks(rng2, park_shape))
        mgr = roll_manager(rng2)
        fo  = roll_fo(rng2)
        if name_regions_cfg.get(league_name):
            mgr_name, _mgr_country = _league_name_picker(league_name, 0xA17C0DE)()
        else:
            mgr_name, _mgr_country = mgr_name_picker()
        # Org_strength rolled on the full 9-tier ladder (uncapped at 95) —
        # ~7% of teams genuinely start with Elite+/Elite development orgs,
        # ~12% Excellent, etc. Drives multi-season player growth without
        # biasing per-pitch outcomes.
        org_strength = _roll_org_grade(rng2)
        team_style = style_profiles_cfg.get(league_name, "")
        team_id = db.execute(
            "INSERT INTO teams (name, abbrev, city, lat, lon, division, league, "
            "park_hr, park_hits, park_name, park_dimensions, "
            "park_shape, park_quirks, "
            "manager_archetype, manager_name, "
            "mgr_quick_hook, "
            "mgr_bullpen_aggression, mgr_leverage_aware, mgr_joker_aggression, "
            "mgr_pinch_hit_aggression, mgr_platoon_aggression, mgr_run_game, "
            "mgr_bench_usage, mgr_shift_aggression, mgr_ibb_aggression, org_strength, "
            "fo_strategy, fo_aggression, fo_archetype_bias, style_profile)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, abbrev, city, lat, lon, division, league_name,
             park_hr, park_hits, park_name, park_dims,
             park_shape, park_quirks,
             mgr["manager_archetype"], mgr_name,
             mgr["mgr_quick_hook"],
             mgr["mgr_bullpen_aggression"], mgr["mgr_leverage_aware"],
             mgr["mgr_joker_aggression"], mgr["mgr_pinch_hit_aggression"],
             mgr["mgr_platoon_aggression"], mgr["mgr_run_game"],
             mgr["mgr_bench_usage"],
             mgr.get("mgr_shift_aggression", 0.5),
             mgr.get("mgr_ibb_aggression", 0.5),
             org_strength,
             fo["fo_strategy"], fo["fo_aggression"], fo["fo_archetype_bias"],
             team_style),
        )
        team_ids.append(team_id)
        team_leagues.append(league_name)

    # Phase 2: generate the league-wide draft pool and snake-draft it.
    # No team bias — every player is rolled from the same 9-tier
    # distribution. Surplus players become free agents (team_id NULL),
    # picked up by the weekly Sunday match-day waiver sweep.
    name_picker = make_name_picker(
        rng2,
        gender         = name_config.get("gender", "male"),
        region_weights = name_config.get("region_weights"),
    )
    if style_profiles_cfg or name_regions_cfg:
        # Per-league generation: each league gets its own talent pool, drawn
        # under that league's style profile AND its own locale name picker,
        # then drafted within itself. This delivers mechanically distinct
        # statistical environments, keeps each league self-contained
        # (concurrent international leagues), and lets a league be located
        # in any region we have name data for. Free agents accumulate across
        # all leagues.
        teams_by_league: dict[str, list[int]] = {}
        for tid, lg in zip(team_ids, team_leagues):
            teams_by_league.setdefault(lg, []).append(tid)
        assignments: dict[int, list[dict]] = {}
        free_agents = []
        for lg, tids in teams_by_league.items():
            profile_key = style_profiles_cfg.get(lg, "")
            style = _STYLE_PROFILES.get(profile_key) or None
            lg_picker = (_league_name_picker(lg, 0x0)
                         if name_regions_cfg.get(lg) else name_picker)
            lg_pool = _generate_draft_pool(len(tids), rng2, lg_picker, style=style)
            lg_assign, lg_fa = _run_snake_draft(tids, lg_pool, rng2)
            assignments.update(lg_assign)
            free_agents.extend(lg_fa)
    else:
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
         work_ethic, work_habits, habit_cup, salary,
         release_angle, pitch_variance, grit, repertoire,
         pull_pct, adaptability, leadership,
         roster_slot, role_hit, role_run, role_two_way, role_field_pos)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

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
                salary,
                p.get("release_angle", 0.5),
                p.get("pitch_variance", 0.0),
                p.get("grit", 0.5),
                p.get("repertoire", None),
                p.get("pull_pct", 0.5),
                p.get("adaptability", 50),
                p.get("leadership", 50),
                p.get("roster_slot", ""),
                int(p.get("role_hit", 1)),
                int(p.get("role_run", 0)),
                int(p.get("role_two_way", 1)),
                p.get("role_field_pos", ""))

    # Cache team-id → league name so each player's salary uses the
    # right tier cap. Also pull manager_archetype for the per-archetype
    # roster tilt (Item 4 follow-up).
    team_meta_rows = db.fetchall("SELECT id, league, manager_archetype FROM teams")
    team_league = {row["id"]: row["league"] for row in team_meta_rows}
    team_archetype = {
        row["id"]: (row["manager_archetype"] or "") for row in team_meta_rows
    }

    for team_id in team_ids:
        apply_archetype_roster_tilt(
            assignments.get(team_id, []),
            team_archetype.get(team_id, ""),
        )

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
