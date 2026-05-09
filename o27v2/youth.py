"""
O27 Youth League — auto-attached prospect-watching league.

A second, structurally separate league that lives alongside the main
pro league on every save. Thirty-two national teams (a deliberately
broader field than the pro WBC roster — modeled on the ICC U19 World
Cup mix of traditional baseball and cricket nations) carry a roster of
12 players each, ages 14-19, and develop year over year. Players who
turn 20 graduate into the pro league's free-agent pool.

Schema is fully separate (`youth_teams`, `youth_players`) so existing
queries against `teams` / `players` are unaffected. Game simulation is
NOT part of this phase — the league exists to give you a window into
developing talent before it reaches the pros, not to produce its own
standings off simulated games.

Public surface:
  * seed_youth_league()   — create the 16 national teams + initial
                            rosters. Idempotent.
  * advance_youth_year()  — annual aging: develop attributes, age
                            everyone +1, graduate age-23 players to the
                            pro FA pool, generate new 18-year-old
                            replacements.
  * youth_teams()         — fetch all youth team rows.
  * youth_roster(team_id) — fetch players on a youth team, ordered.
  * top_prospects(limit)  — sorted view across the league for the UI.
"""
from __future__ import annotations

import random
from typing import Any

from o27v2 import db


# ---------------------------------------------------------------------------
# National teams (32 nations — broader than WBC, mixes baseball + cricket
# nations to fit the U19 World Cup feel)
# ---------------------------------------------------------------------------
# (country_code, name, abbrev, name_region_id_in_regions.json)
# `name_region_id` keys regions defined in o27v2/data/names/regions.json.
# When the region isn't present, the picker falls back to a generic
# americas pool so generation still succeeds.
_NATIONAL_TEAMS: list[tuple[str, str, str, str]] = [
    ("US", "United States",       "USA", "us"),
    ("CA", "Canada",               "CAN", "canada"),
    ("MX", "Mexico",               "MEX", "latin_america"),
    ("DO", "Dominican Republic",  "DOM", "latin_america"),
    ("PR", "Puerto Rico",          "PUR", "latin_america"),
    ("CU", "Cuba",                 "CUB", "latin_america"),
    ("VE", "Venezuela",            "VEN", "latin_america"),
    ("CO", "Colombia",             "COL", "latin_america"),
    ("BR", "Brazil",               "BRA", "south_america"),
    ("AR", "Argentina",            "ARG", "south_america"),
    ("SR", "Suriname",             "SUR", "caribbean_dutch"),
    ("GY", "Guyana",               "GUY", "caribbean_cricket"),
    ("JM", "Jamaica",              "JAM", "caribbean_cricket"),
    ("TT", "Trinidad and Tobago",  "TTO", "caribbean_cricket"),
    ("GB", "United Kingdom",       "GBR", "british_isles"),
    ("IE", "Ireland",              "IRL", "british_isles"),
    ("NL", "Netherlands",          "NED", "europe_western"),
    ("IT", "Italy",                "ITA", "europe_western"),
    ("CZ", "Czech Republic",       "CZE", "europe_eastern"),
    ("FI", "Finland",              "FIN", "nordic"),
    ("ZA", "South Africa",         "RSA", "africa_cricket"),
    ("ZW", "Zimbabwe",             "ZIM", "africa_cricket"),
    ("IN", "India",                "IND", "south_asia"),
    ("PK", "Pakistan",             "PAK", "south_asia"),
    ("MY", "Malaysia",             "MAS", "malaysia"),
    ("PH", "Philippines",          "PHI", "southeast_asia"),
    ("JP", "Japan",                "JPN", "east_asia"),
    ("KR", "South Korea",          "KOR", "east_asia"),
    ("TW", "Taiwan",               "TPE", "east_asia"),
    ("AU", "Australia",            "AUS", "anzac"),
    ("NZ", "New Zealand",          "NZL", "anzac"),
    ("FJ", "Fiji",                 "FIJ", "pacific_islands"),
]

# Geographic region a country belongs to, for grouping the standings on
# /youth so 32 teams aren't a flat list. Order here is the order regions
# render. Any code missing from this map is bucketed under "Other".
_COUNTRY_REGION: dict[str, str] = {
    # North & Central America
    "US": "North America",  "CA": "North America",  "MX": "North America",
    # Caribbean
    "DO": "Caribbean",      "PR": "Caribbean",      "CU": "Caribbean",
    "JM": "Caribbean",      "TT": "Caribbean",      "SR": "Caribbean",
    "GY": "Caribbean",
    # South America
    "VE": "South America",  "CO": "South America",
    "BR": "South America",  "AR": "South America",
    # Europe
    "GB": "Europe",         "IE": "Europe",         "NL": "Europe",
    "IT": "Europe",         "CZ": "Europe",         "FI": "Europe",
    # Africa
    "ZA": "Africa",         "ZW": "Africa",
    # Asia
    "IN": "Asia",           "PK": "Asia",           "MY": "Asia",
    "PH": "Asia",           "JP": "Asia",           "KR": "Asia",
    "TW": "Asia",
    # Oceania
    "AU": "Oceania",        "NZ": "Oceania",        "FJ": "Oceania",
}

REGION_ORDER: list[str] = [
    "North America", "Caribbean", "South America",
    "Europe", "Africa", "Asia", "Oceania",
]


def country_region(country_code: str) -> str:
    return _COUNTRY_REGION.get((country_code or "").upper(), "Other")


# Per-team roster shape for the youth league.
#
# 28 players per team: enough depth to platoon, hold a real bullpen,
# and run jokers without leaving the bench bare. Over 32 teams that's
# 896 youth players league-wide.
#
#   8 starting fielders (one at each canonical position)
#   8 position-player backups (one backup per starting position)
#   9 pitchers              (3 rotation + 6 bullpen, youth scale)
#   3 jokers                (the structural O27 joker trio)
#  ----
#  28 total

_HITTER_POSITIONS = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]
_N_PITCHERS = 9
_N_JOKERS   = 3

ROSTER_SIZE = (
    len(_HITTER_POSITIONS)        # 8 starters
    + len(_HITTER_POSITIONS)      # 8 backups
    + _N_PITCHERS                 # 9 pitchers
    + _N_JOKERS                   # 3 jokers
)  # = 28

# Joker archetypes — same set the pro league rolls.
_JOKER_ARCHETYPES = ["power", "speed", "contact"]

# Age bounds. Players spawn at ages 14-19 in the inaugural pass; in
# subsequent annual passes the AGE_OUT threshold drives graduation
# into the pro FA pool. Players who AGE PAST 19 (i.e. would turn 20)
# graduate at the offseason, mirroring the U19 cricket / under-20 youth
# soccer cap.
_SEED_AGE_LO = 14
_SEED_AGE_HI = 19
AGE_OUT      = 19   # players who would turn 20 graduate this offseason

