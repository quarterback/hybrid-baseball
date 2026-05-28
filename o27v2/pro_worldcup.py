"""
Pro World Cup — international tournament for the pro player pool.

End-of-season showcase that pulls real pro players onto national teams,
runs a regional qualifying phase to fill a 24-nation field, then plays
4 groups of 6 → 8-team knockout. Each season rolls a fresh WC.

Three-phase pipeline:
  1. **Qualifying**: every eligible nation (≥9 active hitters + ≥5
     pitchers in the pro pool) enters a regional round-robin
     (Americas / Asia / Europe / Other). Regional quotas sum to 24.
  2. **Rosters**: each qualified nation gets an auto-rolled 22-man
     roster — best position-by-position pros eligible for that country,
     plus a pitching staff. The web UI lets the user swap players
     before the main tournament starts.
  3. **Main tournament**: 4 groups of 6, top 2 advance to an 8-team
     knockout (QF → SF → Final).

Player eligibility:
  * Auto-pick: only `players.country` (primary nationality).
  * Manual editor: surfaces `secondary_country` heritage call-ups too
    (with a flag in the UI). A player can only be on one national
    team per season — the editor enforces uniqueness.

Engine path: mirrors `o27v2.youth_sim` (the real PA-by-PA engine via
`o27.engine.run_game`), but pulls live pro attributes off `players`
without the youth YPI governor — pros play at their true grade.
Per-game stats land in `game_wc_batter_stats` / `game_wc_pitcher_stats`
so the WC has its own box-score history separate from the regular
season (no double-counting).
"""
from __future__ import annotations

import random
from typing import Any

from o27v2 import db
from o27v2 import scout as _scout
from o27v2 import youth as _youth

from o27.engine.state import Player, Team, GameState
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer


# ---------------------------------------------------------------------------
# Region rollup + quotas
# ---------------------------------------------------------------------------
#
# The youth `_COUNTRY_REGION` map uses 7 sub-regions; the WC rolls those up
# to 4 broader pots so regional quotas balance to exactly 24 main-bracket
# slots. Africa + Oceania share an "Other" pot — together they have just
# enough nations to be competitive for the 2 berths.

_WC_REGION_ROLLUP: dict[str, str] = {
    "North America": "Americas",
    "Caribbean":     "Americas",
    "South America": "Americas",
    "Europe":        "Europe",
    "Asia":          "Asia",
    "Africa":        "Other",
    "Oceania":       "Other",
    "Other":         "Other",
}

WC_REGION_ORDER: list[str] = ["Americas", "Asia", "Europe", "Other"]

# Quotas (sum = 24) — the per-region count that advances out of qualifying
# into the 24-nation main bracket. Tuned to the realistic talent depth
# distribution: Americas dominates (US/DR/PR/Cuba/Venezuela/Mexico/etc.),
# Asia is strong (Japan/Korea/Taiwan), Europe is the third pole (Nether-
# lands/Italy/Germany), Other has limited depth but always gets a couple
# of berths so Australia + RSA don't sit out every cycle.
WC_REGIONAL_QUOTAS: dict[str, int] = {
    "Americas": 9,
    "Asia":     7,
    "Europe":   6,
    "Other":    2,
}

# Max nations per region that enter qualifying. Caps total qualifying-stage
# games. A region with more eligible nations takes its top N by pool
# strength; the rest don't make qualifying that year.
WC_MAX_PER_REGION = 12


def _country_wc_region(country_code: str) -> str:
    sub = _youth.country_region(country_code)
    return _WC_REGION_ROLLUP.get(sub, "Other")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_TEAMS = """
CREATE TABLE IF NOT EXISTS wc_teams (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    season        INTEGER NOT NULL,
    country_code  TEXT NOT NULL,
    name          TEXT NOT NULL,
    abbrev        TEXT NOT NULL,
    region        TEXT NOT NULL,
    pool_strength REAL DEFAULT 0,
    qualified     INTEGER DEFAULT 0,
    final_position TEXT DEFAULT NULL,
    UNIQUE (season, country_code)
);
"""

_SCHEMA_TEAM_PLAYERS = """
CREATE TABLE IF NOT EXISTS wc_team_players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    wc_team_id  INTEGER NOT NULL REFERENCES wc_teams(id),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    roster_slot TEXT DEFAULT '',
    is_active   INTEGER DEFAULT 1,
    UNIQUE (wc_team_id, player_id)
);
"""

_SCHEMA_GROUPS = """
CREATE TABLE IF NOT EXISTS wc_groups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    season       INTEGER NOT NULL,
    stage        TEXT NOT NULL,   -- 'qual:Americas' | 'qual:Asia' | ... | 'main'
    letter       TEXT NOT NULL,
    UNIQUE (season, stage, letter)
);
"""

_SCHEMA_GROUP_MEM = """
CREATE TABLE IF NOT EXISTS wc_group_membership (
    group_id    INTEGER NOT NULL REFERENCES wc_groups(id),
    wc_team_id  INTEGER NOT NULL REFERENCES wc_teams(id),
    UNIQUE (group_id, wc_team_id)
);
"""

_SCHEMA_GAMES = """
CREATE TABLE IF NOT EXISTS wc_games (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    season          INTEGER NOT NULL,
    phase           TEXT NOT NULL,   -- 'qual' | 'group' | 'qf' | 'sf' | 'final'
    group_id        INTEGER REFERENCES wc_groups(id),
    bracket_slot    INTEGER,
    home_wc_team_id INTEGER NOT NULL REFERENCES wc_teams(id),
    away_wc_team_id INTEGER NOT NULL REFERENCES wc_teams(id),
    home_score      INTEGER DEFAULT 0,
    away_score      INTEGER DEFAULT 0,
    winner_wc_team_id INTEGER REFERENCES wc_teams(id),
    played          INTEGER DEFAULT 0,
    seed            INTEGER DEFAULT 0
);
"""

_SCHEMA_BATTER_STATS = """
CREATE TABLE IF NOT EXISTS game_wc_batter_stats (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       INTEGER NOT NULL REFERENCES wc_games(id),
    wc_team_id    INTEGER NOT NULL REFERENCES wc_teams(id),
    player_id     INTEGER NOT NULL REFERENCES players(id),
    pa            INTEGER DEFAULT 0,
    ab            INTEGER DEFAULT 0,
    runs          INTEGER DEFAULT 0,
    hits          INTEGER DEFAULT 0,
    doubles       INTEGER DEFAULT 0,
    triples       INTEGER DEFAULT 0,
    hr            INTEGER DEFAULT 0,
    rbi           INTEGER DEFAULT 0,
    bb            INTEGER DEFAULT 0,
    k             INTEGER DEFAULT 0,
    stays         INTEGER DEFAULT 0,
    outs_recorded INTEGER DEFAULT 0
);
"""

_SCHEMA_PITCHER_STATS = """
CREATE TABLE IF NOT EXISTS game_wc_pitcher_stats (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       INTEGER NOT NULL REFERENCES wc_games(id),
    wc_team_id    INTEGER NOT NULL REFERENCES wc_teams(id),
    player_id     INTEGER NOT NULL REFERENCES players(id),
    is_starter    INTEGER DEFAULT 0,
    batters_faced INTEGER DEFAULT 0,
    outs_recorded INTEGER DEFAULT 0,
    hits_allowed  INTEGER DEFAULT 0,
    runs_allowed  INTEGER DEFAULT 0,
    er            INTEGER DEFAULT 0,
    bb            INTEGER DEFAULT 0,
    k             INTEGER DEFAULT 0,
    hr_allowed    INTEGER DEFAULT 0,
    pitches       INTEGER DEFAULT 0
);
"""

_SCHEMA_META = """
CREATE TABLE IF NOT EXISTS wc_meta (
    season   INTEGER PRIMARY KEY,
    phase    TEXT NOT NULL DEFAULT 'qualifying',
    -- 'qualifying' → 'rosters' → 'tournament' → 'complete'
    rosters_locked INTEGER DEFAULT 0
);
"""


def init_wc_schema() -> None:
    for sql in (
        _SCHEMA_TEAMS, _SCHEMA_TEAM_PLAYERS, _SCHEMA_GROUPS,
        _SCHEMA_GROUP_MEM, _SCHEMA_GAMES,
        _SCHEMA_BATTER_STATS, _SCHEMA_PITCHER_STATS,
        _SCHEMA_META,
    ):
        db.execute(sql)


