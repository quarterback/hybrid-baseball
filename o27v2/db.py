"""
SQLite database layer for O27v2.

All persistence lives in o27v2/o27v2.db (relative to workspace root).
Functions return plain dicts / lists so callers never deal with cursors.
"""
from __future__ import annotations
import os
import sqlite3
from typing import Any

_DB_PATH = os.environ.get(
    "O27V2_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "o27v2.db"),
)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    abbrev    TEXT NOT NULL,
    city      TEXT NOT NULL,
    division  TEXT NOT NULL,
    league    TEXT NOT NULL,
    wins      INTEGER DEFAULT 0,
    losses    INTEGER DEFAULT 0,
    park_hr   REAL DEFAULT 1.0,
    park_hits REAL DEFAULT 1.0,
    -- Manager (re-rolled per league seed; not hard-wired to franchise).
    -- See o27v2/managers.py for archetype catalogue and tendency semantics.
    manager_archetype        TEXT  DEFAULT '',
    mgr_quick_hook           REAL  DEFAULT 0.5,
    mgr_bullpen_aggression   REAL  DEFAULT 0.5,
    mgr_leverage_aware       REAL  DEFAULT 0.5,
    mgr_joker_aggression     REAL  DEFAULT 0.5,
    mgr_pinch_hit_aggression REAL  DEFAULT 0.5,
    mgr_platoon_aggression   REAL  DEFAULT 0.5,
    mgr_run_game             REAL  DEFAULT 0.5,
    mgr_bench_usage          REAL  DEFAULT 0.5
);

CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id       INTEGER NOT NULL REFERENCES teams(id),
    name          TEXT NOT NULL,
    position      TEXT NOT NULL,
    is_pitcher    INTEGER DEFAULT 0,
    is_joker      INTEGER DEFAULT 0,
    skill         INTEGER DEFAULT 50,
    speed         INTEGER DEFAULT 50,
    pitcher_skill INTEGER DEFAULT 50,
    stay_aggressiveness REAL DEFAULT 0.4,
    contact_quality_threshold REAL DEFAULT 0.45,
    archetype             TEXT DEFAULT '',
    pitcher_role          TEXT DEFAULT '',
    hard_contact_delta    REAL DEFAULT 0.0,
    hr_weight_bonus       REAL DEFAULT 0.0,
    age                   INTEGER DEFAULT 27,
    injured_until         TEXT DEFAULT NULL,
    il_tier               TEXT DEFAULT NULL,
    stamina               INTEGER DEFAULT 50,
    is_active             INTEGER DEFAULT 1,
    -- Realism layer (multi-dimensional 20-80 ratings + handedness).
    contact   INTEGER DEFAULT 50,
    power     INTEGER DEFAULT 50,
    eye       INTEGER DEFAULT 50,
    command   INTEGER DEFAULT 50,
    movement  INTEGER DEFAULT 50,
    bats      TEXT DEFAULT 'R',
    throws    TEXT DEFAULT 'R',
    -- Defense layer (range / glove / arm + per-position-group sub-ratings).
    defense           INTEGER DEFAULT 50,
    arm               INTEGER DEFAULT 50,
    defense_infield   INTEGER DEFAULT 50,
    defense_outfield  INTEGER DEFAULT 50,
    defense_catcher   INTEGER DEFAULT 50,
    -- Baserunning skill (reads, routes, slides) and aggressiveness
    -- (willingness to risk extra base). Independent of foot speed.
    baserunning         INTEGER DEFAULT 50,
    run_aggressiveness  INTEGER DEFAULT 50
);

CREATE TABLE IF NOT EXISTS games (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    season       INTEGER DEFAULT 1,
    game_date    TEXT NOT NULL,
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    home_score   INTEGER,
    away_score   INTEGER,
    winner_id    INTEGER REFERENCES teams(id),
    super_inning INTEGER DEFAULT 0,
    played       INTEGER DEFAULT 0,
    seed         INTEGER,
    -- Weather model: stamped at schedule time, visible before the game
    -- runs. Engine reads via prob.py modifiers; everything else passes it
    -- through. See o27/engine/weather.py for tier vocabularies.
    temperature_tier TEXT DEFAULT 'mild',
    wind_tier        TEXT DEFAULT 'neutral',
    humidity_tier    TEXT DEFAULT 'normal',
    precip_tier      TEXT DEFAULT 'none',
    cloud_tier       TEXT DEFAULT 'clear'
);