# Youth Potential Index (YPI) — per-player access factor in [0.22, 0.81].
# Stored on the youth_players row; rolled once at creation and SHRUNK
# by no further mechanic during the youth career. It scales the
# player's true attribute units at engine entry only — stored ratings
# are unmodified, and the governor LIFTS at graduation (the pro pool
# sees the player's full potential).
#
# Effect: a 5★ recruit with a 0.30 YPI plays like a 1-2★ in stats
# (recruiting bust); a 1★ recruit with a 0.79 YPI plays like a 4★
# (sleeper / hidden gem). The user sees stars + observed stats, not
# the underlying ratings nor the YPI itself. When the player graduates
# at age 20, both signals collapse into the pro `players` row's full
# attribute grid and the pro stats tell the truth.
_YPI_LO = 0.22
_YPI_HI = 0.81

# Recruit-star thresholds (US college recruiting feel).
# Composite is averaged from TRUE attribute grades, NOT YPI-modified.
# Hitters: (skill + contact + power + eye) / 4
# Pitchers: (pitcher_skill + command + movement + stamina) / 4
def _stars_from_composite(c: int) -> int:
    # Calibrated to the 9-tier _TALENT_TIERS distribution (capped at 80
    # at seed time). Empirically across 5000 sample rolls these yield
    # approx: 5★ ~1%, 4★ ~10%, 3★ ~30%, 2★ ~40%, 1★ ~20%.
    if c >= 68: return 5    # rare elite — must roll high on most attrs
    if c >= 58: return 4    # solid blue-chip
    if c >= 48: return 3    # most starting-caliber kids
    if c >= 36: return 2    # back-end depth
    return 1                # walk-on tier


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_TEAMS = """
CREATE TABLE IF NOT EXISTS youth_teams (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    country_code  TEXT NOT NULL,
    name          TEXT NOT NULL,
    abbrev        TEXT NOT NULL,
    name_region   TEXT NOT NULL DEFAULT 'us'
);
"""

_SCHEMA_PLAYERS = """
CREATE TABLE IF NOT EXISTS youth_players (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    youth_team_id  INTEGER REFERENCES youth_teams(id),
    name           TEXT NOT NULL,
    country        TEXT DEFAULT '',
    position       TEXT NOT NULL,
    is_pitcher     INTEGER DEFAULT 0,
    is_joker       INTEGER DEFAULT 0,
    joker_archetype TEXT DEFAULT '',
    age            INTEGER NOT NULL,
    seed_year      INTEGER NOT NULL DEFAULT 1,
    -- Attribute set mirrors the engine's hitter / pitcher dev attrs so
    -- we can call o27v2.development._develop_player on these rows
    -- directly. These are TRUE potential — visible to the engine
    -- only after multiplication by youth_potential_index.
    skill              INTEGER DEFAULT 50,
    speed              INTEGER DEFAULT 50,
    pitcher_skill      INTEGER DEFAULT 50,
    contact            INTEGER DEFAULT 50,
    power              INTEGER DEFAULT 50,
    eye                INTEGER DEFAULT 50,
    command            INTEGER DEFAULT 50,
    movement           INTEGER DEFAULT 50,
    stamina            INTEGER DEFAULT 50,
    defense            INTEGER DEFAULT 50,
    arm                INTEGER DEFAULT 50,
    defense_infield    INTEGER DEFAULT 50,
    defense_outfield   INTEGER DEFAULT 50,
    defense_catcher    INTEGER DEFAULT 50,
    baserunning        INTEGER DEFAULT 50,
    run_aggressiveness INTEGER DEFAULT 50,
    bats               TEXT DEFAULT 'R',
    throws             TEXT DEFAULT 'R',
    work_ethic         INTEGER DEFAULT 50,
    work_habits        INTEGER DEFAULT 50,
    habit_cup          REAL    DEFAULT 0.5,
    -- Hidden governor (0.22-0.81). Multiplied into attribute units at
    -- engine entry; never displayed in the UI.
    youth_potential_index REAL DEFAULT 1.0,
    -- Public-facing 1-5 stars derived from TRUE composite at creation.
    -- Sticky once set — does NOT update as the player develops, so a
    -- 14-year-old who got rated 4★ keeps that label even if his
    -- numbers underwhelm by 19 (busted phenom) or vice versa
    -- (late-blooming sleeper).
    recruit_stars      INTEGER DEFAULT 3
);
"""

# Tournament schema. Each season the youth league runs one short
# tournament: 8 groups of 4 → top 2 per group advance → R16 → QF →
# SF → Final. 32 teams total, 63 games per tournament.
_SCHEMA_GROUPS = """
CREATE TABLE IF NOT EXISTS youth_groups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    season       INTEGER NOT NULL,
    group_letter TEXT NOT NULL
);
"""

_SCHEMA_GROUP_MEMBERSHIP = """
CREATE TABLE IF NOT EXISTS youth_group_membership (
    group_id      INTEGER NOT NULL REFERENCES youth_groups(id),
    youth_team_id INTEGER NOT NULL REFERENCES youth_teams(id),
    PRIMARY KEY (group_id, youth_team_id)
);
"""

_SCHEMA_GAMES = """
CREATE TABLE IF NOT EXISTS youth_games (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    season          INTEGER NOT NULL,
    bracket_round   TEXT NOT NULL,            -- 'group','r16','qf','sf','final'
    group_id        INTEGER REFERENCES youth_groups(id),  -- NULL for knockout
    bracket_slot    INTEGER,                  -- index into the bracket fixture list
    home_team_id    INTEGER NOT NULL REFERENCES youth_teams(id),
    away_team_id    INTEGER NOT NULL REFERENCES youth_teams(id),
    home_score      INTEGER,
    away_score      INTEGER,
    winner_id       INTEGER REFERENCES youth_teams(id),
    played          INTEGER NOT NULL DEFAULT 0,
    seed            INTEGER
);
"""