# ---------------------------------------------------------------------------
# Country names + abbrevs (lifted from youth.py's nation seed list so the
# WC and the youth competitions surface the same display name for each
# code).
# ---------------------------------------------------------------------------

_COUNTRY_DISPLAY: dict[str, tuple[str, str]] = {
    # Americas
    "US": ("United States", "USA"),  "CA": ("Canada", "CAN"),
    "MX": ("Mexico", "MEX"),         "DO": ("Dominican Republic", "DOM"),
    "PR": ("Puerto Rico", "PUR"),    "CU": ("Cuba", "CUB"),
    "JM": ("Jamaica", "JAM"),        "TT": ("Trinidad & Tobago", "TTO"),
    "SR": ("Suriname", "SUR"),       "GY": ("Guyana", "GUY"),
    "CW": ("Curaçao", "CUW"),        "HT": ("Haiti", "HAI"),
    "AW": ("Aruba", "ABW"),          "BB": ("Barbados", "BAR"),
    "BS": ("Bahamas", "BAH"),        "BM": ("Bermuda", "BER"),
    "VE": ("Venezuela", "VEN"),      "CO": ("Colombia", "COL"),
    "BR": ("Brazil", "BRA"),         "AR": ("Argentina", "ARG"),
    # Europe
    "GB": ("Great Britain", "GBR"),  "IE": ("Ireland", "IRL"),
    "NL": ("Netherlands", "NED"),    "IT": ("Italy", "ITA"),
    "CZ": ("Czechia", "CZE"),        "FI": ("Finland", "FIN"),
    "GR": ("Greece", "GRE"),         "SE": ("Sweden", "SWE"),
    "NO": ("Norway", "NOR"),         "DK": ("Denmark", "DEN"),
    "DE": ("Germany", "GER"),        "AT": ("Austria", "AUT"),
    "CH": ("Switzerland", "SUI"),    "HR": ("Croatia", "CRO"),
    "SI": ("Slovenia", "SVN"),       "HU": ("Hungary", "HUN"),
    "SK": ("Slovakia", "SVK"),       "RU": ("Russia", "RUS"),
    "UA": ("Ukraine", "UKR"),        "LT": ("Lithuania", "LTU"),
    "TR": ("Turkey", "TUR"),         "SM": ("San Marino", "SMR"),
    "ES": ("Spain", "ESP"),          "PL": ("Poland", "POL"),
    "BE": ("Belgium", "BEL"),
    # Africa
    "ZA": ("South Africa", "RSA"),   "ZW": ("Zimbabwe", "ZIM"),
    "NA": ("Namibia", "NAM"),        "CV": ("Cape Verde", "CPV"),
    "MU": ("Mauritius", "MRI"),      "UG": ("Uganda", "UGA"),
    # Asia
    "IN": ("India", "IND"),          "PK": ("Pakistan", "PAK"),
    "MY": ("Malaysia", "MAS"),       "PH": ("Philippines", "PHI"),
    "JP": ("Japan", "JPN"),          "KR": ("Korea", "KOR"),
    "TW": ("Chinese Taipei", "TPE"), "LK": ("Sri Lanka", "SRI"),
    "BD": ("Bangladesh", "BAN"),     "NP": ("Nepal", "NEP"),
    "AF": ("Afghanistan", "AFG"),    "IL": ("Israel", "ISR"),
    "ID": ("Indonesia", "INA"),      "TH": ("Thailand", "THA"),
    "KZ": ("Kazakhstan", "KAZ"),     "HK": ("Hong Kong", "HKG"),
    "IR": ("Iran", "IRI"),           "PS": ("Palestine", "PLE"),
    "LB": ("Lebanon", "LBN"),
    # Oceania
    "AU": ("Australia", "AUS"),      "NZ": ("New Zealand", "NZL"),
    "FJ": ("Fiji", "FIJ"),           "GU": ("Guam", "GUM"),
    "WS": ("Samoa", "SAM"),
}


def _display_for(country_code: str) -> tuple[str, str]:
    """Return (full_name, abbrev) for a country code. Falls back to the
    code itself if unknown."""
    cc = (country_code or "").upper()
    return _COUNTRY_DISPLAY.get(cc, (cc, cc[:3]))


# ---------------------------------------------------------------------------
# Player composite scoring (drives auto-roster picks)
# ---------------------------------------------------------------------------

def _player_composite(p: dict) -> float:
    """Single-number talent score for ranking pros within a country pool.
    Higher = better. Hitters weight the offensive triad heavily with
    secondary credit for defense + speed; pitchers weight raw stuff +
    command + movement + stamina."""
    if p.get("is_pitcher"):
        return (
            (p.get("pitcher_skill") or 50) * 0.40 +
            (p.get("command")       or 50) * 0.20 +
            (p.get("movement")      or 50) * 0.20 +
            (p.get("stamina")       or 50) * 0.20
        )
    return (
        (p.get("skill")   or 50) * 0.25 +
        (p.get("contact") or 50) * 0.20 +
        (p.get("power")   or 50) * 0.18 +
        (p.get("eye")     or 50) * 0.12 +
        (p.get("defense") or 50) * 0.10 +
        (p.get("arm")     or 50) * 0.05 +
        (p.get("speed")   or 50) * 0.10
    )


def _country_pool(country_code: str, *, include_secondary: bool = False) -> list[dict]:
    """All active pros eligible for `country_code`. Primary nationality
    always counts; `include_secondary=True` adds heritage call-ups via
    `secondary_country` (used by the roster editor, NOT the auto-picker
    so a dual-national doesn't accidentally appear on two squads at
    once)."""
    cc = (country_code or "").upper()
    if include_secondary:
        rows = db.fetchall(
            "SELECT * FROM players "
            "WHERE is_active = 1 AND (UPPER(country) = ? OR UPPER(secondary_country) = ?)",
            (cc, cc),
        )
    else:
        rows = db.fetchall(
            "SELECT * FROM players WHERE is_active = 1 AND UPPER(country) = ?",
            (cc,),
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Roster picker (auto-mode)
# ---------------------------------------------------------------------------

_HITTER_POSITIONS_ORDER = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]
WC_ROSTER_SIZE = 22


def _pick_auto_roster(pool: list[dict]) -> list[int]:
    """Pick a 22-man squad from a country's eligible pool.

    Slot shape: 8 positional starters (best at each canonical position)
    + 4 best position-player backups + 8 best pitchers + 2 jokers (or 2
    more position-player backups if the pool is shallow on jokers).

    Returns player IDs in roster order: starters by position, then
    backups, then pitchers, then jokers. The engine builder reads this
    order to stamp the first-at-position-is-starter convention.
    """
    # Bucket the pool.
    by_pos: dict[str, list[dict]] = {}
    pitchers: list[dict] = []
    jokers:   list[dict] = []
    backups:  list[dict] = []
    for p in pool:
        if p.get("roster_slot") == "joker" or p.get("is_joker"):
            jokers.append(p)
        elif p.get("is_pitcher"):
            pitchers.append(p)
        else:
            pos = str(p.get("position") or "")
            by_pos.setdefault(pos, []).append(p)
            backups.append(p)

    # Best at each fielding position.
    picked: list[dict] = []
    picked_ids: set[int] = set()

    for pos in _HITTER_POSITIONS_ORDER:
        candidates = sorted(by_pos.get(pos, []),
                            key=_player_composite, reverse=True)
        if candidates:
            best = candidates[0]
            picked.append(best)
            picked_ids.add(int(best["id"]))

    # 4 best backups (any non-pitcher not already picked).
    remaining_backups = sorted(
        (p for p in backups if int(p["id"]) not in picked_ids),
        key=_player_composite,
        reverse=True,
    )
    for p in remaining_backups[:4]:
        picked.append(p)
        picked_ids.add(int(p["id"]))

    # 8 best pitchers.
    top_pitchers = sorted(pitchers, key=_player_composite, reverse=True)[:8]
    for p in top_pitchers:
        picked.append(p)
        picked_ids.add(int(p["id"]))

    # 2 jokers (or extra backups if no jokers available).
    top_jokers = sorted(jokers, key=_player_composite, reverse=True)[:2]
    for p in top_jokers:
        picked.append(p)
        picked_ids.add(int(p["id"]))
    while len(picked) < WC_ROSTER_SIZE:
        # Fall through to more position-player depth if jokers were thin.
        extras = [p for p in remaining_backups
                  if int(p["id"]) not in picked_ids]
        if not extras:
            extras = [p for p in pitchers if int(p["id"]) not in picked_ids]
        if not extras:
            break
        nxt = extras[0]
        picked.append(nxt)
        picked_ids.add(int(nxt["id"]))

    return [int(p["id"]) for p in picked]


