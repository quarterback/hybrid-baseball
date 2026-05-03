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
_teams_db: list[dict] | None = None


def _load_name_pools() -> dict[str, dict]:
    global _name_pools
    if _name_pools is None:
        _name_pools = {}
        for region in ("usa", "latin", "japan_korea", "other"):
            path = os.path.join(_NAMES_DIR, f"{region}.json")
            with open(path, encoding="utf-8") as fh:
                _name_pools[region] = json.load(fh)
    return _name_pools


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


# ---------------------------------------------------------------------------
# Division assignment helpers
# ---------------------------------------------------------------------------

_LEAGUE_NAMES = ["AL", "NL"]
_DIV_SUFFIXES = ["East", "Central", "West"]


def _build_division_map(config: dict) -> list[tuple[str, str]]:
    """
    Return a list of (league, division) tuples, one per team slot,
    in order so teams can be assigned to divisions round-robin.
    """
    leagues          = config.get("leagues", ["AL", "NL"])
    divs_per_league  = config["divisions_per_league"]
    teams_per_div    = config["teams_per_division"]

    div_suffixes = _DIV_SUFFIXES[:divs_per_league]

    assignment: list[tuple[str, str]] = []
    for lg in leagues:
        for suf in div_suffixes:
            for _ in range(teams_per_div):
                assignment.append((lg, f"{lg} {suf}"))
    return assignment


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

_REGION_WEIGHTS = [
    ("usa",         0.50),
    ("latin",       0.30),
    ("japan_korea", 0.10),
    ("other",       0.10),
]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _weighted_region(rng: random.Random) -> str:
    """Pick a region using the configured weights."""
    r = rng.random()
    cumulative = 0.0
    for region, weight in _REGION_WEIGHTS:
        cumulative += weight
        if r < cumulative:
            return region
    return "usa"


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
    (0.02, 75, 80),  # Elite
    (0.05, 65, 74),  # Excellent
    (0.10, 60, 64),  # Very Good
    (0.15, 55, 59),  # Good
    (0.18, 50, 54),  # Above Average
    (0.20, 45, 49),  # Average
    (0.15, 40, 44),  # Below Average
    (0.10, 30, 39),  # Replacement
    (0.05, 20, 29),  # Sub-Replacement
]


def _roll_tier_grade(rng: random.Random) -> int:
    """Roll one attribute against the 9-tier league talent distribution.

    Returns an integer 20-80 scout grade. Used independently for every
    hitter and pitcher attribute on every player.
    """
    r = rng.random()
    cumulative = 0.0
    for prob, lo, hi in _TALENT_TIERS:
        cumulative += prob
        if r < cumulative:
            return rng.randint(lo, hi)
    # Floating-point safety net (probabilities sum to 1.0).
    lo, hi = _TALENT_TIERS[-1][1], _TALENT_TIERS[-1][2]
    return rng.randint(lo, hi)


def _tier_unit(rng: random.Random) -> float:
    """Tier-rolled grade converted to the [0,1] unit float the engine uses."""
    return _scout.to_unit(_roll_tier_grade(rng))


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
) -> dict:
    """Build one position-player dict with every attribute rolled
    independently against the talent-tier distribution (Task #65).

    `skill` is the engine's overall hitter rating; `speed` is its own
    independent roll. Both come from the same 9-tier ladder so genuine
    elite bats and burners exist alongside replacement-level players.
    """
    skill_g  = _roll_tier_grade(rng)
    speed_g  = _roll_tier_grade(rng)
    # Pitcher_skill on a position player is only used in emergencies.
    pskill_g = _roll_tier_grade(rng) // 2 + 10  # cap fielder-pitching at low grades
    return {
        "name": name,
        "position": pos,
        "is_pitcher": 0,
        "is_joker": 0,
        "skill": skill_g,
        "speed": speed_g,
        "pitcher_skill": max(20, min(45, pskill_g)),
        "stay_aggressiveness": round(_clamp(rng.gauss(0.10, 0.05)), 3),
        "contact_quality_threshold": round(_clamp(rng.gauss(0.28, 0.06)), 3),
        "archetype": "",
        "pitcher_role": "",
        "hard_contact_delta": 0.0,
        "hr_weight_bonus":    0.0,
        "age": _player_age(rng),
        "stamina":   _roll_tier_grade(rng) // 2 + 10,  # irrelevant for hitters
        "is_active": is_active,
    }