def init_youth_schema() -> None:
    """Create the youth_* tables if they don't exist. Idempotent.

    Also runs lightweight ALTER TABLE migrations for the columns added
    in the joker / YPI / stars overhaul, since older saves seeded
    before that work won't have them.
    """
    db.execute(_SCHEMA_TEAMS)
    db.execute(_SCHEMA_PLAYERS)
    db.execute(_SCHEMA_GROUPS)
    db.execute(_SCHEMA_GROUP_MEMBERSHIP)
    db.execute(_SCHEMA_GAMES)

    # Per-game stat tables live in `youth_sim` but are referenced from
    # `top_prospects()` and `player_observed_stats()` here. Co-initialise
    # so /youth doesn't 500 on a save where the tournament has never
    # been run yet (the LEFT JOINs would otherwise hit missing tables).
    from o27v2 import youth_sim as _youth_sim
    _youth_sim.init_youth_sim_schema()

    # Migrations for older youth tables. ALTER TABLE ... ADD COLUMN is
    # idempotent in spirit (we ignore "duplicate column" errors).
    _migrations: list[tuple[str, str]] = [
        ("youth_players", "is_joker INTEGER DEFAULT 0"),
        ("youth_players", "joker_archetype TEXT DEFAULT ''"),
        ("youth_players", "youth_potential_index REAL DEFAULT 1.0"),
        ("youth_players", "recruit_stars INTEGER DEFAULT 3"),
    ]
    for table, col_def in _migrations:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Player generation (reuses league.py's _make_hitter / _make_pitcher,
# with the age field overridden into the youth band)
# ---------------------------------------------------------------------------

def _composite_for_player(p: dict) -> int:
    """Composite of TRUE attribute grades. Used to derive recruit_stars
    at creation. Pitchers and hitters use their respective archetype
    bundles so stars ladder reflects what the player will be evaluated
    on at age 19."""
    if p.get("is_pitcher"):
        return int(round((
            int(p.get("pitcher_skill", 50))
            + int(p.get("command", 50))
            + int(p.get("movement", 50))
            + int(p.get("stamina", 50))
        ) / 4.0))
    return int(round((
        int(p.get("skill", 50))
        + int(p.get("contact", 50))
        + int(p.get("power", 50))
        + int(p.get("eye", 50))
    ) / 4.0))


def _make_youth_player(
    rng: random.Random,
    pos: str,
    is_pitcher: bool,
    name: str,
    country: str,
    age: int,
    *,
    is_joker: bool = False,
    joker_archetype: str = "",
) -> dict:
    """Build one youth player dict. Reuses the standard pro maker so
    the attribute distribution stays consistent with the pro league,
    then:
      * forces age into the youth band,
      * rolls a per-player Youth Potential Index in [0.22, 0.81],
      * derives a sticky 1-5 recruit-stars grade from true composite,
      * tags joker rows with archetype.
    """
    from o27v2.league import _make_hitter, _make_pitcher
    if is_pitcher:
        p = _make_pitcher(rng, is_active=1, name=name, country=country)
    else:
        p = _make_hitter(rng, pos, is_active=1, name=name, country=country)
    p["age"] = age
    p["is_joker"] = 1 if is_joker else 0
    p["joker_archetype"] = joker_archetype if is_joker else ""
    p["youth_potential_index"] = round(rng.uniform(_YPI_LO, _YPI_HI), 3)
    p["recruit_stars"] = _stars_from_composite(_composite_for_player(p))
    return p


def _name_picker_for_region(rng: random.Random, region_id: str,
                             country_code: str = ""):
    """Build a name picker for a youth team. When the team's
    country_code matches a subregion in the named region, the picker
    pins to that country only — Team Japan gets Japanese names, not
    a JP/KR/TW/CN mix from east_asia's full distribution. Falls back
    to the regular region-weighted picker (or the us pool) if no
    match."""
    from o27v2.league import (make_name_picker, make_country_pinned_picker,
                              get_name_regions)
    regions = get_name_regions()
    if region_id not in regions:
        return make_name_picker(rng, gender="male", region_weights={"us": 1.0})
    if country_code:
        return make_country_pinned_picker(rng, region_id, country_code, "male")
    return make_name_picker(rng, gender="male",
                            region_weights={region_id: 1.0})


def _spawn_roster(
    team: dict,
    rng: random.Random,
    seed_year: int,
) -> list[dict]:
    """Build a full 28-player youth roster for one team:
       8 starting fielders + 8 position-player backups
       + 9 pitchers + 3 jokers.
    """
    name_pick = _name_picker_for_region(rng, team["name_region"],
                                          team.get("country_code", ""))
    rows: list[dict] = []

    def _age() -> int:
        return rng.randint(_SEED_AGE_LO, _SEED_AGE_HI)

    def _spawn(pos: str, *, is_pitcher: bool, is_joker: bool = False,
               joker_archetype: str = "") -> dict:
        nm, ctry = name_pick()
        country = ctry or team["country_code"]
        p = _make_youth_player(
            rng, pos, is_pitcher=is_pitcher, name=nm, country=country,
            age=_age(),
            is_joker=is_joker, joker_archetype=joker_archetype,
        )
        p["seed_year"] = seed_year
        return p

    # 8 starting fielders.
    for pos in _HITTER_POSITIONS:
        rows.append(_spawn(pos, is_pitcher=False))
    # 8 position-player backups (one per starting position).
    for pos in _HITTER_POSITIONS:
        rows.append(_spawn(pos, is_pitcher=False))
    # 9 pitchers.
    for _ in range(_N_PITCHERS):
        rows.append(_spawn("P", is_pitcher=True))
    # 3 jokers — one of each archetype.
    for archetype in _JOKER_ARCHETYPES[:_N_JOKERS]:
        rows.append(_spawn("DH", is_pitcher=False,
                           is_joker=True, joker_archetype=archetype))

    return rows


# ---------------------------------------------------------------------------
# Inserts
# ---------------------------------------------------------------------------

_PLAYER_COLS = (
    "youth_team_id", "name", "country", "position", "is_pitcher",
    "is_joker", "joker_archetype", "age", "seed_year",
    "skill", "speed", "pitcher_skill",
    "contact", "power", "eye", "command", "movement", "stamina",
    "defense", "arm", "defense_infield", "defense_outfield", "defense_catcher",
    "baserunning", "run_aggressiveness",
    "bats", "throws", "work_ethic", "work_habits", "habit_cup",
    "youth_potential_index", "recruit_stars",
)


def _insert_player(team_id: int, p: dict) -> None:
    cols = ", ".join(_PLAYER_COLS)
    qs   = ", ".join("?" for _ in _PLAYER_COLS)
    vals = (
        team_id, p["name"], p.get("country", ""),
        p["position"], int(p["is_pitcher"]),
        int(p.get("is_joker", 0)),
        str(p.get("joker_archetype", "") or ""),
        int(p["age"]),
        int(p.get("seed_year", 1)),
        p.get("skill", 50), p.get("speed", 50), p.get("pitcher_skill", 50),
        p.get("contact", 50), p.get("power", 50), p.get("eye", 50),
        p.get("command", 50), p.get("movement", 50), p.get("stamina", 50),
        p.get("defense", 50), p.get("arm", 50),
        p.get("defense_infield", 50), p.get("defense_outfield", 50),
        p.get("defense_catcher", 50),
        p.get("baserunning", 50), p.get("run_aggressiveness", 50),
        p.get("bats", "R"), p.get("throws", "R"),
        p.get("work_ethic", 50), p.get("work_habits", 50),
        float(p.get("habit_cup", 0.5)),
        float(p.get("youth_potential_index", 1.0)),
        int(p.get("recruit_stars", 3)),
    )
    db.execute(
        f"INSERT INTO youth_players ({cols}) VALUES ({qs})",
        vals,
    )


# ---------------------------------------------------------------------------
# Public seeding entry point
# ---------------------------------------------------------------------------

def seed_youth_league(rng_seed: int = 0, seed_year: int = 1) -> int:
    """Create the 16 national teams + initial 12-player rosters. Safe
    to call only once — early-returns if youth_teams already has rows.

    Returns the count of teams inserted (0 if the league already
    existed)."""
    init_youth_schema()
    existing = db.fetchone("SELECT COUNT(*) AS n FROM youth_teams")
    if existing and existing["n"] > 0:
        return 0

    rng = random.Random((rng_seed or 0) ^ 0x59_0_4_7_4)  # "YOUTH" → const seed jolt

    inserted = 0
    for code, name, abbrev, region in _NATIONAL_TEAMS:
        team_id = db.execute(
            "INSERT INTO youth_teams (country_code, name, abbrev, name_region) "
            "VALUES (?, ?, ?, ?)",
            (code, name, abbrev, region),
        )
        team_dict = {
            "id":            team_id,
            "country_code":  code,
            "name":          name,
            "abbrev":        abbrev,
            "name_region":   region,
        }
        roster = _spawn_roster(team_dict, rng, seed_year=seed_year)
        for p in roster:
            _insert_player(team_id, p)
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Annual roll-forward
# ---------------------------------------------------------------------------

def _develop_youth_row(p: dict, rng: random.Random) -> tuple[dict, int]:
    """Apply one season of development to a youth player. Reuses the
    pro-league dev formula with org_strength=50 (neutral)."""
    from o27v2.development import _develop_player
    return _develop_player(p, org_strength=50, rng=rng,
                           is_pitcher=bool(p.get("is_pitcher")))


def _graduate_to_pro_fa(p: dict) -> int | None:
    """Insert a graduated youth player as a free agent in the pro
    `players` table (team_id = NULL, is_active = 0). Returns the new
    pro-side player id, or None on failure.

    The Youth Potential Index is NOT applied here — the pro pool sees
    the player's full TRUE attribute grades, which is what produces
    the busted-prospect / hidden-gem narrative when pro stats reveal
    the divergence from youth-tournament observed stats.
    """
    cols = (
        "team_id", "name", "country", "position", "is_pitcher",
        "skill", "speed", "pitcher_skill",
        "stay_aggressiveness", "contact_quality_threshold",
        "archetype", "pitcher_role", "hard_contact_delta", "hr_weight_bonus",
        "age", "stamina", "is_active",
        "contact", "power", "eye", "command", "movement",
        "bats", "throws", "defense", "arm",
        "defense_infield", "defense_outfield", "defense_catcher",
        "baserunning", "run_aggressiveness",
        "work_ethic", "work_habits", "habit_cup",
    )
    sql = (
        "INSERT INTO players (" + ", ".join(cols) + ") VALUES ("
        + ", ".join("?" for _ in cols) + ")"
    )
    vals = (
        None,                               # team_id (free agent)
        p["name"], p.get("country", ""),
        p["position"], int(p["is_pitcher"]),
        int(p.get("skill", 50)), int(p.get("speed", 50)),
        int(p.get("pitcher_skill", 50)),
        0.30, 0.50,                          # default Stay tuning (matches league.py)
        "", "", 0.0, 0.0,                    # archetype, role, deltas
        int(p["age"]),
        int(p.get("stamina", 50)), 0,        # is_active=0 — graduated FAs sit on the wire
        int(p.get("contact", 50)), int(p.get("power", 50)), int(p.get("eye", 50)),
        int(p.get("command", 50)), int(p.get("movement", 50)),
        p.get("bats", "R"), p.get("throws", "R"),
        int(p.get("defense", 50)), int(p.get("arm", 50)),
        int(p.get("defense_infield", 50)), int(p.get("defense_outfield", 50)),
        int(p.get("defense_catcher", 50)),
        int(p.get("baserunning", 50)), int(p.get("run_aggressiveness", 50)),
        int(p.get("work_ethic", 50)), int(p.get("work_habits", 50)),
        float(p.get("habit_cup", 0.5)),
    )
    return db.execute(sql, vals)


def _refill_team(team: dict, rng: random.Random, seed_year: int) -> int:
    """After graduations, top up the team back to its target shape
    with new 14-year-olds. Each role bucket (starter slot at every
    position, backup slot at every position, pitchers, jokers) gets
    refilled independently so the roster doesn't drift in shape over
    multiple seasons.
    """
    rows = db.fetchall(
        "SELECT position, is_pitcher, is_joker FROM youth_players "
        "WHERE youth_team_id = ?",
        (team["id"],),
    )

    # Bucket by role.
    pitcher_count = sum(1 for r in rows if r["is_pitcher"] and not r["is_joker"])
    joker_count   = sum(1 for r in rows if r["is_joker"])
    pos_counts: dict[str, int] = {pos: 0 for pos in _HITTER_POSITIONS}
    for r in rows:
        if not r["is_pitcher"] and not r["is_joker"]:
            pos_counts[r["position"]] = pos_counts.get(r["position"], 0) + 1

    name_pick = _name_picker_for_region(rng, team["name_region"],
                                          team.get("country_code", ""))
    added = 0

    def _spawn_and_insert(pos: str, *, is_pitcher: bool,
                          is_joker: bool = False,
                          joker_archetype: str = "") -> None:
        nonlocal added
        nm, ctry = name_pick()
        country = ctry or team["country_code"]
        p = _make_youth_player(
            rng, pos, is_pitcher=is_pitcher,
            name=nm, country=country, age=_SEED_AGE_LO,
            is_joker=is_joker, joker_archetype=joker_archetype,
        )
        p["seed_year"] = seed_year
        _insert_player(team["id"], p)
        added += 1

    # Each canonical position needs a starter + backup = 2 bodies.
    POSITION_TARGET = 2
    for pos in _HITTER_POSITIONS:
        while pos_counts.get(pos, 0) < POSITION_TARGET:
            _spawn_and_insert(pos, is_pitcher=False)
            pos_counts[pos] = pos_counts.get(pos, 0) + 1

    # Pitchers.
    while pitcher_count < _N_PITCHERS:
        _spawn_and_insert("P", is_pitcher=True)
        pitcher_count += 1

    # Jokers — preserve archetype rotation so each team always has all
    # three flavours.
    have_archetypes = {
        r["position"]: True for r in rows  # not actually used; cover by archetype below
    }
    existing_archetypes = set()
    if joker_count:
        existing_jokers = db.fetchall(
            "SELECT joker_archetype FROM youth_players "
            "WHERE youth_team_id = ? AND is_joker = 1",
            (team["id"],),
        )
        existing_archetypes = {
            (r["joker_archetype"] or "") for r in existing_jokers
        }
    for archetype in _JOKER_ARCHETYPES[:_N_JOKERS]:
        if archetype not in existing_archetypes:
            _spawn_and_insert("DH", is_pitcher=False,
                              is_joker=True, joker_archetype=archetype)
            existing_archetypes.add(archetype)

    return added


# Note on the development draw: `o27v2.development._mu_age` returns 2.5
# for ages < 21, which is the strongest growth band in the curve. The
# 14-19 youth window therefore lands in continuous +2.5 mean territory,
# producing the characteristic prospect-development arc (a teenager
# who's a 45 across the board can plausibly be a 60 by graduation).


def advance_youth_year(rng_seed: int = 0,
                       new_season_year: int = 1) -> dict[str, Any]:
    """Run the youth-league offseason pass.

    Order of operations per team:
      1. For each player, run the per-attribute development draw and
         bump age by 1.
      2. If the new age > AGE_OUT (i.e. now 23+), insert into the pro
         `players` table as a free agent and delete the youth row.
      3. Refill the team back to ROSTER_SIZE with new 18-year-olds.

    Returns a structured summary suitable for UI display.
    """
    init_youth_schema()
    rng = random.Random((rng_seed or 0) ^ 0xA1_5E_7_3)

    teams = db.fetchall("SELECT * FROM youth_teams ORDER BY id")
    if not teams:
        return {"teams": 0, "developed": 0, "graduated": [], "refilled": 0}

    graduated: list[dict] = []
    n_developed = 0
    n_refilled  = 0

    for team in teams:
        rows = db.fetchall(
            "SELECT * FROM youth_players WHERE youth_team_id = ?",
            (team["id"],),
        )
        for p in rows:
            updated, new_age = _develop_youth_row(p, rng)
            cols   = list(updated.keys()) + ["age"]
            values = [updated[k] for k in updated.keys()] + [new_age, p["id"]]
            sql = ("UPDATE youth_players SET "
                   + ", ".join(f"{c} = ?" for c in cols)
                   + " WHERE id = ?")
            db.execute(sql, tuple(values))
            n_developed += 1

            # Graduation: bumped age now exceeds AGE_OUT.
            if new_age > AGE_OUT:
                # Reload the freshly-updated row so the developed
                # attributes flow through to the pro-side insert.
                fresh = db.fetchone(
                    "SELECT * FROM youth_players WHERE id = ?", (p["id"],),
                )
                if fresh is None:
                    continue
                pro_id = _graduate_to_pro_fa(dict(fresh))
                graduated.append({
                    "name":           fresh["name"],
                    "from_team":      team["name"],
                    "from_team_abbrev": team["abbrev"],
                    "age":            new_age,
                    "is_pitcher":     bool(fresh["is_pitcher"]),
                    "position":       fresh["position"],
                    "pro_player_id":  pro_id,
                })
                # Cascade delete the graduate's tournament stat rows so
                # the youth_players FK constraint can resolve. The
                # tournament happened, the player has moved on; their
                # youth-side stats are no longer needed (the pro FA
                # pool only cares about their attributes).
                for child in ("game_youth_batter_stats", "game_youth_pitcher_stats"):
                    try:
                        db.execute(
                            f"DELETE FROM {child} WHERE player_id = ?",
                            (p["id"],),
                        )
                    except Exception:
                        pass
                db.execute("DELETE FROM youth_players WHERE id = ?", (p["id"],))

        # Refill missing slots with new 18-year-olds.
        n_refilled += _refill_team(dict(team), rng, seed_year=new_season_year)

    return {
        "teams":       len(teams),
        "developed":   n_developed,
        "graduated":   graduated,
        "refilled":    n_refilled,
    }


# ---------------------------------------------------------------------------
# Read-side helpers used by the web layer
# ---------------------------------------------------------------------------

def youth_teams() -> list[dict]:
    """Public team-roster summary. Reveals only public-facing signals:
    roster size, average age, recruit-stars distribution, and observed
    win-loss from played tournament games. Numerical attribute grades
    are deliberately not exposed."""
    init_youth_schema()
    rows = db.fetchall(
        "SELECT t.*, "
        "       (SELECT COUNT(*) FROM youth_players p "
        "          WHERE p.youth_team_id = t.id) AS roster_size, "
        "       (SELECT ROUND(AVG(p.age), 1) FROM youth_players p "
        "          WHERE p.youth_team_id = t.id) AS avg_age, "
        "       (SELECT ROUND(AVG(p.recruit_stars), 1) FROM youth_players p "
        "          WHERE p.youth_team_id = t.id) AS avg_stars, "
        "       (SELECT COUNT(*) FROM youth_players p "
        "          WHERE p.youth_team_id = t.id AND p.recruit_stars >= 4) AS top_stars_count, "
        "       (SELECT COUNT(*) FROM youth_games g "
        "          WHERE g.played = 1 AND g.winner_id = t.id) AS w, "
        "       (SELECT COUNT(*) FROM youth_games g "
        "          WHERE g.played = 1 AND (g.home_team_id = t.id OR g.away_team_id = t.id) "
        "                AND g.winner_id IS NOT NULL AND g.winner_id != t.id) AS l "
        "FROM youth_teams t "
        "ORDER BY t.id"
    )
    return [dict(r) for r in rows]


def youth_roster(team_id: int) -> list[dict]:
    init_youth_schema()
    rows = db.fetchall(
        "SELECT * FROM youth_players WHERE youth_team_id = ? "
        "ORDER BY is_pitcher, position, age DESC, name",
        (team_id,),
    )
    return [dict(r) for r in rows]


def top_prospects(limit: int = 25,
                  archetype: str = "bat") -> list[dict]:
    """Return the top youth players league-wide by OBSERVED
    tournament performance (no rating reveal).

    `archetype`:
      - "bat":   hitters ordered by hits desc (then OPS-ish proxy)
      - "arm":   pitchers ordered by K desc (then ER asc)
      - "stars": all players ordered by recruit_stars desc, age desc
                 — used for a pre-tournament roster view when no
                 stats exist yet.

    Numerical attribute grades are NOT projected into the result —
    the user gets stars + observed stats only. The "stars" archetype
    is the pre-season default since it's the only ranking that exists
    before any games are played.
    """
    init_youth_schema()
    if archetype == "arm":
        rows = db.fetchall(
            "SELECT p.id, p.name, p.country, p.position, p.age, "
            "       p.is_pitcher, p.is_joker, p.recruit_stars, "
            "       t.name AS team_name, t.abbrev AS team_abbrev, "
            "       t.country_code AS team_country, "
            "       COALESCE(SUM(gp.outs_recorded), 0) AS outs, "
            "       COALESCE(SUM(gp.k), 0) AS k, "
            "       COALESCE(SUM(gp.bb), 0) AS bb, "
            "       COALESCE(SUM(gp.er), 0) AS er, "
            "       COUNT(gp.id) AS appearances "
            "FROM youth_players p "
            "JOIN youth_teams t ON t.id = p.youth_team_id "
            "LEFT JOIN game_youth_pitcher_stats gp ON gp.player_id = p.id "
            "WHERE p.is_pitcher = 1 "
            "GROUP BY p.id "
            "HAVING outs > 0 "
            "ORDER BY k DESC, er ASC, outs DESC "
            "LIMIT ?", (limit,),
        )
    elif archetype == "stars":
        rows = db.fetchall(
            "SELECT p.id, p.name, p.country, p.position, p.age, "
            "       p.is_pitcher, p.is_joker, p.recruit_stars, "
            "       t.name AS team_name, t.abbrev AS team_abbrev, "
            "       t.country_code AS team_country "
            "FROM youth_players p "
            "JOIN youth_teams t ON t.id = p.youth_team_id "
            "ORDER BY p.recruit_stars DESC, p.age DESC, p.id "
            "LIMIT ?", (limit,),
        )
    else:  # bat
        rows = db.fetchall(
            "SELECT p.id, p.name, p.country, p.position, p.age, "
            "       p.is_pitcher, p.is_joker, p.recruit_stars, "
            "       t.name AS team_name, t.abbrev AS team_abbrev, "
            "       t.country_code AS team_country, "
            "       COALESCE(SUM(gb.pa), 0) AS pa, "
            "       COALESCE(SUM(gb.ab), 0) AS ab, "
            "       COALESCE(SUM(gb.hits), 0) AS h, "
            "       COALESCE(SUM(gb.hr), 0) AS hr, "
            "       COALESCE(SUM(gb.rbi), 0) AS rbi, "
            "       COALESCE(SUM(gb.bb), 0) AS bb, "
            "       COALESCE(SUM(gb.k), 0) AS k "
            "FROM youth_players p "
            "JOIN youth_teams t ON t.id = p.youth_team_id "
            "LEFT JOIN game_youth_batter_stats gb ON gb.player_id = p.id "
            "WHERE p.is_pitcher = 0 "
            "GROUP BY p.id "
            "HAVING pa > 0 "
            "ORDER BY h DESC, hr DESC, rbi DESC "
            "LIMIT ?", (limit,),
        )
    return [dict(r) for r in rows]


def player_observed_stats(player_id: int) -> dict:
    """Aggregate this player's tournament line so the per-team page
    can show stats without revealing ratings."""
    init_youth_schema()
    bat = db.fetchone(
        "SELECT COALESCE(SUM(pa),0) AS pa, COALESCE(SUM(ab),0) AS ab, "
        "       COALESCE(SUM(hits),0) AS h, COALESCE(SUM(hr),0) AS hr, "
        "       COALESCE(SUM(rbi),0) AS rbi, COALESCE(SUM(bb),0) AS bb, "
        "       COALESCE(SUM(k),0) AS k, COALESCE(SUM(runs),0) AS r "
        "FROM game_youth_batter_stats WHERE player_id = ?",
        (player_id,),
    ) or {}
    pit = db.fetchone(
        "SELECT COALESCE(COUNT(*),0) AS g, "
        "       COALESCE(SUM(is_starter),0) AS gs, "
        "       COALESCE(SUM(outs_recorded),0) AS outs, "
        "       COALESCE(SUM(k),0) AS k, COALESCE(SUM(bb),0) AS bb, "
        "       COALESCE(SUM(runs_allowed),0) AS r, "
        "       COALESCE(SUM(er),0) AS er, "
        "       COALESCE(SUM(hits_allowed),0) AS h "
        "FROM game_youth_pitcher_stats WHERE player_id = ?",
        (player_id,),
    ) or {}
    return {"bat": dict(bat), "pit": dict(pit)}


# ---------------------------------------------------------------------------
# Tournament: group draw, schedule, simulation, knockout bracket
# ---------------------------------------------------------------------------
#
# Format (per season):
#   * 32 teams drawn into 8 groups of 4 (random — no pots/seeding for v1).
#   * Each group: full round-robin, 3 games per team (each pair plays
#     once). 6 games per group × 8 groups = 48 group games.
#   * Top 2 per group advance to R16 (single-elim). 8 + 4 + 2 + 1 = 15
#     knockout games. Tournament total = 63 games.
#
# Game simulation here is a HEURISTIC, not the full PA-by-PA O27 engine.
# We compute each team's overall composite from its current youth
# roster, draw a winner via win-prob sigmoid on the rating gap, and
# generate a plausible scoreline. This is intentional — the youth
# league is a prospect-watching layer, not a separate fully-modelled
# competition. Per-PA stats would balloon the schema for marginal
# narrative value, and the development engine (which IS the substance
# of the youth feature) does not depend on per-game stats.

_GROUP_LETTERS  = ["A", "B", "C", "D", "E", "F", "G", "H"]
_TEAMS_PER_GROUP = 4


def _team_overall(team_id: int) -> float:
    """Average composite rating across the team's active youth roster.
    Hitters use (skill + contact + power + eye)/4, pitchers use
    (pitcher_skill + command + movement)/3. We blend by population
    weight inside the roster, so a team with one stud pitcher and a
    weak lineup is correctly punished."""
    rows = db.fetchall(
        "SELECT is_pitcher, skill, contact, power, eye, "
        "       pitcher_skill, command, movement "
        "FROM youth_players WHERE youth_team_id = ?",
        (team_id,),
    )
    if not rows:
        return 50.0
    total = 0.0
    for r in rows:
        if r["is_pitcher"]:
            total += (r["pitcher_skill"] + r["command"] + r["movement"]) / 3.0
        else:
            total += (r["skill"] + r["contact"] + r["power"] + r["eye"]) / 4.0
    return total / len(rows)


def _simulate_youth_game_result(
    home_overall: float,
    away_overall: float,
    rng: random.Random,
) -> tuple[int, int]:
    """Heuristic result generator for one youth game. Returns
    (home_score, away_score). Win prob comes from a logistic on the
    rating gap; scores are then drawn around an expected total tied to
    the higher-rated team's grade. No ties — if scores collide we
    extend until one side scores a final run."""
    # Logistic with a sensible scale: a 10-grade gap → ~64% win prob
    # for the better team, a 20-grade gap → ~76%.
    import math
    gap = home_overall - away_overall
    home_win_prob = 1.0 / (1.0 + math.exp(-gap / 12.0))

    expected_total = max(4, round((home_overall + away_overall) / 14.0))
    # Draw a total around expected (Poisson-ish via gauss + clamp).
    total = max(2, round(rng.gauss(expected_total, 2.5)))

    # Split via win prob.
    favorite_share = max(0.5, min(0.85, 0.5 + (home_win_prob - 0.5) * 0.7))
    home_share = favorite_share if home_win_prob >= 0.5 else (1 - favorite_share)
    home_score = max(1, round(total * home_share))
    away_score = max(0, total - home_score)

    # Resolve any tie with a sudden-death extra run for the favourite.
    if home_score == away_score:
        if rng.random() < home_win_prob:
            home_score += 1
        else:
            away_score += 1
    return home_score, away_score


def _next_season_number() -> int:
    """The youth tournament uses the same season number as the pro
    league. Falls back to 1 when sim_meta has no record."""
    row = db.fetchone(
        "SELECT value FROM sim_meta WHERE key = 'season_number'"
    )
    if row and row.get("value"):
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            pass
    return 1


def _has_tournament_for_season(season: int) -> bool:
    init_youth_schema()
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM youth_games WHERE season = ?",
        (season,),
    )
    return bool(row and row["n"])