def _pool_strength(pool: list[dict]) -> float:
    """Average composite of a country's TOP 22 pros — drives qualifying
    seeding and the cut to 12 nations per region."""
    if not pool:
        return 0.0
    top = sorted(pool, key=_player_composite, reverse=True)[:WC_ROSTER_SIZE]
    return sum(_player_composite(p) for p in top) / len(top)


def _eligible_country_codes() -> list[str]:
    """Every country code that has enough active pros (≥9 hitters and
    ≥5 pitchers) to field a national team."""
    rows = db.fetchall(
        "SELECT UPPER(country) AS cc, "
        "       SUM(CASE WHEN is_pitcher = 0 THEN 1 ELSE 0 END) AS h, "
        "       SUM(CASE WHEN is_pitcher = 1 THEN 1 ELSE 0 END) AS p "
        "FROM players WHERE is_active = 1 AND country != '' "
        "GROUP BY UPPER(country)"
    )
    out: list[str] = []
    for r in rows:
        if (r["h"] or 0) >= 9 and (r["p"] or 0) >= 5:
            out.append(r["cc"])
    return out


# ---------------------------------------------------------------------------
# Engine team conversion (mirrors youth_sim._build_youth_engine_team
# but pulls real pro attributes without the YPI governor)
# ---------------------------------------------------------------------------

def _make_engine_player(p: dict, *, home_bonus: float = 0.0) -> Player:
    """Build an engine Player from a `players` row. No YPI multiplier —
    pros play at their true grade."""
    stamina_grade = p.get("stamina") or p.get("pitcher_skill") or 50
    archetype = ""
    if p.get("roster_slot") == "joker" or p.get("is_joker"):
        archetype = str(p.get("archetype") or "")
    ep = Player(
        player_id=str(p["id"]),
        name=p["name"],
        is_pitcher=bool(p["is_pitcher"]),
        skill=_scout.to_unit(p["skill"]) + home_bonus,
        speed=_scout.to_unit(p["speed"]),
        pitcher_skill=_scout.to_unit(p["pitcher_skill"]),
        stamina=_scout.to_unit(stamina_grade),
        stay_aggressiveness=float(p.get("stay_aggressiveness") or 0.30),
        contact_quality_threshold=float(p.get("contact_quality_threshold") or 0.50),
        archetype=archetype,
        pitcher_role=str(p.get("pitcher_role") or ""),
        hard_contact_delta=float(p.get("hard_contact_delta") or 0.0),
        hr_weight_bonus=float(p.get("hr_weight_bonus") or 0.0),
        contact=_scout.to_unit(p.get("contact") or 50),
        power=_scout.to_unit(p.get("power") or 50),
        eye=_scout.to_unit(p.get("eye") or 50),
        command=_scout.to_unit(p.get("command") or 50),
        movement=_scout.to_unit(p.get("movement") or 50),
        bats=str(p.get("bats") or "R"),
        throws=str(p.get("throws") or "R"),
        defense=_scout.to_unit(p.get("defense") or 50),
        arm=_scout.to_unit(p.get("arm") or 50),
        defense_infield=_scout.to_unit(p.get("defense_infield") or 50),
        defense_outfield=_scout.to_unit(p.get("defense_outfield") or 50),
        defense_catcher=_scout.to_unit(p.get("defense_catcher") or 50),
        baserunning=_scout.to_unit(p.get("baserunning") or 50),
        run_aggressiveness=_scout.to_unit(p.get("run_aggressiveness") or 50),
        position=str(p.get("position") or ("P" if p.get("is_pitcher") else "DH")),
    )
    rs = p.get("roster_slot")
    if rs:
        ep.roster_slot = str(rs)
    rh = p.get("role_hit")
    if rh is not None:
        ep.role_hit = bool(int(rh))
    rr = p.get("role_run")
    if rr is not None:
        ep.role_run = bool(int(rr))
    rtw = p.get("role_two_way")
    if rtw is not None:
        ep.role_two_way = bool(int(rtw))
    rfp = p.get("role_field_pos")
    if rfp is not None:
        ep.role_field_pos = str(rfp)
    return ep


def _pick_wc_starter(wc_team_id: int, season: int,
                     rng: random.Random) -> int | None:
    """Pick today's SP for a WC team — fewest tournament starts so far,
    then highest pitcher_skill. Mirrors youth_sim._pick_youth_starter."""
    pitchers = db.fetchall(
        "SELECT p.id, p.pitcher_skill FROM wc_team_players wtp "
        "JOIN players p ON p.id = wtp.player_id "
        "WHERE wtp.wc_team_id = ? AND wtp.is_active = 1 AND p.is_pitcher = 1",
        (wc_team_id,),
    )
    if not pitchers:
        return None
    starts = db.fetchall(
        "SELECT player_id, COUNT(*) AS n "
        "FROM game_wc_pitcher_stats gp "
        "JOIN wc_games wg ON wg.id = gp.game_id "
        "WHERE gp.wc_team_id = ? AND gp.is_starter = 1 AND wg.season = ? "
        "GROUP BY player_id",
        (wc_team_id, season),
    )
    starts_by_pid = {r["player_id"]: r["n"] for r in starts}
    ranked = sorted(
        (dict(p) for p in pitchers),
        key=lambda p: (
            starts_by_pid.get(p["id"], 0),
            -int(p["pitcher_skill"] or 50),
            p["id"],
        ),
    )
    return ranked[0]["id"]


def _build_wc_engine_team(
    wc_team_id: int,
    team_role: str,
    season: int,
    rng: random.Random,
) -> tuple[Team, list[dict], int]:
    """Engine Team from a WC roster. Returns (team, player_rows, starter_pid)."""
    team_row = db.fetchone("SELECT * FROM wc_teams WHERE id = ?", (wc_team_id,))
    if team_row is None:
        raise ValueError(f"WC team {wc_team_id} not found")

    rows = db.fetchall(
        "SELECT p.* FROM wc_team_players wtp "
        "JOIN players p ON p.id = wtp.player_id "
        "WHERE wtp.wc_team_id = ? AND wtp.is_active = 1 "
        "ORDER BY wtp.id",
        (wc_team_id,),
    )
    players = [dict(r) for r in rows]
    if not players:
        raise ValueError(f"WC team {wc_team_id} has no players")

    starter_pid = _pick_wc_starter(wc_team_id, season, rng)
    if starter_pid is None:
        raise ValueError(f"WC team {wc_team_id} has no pitchers")

    HOME_BONUS = 0.005 if team_role == "home" else 0.0
    engine_players: list[Player] = []
    starting_hitters_by_pos: dict[str, Player] = {}
    backup_hitters: list[Player] = []
    pitchers_engine: list[Player] = []
    jokers_engine: list[Player] = []
    starter_engine: Player | None = None

    seen_starter_pos: set[str] = set()
    for p in players:
        is_joker = (p.get("roster_slot") == "joker" or bool(p.get("is_joker")))
        is_pitcher = bool(p.get("is_pitcher"))
        ep = _make_engine_player(p, home_bonus=HOME_BONUS)
        engine_players.append(ep)
        if is_joker:
            jokers_engine.append(ep)
            continue
        if is_pitcher:
            pitchers_engine.append(ep)
            if p["id"] == starter_pid:
                starter_engine = ep
            continue
        pos = str(p.get("position") or "")
        if pos in _HITTER_POSITIONS_ORDER and pos not in seen_starter_pos:
            starting_hitters_by_pos[pos] = ep
            seen_starter_pos.add(pos)
        else:
            backup_hitters.append(ep)

    if starter_engine is None and pitchers_engine:
        starter_engine = max(pitchers_engine,
                             key=lambda x: getattr(x, "stamina", 0.5))
    if starter_engine is None:
        raise ValueError(f"WC team {wc_team_id} has no usable pitchers")

    starting_fielders: list[Player] = [
        starting_hitters_by_pos[pos]
        for pos in _HITTER_POSITIONS_ORDER
        if pos in starting_hitters_by_pos
    ]
    while len(starting_fielders) < 8 and backup_hitters:
        starting_fielders.append(backup_hitters.pop(0))

    from o27v2.sim import _ordered_lineup, _assign_game_positions
    _assign_game_positions(starting_fielders, [starter_engine], jokers_engine)
    lineup = _ordered_lineup(starting_fielders, [starter_engine])

    if starter_engine in engine_players:
        engine_players = [starter_engine] + [
            p for p in engine_players if p is not starter_engine
        ]

    team = Team(
        team_id=team_role,
        name=team_row["name"],
        roster=engine_players,
        lineup=lineup,
        park_hr=1.0,
        park_hits=1.0,
        defense_rating=0.5,
        catcher_arm=0.5,
        manager_archetype="",
        mgr_quick_hook=0.5,
        mgr_bullpen_aggression=0.5,
        mgr_leverage_aware=0.5,
        mgr_joker_aggression=0.5,
        mgr_pinch_hit_aggression=0.5,
        mgr_platoon_aggression=0.5,
        mgr_run_game=0.5,
        mgr_bench_usage=0.5,
        jokers_available=jokers_engine,
    )
    team.bench = list(backup_hitters)
    return team, players, int(starter_engine.player_id)