CREATE TABLE IF NOT EXISTS game_batter_stats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    INTEGER NOT NULL REFERENCES games(id),
    team_id    INTEGER NOT NULL REFERENCES teams(id),
    player_id  INTEGER NOT NULL REFERENCES players(id),
    phase      INTEGER NOT NULL DEFAULT 0,
    pa         INTEGER DEFAULT 0,
    ab         INTEGER DEFAULT 0,
    runs       INTEGER DEFAULT 0,
    hits       INTEGER DEFAULT 0,
    doubles    INTEGER DEFAULT 0,
    triples    INTEGER DEFAULT 0,
    hr         INTEGER DEFAULT 0,
    rbi        INTEGER DEFAULT 0,
    bb         INTEGER DEFAULT 0,
    k          INTEGER DEFAULT 0,
    stays      INTEGER DEFAULT 0,
    outs_recorded INTEGER DEFAULT 0,
    -- Counting stats persisted post-realism layer.
    hbp        INTEGER DEFAULT 0,   -- hit by pitch (NOT a PA-AB; OBP numerator)
    sb         INTEGER DEFAULT 0,   -- successful steals
    cs         INTEGER DEFAULT 0,   -- caught stealing (subset of outs_recorded)
    fo         INTEGER DEFAULT 0,   -- foul-outs (3-foul rule; subset of outs_recorded)
    multi_hit_abs INTEGER DEFAULT 0,
    stay_rbi   INTEGER DEFAULT 0,
    stay_hits  INTEGER DEFAULT 0,   -- hits credited on a 2C event (subset of hits)
    roe        INTEGER DEFAULT 0,   -- reached on error (NOT a hit; AB credited)
    -- Per-fielder defensive events (the player as a FIELDER, not as a batter).
    po         INTEGER DEFAULT 0,   -- putouts as primary fielder
    e          INTEGER DEFAULT 0,   -- errors committed
    UNIQUE(player_id, game_id, phase)
);

-- Phase 11D — per-PA event log. One row per ball_in_play event; captures
-- the swing index within the AB (so swing-1 vs swing-2+ conversion can be
-- measured), the contact quality, the stay/run choice, and whether a stay
-- was credited. Diagnostic-grade (not surfaced in templates) — used for
-- V2 swing-split conversion verification and Δ-source decomposition.
CREATE TABLE IF NOT EXISTS game_pa_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       INTEGER NOT NULL REFERENCES games(id),
    team_id       INTEGER NOT NULL REFERENCES teams(id),
    batter_id     INTEGER NOT NULL REFERENCES players(id),
    pitcher_id    INTEGER REFERENCES players(id),
    phase         INTEGER NOT NULL DEFAULT 0,    -- 0 = regulation, N>=1 = SI round N
    ab_seq        INTEGER NOT NULL,              -- which AB in the game (per team)
    swing_idx     INTEGER NOT NULL,              -- which contact event in the AB (1, 2, or 3)
    choice        TEXT NOT NULL,                 -- 'run' | 'stay'
    quality       TEXT,                          -- 'weak' | 'medium' | 'hard'
    hit_type      TEXT,                          -- underlying fielding outcome
    was_stay      INTEGER NOT NULL DEFAULT 0,
    stay_credited INTEGER NOT NULL DEFAULT 0,
    runs_scored   INTEGER NOT NULL DEFAULT 0,
    rbi_credited  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pa_log_game ON game_pa_log(game_id);
CREATE INDEX IF NOT EXISTS idx_pa_log_batter ON game_pa_log(batter_id);