def draw_groups(season: int, rng_seed: int = 0) -> list[dict]:
    """Random 8-group draw. Inserts youth_groups + membership rows.
    Returns a list of {group_letter, team_ids} for inspection.

    Idempotent: returns the existing draw when one already exists for
    `season`."""
    init_youth_schema()
    existing = db.fetchall(
        "SELECT g.id, g.group_letter, m.youth_team_id "
        "FROM youth_groups g "
        "LEFT JOIN youth_group_membership m ON m.group_id = g.id "
        "WHERE g.season = ? "
        "ORDER BY g.group_letter, m.youth_team_id",
        (season,),
    )
    if existing:
        out: dict[str, dict] = {}
        for r in existing:
            grp = out.setdefault(r["group_letter"], {
                "group_letter": r["group_letter"],
                "team_ids":     [],
            })
            if r["youth_team_id"] is not None:
                grp["team_ids"].append(r["youth_team_id"])
        return list(out.values())

    teams = db.fetchall("SELECT id FROM youth_teams ORDER BY id")
    if len(teams) < len(_GROUP_LETTERS) * _TEAMS_PER_GROUP:
        raise RuntimeError(
            f"Need at least {len(_GROUP_LETTERS) * _TEAMS_PER_GROUP} youth "
            f"teams to draw groups; have {len(teams)}."
        )
    rng = random.Random((rng_seed or 0) ^ 0xD2A4_1)
    ids = [t["id"] for t in teams]
    rng.shuffle(ids)

    out_groups: list[dict] = []
    for i, letter in enumerate(_GROUP_LETTERS):
        gid = db.execute(
            "INSERT INTO youth_groups (season, group_letter) VALUES (?, ?)",
            (season, letter),
        )
        members = ids[i * _TEAMS_PER_GROUP : (i + 1) * _TEAMS_PER_GROUP]
        for tid in members:
            db.execute(
                "INSERT INTO youth_group_membership (group_id, youth_team_id) "
                "VALUES (?, ?)",
                (gid, tid),
            )
        out_groups.append({
            "group_id":     gid,
            "group_letter": letter,
            "team_ids":     members,
        })
    return out_groups