# ---------------------------------------------------------------------------
# Stat extraction (mirrors youth_sim's lean extractor)
# ---------------------------------------------------------------------------

def _extract_batter_rows(renderer: Renderer, wc_team_id: int,
                         players: list[dict]) -> list[dict]:
    pids = {p["id"] for p in players}
    out: list[dict] = []
    phases = renderer.phases_seen() or [0]
    for phase in phases:
        phase_stats = (renderer.batter_stats_for_phase(phase)
                       if phases != [0] or renderer.phases_seen()
                       else dict(renderer._batter_stats))
        for engine_pid, bstat in phase_stats.items():
            try:
                pid = int(engine_pid)
            except (TypeError, ValueError):
                continue
            if pid not in pids:
                continue
            out.append({
                "wc_team_id": wc_team_id,
                "player_id":  pid,
                "pa":         bstat.pa,
                "ab":         bstat.ab,
                "runs":       bstat.runs,
                "hits":       bstat.hits,
                "doubles":    bstat.doubles,
                "triples":    bstat.triples,
                "hr":         bstat.hr,
                "rbi":        bstat.rbi,
                "bb":         bstat.bb,
                "k":          bstat.k,
                "stays":      getattr(bstat, "sty", 0),
                "outs_recorded": bstat.outs_recorded,
            })
    agg: dict[int, dict] = {}
    for r in out:
        a = agg.setdefault(r["player_id"], {**r})
        if a is r:
            continue
        for k, v in r.items():
            if isinstance(v, int) and k not in ("wc_team_id", "player_id"):
                a[k] = a.get(k, 0) + v
    return list(agg.values())


def _extract_pitcher_rows(state: GameState, wc_team_id: int,
                          players: list[dict],
                          starter_pid: int) -> list[dict]:
    from o27.stats.pitcher import PitcherStats
    pids = {p["id"] for p in players}
    by_pid: dict[int, list] = {}
    for rec in state.spell_log:
        try:
            pid = int(rec.pitcher_id)
        except (TypeError, ValueError):
            continue
        if pid not in pids:
            continue
        by_pid.setdefault(pid, []).append(rec)
    out: list[dict] = []
    for pid, spells in by_pid.items():
        ps = PitcherStats.from_spell_log(spells, str(pid), "")
        out.append({
            "wc_team_id":    wc_team_id,
            "player_id":     pid,
            "is_starter":    1 if pid == starter_pid else 0,
            "batters_faced": ps.batters_faced,
            "outs_recorded": ps.outs_recorded,
            "hits_allowed":  ps.hits_allowed,
            "runs_allowed":  ps.runs_allowed,
            "er":            max(0, ps.runs_allowed - getattr(ps, "unearned_runs", 0)),
            "bb":            ps.bb,
            "k":             ps.k,
            "hr_allowed":    ps.hr_allowed,
            "pitches":       ps.pitches_thrown,
        })
    return out


def _insert_batter_rows(game_id: int, rows: list[dict]) -> None:
    cols = ("game_id", "wc_team_id", "player_id", "pa", "ab", "runs", "hits",
            "doubles", "triples", "hr", "rbi", "bb", "k", "stays",
            "outs_recorded")
    sql = (f"INSERT INTO game_wc_batter_stats ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' for _ in cols)})")
    for r in rows:
        db.execute(sql, tuple([game_id] + [r.get(c, 0) for c in cols[1:]]))


def _insert_pitcher_rows(game_id: int, rows: list[dict]) -> None:
    cols = ("game_id", "wc_team_id", "player_id", "is_starter",
            "batters_faced", "outs_recorded", "hits_allowed", "runs_allowed",
            "er", "bb", "k", "hr_allowed", "pitches")
    sql = (f"INSERT INTO game_wc_pitcher_stats ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' for _ in cols)})")
    for r in rows:
        db.execute(sql, tuple([game_id] + [r.get(c, 0) for c in cols[1:]]))


# ---------------------------------------------------------------------------
# Game sim
# ---------------------------------------------------------------------------

def _heuristic_score(home_strength: float, away_strength: float,
                     rng: random.Random) -> tuple[int, int]:
    """Fallback when the engine fails (degenerate roster). Mirrors
    youth._simulate_youth_game_result."""
    import math
    gap = home_strength - away_strength
    home_win_prob = 1.0 / (1.0 + math.exp(-gap / 12.0))
    expected_total = max(4, round((home_strength + away_strength) / 14.0))
    total = max(2, round(rng.gauss(expected_total, 2.5)))
    favorite_share = max(0.5, min(0.85, 0.5 + (home_win_prob - 0.5) * 0.7))
    home_share = favorite_share if home_win_prob >= 0.5 else (1 - favorite_share)
    hs = max(1, round(total * home_share))
    as_ = max(0, total - hs)
    if hs == as_:
        if rng.random() < home_win_prob:
            hs += 1
        else:
            as_ += 1
    return hs, as_


def simulate_wc_game(game_id: int, seed: int | None = None) -> dict:
    """Run the real O27 engine for one WC game. Persists score + stats."""
    init_wc_schema()
    game = db.fetchone("SELECT * FROM wc_games WHERE id = ?", (game_id,))
    if game is None:
        raise ValueError(f"WC game {game_id} not found")
    if game["played"]:
        return {"game_id": game_id, "skipped": "already played"}

    season = int(game["season"])
    if seed is None:
        seed = int(game["seed"] or random.randint(1, 2**31 - 1))
    rng = random.Random(seed)

    try:
        visitors_team, away_players, away_starter_pid = _build_wc_engine_team(
            int(game["away_wc_team_id"]), "visitors", season, rng,
        )
        home_team, home_players, home_starter_pid = _build_wc_engine_team(
            int(game["home_wc_team_id"]), "home", season, rng,
        )

        state = GameState(visitors=visitors_team, home=home_team)
        provider = ProbabilisticProvider(rng)
        renderer = Renderer()
        final_state, _ = run_game(state, provider, renderer)

        home_score = int(final_state.score.get("home", 0))
        away_score = int(final_state.score.get("visitors", 0))
    except Exception:
        # Heuristic fallback so the bracket can still progress.
        h_team = db.fetchone("SELECT pool_strength FROM wc_teams WHERE id = ?",
                             (int(game["home_wc_team_id"]),))
        a_team = db.fetchone("SELECT pool_strength FROM wc_teams WHERE id = ?",
                             (int(game["away_wc_team_id"]),))
        h_str = float((h_team or {}).get("pool_strength") or 50)
        a_str = float((a_team or {}).get("pool_strength") or 50)
        home_score, away_score = _heuristic_score(h_str, a_str, rng)
        winner = (int(game["home_wc_team_id"]) if home_score > away_score
                  else int(game["away_wc_team_id"]))
        db.execute(
            "UPDATE wc_games SET home_score = ?, away_score = ?, "
            "winner_wc_team_id = ?, played = 1 WHERE id = ?",
            (home_score, away_score, winner, game_id),
        )
        return {
            "game_id": game_id, "home_score": home_score,
            "away_score": away_score, "winner": winner, "fallback": True,
        }

    winner = (int(game["home_wc_team_id"]) if home_score > away_score
              else int(game["away_wc_team_id"]))
    db.execute(
        "UPDATE wc_games SET home_score = ?, away_score = ?, "
        "winner_wc_team_id = ?, played = 1 WHERE id = ?",
        (home_score, away_score, winner, game_id),
    )
    away_brows = _extract_batter_rows(renderer, int(game["away_wc_team_id"]), away_players)
    home_brows = _extract_batter_rows(renderer, int(game["home_wc_team_id"]), home_players)
    away_prows = _extract_pitcher_rows(final_state, int(game["away_wc_team_id"]),
                                       away_players, away_starter_pid)
    home_prows = _extract_pitcher_rows(final_state, int(game["home_wc_team_id"]),
                                       home_players, home_starter_pid)
    _insert_batter_rows(game_id, away_brows + home_brows)
    _insert_pitcher_rows(game_id, away_prows + home_prows)

    return {
        "game_id": game_id, "home_score": home_score,
        "away_score": away_score, "winner": winner,
        "super_inning": getattr(final_state, "super_inning_number", 0),
    }


