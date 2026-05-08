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

# Per-team roster shape for the youth league. 8 hitters at canonical
# positions + 4 pitchers — small enough to scan on a single page,
# large enough to populate a prospect list.
_HITTER_POSITIONS = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]
_N_PITCHERS = 4

# Total per team = 12. Over 32 teams = 384 youth players league-wide.
ROSTER_SIZE = len(_HITTER_POSITIONS) + _N_PITCHERS

# Age bounds. Players spawn at ages 14-19 in the inaugural pass; in
# subsequent annual passes the AGE_OUT threshold drives graduation
# into the pro FA pool. Players who AGE PAST 19 (i.e. would turn 20)
# graduate at the offseason, mirroring the U19 cricket / under-20 youth
# soccer cap.
_SEED_AGE_LO = 14
_SEED_AGE_HI = 19
AGE_OUT      = 19   # players who would turn 20 graduate this offseason


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
    age            INTEGER NOT NULL,
    seed_year      INTEGER NOT NULL DEFAULT 1,
    -- Attribute set mirrors the engine's hitter / pitcher dev attrs so
    -- we can call o27v2.development._develop_player on these rows
    -- directly.
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
    habit_cup          REAL    DEFAULT 0.5
);
"""


def init_youth_schema() -> None:
    """Create the youth_* tables if they don't exist. Idempotent."""
    db.execute(_SCHEMA_TEAMS)
    db.execute(_SCHEMA_PLAYERS)


# ---------------------------------------------------------------------------
# Player generation (reuses league.py's _make_hitter / _make_pitcher,
# with the age field overridden into the youth band)
# ---------------------------------------------------------------------------

def _make_youth_player(
    rng: random.Random,
    pos: str,
    is_pitcher: bool,
    name: str,
    country: str,
    age: int,
) -> dict:
    """Build one youth player dict. Reuses the standard maker so the
    attribute distribution stays consistent with the pro league, then
    forces age into the youth band."""
    from o27v2.league import _make_hitter, _make_pitcher
    if is_pitcher:
        p = _make_pitcher(rng, is_active=1, name=name, country=country)
    else:
        p = _make_hitter(rng, pos, is_active=1, name=name, country=country)
    p["age"] = age
    return p


def _name_picker_for_region(rng: random.Random, region_id: str):
    """Build a name picker biased entirely toward the given region,
    falling back to americas_pro if the region isn't found."""
    from o27v2.league import make_name_picker, get_name_regions
    regions = get_name_regions()
    if region_id in regions:
        weights = {region_id: 1.0}
    else:
        # Fallback: the americas/us pool, which is always present.
        weights = {"us": 1.0}
    return make_name_picker(rng, gender="male", region_weights=weights)


def _spawn_roster(
    team: dict,
    rng: random.Random,
    seed_year: int,
    age_dist: list[int] | None = None,
) -> list[dict]:
    """Build a full 12-player roster for one youth team."""
    name_pick = _name_picker_for_region(rng, team["name_region"])
    rows: list[dict] = []
    if age_dist is None:
        # Inaugural seed — spread across the youth age band.
        age_dist = [
            rng.randint(_SEED_AGE_LO, _SEED_AGE_HI)
            for _ in range(ROSTER_SIZE)
        ]
    for i, pos in enumerate(_HITTER_POSITIONS):
        nm, ctry = name_pick()
        country = ctry or team["country_code"]
        p = _make_youth_player(rng, pos, is_pitcher=False, name=nm,
                               country=country, age=age_dist[i])
        p["seed_year"] = seed_year
        rows.append(p)
    for j in range(_N_PITCHERS):
        nm, ctry = name_pick()
        country = ctry or team["country_code"]
        p = _make_youth_player(rng, "P", is_pitcher=True, name=nm,
                               country=country,
                               age=age_dist[len(_HITTER_POSITIONS) + j])
        p["seed_year"] = seed_year
        rows.append(p)
    return rows


# ---------------------------------------------------------------------------
# Inserts
# ---------------------------------------------------------------------------

_PLAYER_COLS = (
    "youth_team_id", "name", "country", "position", "is_pitcher", "age",
    "seed_year",
    "skill", "speed", "pitcher_skill",
    "contact", "power", "eye", "command", "movement", "stamina",
    "defense", "arm", "defense_infield", "defense_outfield", "defense_catcher",
    "baserunning", "run_aggressiveness",
    "bats", "throws", "work_ethic", "work_habits", "habit_cup",
)