def _schedule_group_games(season: int, groups: list[dict],
                          rng: random.Random) -> int:
    """For each group, insert the C(4,2) = 6 round-robin games. Home/
    away alternates within the pair list."""
    n = 0
    for grp in groups:
        team_ids = list(grp["team_ids"])
        for i in range(len(team_ids)):
            for j in range(i + 1, len(team_ids)):
                a, b = team_ids[i], team_ids[j]
                home, away = (a, b) if rng.random() < 0.5 else (b, a)
                db.execute(
                    "INSERT INTO youth_games "
                    "(season, bracket_round, group_id, home_team_id, "
                    " away_team_id, seed) "
                    "VALUES (?, 'group', ?, ?, ?, ?)",
                    (season, grp["group_id"], home, away,
                     rng.randint(1, 2**31 - 1)),
                )
                n += 1
    return n


def _simulate_unplayed_games(season: int, bracket_round: str,
                             rng: random.Random) -> int:
    """Run the real O27 engine over every unplayed game in the given
    round. Each game gets persisted with score + winner + per-player
    stats via `o27v2.youth_sim.simulate_youth_game`. Returns the count
    of games played in this call."""
    from o27v2 import youth_sim
    rows = db.fetchall(
        "SELECT id, seed FROM youth_games "
        "WHERE season = ? AND bracket_round = ? AND played = 0 "
        "ORDER BY id",
        (season, bracket_round),
    )
    n = 0
    for r in rows:
        seed = r["seed"] or rng.randint(1, 2**31 - 1)
        try:
            youth_sim.simulate_youth_game(r["id"], seed=seed)
            n += 1
        except Exception:
            # If a single game fails (e.g. a degenerate roster), fall
            # back to the heuristic so the tournament can still
            # progress. Real-engine bugs are caught in tests, not in
            # production tournaments — better to ship a result than
            # leave the bracket stuck.
            game = db.fetchone("SELECT * FROM youth_games WHERE id = ?", (r["id"],))
            if not game:
                continue
            game_rng = random.Random(seed)
            h_overall = _team_overall(game["home_team_id"])
            a_overall = _team_overall(game["away_team_id"])
            hs, as_ = _simulate_youth_game_result(h_overall, a_overall, game_rng)
            winner = game["home_team_id"] if hs > as_ else game["away_team_id"]
            db.execute(
                "UPDATE youth_games SET home_score = ?, away_score = ?, "
                "winner_id = ?, played = 1 WHERE id = ?",
                (hs, as_, winner, r["id"]),
            )
            n += 1
    return n