# ---------------------------------------------------------------------------
# Season number helper
# ---------------------------------------------------------------------------

def _current_season() -> int:
    row = db.fetchone(
        "SELECT value FROM sim_meta WHERE key = 'season_number'"
    )
    if row and row.get("value"):
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            pass
    return 1


# ---------------------------------------------------------------------------
# Phase 1: Qualifying
# ---------------------------------------------------------------------------

def initialize_qualifying(season: int | None = None,
                          rng_seed: int = 0) -> dict:
    """Build wc_teams for every eligible nation this season, split them
    into regional pots, schedule a single round-robin per region.
    Idempotent: if qualifying for the season already exists, returns the
    current state."""
    init_wc_schema()
    if season is None:
        season = _current_season()

    existing = db.fetchone(
        "SELECT COUNT(*) AS n FROM wc_teams WHERE season = ?",
        (season,),
    )
    if existing and existing["n"]:
        return {"season": season, "already_initialized": True}

    rng = random.Random((rng_seed or 0) ^ 0xC07_57)

    # Build country pools + filter to eligible.
    eligible = _eligible_country_codes()
    pools: list[tuple[str, list[dict], float, str]] = []
    for cc in eligible:
        pool = _country_pool(cc, include_secondary=False)
        if len(pool) < 14:  # safety net beyond the SQL filter
            continue
        strength = _pool_strength(pool)
        region = _country_wc_region(cc)
        pools.append((cc, pool, strength, region))

    # Group by region, cap each at WC_MAX_PER_REGION (top by strength).
    by_region: dict[str, list[tuple[str, float]]] = {r: [] for r in WC_REGION_ORDER}
    for cc, _pool, strength, region in pools:
        by_region.setdefault(region, []).append((cc, strength))
    for region in by_region:
        by_region[region].sort(key=lambda kv: -kv[1])
        if len(by_region[region]) > WC_MAX_PER_REGION:
            by_region[region] = by_region[region][:WC_MAX_PER_REGION]

    # Persist wc_teams + the qualifying group (one per region = single
    # round-robin within the region).
    inserted_teams = 0
    inserted_games = 0
    for region in WC_REGION_ORDER:
        members = by_region.get(region, [])
        if len(members) < 2:
            # Region too small to run qualifying — auto-qualify everyone
            # in it up to the regional quota, no games played.
            for cc, strength in members:
                name, abbrev = _display_for(cc)
                db.execute(
                    "INSERT INTO wc_teams (season, country_code, name, abbrev, "
                    " region, pool_strength, qualified) VALUES (?, ?, ?, ?, ?, ?, 1)",
                    (season, cc, name, abbrev, region, strength),
                )
                inserted_teams += 1
            continue

        gid = db.execute(
            "INSERT INTO wc_groups (season, stage, letter) VALUES (?, ?, ?)",
            (season, f"qual:{region}", "Q"),
        )
        team_ids: list[int] = []
        for cc, strength in members:
            name, abbrev = _display_for(cc)
            tid = db.execute(
                "INSERT INTO wc_teams (season, country_code, name, abbrev, "
                " region, pool_strength) VALUES (?, ?, ?, ?, ?, ?)",
                (season, cc, name, abbrev, region, strength),
            )
            inserted_teams += 1
            team_ids.append(tid)
            db.execute(
                "INSERT INTO wc_group_membership (group_id, wc_team_id) VALUES (?, ?)",
                (gid, tid),
            )

        # Schedule round-robin: each pair plays once, random home/away.
        for i in range(len(team_ids)):
            for j in range(i + 1, len(team_ids)):
                a, b = team_ids[i], team_ids[j]
                home, away = (a, b) if rng.random() < 0.5 else (b, a)
                db.execute(
                    "INSERT INTO wc_games (season, phase, group_id, "
                    " home_wc_team_id, away_wc_team_id, seed) "
                    "VALUES (?, 'qual', ?, ?, ?, ?)",
                    (season, gid, home, away, rng.randint(1, 2**31 - 1)),
                )
                inserted_games += 1

    db.execute(
        "INSERT OR REPLACE INTO wc_meta (season, phase, rosters_locked) "
        "VALUES (?, 'qualifying', 0)",
        (season,),
    )
    return {
        "season":         season,
        "nations":        inserted_teams,
        "qualifying_games": inserted_games,
    }


def simulate_qualifying(season: int | None = None,
                        rng_seed: int = 0) -> int:
    """Run every unplayed qualifying game. Returns count played."""
    init_wc_schema()
    if season is None:
        season = _current_season()
    rows = db.fetchall(
        "SELECT id, seed FROM wc_games "
        "WHERE season = ? AND phase = 'qual' AND played = 0 "
        "ORDER BY id",
        (season,),
    )
    rng = random.Random((rng_seed or 0) ^ 0xC07_58)
    n = 0
    for r in rows:
        seed = r["seed"] or rng.randint(1, 2**31 - 1)
        simulate_wc_game(r["id"], seed=seed)
        n += 1
    return n


def _regional_qualifying_table(season: int) -> dict[str, list[dict]]:
    """W-L table for each region's qualifying group. Returns
    {region: [rows sorted by W desc / RD / RS]}."""
    rows = db.fetchall(
        "SELECT wt.id AS team_id, wt.country_code, wt.name, wt.abbrev, "
        "       wt.region, wt.pool_strength, wt.qualified, "
        "       SUM(CASE WHEN wg.winner_wc_team_id = wt.id THEN 1 ELSE 0 END) AS w, "
        "       SUM(CASE WHEN wg.played = 1 AND wg.winner_wc_team_id IS NOT NULL "
        "                AND wg.winner_wc_team_id != wt.id THEN 1 ELSE 0 END) AS l, "
        "       SUM(CASE WHEN wg.home_wc_team_id = wt.id THEN wg.home_score "
        "                WHEN wg.away_wc_team_id = wt.id THEN wg.away_score "
        "                ELSE 0 END) AS rs, "
        "       SUM(CASE WHEN wg.home_wc_team_id = wt.id THEN wg.away_score "
        "                WHEN wg.away_wc_team_id = wt.id THEN wg.home_score "
        "                ELSE 0 END) AS ra "
        "FROM wc_teams wt "
        "LEFT JOIN wc_games wg "
        "  ON wg.season = wt.season AND wg.phase = 'qual' AND wg.played = 1 "
        " AND (wg.home_wc_team_id = wt.id OR wg.away_wc_team_id = wt.id) "
        "WHERE wt.season = ? "
        "GROUP BY wt.id "
        "ORDER BY wt.region, w DESC, (rs - ra) DESC, rs DESC, wt.id",
        (season,),
    )
    out: dict[str, list[dict]] = {r: [] for r in WC_REGION_ORDER}
    for r in rows:
        out.setdefault(r["region"], []).append({
            "team_id":      r["team_id"],
            "country_code": r["country_code"],
            "name":         r["name"],
            "abbrev":       r["abbrev"],
            "region":       r["region"],
            "pool_strength": r["pool_strength"],
            "qualified":    bool(r["qualified"]),
            "w":            r["w"] or 0,
            "l":            r["l"] or 0,
            "rs":           r["rs"] or 0,
            "ra":           r["ra"] or 0,
        })
    return out