CREATE TABLE IF NOT EXISTS game_pitcher_stats (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id        INTEGER NOT NULL REFERENCES games(id),
    team_id        INTEGER NOT NULL REFERENCES teams(id),
    player_id      INTEGER NOT NULL REFERENCES players(id),
    phase          INTEGER NOT NULL DEFAULT 0,
    batters_faced  INTEGER DEFAULT 0,
    outs_recorded  INTEGER DEFAULT 0,
    hits_allowed   INTEGER DEFAULT 0,
    runs_allowed   INTEGER DEFAULT 0,
    er             INTEGER DEFAULT 0,
    bb             INTEGER DEFAULT 0,
    k              INTEGER DEFAULT 0,
    hr_allowed     INTEGER DEFAULT 0,
    pitches        INTEGER DEFAULT 0,
    -- Counting stats persisted post-realism layer.
    hbp_allowed    INTEGER DEFAULT 0,   -- HBP charged against this pitcher
    unearned_runs  INTEGER DEFAULT 0,   -- subset of runs_allowed (passed-ball)
    sb_allowed     INTEGER DEFAULT 0,   -- successful steals while on the mound
    cs_caught      INTEGER DEFAULT 0,   -- runners caught stealing
    fo_induced     INTEGER DEFAULT 0,   -- foul-out outs ending an AB on this pitcher
    -- Arc-bucketed counters (1-9 outs / 10-18 outs / 19-27 outs of the
    -- defending team's 27-out half). Powers wERA / xFIP / Decay; super-
    -- innings outs roll into arc 3.
    er_arc1        INTEGER DEFAULT 0,
    er_arc2        INTEGER DEFAULT 0,
    er_arc3        INTEGER DEFAULT 0,
    k_arc1         INTEGER DEFAULT 0,
    k_arc2         INTEGER DEFAULT 0,
    k_arc3         INTEGER DEFAULT 0,
    fo_arc1        INTEGER DEFAULT 0,
    fo_arc2        INTEGER DEFAULT 0,
    fo_arc3        INTEGER DEFAULT 0,
    bf_arc1        INTEGER DEFAULT 0,
    bf_arc2        INTEGER DEFAULT 0,
    bf_arc3        INTEGER DEFAULT 0,
    is_starter     INTEGER DEFAULT 0,   -- 1 if this pitcher started the game
    UNIQUE(player_id, game_id, phase)
);

-- Task #58: per-team unattributed outs per phase (CS / FC / pickoffs that
-- the engine couldn't charge to a specific batter). Powers the Game Notes
-- section in the box score; replaces the legacy CS/FC patch row entirely.
CREATE TABLE IF NOT EXISTS team_phase_outs (
    game_id           INTEGER NOT NULL REFERENCES games(id),
    team_id           INTEGER NOT NULL REFERENCES teams(id),
    phase             INTEGER NOT NULL DEFAULT 0,
    unattributed_outs INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, team_id, phase)
);

CREATE TABLE IF NOT EXISTS sim_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    season     INTEGER DEFAULT 1,
    game_date  TEXT NOT NULL,
    event_type TEXT NOT NULL,
    team_id    INTEGER REFERENCES teams(id),
    player_id  INTEGER REFERENCES players(id),
    detail     TEXT NOT NULL DEFAULT ''
);