def _group_standings(season: int) -> dict[int, list[dict]]:
    """Compute group standings. Tie-break: wins desc, run diff desc,
    runs scored desc, then team id (stable)."""
    rows = db.fetchall(
        "SELECT g.group_id, g.group_letter, g.youth_team_id AS team_id, "
        "       SUM(g.w)  AS w, SUM(g.l) AS l, "
        "       SUM(g.rs) AS rs, SUM(g.ra) AS ra "
        "FROM ( "
        "  SELECT m.group_id, gr.group_letter, m.youth_team_id, "
        "         CASE WHEN yg.winner_id = m.youth_team_id THEN 1 ELSE 0 END AS w, "
        "         CASE WHEN yg.winner_id IS NOT NULL "
        "              AND yg.winner_id != m.youth_team_id THEN 1 ELSE 0 END AS l, "
        "         CASE WHEN yg.home_team_id = m.youth_team_id THEN yg.home_score "
        "              WHEN yg.away_team_id = m.youth_team_id THEN yg.away_score "
        "              ELSE 0 END AS rs, "
        "         CASE WHEN yg.home_team_id = m.youth_team_id THEN yg.away_score "
        "              WHEN yg.away_team_id = m.youth_team_id THEN yg.home_score "
        "              ELSE 0 END AS ra "
        "  FROM youth_group_membership m "
        "  JOIN youth_groups gr ON gr.id = m.group_id "
        "  LEFT JOIN youth_games yg "
        "       ON yg.season = gr.season "
        "      AND yg.bracket_round = 'group' "
        "      AND yg.played = 1 "
        "      AND (yg.home_team_id = m.youth_team_id OR yg.away_team_id = m.youth_team_id) "
        "  WHERE gr.season = ? "
        ") g "
        "GROUP BY g.group_id, g.group_letter, g.youth_team_id "
        "ORDER BY g.group_letter, "
        "         SUM(g.w) DESC, "
        "         SUM(g.rs) - SUM(g.ra) DESC, "
        "         SUM(g.rs) DESC, "
        "         g.youth_team_id",
        (season,),
    )
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["group_id"], []).append({
            "team_id":      r["team_id"],
            "group_letter": r["group_letter"],
            "w":            r["w"] or 0,
            "l":            r["l"] or 0,
            "rs":           r["rs"] or 0,
            "ra":           r["ra"] or 0,
        })
    return out