def lock_qualifiers(season: int | None = None) -> dict:
    """After qualifying completes, mark the top-N per region as
    `qualified=1`. Quotas come from WC_REGIONAL_QUOTAS, with leftover
    slots cascading to the next-best across all regions to keep the
    field at exactly 24."""
    init_wc_schema()
    if season is None:
        season = _current_season()

    table = _regional_qualifying_table(season)
    target = 24
    qualified_ids: list[int] = []

    # First pass: take the quota from each region.
    overflow_pool: list[dict] = []   # next-best after quota — used to fill
    for region in WC_REGION_ORDER:
        rows = [r for r in table.get(region, []) if not r["qualified"]]
        # Pre-qualified rows (small regions auto-advanced at init):
        for r in table.get(region, []):
            if r["qualified"]:
                qualified_ids.append(r["team_id"])
        quota = WC_REGIONAL_QUOTAS.get(region, 0)
        take = min(quota, len(rows))
        for r in rows[:take]:
            qualified_ids.append(r["team_id"])
        # Remainder feeds the overflow pool with a strength tiebreak.
        for r in rows[take:]:
            overflow_pool.append(r)

    # Second pass: fill any remaining slot with the best overflow nations
    # (W desc, then pool_strength desc).
    if len(qualified_ids) < target and overflow_pool:
        overflow_pool.sort(key=lambda r: (-r["w"], -(r.get("pool_strength") or 0)))
        for r in overflow_pool:
            if len(qualified_ids) >= target:
                break
            qualified_ids.append(r["team_id"])

    # Truncate (defensive — never have more than 24 qualifiers).
    qualified_ids = qualified_ids[:target]
    if qualified_ids:
        placeholders = ",".join("?" for _ in qualified_ids)
        db.execute(
            f"UPDATE wc_teams SET qualified = 1 WHERE id IN ({placeholders})",
            tuple(qualified_ids),
        )

    db.execute(
        "INSERT OR REPLACE INTO wc_meta (season, phase, rosters_locked) "
        "VALUES (?, 'rosters', 0)",
        (season,),
    )
    return {
        "season":          season,
        "qualified_count": len(qualified_ids),
        "qualified_ids":   qualified_ids,
    }


# ---------------------------------------------------------------------------
# Phase 2: Rosters
# ---------------------------------------------------------------------------

def auto_pick_rosters(season: int | None = None,
                      overwrite: bool = False) -> dict:
    """Roll a 22-man auto-roster for every qualified nation that doesn't
    have one yet. `overwrite=True` clears existing rosters first."""
    init_wc_schema()
    if season is None:
        season = _current_season()
    teams = db.fetchall(
        "SELECT * FROM wc_teams WHERE season = ? AND qualified = 1 "
        "ORDER BY region, country_code",
        (season,),
    )
    n_teams = 0
    n_players = 0
    for t in teams:
        if overwrite:
            db.execute("DELETE FROM wc_team_players WHERE wc_team_id = ?", (t["id"],))
        has_roster = db.fetchone(
            "SELECT COUNT(*) AS n FROM wc_team_players WHERE wc_team_id = ?",
            (t["id"],),
        )
        if has_roster and has_roster["n"]:
            continue
        pool = _country_pool(t["country_code"], include_secondary=False)
        if not pool:
            continue
        picks = _pick_auto_roster(pool)
        for pid in picks:
            try:
                db.execute(
                    "INSERT INTO wc_team_players (wc_team_id, player_id) "
                    "VALUES (?, ?)",
                    (t["id"], pid),
                )
                n_players += 1
            except Exception:
                pass
        n_teams += 1
    return {"season": season, "teams_filled": n_teams, "players_added": n_players}


def set_roster(wc_team_id: int, player_ids: list[int]) -> dict:
    """Replace a national team's roster with the supplied player IDs.
    Validates: every ID belongs to the country's pool (primary or
    secondary), the team is qualified, and the roster hasn't been
    locked. Also clears any other team's claim on these players so
    a dual-national doesn't appear on two squads at once."""
    init_wc_schema()
    team = db.fetchone("SELECT * FROM wc_teams WHERE id = ?", (wc_team_id,))
    if not team:
        raise ValueError(f"WC team {wc_team_id} not found")

    meta = db.fetchone("SELECT * FROM wc_meta WHERE season = ?", (team["season"],))
    if meta and meta["rosters_locked"]:
        raise ValueError("Rosters are locked for this season's tournament.")

    pool = _country_pool(team["country_code"], include_secondary=True)
    pool_ids = {int(p["id"]) for p in pool}
    invalid = [pid for pid in player_ids if int(pid) not in pool_ids]
    if invalid:
        raise ValueError(
            f"Players {invalid} are not eligible for {team['country_code']}."
        )

    # Other-team claims for this season (so a dual-national gets reassigned).
    if player_ids:
        placeholders = ",".join("?" for _ in player_ids)
        db.execute(
            f"DELETE FROM wc_team_players "
            f"WHERE player_id IN ({placeholders}) "
            f"  AND wc_team_id IN (SELECT id FROM wc_teams WHERE season = ?)",
            tuple(player_ids) + (team["season"],),
        )

    db.execute("DELETE FROM wc_team_players WHERE wc_team_id = ?", (wc_team_id,))
    for pid in player_ids:
        db.execute(
            "INSERT INTO wc_team_players (wc_team_id, player_id) VALUES (?, ?)",
            (wc_team_id, int(pid)),
        )
    return {"wc_team_id": wc_team_id, "size": len(player_ids)}


def get_eligible_for_team(wc_team_id: int) -> list[dict]:
    """List of pros eligible for this national team — primary nationals
    first, then secondary (heritage) call-ups. Each row carries the
    composite score and a flag for whether they're a heritage pick."""
    team = db.fetchone("SELECT * FROM wc_teams WHERE id = ?", (wc_team_id,))
    if not team:
        return []
    cc = team["country_code"].upper()
    rows = db.fetchall(
        "SELECT p.*, t.abbrev AS club_abbrev, t.name AS club_name "
        "FROM players p LEFT JOIN teams t ON t.id = p.team_id "
        "WHERE p.is_active = 1 AND ("
        "  UPPER(p.country) = ? OR UPPER(p.secondary_country) = ?)",
        (cc, cc),
    )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        d["composite"] = round(_player_composite(d), 1)
        d["is_heritage"] = (str(d.get("country") or "").upper() != cc
                            and str(d.get("secondary_country") or "").upper() == cc)
        out.append(d)
    # Best first within group; primaries before heritage picks.
    out.sort(key=lambda r: (r["is_heritage"], -r["composite"]))
    return out


def get_roster(wc_team_id: int) -> list[dict]:
    rows = db.fetchall(
        "SELECT wtp.player_id, wtp.roster_slot, p.name, p.position, p.is_pitcher, "
        "       p.country, p.secondary_country, p.skill, p.contact, p.power, p.eye, "
        "       p.speed, p.defense, p.pitcher_skill, p.command, p.movement, "
        "       p.stamina, p.archetype, p.is_joker, p.roster_slot AS pro_slot, "
        "       t.abbrev AS club_abbrev "
        "FROM wc_team_players wtp "
        "JOIN players p ON p.id = wtp.player_id "
        "LEFT JOIN teams t ON t.id = p.team_id "
        "WHERE wtp.wc_team_id = ? "
        "ORDER BY wtp.id",
        (wc_team_id,),
    )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        d["composite"] = round(_player_composite(d), 1)
        out.append(d)
    return out


def lock_rosters(season: int | None = None) -> None:
    init_wc_schema()
    if season is None:
        season = _current_season()
    db.execute(
        "INSERT OR REPLACE INTO wc_meta (season, phase, rosters_locked) "
        "VALUES (?, 'tournament', 1)",
        (season,),
    )


# ---------------------------------------------------------------------------
# Phase 3: Main tournament (4 groups of 6 → QF/SF/Final)
# ---------------------------------------------------------------------------

MAIN_GROUP_LETTERS = ["A", "B", "C", "D"]
MAIN_TEAMS_PER_GROUP = 6


