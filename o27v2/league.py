"""
League definition and player generation for O27v2.

Supports configurable team counts (8–36) via league config JSON files.
Player names are drawn from regional pools with weighted sampling:
  USA 50% | Latin 30% | Japan/Korea 10% | Other 10%

Each team has 18 players:
  - 9 position players (slot 9 = pitcher)
  - 9 jokers (3 per archetype: power, speed, contact)
"""
from __future__ import annotations
import json
import os
import random
from typing import Any

from o27v2 import config as v2cfg

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


def generate_players(
    team_idx: int,
    rng: random.Random,
    home_bonus: float = 0.0,
) -> list[dict]:
    """
    Generate 18 players for a team (9 position + 9 jokers).

    Phase 8 additions:
      - The starting pitcher (position "P") is tagged pitcher_role="workhorse".
      - Three position players (CF, SS, 2B) are tagged pitcher_role="committee"
        with boosted pitcher_skill to serve as relievers.
      - Nine jokers: JOKERS_PER_ARCHETYPE (3) copies of each archetype
        (power, speed, contact), shuffled into the roster.

    Phase 9 addition:
      - Each player receives an age drawn from a bell curve (22-38, peak 27-30).

    Names are sampled from regional pools with weighted distribution.
    team_idx influences the skill distribution to give each team personality.

    home_bonus: small skill offset applied to position-player batting skill
                to model home-field advantage and reduce tie/super-inning rate.
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

    profile      = team_idx % 5
    skill_base   = [0.52, 0.50, 0.54, 0.48, 0.51][profile] + home_bonus
    speed_base   = [0.52, 0.60, 0.48, 0.55, 0.50][profile]
    pitcher_base = [0.54, 0.48, 0.52, 0.56, 0.50][profile]

    players = []
    for pos in POSITIONS:
        is_p        = (pos == "P")
        is_comm     = (pos in _COMMITTEE_POSITIONS)
        skill       = _clamp(rng.gauss(skill_base,   0.10))
        speed       = _clamp(rng.gauss(speed_base,   0.12))
        if is_p:
            pskill = _clamp(rng.gauss(pitcher_base, 0.12))
        elif is_comm:
            pskill = _clamp(rng.gauss(0.52, 0.09))
        else:
            pskill = _clamp(rng.gauss(0.35, 0.08))
        stay_a = _clamp(rng.gauss(0.10, 0.05))   # v2 calibrated to 1.0–2.5 stays/game
        cqt    = _clamp(rng.gauss(0.28, 0.06))

        if is_p:
            pitcher_role = "workhorse"
        elif is_comm:
            pitcher_role = "committee"
        else:
            pitcher_role = ""

        players.append({
            "name": _name(),
            "position": pos,
            "is_pitcher": int(is_p),
            "is_joker": 0,
            "skill": round(skill, 3),
            "speed": round(speed, 3),
            "pitcher_skill": round(pskill, 3),
            "stay_aggressiveness": round(stay_a, 3),
            "contact_quality_threshold": round(cqt, 3),
            "archetype": "",
            "pitcher_role": pitcher_role,
            "age": _player_age(rng),
        })

    # JOKERS_PER_ARCHETYPE jokers per archetype per team (1 per archetype = 3 total).
    archetypes = ["power", "speed", "contact"] * v2cfg.JOKERS_PER_ARCHETYPE
    rng.shuffle(archetypes)

    joker_names_used: set[str] = set()
    for archetype in archetypes:
        jname = rng.choice(_JOKER_NAMES)
        while jname in joker_names_used:
            jname = rng.choice(_JOKER_NAMES)
        joker_names_used.add(jname)

        ap     = _JOKER_ARCHETYPES[archetype]
        pamod  = _JOKER_PA_MODIFIERS.get(archetype, {})
        skill  = _clamp(rng.gauss(ap["skill_mu"],  ap["skill_sig"]))
        speed  = _clamp(rng.gauss(ap["speed_mu"],  ap["speed_sig"]))
        stay_a = _clamp(rng.gauss(ap["stay_a_mu"], ap["stay_a_sig"]))
        cqt    = _clamp(rng.gauss(ap["cqt_mu"],    ap["cqt_sig"]))
        pskill = _clamp(rng.gauss(0.38, 0.09))

        players.append({
            "name": jname,
            "position": "JKR",
            "is_pitcher": 0,
            "is_joker": 1,
            "skill": round(skill, 3),
            "speed": round(speed, 3),
            "pitcher_skill": round(pskill, 3),
            "stay_aggressiveness": round(stay_a, 3),
            "contact_quality_threshold": round(cqt, 3),
            "archetype": archetype,
            "pitcher_role": "",
            "hard_contact_delta": pamod.get("hard_contact_delta", 0.0),
            "hr_weight_bonus":    pamod.get("hr_weight_bonus",    0.0),
            "age": _player_age(rng),
        })
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
               (team_id, name, position, is_pitcher, is_joker, skill, speed,
                pitcher_skill, stay_aggressiveness, contact_quality_threshold,
                archetype, pitcher_role, hard_contact_delta, hr_weight_bonus, age)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(team_id, p["name"], p["position"], p["is_pitcher"], p["is_joker"],
              p["skill"], p["speed"], p["pitcher_skill"],
              p["stay_aggressiveness"], p["contact_quality_threshold"],
              p.get("archetype", ""), p.get("pitcher_role", ""),
              p.get("hard_contact_delta", 0.0), p.get("hr_weight_bonus", 0.0),
              p.get("age", 27))
             for p in players],
        )