def _build_knockout_bracket(season: int, rng: random.Random) -> int:
    """After the group stage finishes, seed the R16. Standard pairings:
    group winner of A vs runner-up of B, A2 vs B1, C1 vs D2, etc. Then
    QF/SF/Final are scheduled with NULL team ids and filled as winners
    propagate (we just pre-create the slots). Returns count of R16
    games inserted."""
    groups = _group_standings(season)
    if not groups:
        return 0

    # Map group_letter → standings (length 4) for easy access.
    by_letter: dict[str, list[dict]] = {}
    for rows in groups.values():
        if rows:
            by_letter[rows[0]["group_letter"]] = rows

    # Pair group winners with runners-up of the next group, walking
    # the alphabet. (A1 vs B2, B1 vs A2, C1 vs D2, D1 vs C2, ...)
    # This gives all 8 group winners a different opponent, no immediate
    # rematch, and matches typical UEFA-style draw constraints.
    r16_pairs: list[tuple[int, int]] = []
    for i in range(0, len(_GROUP_LETTERS), 2):
        gl_a = _GROUP_LETTERS[i]
        gl_b = _GROUP_LETTERS[i + 1]
        a = by_letter.get(gl_a, [])
        b = by_letter.get(gl_b, [])
        if len(a) >= 2 and len(b) >= 2:
            r16_pairs.append((a[0]["team_id"], b[1]["team_id"]))
            r16_pairs.append((b[0]["team_id"], a[1]["team_id"]))

    n = 0
    for slot, (home, away) in enumerate(r16_pairs):
        db.execute(
            "INSERT INTO youth_games "
            "(season, bracket_round, bracket_slot, home_team_id, "
            " away_team_id, seed) "
            "VALUES (?, 'r16', ?, ?, ?, ?)",
            (season, slot, home, away, rng.randint(1, 2**31 - 1)),
        )
        n += 1
    return n