def _seed_main_groups(season: int, rng: random.Random) -> list[dict]:
    """Snake-draw 24 qualified nations into 4 groups of 6 using a
    pot-based seeding: pots are formed by pool_strength, then teams from
    the same pot get distributed across all groups so no group stacks
    the elite tier."""
    qualified = db.fetchall(
        "SELECT * FROM wc_teams WHERE season = ? AND qualified = 1 "
        "ORDER BY pool_strength DESC",
        (season,),
    )
    qualified = [dict(t) for t in qualified]
    if len(qualified) < 24:
        # Fill from the next-best non-qualifiers to reach 24.
        backups = db.fetchall(
            "SELECT * FROM wc_teams WHERE season = ? AND qualified = 0 "
            "ORDER BY pool_strength DESC LIMIT ?",
            (season, 24 - len(qualified)),
        )
        for b in backups:
            d = dict(b)
            db.execute("UPDATE wc_teams SET qualified = 1 WHERE id = ?", (d["id"],))
            d["qualified"] = 1
            qualified.append(d)
    qualified = qualified[:24]

    pot_size = 6
    pots: list[list[dict]] = [qualified[i:i + pot_size] for i in range(0, 24, pot_size)]
    # Shuffle WITHIN each pot so the elite group isn't always
    # geographically deterministic.
    for pot in pots:
        rng.shuffle(pot)

    # Rotating snake assignment. With 6-team pots and 4 groups, the
    # straight i%4 mapping would always stack the "extra 2" from each
    # pot onto groups A/B — biasing those groups stronger. Rotating the
    # start index by `pot_size` between pots spreads the overflow evenly
    # across all four groups over the four pots.
    groups: list[list[dict]] = [[] for _ in MAIN_GROUP_LETTERS]
    start = 0
    for pot in pots:
        for i, team in enumerate(pot):
            groups[(start + i) % len(groups)].append(team)
        start = (start + len(pot)) % len(groups)

    out: list[dict] = []
    for letter, members in zip(MAIN_GROUP_LETTERS, groups):
        gid = db.execute(
            "INSERT INTO wc_groups (season, stage, letter) VALUES (?, 'main', ?)",
            (season, letter),
        )
        for t in members:
            db.execute(
                "INSERT INTO wc_group_membership (group_id, wc_team_id) "
                "VALUES (?, ?)",
                (gid, t["id"]),
            )
        out.append({
            "group_id":     gid,
            "group_letter": letter,
            "team_ids":     [t["id"] for t in members],
        })
    return out


def _schedule_main_group_games(season: int, groups: list[dict],
                               rng: random.Random) -> int:
    n = 0
    for grp in groups:
        ids = list(grp["team_ids"])
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                home, away = (a, b) if rng.random() < 0.5 else (b, a)
                db.execute(
                    "INSERT INTO wc_games (season, phase, group_id, "
                    " home_wc_team_id, away_wc_team_id, seed) "
                    "VALUES (?, 'group', ?, ?, ?, ?)",
                    (season, grp["group_id"], home, away,
                     rng.randint(1, 2**31 - 1)),
                )
                n += 1
    return n


