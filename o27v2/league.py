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

    Returns an integer 20-95 scout grade. Used independently for every
    hitter and pitcher attribute on every player.

    `team_shift` is the per-team org-strength offset (see
    `generate_players`). It's applied AFTER the tier-bucket roll so a
    +shift team still has the same shape of tier distribution — just
    centered higher. Clamped to [20, 95] post-shift so an elite team
    can't push into superhuman territory and a cellar team still has
    some grade-20 floor.
    """
    r = rng.random()
    cumulative = 0.0
    for prob, lo, hi in _TALENT_TIERS:
        cumulative += prob
        if r < cumulative:
            return max(20, min(95, rng.randint(lo, hi) + team_shift))
    # Floating-point safety net (probabilities sum to 1.0).
    lo, hi = _TALENT_TIERS[-1][1], _TALENT_TIERS[-1][2]
    return max(20, min(95, rng.randint(lo, hi) + team_shift))


def _tier_unit(rng: random.Random, team_shift: int = 0) -> float:
    """Tier-rolled grade converted to the [0,1] unit float the engine uses."""
    return _scout.to_unit(_roll_tier_grade(rng, team_shift))


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
    # - With small probability (or for UT-slot players), roll all three
    #   at full tier → utility player (Ben Zobrist style). UT slots are
    #   ~10% of the active roster and are explicitly meant to be
    #   multi-position contributors.
    is_utility = (pos == "UT") or rng.random() < 0.10
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
    }


def _make_pitcher(
    rng: random.Random,
    is_active: int,
    name: str,
    team_shift: int = 0,
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
    }


def generate_players(
    team_idx: int,
    rng: random.Random,
    home_bonus: float = 0.0,
    org_strength: int = 50,
) -> list[dict]:
    """Generate ~47 players for a team (Task #65 expanded roster).

    Composition (active = 34, reserve = 13, total = 47):
      - 12 active position players (8 starters at canonical positions
        CF/SS/2B/3B/RF/LF/1B/C plus 4 utility bench)
      -  3 active DH/utility bats
      - 19 active pitchers (no role buckets at generation time — every
        pitcher is rolled independently against the tier ladder, so the
        active staff naturally contains workhorses, short-burst arms, and
        everything in between)
      -  8 reserve position players (is_active=0)
      -  5 reserve pitchers (is_active=0)

    Every attribute is rolled independently against the talent-tier
    distribution (`_TALENT_TIERS`), producing the spiky archetypes the
    league needs to surface real stars on the leaderboards.

    `org_strength` (20-95 scout grade) is the team-level talent
    attribute — it's persisted on the teams row by seed_league() and
    drives `team_shift = org_strength - 50` here. An Elite org
    (org_strength=80) shifts every tier roll +30, compressing rolls
    against the grade-95 ceiling and producing 75-85 team-mean skill;
    a Sub-Replacement org (25) shifts -25, producing 25-35 team-mean.
    This is what gives teams identifiable talent levels.

    `team_idx` and `home_bonus` are accepted for backward compatibility
    but don't affect the distribution.
    """
    pools = _load_name_pools()
    used_names: set[str] = set()

    org_shift = _org_strength_to_shift(org_strength)

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
        players.append(_make_hitter(rng, pos, is_active=1, name=_name(),
                                    team_shift=org_shift))
    bench_positions = ["UT", "UT", "UT", "UT"]
    for pos in bench_positions:
        players.append(_make_hitter(rng, pos, is_active=1, name=_name(),
                                    team_shift=org_shift))

    # ---- Active DH/utility bats ----
    for _ in range(ACTIVE_DH):
        players.append(_make_hitter(rng, "DH", is_active=1, name=_name(),
                                    team_shift=org_shift))

    # ---- Active pitching staff (no role buckets) ----
    for _ in range(ACTIVE_PITCHERS):
        players.append(_make_pitcher(rng, is_active=1, name=_name(),
                                     team_shift=org_shift))

    # ---- Reserve pool: bench-level depth, promoted on injury ----
    for _ in range(RESERVE_HITTERS):
        players.append(_make_hitter(rng, "UT", is_active=0, name=_name(),
                                    team_shift=org_shift))
    for _ in range(RESERVE_PITCHERS):
        players.append(_make_pitcher(rng, is_active=0, name=_name(),
                                     team_shift=org_shift))

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

        park_hr, park_hits = _roll_park_factors(rng2)
        from o27v2.managers import roll_manager
        mgr = roll_manager(rng2)
        # Roll team-level org strength on the same 9-tier ladder players
        # use. The rolled grade is both persisted (visible on the team
        # page, sortable) and used to derive every player's team_shift.
        org_strength = _roll_tier_grade(rng2)
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
        players = generate_players(idx, rng2, org_strength=org_strength)
        db.executemany(
            """INSERT INTO players
               (team_id, name, position, is_pitcher, skill, speed,
                pitcher_skill, stay_aggressiveness, contact_quality_threshold,
                archetype, pitcher_role, hard_contact_delta, hr_weight_bonus,
                age, stamina, is_active,
                contact, power, eye, command, movement, bats, throws,
                defense, arm,
                defense_infield, defense_outfield, defense_catcher,
                baserunning, run_aggressiveness)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(team_id, p["name"], p["position"], p["is_pitcher"],
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
              p.get("run_aggressiveness", 50))
             for p in players],
        )