def _advance_knockout_round(
    season: int,
    from_round: str,
    to_round: str,
    rng: random.Random,
) -> int:
    """Pair winners of `from_round` games into `to_round` matchups.
    Slots come from the order of from_round's `bracket_slot` field;
    winners of slot 0+1 face each other in slot 0 of the next round,
    and so on. Returns count of next-round games inserted."""
    rows = db.fetchall(
        "SELECT bracket_slot, winner_id FROM youth_games "
        "WHERE season = ? AND bracket_round = ? AND played = 1 "
        "ORDER BY bracket_slot",
        (season, from_round),
    )
    if len(rows) < 2:
        return 0
    n = 0
    new_slot = 0
    for i in range(0, len(rows), 2):
        if i + 1 >= len(rows):
            break
        a = rows[i]["winner_id"]
        b = rows[i + 1]["winner_id"]
        if not a or not b:
            continue
        # Coin-flip home assignment (no real home/away in knockout).
        if rng.random() < 0.5:
            home, away = a, b
        else:
            home, away = b, a
        db.execute(
            "INSERT INTO youth_games "
            "(season, bracket_round, bracket_slot, home_team_id, "
            " away_team_id, seed) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (season, to_round, new_slot, home, away,
             rng.randint(1, 2**31 - 1)),
        )
        n += 1
        new_slot += 1
    return n


def run_youth_tournament(rng_seed: int = 0,
                         season: int | None = None) -> dict[str, Any]:
    """Run the full tournament: group draw → group games → knockout
    bracket. If a tournament for `season` already exists, this becomes
    a no-op (use `reset_youth_tournament` first to re-run).

    Returns a structured summary suitable for the UI.
    """
    init_youth_schema()
    if season is None:
        season = _next_season_number()
    rng = random.Random((rng_seed or 0) ^ 0xC07_07)

    if _has_tournament_for_season(season):
        return _summarise_tournament(season)

    groups = draw_groups(season, rng_seed=rng_seed)
    n_group_games = _schedule_group_games(season, groups, rng)
    _simulate_unplayed_games(season, "group", rng)
    n_r16 = _build_knockout_bracket(season, rng)
    _simulate_unplayed_games(season, "r16", rng)
    n_qf = _advance_knockout_round(season, "r16", "qf", rng)
    _simulate_unplayed_games(season, "qf", rng)
    n_sf = _advance_knockout_round(season, "qf", "sf", rng)
    _simulate_unplayed_games(season, "sf", rng)
    n_final = _advance_knockout_round(season, "sf", "final", rng)
    _simulate_unplayed_games(season, "final", rng)

    return _summarise_tournament(season)


def _summarise_tournament(season: int) -> dict[str, Any]:
    """Return a UI-friendly summary of the season's tournament state."""
    init_youth_schema()
    groups   = _group_standings(season)
    games    = db.fetchall(
        "SELECT yg.*, "
        "       ht.abbrev AS home_abbrev, ht.name AS home_name, "
        "       at.abbrev AS away_abbrev, at.name AS away_name "
        "FROM youth_games yg "
        "LEFT JOIN youth_teams ht ON ht.id = yg.home_team_id "
        "LEFT JOIN youth_teams at ON at.id = yg.away_team_id "
        "WHERE yg.season = ? "
        "ORDER BY CASE yg.bracket_round "
        "         WHEN 'group'  THEN 0 "
        "         WHEN 'r16'    THEN 1 "
        "         WHEN 'qf'     THEN 2 "
        "         WHEN 'sf'     THEN 3 "
        "         WHEN 'final'  THEN 4 ELSE 5 END, "
        "         yg.id",
        (season,),
    )
    by_round: dict[str, list[dict]] = {}
    for g in games:
        by_round.setdefault(g["bracket_round"], []).append(dict(g))

    final_game = (by_round.get("final") or [None])[0]
    champion = None
    if final_game and final_game.get("winner_id"):
        champ_team = db.fetchone(
            "SELECT * FROM youth_teams WHERE id = ?",
            (final_game["winner_id"],),
        )
        if champ_team:
            champion = dict(champ_team)

    # Attach team meta to each group standings row for display.
    teams = {t["id"]: dict(t) for t in db.fetchall("SELECT * FROM youth_teams")}
    enriched_groups: list[dict] = []
    for gid, rows in sorted(groups.items(),
                            key=lambda kv: kv[1][0]["group_letter"] if kv[1] else ""):
        enriched_rows = []
        for r in rows:
            t = teams.get(r["team_id"], {})
            enriched_rows.append({
                **r,
                "abbrev": t.get("abbrev", ""),
                "name":   t.get("name", ""),
                "country_code": t.get("country_code", ""),
            })
        if enriched_rows:
            enriched_groups.append({
                "group_id":     gid,
                "group_letter": enriched_rows[0]["group_letter"],
                "rows":         enriched_rows,
            })

    return {
        "season":     season,
        "groups":     enriched_groups,
        "by_round":   by_round,
        "champion":   champion,
        "complete":   bool(final_game and final_game.get("played")),
    }


def reset_youth_tournament(season: int | None = None) -> int:
    """Wipe the tournament for `season` so it can be re-run. Returns
    the count of game rows deleted. Mostly useful for re-rolling the
    bracket during a demo session."""
    init_youth_schema()
    if season is None:
        season = _next_season_number()
    n = db.fetchone(
        "SELECT COUNT(*) AS n FROM youth_games WHERE season = ?",
        (season,),
    )["n"]
    # Clear child stat rows before parent. The youth_sim tables were
    # added in a follow-up commit so legacy DBs may not have them yet —
    # tolerate the absence so a reset doesn't blow up on a pre-Phase-3
    # save.
    for child_table in ("game_youth_batter_stats", "game_youth_pitcher_stats"):
        try:
            db.execute(
                f"DELETE FROM {child_table} "
                f"WHERE game_id IN (SELECT id FROM youth_games WHERE season = ?)",
                (season,),
            )
        except Exception:
            pass
    db.execute("DELETE FROM youth_games WHERE season = ?", (season,))
    db.execute(
        "DELETE FROM youth_group_membership "
        "WHERE group_id IN (SELECT id FROM youth_groups WHERE season = ?)",
        (season,),
    )
    db.execute("DELETE FROM youth_groups WHERE season = ?", (season,))
    return n


def get_tournament(season: int | None = None) -> dict[str, Any] | None:
    """Read-only fetch of a season's tournament summary, or None when
    no tournament has been run yet."""
    init_youth_schema()
    if season is None:
        season = _next_season_number()
    if not _has_tournament_for_season(season):
        return None
    return _summarise_tournament(season)