def _main_group_standings(season: int) -> dict[int, list[dict]]:
    rows = db.fetchall(
        "SELECT g.group_id, g.group_letter, g.wc_team_id AS team_id, "
        "       SUM(g.w) AS w, SUM(g.l) AS l, "
        "       SUM(g.rs) AS rs, SUM(g.ra) AS ra "
        "FROM ("
        "  SELECT m.group_id, gr.letter AS group_letter, m.wc_team_id, "
        "         CASE WHEN wg.winner_wc_team_id = m.wc_team_id THEN 1 ELSE 0 END AS w, "
        "         CASE WHEN wg.winner_wc_team_id IS NOT NULL "
        "              AND wg.winner_wc_team_id != m.wc_team_id THEN 1 ELSE 0 END AS l, "
        "         CASE WHEN wg.home_wc_team_id = m.wc_team_id THEN wg.home_score "
        "              WHEN wg.away_wc_team_id = m.wc_team_id THEN wg.away_score "
        "              ELSE 0 END AS rs, "
        "         CASE WHEN wg.home_wc_team_id = m.wc_team_id THEN wg.away_score "
        "              WHEN wg.away_wc_team_id = m.wc_team_id THEN wg.home_score "
        "              ELSE 0 END AS ra "
        "  FROM wc_group_membership m "
        "  JOIN wc_groups gr ON gr.id = m.group_id "
        "  LEFT JOIN wc_games wg "
        "    ON wg.season = gr.season AND wg.phase = 'group' AND wg.played = 1 "
        "   AND (wg.home_wc_team_id = m.wc_team_id OR wg.away_wc_team_id = m.wc_team_id) "
        "  WHERE gr.season = ? AND gr.stage = 'main'"
        ") g "
        "GROUP BY g.group_id, g.group_letter, g.wc_team_id "
        "ORDER BY g.group_letter, SUM(g.w) DESC, "
        "         SUM(g.rs) - SUM(g.ra) DESC, SUM(g.rs) DESC, g.wc_team_id",
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


def _build_knockout(season: int, rng: random.Random) -> int:
    standings = _main_group_standings(season)
    by_letter: dict[str, list[dict]] = {}
    for rows in standings.values():
        if rows:
            by_letter[rows[0]["group_letter"]] = rows

    # QF pairings: A1/B2, B1/A2, C1/D2, D1/C2 — adjacent-group cross.
    pairs: list[tuple[int, int]] = []
    for i in range(0, len(MAIN_GROUP_LETTERS), 2):
        a = by_letter.get(MAIN_GROUP_LETTERS[i], [])
        b = by_letter.get(MAIN_GROUP_LETTERS[i + 1], [])
        if len(a) >= 2 and len(b) >= 2:
            pairs.append((a[0]["team_id"], b[1]["team_id"]))
            pairs.append((b[0]["team_id"], a[1]["team_id"]))
    n = 0
    for slot, (home, away) in enumerate(pairs):
        db.execute(
            "INSERT INTO wc_games (season, phase, bracket_slot, "
            " home_wc_team_id, away_wc_team_id, seed) "
            "VALUES (?, 'qf', ?, ?, ?, ?)",
            (season, slot, home, away, rng.randint(1, 2**31 - 1)),
        )
        n += 1
    return n


def _advance_knockout(season: int, from_round: str, to_round: str,
                      rng: random.Random) -> int:
    rows = db.fetchall(
        "SELECT bracket_slot, winner_wc_team_id FROM wc_games "
        "WHERE season = ? AND phase = ? AND played = 1 "
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
        a = rows[i]["winner_wc_team_id"]
        b = rows[i + 1]["winner_wc_team_id"]
        if not a or not b:
            continue
        home, away = (a, b) if rng.random() < 0.5 else (b, a)
        db.execute(
            "INSERT INTO wc_games (season, phase, bracket_slot, "
            " home_wc_team_id, away_wc_team_id, seed) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (season, to_round, new_slot, home, away,
             rng.randint(1, 2**31 - 1)),
        )
        n += 1
        new_slot += 1
    return n


def _simulate_main_round(season: int, phase: str,
                         rng: random.Random) -> int:
    rows = db.fetchall(
        "SELECT id, seed FROM wc_games WHERE season = ? AND phase = ? AND played = 0 "
        "ORDER BY id",
        (season, phase),
    )
    n = 0
    for r in rows:
        seed = r["seed"] or rng.randint(1, 2**31 - 1)
        simulate_wc_game(r["id"], seed=seed)
        n += 1
    return n


def run_main_tournament(season: int | None = None,
                        rng_seed: int = 0) -> dict:
    """Seed groups, schedule games, play groups + knockout chain."""
    init_wc_schema()
    if season is None:
        season = _current_season()
    rng = random.Random((rng_seed or 0) ^ 0xC07_5A)

    existing = db.fetchone(
        "SELECT COUNT(*) AS n FROM wc_groups WHERE season = ? AND stage = 'main'",
        (season,),
    )
    if not (existing and existing["n"]):
        groups = _seed_main_groups(season, rng)
        _schedule_main_group_games(season, groups, rng)

    _simulate_main_round(season, "group", rng)

    already_qf = db.fetchone(
        "SELECT COUNT(*) AS n FROM wc_games WHERE season = ? AND phase = 'qf'",
        (season,),
    )
    if not (already_qf and already_qf["n"]):
        _build_knockout(season, rng)
    _simulate_main_round(season, "qf", rng)

    already_sf = db.fetchone(
        "SELECT COUNT(*) AS n FROM wc_games WHERE season = ? AND phase = 'sf'",
        (season,),
    )
    if not (already_sf and already_sf["n"]):
        _advance_knockout(season, "qf", "sf", rng)
    _simulate_main_round(season, "sf", rng)

    already_final = db.fetchone(
        "SELECT COUNT(*) AS n FROM wc_games WHERE season = ? AND phase = 'final'",
        (season,),
    )
    if not (already_final and already_final["n"]):
        _advance_knockout(season, "sf", "final", rng)
    _simulate_main_round(season, "final", rng)

    # Stamp final positions.
    final_game = db.fetchone(
        "SELECT * FROM wc_games WHERE season = ? AND phase = 'final' AND played = 1",
        (season,),
    )
    if final_game and final_game["winner_wc_team_id"]:
        champ = final_game["winner_wc_team_id"]
        runner = (final_game["home_wc_team_id"]
                  if final_game["away_wc_team_id"] == champ
                  else final_game["away_wc_team_id"])
        db.execute("UPDATE wc_teams SET final_position = 'champion' WHERE id = ?",
                   (champ,))
        db.execute("UPDATE wc_teams SET final_position = 'runner_up' WHERE id = ?",
                   (runner,))

    db.execute(
        "INSERT OR REPLACE INTO wc_meta (season, phase, rosters_locked) "
        "VALUES (?, 'complete', 1)",
        (season,),
    )
    return summarise(season)


# ---------------------------------------------------------------------------
# Auto pipeline (the season-rollover hook)
# ---------------------------------------------------------------------------

def run_full_world_cup(season: int | None = None,
                       rng_seed: int = 0) -> dict:
    """End-to-end auto-run: qualifying → auto rosters → main tournament.
    Idempotent at each stage — safe to call after a partial mid-season
    manual run. Returns the final summary."""
    init_wc_schema()
    if season is None:
        season = _current_season()
    initialize_qualifying(season=season, rng_seed=rng_seed)
    simulate_qualifying(season=season, rng_seed=rng_seed)
    lock_qualifiers(season=season)
    auto_pick_rosters(season=season, overwrite=False)
    lock_rosters(season=season)
    return run_main_tournament(season=season, rng_seed=rng_seed)


# ---------------------------------------------------------------------------
# Read-side: summary for the UI
# ---------------------------------------------------------------------------

def summarise(season: int | None = None) -> dict[str, Any] | None:
    init_wc_schema()
    if season is None:
        season = _current_season()
    meta = db.fetchone("SELECT * FROM wc_meta WHERE season = ?", (season,))
    if not meta:
        return None

    qual_table = _regional_qualifying_table(season)
    main_standings = _main_group_standings(season)

    teams = {t["id"]: dict(t) for t in db.fetchall(
        "SELECT * FROM wc_teams WHERE season = ?", (season,)
    )}

    games = db.fetchall(
        "SELECT wg.*, ht.abbrev AS home_abbrev, ht.name AS home_name, "
        "       ht.country_code AS home_country, "
        "       at.abbrev AS away_abbrev, at.name AS away_name, "
        "       at.country_code AS away_country "
        "FROM wc_games wg "
        "LEFT JOIN wc_teams ht ON ht.id = wg.home_wc_team_id "
        "LEFT JOIN wc_teams at ON at.id = wg.away_wc_team_id "
        "WHERE wg.season = ? "
        "ORDER BY CASE wg.phase "
        "         WHEN 'qual'  THEN 0 "
        "         WHEN 'group' THEN 1 "
        "         WHEN 'qf'    THEN 2 "
        "         WHEN 'sf'    THEN 3 "
        "         WHEN 'final' THEN 4 ELSE 5 END, "
        "         wg.id",
        (season,),
    )
    by_phase: dict[str, list[dict]] = {}
    for g in games:
        by_phase.setdefault(g["phase"], []).append(dict(g))

    enriched_main_groups: list[dict] = []
    for gid, rows in sorted(main_standings.items(),
                            key=lambda kv: kv[1][0]["group_letter"] if kv[1] else ""):
        enriched_rows = []
        for r in rows:
            t = teams.get(r["team_id"], {})
            enriched_rows.append({
                **r,
                "abbrev":       t.get("abbrev", ""),
                "name":         t.get("name", ""),
                "country_code": t.get("country_code", ""),
            })
        if enriched_rows:
            enriched_main_groups.append({
                "group_id":     gid,
                "group_letter": enriched_rows[0]["group_letter"],
                "rows":         enriched_rows,
            })

    # Enrich the regional qualifying table with the quota line that marks
    # the cut-off between qualified and eliminated.
    qual_blocks: list[dict] = []
    for region in WC_REGION_ORDER:
        rows = qual_table.get(region, [])
        if not rows:
            continue
        qual_blocks.append({
            "region":   region,
            "quota":    WC_REGIONAL_QUOTAS.get(region, 0),
            "rows":     rows,
        })

    qualified_teams = [t for t in teams.values() if t.get("qualified")]
    final_game = (by_phase.get("final") or [None])[0]
    champion = None
    if final_game and final_game.get("winner_wc_team_id"):
        champion = teams.get(final_game["winner_wc_team_id"])

    return {
        "season":            season,
        "phase":             meta["phase"],
        "rosters_locked":    bool(meta["rosters_locked"]),
        "qualifying_groups": qual_blocks,
        "main_groups":       enriched_main_groups,
        "by_phase":          by_phase,
        "qualified_count":   len(qualified_teams),
        "qualified_teams":   sorted(qualified_teams,
                                    key=lambda t: (t["region"], t["name"])),
        "champion":          champion,
        "complete":          meta["phase"] == "complete",
    }


def get_team(wc_team_id: int) -> dict | None:
    init_wc_schema()
    t = db.fetchone("SELECT * FROM wc_teams WHERE id = ?", (wc_team_id,))
    return dict(t) if t else None


def reset_world_cup(season: int | None = None) -> int:
    """Wipe a season's WC so it can be re-run from scratch."""
    init_wc_schema()
    if season is None:
        season = _current_season()
    n = db.fetchone(
        "SELECT COUNT(*) AS n FROM wc_games WHERE season = ?", (season,)
    )["n"]
    for child in ("game_wc_batter_stats", "game_wc_pitcher_stats"):
        db.execute(
            f"DELETE FROM {child} "
            f"WHERE game_id IN (SELECT id FROM wc_games WHERE season = ?)",
            (season,),
        )
    db.execute(
        "DELETE FROM wc_group_membership "
        "WHERE group_id IN (SELECT id FROM wc_groups WHERE season = ?)",
        (season,),
    )
    db.execute(
        "DELETE FROM wc_team_players "
        "WHERE wc_team_id IN (SELECT id FROM wc_teams WHERE season = ?)",
        (season,),
    )
    db.execute("DELETE FROM wc_games WHERE season = ?", (season,))
    db.execute("DELETE FROM wc_groups WHERE season = ?", (season,))
    db.execute("DELETE FROM wc_teams WHERE season = ?", (season,))
    db.execute("DELETE FROM wc_meta WHERE season = ?", (season,))
    return n


# ---------------------------------------------------------------------------
# Box-score read helper (for the per-game route)
# ---------------------------------------------------------------------------

def get_box_score(game_id: int) -> dict | None:
    init_wc_schema()
    game = db.fetchone(
        "SELECT wg.*, "
        "       ht.name AS home_name, ht.abbrev AS home_abbrev, "
        "       at.name AS away_name, at.abbrev AS away_abbrev "
        "FROM wc_games wg "
        "LEFT JOIN wc_teams ht ON ht.id = wg.home_wc_team_id "
        "LEFT JOIN wc_teams at ON at.id = wg.away_wc_team_id "
        "WHERE wg.id = ?",
        (game_id,),
    )
    if not game:
        return None
    batters = db.fetchall(
        "SELECT b.*, p.name AS player_name, p.position, p.is_pitcher "
        "FROM game_wc_batter_stats b "
        "JOIN players p ON p.id = b.player_id "
        "WHERE b.game_id = ? "
        "ORDER BY b.wc_team_id, p.is_pitcher, p.position",
        (game_id,),
    )
    pitchers = db.fetchall(
        "SELECT pi.*, p.name AS player_name "
        "FROM game_wc_pitcher_stats pi "
        "JOIN players p ON p.id = pi.player_id "
        "WHERE pi.game_id = ? "
        "ORDER BY pi.wc_team_id, pi.is_starter DESC, pi.outs_recorded DESC",
        (game_id,),
    )
    return {
        "game":     dict(game),
        "batters":  [dict(r) for r in batters],
        "pitchers": [dict(r) for r in pitchers],
    }