def _make_pitcher(
    rng: random.Random,
    is_active: int,
    name: str,
) -> dict:
    """Build one pitcher dict with Stuff (`pitcher_skill`) and Stamina
    rolled INDEPENDENTLY against the tier ladder.

    No pitcher_role is set — the manager AI derives today's role at game
    time from the live attribute values, so an aging arm with decayed
    Stamina automatically slides from rotation into middle relief without
    any persisted re-tagging.
    """
    stuff_g   = _roll_tier_grade(rng)
    stamina_g = _roll_tier_grade(rng)
    return {
        "name": name,
        "position": "P",
        "is_pitcher": 1,
        "is_joker": 0,
        "skill":  max(20, _roll_tier_grade(rng) // 2 + 10),  # weak bat
        "speed":  max(20, _roll_tier_grade(rng) // 2 + 15),
        "pitcher_skill": stuff_g,
        "stay_aggressiveness": round(_clamp(rng.gauss(0.05, 0.03)), 3),
        "contact_quality_threshold": round(_clamp(rng.gauss(0.20, 0.05)), 3),
        "archetype": "",
        "pitcher_role": "",   # Task #65: live derivation only — never stored.
        "hard_contact_delta": 0.0,
        "hr_weight_bonus":    0.0,
        "age": _player_age(rng),
        "stamina":   stamina_g,
        "is_active": is_active,
    }


def generate_players(
    team_idx: int,
    rng: random.Random,
    home_bonus: float = 0.0,
) -> list[dict]:
    """Generate ~47 players for a team (Task #65 expanded roster).

    Composition (active = 35, reserve = 12, total = 47):
      - 12 active position players (8 starters at canonical positions
        CF/SS/2B/3B/RF/LF/1B/C plus 4 utility bench)
      -  4 active DH/utility bats
      - 19 active pitchers (no role buckets at generation time — every
        pitcher is rolled independently against the tier ladder, so the
        active staff naturally contains workhorses, short-burst arms, and
        everything in between)
      -  7 reserve position players (is_active=0)
      -  5 reserve pitchers (is_active=0)

    Every attribute is rolled independently against the talent-tier
    distribution (`_TALENT_TIERS`), producing the spiky archetypes the
    league needs to surface real stars on the leaderboards.

    `team_idx` and `home_bonus` are accepted for backward compatibility
    but no longer skew the distribution — the league's variance now comes
    from per-player tier rolls, not per-team gaussian centers.
    """
    pools = _load_name_pools()
    used_names: set[str] = set()

    def _name() -> str:
        for _ in range(200):
            region = _weighted_region(rng)
            pool   = pools[region]
            first  = rng.choice(pool["first_names"])
            last   = rng.choice(pool["last_names"])
            full   = f"{first} {last}"
            if full not in used_names:
                used_names.add(full)
                return full
        return f"Player {rng.randint(100, 999)}"

    players: list[dict] = []

    # ---- Active position players: 8 starting positions + 4 bench ----
    for pos in FIELDER_POSITIONS:
        players.append(_make_hitter(rng, pos, is_active=1, name=_name()))
    bench_positions = ["UT", "UT", "UT", "UT"]
    for pos in bench_positions:
        players.append(_make_hitter(rng, pos, is_active=1, name=_name()))

    # ---- Active DH/utility bats ----
    for _ in range(ACTIVE_DH):
        players.append(_make_hitter(rng, "DH", is_active=1, name=_name()))

    # ---- Active pitching staff (no role buckets) ----
    for _ in range(ACTIVE_PITCHERS):
        players.append(_make_pitcher(rng, is_active=1, name=_name()))

    # ---- Reserve pool: bench-level depth, promoted on injury ----
    for _ in range(RESERVE_HITTERS):
        players.append(_make_hitter(rng, "UT", is_active=0, name=_name()))
    for _ in range(RESERVE_PITCHERS):
        players.append(_make_pitcher(rng, is_active=0, name=_name()))

    return players


def seed_league(rng_seed: int = 42, config_id: str = "30teams") -> None:
    """
    Insert teams and their players into the database.
    Safe to call only once (checks for existing data first).

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

    config  = get_config(config_id)
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

    div_map = _build_division_map(config)

    rng2 = random.Random(rng_seed)
    for idx, (team_def, (league_name, division)) in enumerate(zip(selected, div_map)):
        # Build a 3-letter abbreviation if needed
        abbrev = team_def.get("abbreviation") or team_def.get("abbrev", "???")
        city   = team_def.get("city", "")
        name   = team_def.get("name", "Team")

        team_id = db.execute(
            "INSERT INTO teams (name, abbrev, city, division, league) VALUES (?,?,?,?,?)",
            (name, abbrev, city, division, league_name),
        )
        players = generate_players(idx, rng2)
        db.executemany(
            """INSERT INTO players
               (team_id, name, position, is_pitcher, skill, speed,
                pitcher_skill, stay_aggressiveness, contact_quality_threshold,
                archetype, pitcher_role, hard_contact_delta, hr_weight_bonus,
                age, stamina, is_active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(team_id, p["name"], p["position"], p["is_pitcher"],
              p["skill"], p["speed"], p["pitcher_skill"],
              p["stay_aggressiveness"], p["contact_quality_threshold"],
              p.get("archetype", ""), p.get("pitcher_role", ""),
              p.get("hard_contact_delta", 0.0), p.get("hr_weight_bonus", 0.0),
              p.get("age", 27),
              p.get("stamina", p.get("pitcher_skill", 50)),
              p.get("is_active", 1))
             for p in players],
        )