-- Task #62: archived season history. These tables persist ACROSS
-- the drop_all() / reseed cycle (drop_all() leaves them intact) so a
-- multi-season test run can compare model output across seasons.
CREATE TABLE IF NOT EXISTS seasons (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    season_number      INTEGER NOT NULL,
    rng_seed           INTEGER,
    config_id          TEXT,
    team_count         INTEGER,
    started_at         TEXT,
    ended_at           TEXT,
    champion_team_name TEXT,
    champion_abbrev    TEXT,
    champion_w         INTEGER,
    champion_l         INTEGER,
    games_played       INTEGER DEFAULT 0,
    year               INTEGER,
    invariant_pass     INTEGER DEFAULT 0,
    invariant_fail     INTEGER DEFAULT 0,
    invariant_summary  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS season_standings (
    season_id   INTEGER NOT NULL REFERENCES seasons(id),
    league      TEXT,
    division    TEXT,
    team_name   TEXT NOT NULL,
    team_abbrev TEXT,
    wins        INTEGER,
    losses      INTEGER,
    rs          INTEGER,
    ra          INTEGER,
    PRIMARY KEY (season_id, team_name)
);

CREATE TABLE IF NOT EXISTS season_batting_leaders (
    season_id   INTEGER NOT NULL REFERENCES seasons(id),
    category    TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    player_name TEXT,
    team_abbrev TEXT,
    g           INTEGER,
    pa          INTEGER,
    ab          INTEGER,
    h           INTEGER,
    hr          INTEGER,
    rbi         INTEGER,
    bb          INTEGER,
    avg         REAL,
    obp         REAL,
    slg         REAL,
    ops         REAL,
    PRIMARY KEY (season_id, category, rank)
);

CREATE TABLE IF NOT EXISTS season_pitching_leaders (
    season_id   INTEGER NOT NULL REFERENCES seasons(id),
    category    TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    player_name TEXT,
    team_abbrev TEXT,
    g           INTEGER,
    w           INTEGER,
    l           INTEGER,
    outs        INTEGER,
    er          INTEGER,
    k           INTEGER,
    bb          INTEGER,
    era         REAL,
    fip         REAL,
    whip        REAL,
    oavg        REAL DEFAULT 0,   -- opponent batting average (H / (BF - BB))
    PRIMARY KEY (season_id, category, rank)
);
"""


def _wipe_if_stale() -> None:
    """
    Detect a pre-Phase-8 database and wipe it so seed_league() can reseed.

    Signal: any pitcher row (is_pitcher=1) with a blank pitcher_role is
    guaranteed to be from before Phase 8 — generate_players() always sets
    pitcher_role='workhorse' for pitchers.  If such rows exist, every player
    in the DB lacks archetype/role/modifier data and the whole roster must be
    regenerated.

    A fresh empty DB (tables don't exist yet) is silently ignored.
    """
    try:
        row = fetchone(
            "SELECT COUNT(*) AS n FROM players WHERE is_pitcher = 1 AND pitcher_role = ''"
        )
        if row and row["n"] > 0:
            drop_all()
    except Exception:
        pass  # tables don't exist yet — nothing to wipe


def init_db() -> None:
    """
    Create tables and apply column migrations (idempotent).

    Order:
      1. ALTER TABLE — adds Phase-8 and Phase-9 columns to existing tables.
      2. _wipe_if_stale() — wipe pre-Phase-8 data if found.
      3. executescript(SCHEMA) — create missing tables.
    """
    # Step 0: ensure parent directory exists (e.g. /data on fly volumes)
    db_dir = os.path.dirname(_DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    # Step 1: column migrations (no-op if tables absent or columns present)
    with get_conn() as conn:
        # Phase 8 columns
        phase8_text = [("archetype", "''"), ("pitcher_role", "''")]
        phase8_real = [("hard_contact_delta", "0.0"), ("hr_weight_bonus", "0.0")]
        # Phase 9 columns
        phase9_int  = [("age", "27")]
        phase9_text = [("injured_until", "NULL"), ("il_tier", "NULL")]
        # Task #65 columns: per-pitcher Stamina rolled independently from
        # tier distribution, plus active/reserve roster split flag.
        task65_int  = [("stamina", "50"), ("is_active", "1")]

        for col, defval in phase8_text + phase9_text:
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} TEXT DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass
        for col, defval in phase8_real:
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} REAL DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass
        for col, defval in phase9_int + task65_int:
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass

        # Realism layer columns (multi-dimensional ratings + handedness).
        # Defaults of 50 / 'R' make pre-realism rows score-neutral so the
        # engine produces identical output until a fresh seed populates them.
        realism_int  = [
            ("contact",  "50"),
            ("power",    "50"),
            ("eye",      "50"),
            ("command",  "50"),
            ("movement", "50"),
        ]
        realism_text = [("bats", "'R'"), ("throws", "'R'")]
        for col, defval in realism_int:
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass
        for col, defval in realism_text:
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} TEXT DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass

        # Realism layer team columns (ballpark factors).
        for col, defval in [("park_hr", "1.0"), ("park_hits", "1.0")]:
            try:
                conn.execute(f"ALTER TABLE teams ADD COLUMN {col} REAL DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass

        # Manager persona columns (re-rolled on every reseed; see managers.py).
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN manager_archetype TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass
        for col in ("mgr_quick_hook", "mgr_bullpen_aggression",
                    "mgr_leverage_aware", "mgr_joker_aggression",
                    "mgr_pinch_hit_aggression", "mgr_platoon_aggression",
                    "mgr_run_game", "mgr_bench_usage"):
            try:
                conn.execute(f"ALTER TABLE teams ADD COLUMN {col} REAL DEFAULT 0.5")
                conn.commit()
            except Exception:
                pass

        # Baserunning skill + aggressiveness (independent of speed).
        for col in ("baserunning", "run_aggressiveness"):
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT 50")
                conn.commit()
            except Exception:
                pass

        # Defense layer columns. Defaults of 50 = neutral.
        # Per-position sub-ratings (infield / outfield / catcher) let a
        # player be a true specialist (elite at one group, replacement
        # elsewhere) or a legit utility guy (decent across groups).
        for col in ("defense", "arm", "defense_infield",
                    "defense_outfield", "defense_catcher"):
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT 50")
                conn.commit()
            except Exception:
                pass

        # Counting-stat columns persisted post-realism (Stage 1 of stats expansion).
        # Defaults of 0 leave pre-existing rows neutral; new games populate fully.
        for col in ("hbp", "sb", "cs", "fo", "multi_hit_abs", "stay_rbi", "stay_hits"):
            try:
                conn.execute(f"ALTER TABLE game_batter_stats ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass
        # Defense-event column: batter "reached on error" count (per-batter).
        # Team errors-committed are derived as the sum of OPPOSING batters'
        # ROE in a given game, so no separate team-level column is needed.
        try:
            conn.execute("ALTER TABLE game_batter_stats ADD COLUMN roe INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass

        # Per-fielder defensive events: PO and E credited to the player
        # who actually made (or muffed) the play, NOT the batter at the
        # plate. The renderer credits these via _select_fielder picking
        # a position-weighted fielder per BIP outcome.
        for col in ("po", "e"):
            try:
                conn.execute(f"ALTER TABLE game_batter_stats ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass
        for col in ("hbp_allowed", "unearned_runs", "sb_allowed", "cs_caught", "fo_induced"):
            try:
                conn.execute(f"ALTER TABLE game_pitcher_stats ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass

        # Task #47/#32 game_pitcher_stats columns: HR allowed + Pitches thrown
        # Task #48: ER (earned runs, distinct from runs_allowed)
        for col in ("hr_allowed", "pitches", "er"):
            try:
                conn.execute(f"ALTER TABLE game_pitcher_stats ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass

        # wERA / xFIP / Decay: arc-bucketed counters keyed off the
        # defending team's running 27-out count (1-9 / 10-18 / 19-27).
        # Plus is_starter for GS / WS%.
        _arc_cols = (
            "er_arc1", "er_arc2", "er_arc3",
            "k_arc1",  "k_arc2",  "k_arc3",
            "fo_arc1", "fo_arc2", "fo_arc3",
            "bf_arc1", "bf_arc2", "bf_arc3",
            "is_starter",
        )
        for col in _arc_cols:
            try:
                conn.execute(f"ALTER TABLE game_pitcher_stats ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass

        # Task #49: per-batter outs_recorded (CS / FC / pickoffs charged
        # to responsible batter so OR column sums to 27 per half).
        try:
            conn.execute("ALTER TABLE game_batter_stats ADD COLUMN outs_recorded INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass

        # Weather model columns on games (stamped at schedule time).
        for col, defval in (
            ("temperature_tier", "'mild'"),
            ("wind_tier",        "'neutral'"),
            ("humidity_tier",    "'normal'"),
            ("precip_tier",      "'none'"),
            ("cloud_tier",       "'clear'"),
        ):
            try:
                conn.execute(
                    f"ALTER TABLE games ADD COLUMN {col} TEXT DEFAULT {defval}"
                )
                conn.commit()
            except Exception:
                pass

        # Task #62: add oavg column to existing season_pitching_leaders.
        try:
            conn.execute("ALTER TABLE season_pitching_leaders ADD COLUMN oavg REAL DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        # Task #62: add year column to existing seasons table.
        try:
            conn.execute("ALTER TABLE seasons ADD COLUMN year INTEGER")
            conn.commit()
        except Exception:
            pass

        # Task #58: phase column on both stat tables (0 = regulation,
        # N>=1 = super-inning round N). Existing rows are backfilled to
        # phase=0 (historical super-inning games stay structurally
        # unsplit, per the agreed migration policy).
        for tbl in ("game_batter_stats", "game_pitcher_stats"):
            try:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN phase INTEGER NOT NULL DEFAULT 0")
                conn.commit()
            except Exception:
                pass
            # Try to add the UNIQUE invariant. If legacy duplicates exist
            # the index creation fails — that's acceptable; the constraint
            # then guards only fresh DBs (via the inline UNIQUE in SCHEMA).
            try:
                conn.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS "
                    f"idx_{tbl}_unique ON {tbl}(player_id, game_id, phase)"
                )
                conn.commit()
            except Exception:
                pass

    # Step 2: wipe stale pre-Phase-8 data
    _wipe_if_stale()

    # Step 3: (re)create any missing tables
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def drop_all() -> None:
    """Drop all tables (for re-seeding)."""
    with get_conn() as conn:
        # Drop child tables before parents to avoid FK constraint failures.
        # team_phase_outs and sim_meta were added in Task #58; if they hold
        # rows when we try to drop `games` they raise FOREIGN KEY constraint
        # failed and the whole reset aborts.
        conn.executescript("""
            DROP TABLE IF EXISTS transactions;
            DROP TABLE IF EXISTS game_pa_log;
            DROP TABLE IF EXISTS game_pitcher_stats;
            DROP TABLE IF EXISTS game_batter_stats;
            DROP TABLE IF EXISTS team_phase_outs;
            DROP TABLE IF EXISTS sim_meta;
            DROP TABLE IF EXISTS games;
            DROP TABLE IF EXISTS players;
            DROP TABLE IF EXISTS teams;
        """)


def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def fetchone(sql: str, params: tuple = ()) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: tuple = ()) -> int:
    """Execute a DML statement; returns lastrowid."""
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def executemany(sql: str, param_list: list[tuple]) -> None:
    with get_conn() as conn:
        conn.executemany(sql, param_list)
        conn.commit()