def _insert_player(team_id: int, p: dict) -> None:
    cols = ", ".join(_PLAYER_COLS)
    qs   = ", ".join("?" for _ in _PLAYER_COLS)
    vals = (
        team_id, p["name"], p.get("country", ""),
        p["position"], int(p["is_pitcher"]), int(p["age"]),
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
    pro-side player id, or None on failure."""
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
    """After graduations, top up the team back to ROSTER_SIZE with new
    18-year-olds. Matches each missing position so the roster stays
    structurally complete."""
    rows = db.fetchall(
        "SELECT position, is_pitcher FROM youth_players "
        "WHERE youth_team_id = ?",
        (team["id"],),
    )
    have_positions: list[str] = [r["position"] for r in rows]
    have_pitcher_count = sum(1 for r in rows if r["is_pitcher"])

    name_pick = _name_picker_for_region(rng, team["name_region"])

    # Restore canonical hitter slots first.
    added = 0
    for pos in _HITTER_POSITIONS:
        if pos not in have_positions:
            nm, ctry = name_pick()
            country = ctry or team["country_code"]
            p = _make_youth_player(rng, pos, is_pitcher=False,
                                   name=nm, country=country,
                                   age=_SEED_AGE_LO)
            p["seed_year"] = seed_year
            _insert_player(team["id"], p)
            have_positions.append(pos)
            added += 1

    # Restore the pitcher pool.
    while have_pitcher_count < _N_PITCHERS:
        nm, ctry = name_pick()
        country = ctry or team["country_code"]
        p = _make_youth_player(rng, "P", is_pitcher=True,
                               name=nm, country=country,
                               age=_SEED_AGE_LO)
        p["seed_year"] = seed_year
        _insert_player(team["id"], p)
        have_pitcher_count += 1
        added += 1
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
    init_youth_schema()
    rows = db.fetchall(
        "SELECT t.*, "
        "       (SELECT COUNT(*) FROM youth_players p WHERE p.youth_team_id = t.id) AS roster_size, "
        "       (SELECT ROUND(AVG(p.age), 1)  FROM youth_players p WHERE p.youth_team_id = t.id) AS avg_age, "
        "       (SELECT ROUND(AVG((p.skill + p.contact + p.power + p.eye) / 4.0)) "
        "          FROM youth_players p "
        "          WHERE p.youth_team_id = t.id AND p.is_pitcher = 0) AS bat_grade, "
        "       (SELECT ROUND(AVG((p.pitcher_skill + p.command + p.movement) / 3.0)) "
        "          FROM youth_players p "
        "          WHERE p.youth_team_id = t.id AND p.is_pitcher = 1) AS arm_grade "
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
                  archetype: str = "overall") -> list[dict]:
    """Return the top youth players league-wide by the requested
    archetype.

    `archetype` ∈ {"overall", "bat", "arm", "speed"}. The composite
    used in each branch is the same one the team summary uses, so the
    /youth list and per-team list line up.
    """
    init_youth_schema()
    if archetype == "bat":
        sort_expr = "(skill + contact + power + eye) / 4.0"
        where = " WHERE p.is_pitcher = 0"
    elif archetype == "arm":
        sort_expr = "(pitcher_skill + command + movement) / 3.0"
        where = " WHERE p.is_pitcher = 1"
    elif archetype == "speed":
        sort_expr = "speed"
        where = ""
    else:  # overall
        sort_expr = (
            "CASE WHEN is_pitcher = 1 "
            "THEN (pitcher_skill + command + movement) / 3.0 "
            "ELSE (skill + contact + power + eye) / 4.0 END"
        )
        where = ""
    rows = db.fetchall(
        f"SELECT p.*, t.name AS team_name, t.abbrev AS team_abbrev, "
        f"       t.country_code AS team_country, "
        f"       ROUND({sort_expr}, 1) AS composite "
        f"FROM youth_players p "
        f"JOIN youth_teams t ON t.id = p.youth_team_id "
        f"{where} "
        f"ORDER BY composite DESC, p.age ASC "
        f"LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]
