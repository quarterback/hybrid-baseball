"""
SQLite database layer for O27v2.

All persistence lives in o27v2/o27v2.db (relative to workspace root).
Functions return plain dicts / lists so callers never deal with cursors.
"""
from __future__ import annotations
import os
import sqlite3
import time
from typing import Any


def _is_locked_error(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def _retry_on_locked(fn, attempts: int = 6, base_delay: float = 0.05):
    """Run a write callable, retrying on a transient ``database is locked``.

    busy_timeout (set in get_conn) already makes a connection WAIT for the WAL
    writer lock, but it does NOT cover the read→write upgrade case (a txn that
    SELECTs then writes is denied immediately to avoid deadlock). A short
    bounded retry with backoff turns those momentary collisions — a page-load
    write racing a running sim — into a brief wait instead of a 500 / failed
    game. Re-raises anything that is not a lock error, or the last lock error
    once attempts are exhausted."""
    delay = base_delay
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not _is_locked_error(exc) or i == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2

_DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "o27v2.db")
_ENV_DB_PATH = os.environ.get("O27V2_DB_PATH")
# _DB_PATH is the "single fixed DB" path. When it differs from the frozen
# default — either because O27V2_DB_PATH is set, or a test reassigned it at
# runtime — path resolution bypasses the saves registry entirely. Otherwise
# the active save (o27v2/saves.py) drives which file every connection opens.
_DB_PATH = _ENV_DB_PATH or _DEFAULT_DB_PATH
_DB_PATH_OVERRIDDEN = _ENV_DB_PATH is not None


def _resolve_path() -> str:
    """Path of the SQLite file every connection should open.

    Explicit override (O27V2_DB_PATH env var, or a runtime reassignment of
    _DB_PATH) wins and selects a single fixed DB — this is what the test
    suite relies on. With no override, resolve the active save's file; if no
    save is active yet (fresh box, pre-migration) fall back to the default.
    """
    if _DB_PATH_OVERRIDDEN or _DB_PATH != _DEFAULT_DB_PATH:
        return _DB_PATH
    try:
        from o27v2 import saves
        active = saves.active_db_path()
    except Exception:
        active = None
    return active or _DB_PATH


class _ManagedConnection(sqlite3.Connection):
    """A connection that *closes itself* when used as a context manager.

    Python's stock ``sqlite3`` makes ``with conn:`` manage only the
    *transaction* — it commits on success / rolls back on error and then
    deliberately leaves the connection OPEN. Every ``with get_conn() as conn:``
    in this codebase therefore leaked the underlying file descriptors (the DB
    handle plus, in WAL mode, the ``-wal`` / ``-shm`` sidecars) until CPython's
    GC eventually finalized the object. Under sustained request load (each page
    fans out into many ``fetchone``/``fetchall`` calls) the process drifted up
    against its open-file limit, at which point SQLite could no longer open the
    DB or its sidecars and raised ``OperationalError: disk I/O error`` — even on
    a bare ``PRAGMA`` at connect time, exactly the symptom we saw in the logs.

    Overriding ``__exit__`` to close after the normal commit/rollback plugs the
    leak while preserving the existing transaction semantics, so every
    ``with get_conn() as conn:`` block is now both committed and closed.
    """

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_resolve_path(), factory=_ManagedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # synchronous=NORMAL is per-connection (not persisted like journal_mode).
    # Paired with WAL (set once in init_db) it keeps each commit() from
    # fsync'ing the whole DB — the dominant cost when bulk-simming hundreds
    # of games, each of which commits several times. NORMAL is crash-safe
    # under WAL (only a power/OS crash can lose the last txn, acceptable for
    # a game sim).
    conn.execute("PRAGMA synchronous = NORMAL")
    # busy_timeout makes a connection WAIT (up to N ms) for a lock instead of
    # throwing `database is locked` the instant another connection holds the
    # writer. Without it (the sqlite3 default is 0) any concurrent write —
    # the almanac-warm thread spawned at league creation, a running sim, a
    # second browser tab — surfaces as an immediate 500 that "fixes itself"
    # on refresh once the lock clears. 10s comfortably covers a seed/commit.
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    abbrev    TEXT NOT NULL,
    city      TEXT NOT NULL,
    -- Geographic coordinates of the home market. Drives "nearest city"
    -- weather (o27/engine/weather.archetype_for_coords) and the
    -- west→east division placement. NULL on legacy rows; weather falls
    -- back to the city-name lookup when absent.
    lat       REAL,
    lon       REAL,
    division  TEXT NOT NULL,
    league    TEXT NOT NULL,
    wins      INTEGER DEFAULT 0,
    losses    INTEGER DEFAULT 0,
    park_hr   REAL DEFAULT 1.0,
    park_hits REAL DEFAULT 1.0,
    park_name TEXT DEFAULT '',
    -- Generated ballpark dimensions (JSON: lf, lcf, cf, rcf, rf in feet,
    -- plus wall_h for the outfield wall height). Flavor-only for now —
    -- park_hr and park_hits remain the mechanical multipliers.
    park_dimensions TEXT DEFAULT '',
    -- Park shape archetype — narrative key driving the dimension
    -- distribution. One of: balanced / short_porch_rf / short_porch_lf
    -- / cavernous / bathtub / triangle / oval. Empty on legacy rows.
    park_shape      TEXT DEFAULT '',
    -- Architectural quirks (JSON list of {key, label, blurb}). 0-3
    -- per park, drawn from a catalog evoking the 1910s-20s ballpark
    -- revival (Tal's Hill, Wire Basket, Hand-Operated Scoreboard,
    -- Flag Pole in Play, etc.).
    park_quirks     TEXT DEFAULT '',
    -- Manager (re-rolled per league seed; not hard-wired to franchise).
    -- See o27v2/managers.py for archetype catalogue and tendency semantics.
    manager_archetype        TEXT  DEFAULT '',
    manager_name             TEXT  DEFAULT '',
    mgr_quick_hook           REAL  DEFAULT 0.5,
    mgr_bullpen_aggression   REAL  DEFAULT 0.5,
    mgr_leverage_aware       REAL  DEFAULT 0.5,
    mgr_joker_aggression     REAL  DEFAULT 0.5,
    mgr_pinch_hit_aggression REAL  DEFAULT 0.5,
    mgr_platoon_aggression   REAL  DEFAULT 0.5,
    mgr_run_game             REAL  DEFAULT 0.5,
    mgr_bench_usage          REAL  DEFAULT 0.5,
    mgr_shift_aggression     REAL  DEFAULT 0.5,
    -- mgr_ibb_aggression — willingness to issue an intentional walk to
    -- a hot or elite batter. Read by manager.should_intentional_walk.
    mgr_ibb_aggression       REAL  DEFAULT 0.5,
    -- Declared Seconds — two new persona axes. mgr_declare_aggression
    -- governs willingness to bank outs for a rebuttal half;
    -- mgr_bat_first_pref is the home-team bat-first/bat-second bias.
    mgr_declare_aggression   REAL  DEFAULT 0.5,
    mgr_bat_first_pref       REAL  DEFAULT 0.5,
    -- mgr_flip_aggression — Cricket Batting Order (optional rule) persona:
    -- how readily the skipper spends an earned joker-free flip, and inversely
    -- how reluctant he is to burn a joker that would forfeit it. Read by
    -- manager.should_use_flip / should_insert_joker.
    mgr_flip_aggression      REAL  DEFAULT 0.5,
    org_strength             INTEGER DEFAULT 50,
    -- Front-office persona (see o27v2/front_office.py). Drives trade
    -- motivations and acceptance thresholds; drifts year over year.
    fo_strategy        TEXT    DEFAULT 'balanced',
    fo_aggression      REAL    DEFAULT 0.5,
    fo_archetype_bias  TEXT    DEFAULT '',
    fo_losing_streak   INTEGER DEFAULT 0,
    -- Team-wide performance streak overlay (see o27v2/streaks.py).
    streak_state       INTEGER DEFAULT 0,
    streak_weeks       INTEGER DEFAULT 0,
    streak_games       INTEGER DEFAULT 0,
    streak_heat        REAL    DEFAULT 0.0,
    fo_last_trade_date TEXT    DEFAULT '',
    -- Per-league playing-style profile (see o27v2/league.py _STYLE_PROFILES).
    -- Empty = neutral generation. Set when a config opts into mechanical
    -- style diversity; drives a per-attribute bias at seed time and is
    -- surfaced as a badge in the UI.
    style_profile      TEXT    DEFAULT '',
    -- Power Play (optional rule) — per-league opt-in set at league creation
    -- (the checkbox on new_league.html). Stamped onto every team in the
    -- league; sim.py reads it into state.power_play_enabled per game. 0 = off.
    power_play_enabled INTEGER DEFAULT 0,
    -- Cricket Batting Order (optional rule) — per-league opt-in, same plumbing
    -- as power_play_enabled. Stamped onto every team in the league; sim.py
    -- reads it into team.cricket_order_enabled per game. The order flips
    -- 1-9 -> 9-1 at the end of every joker-free trip through the lineup.
    -- 0 = off.
    cricket_order_enabled INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    -- team_id NULL = unsigned free agent (in the waiver / match-day pool).
    team_id       INTEGER REFERENCES teams(id),
    name          TEXT NOT NULL,
    position      TEXT NOT NULL,
    is_pitcher    INTEGER DEFAULT 0,
    is_joker      INTEGER DEFAULT 0,
    skill         INTEGER DEFAULT 50,
    speed         INTEGER DEFAULT 50,
    pitcher_skill INTEGER DEFAULT 50,
    stay_aggressiveness REAL DEFAULT 0.4,
    contact_quality_threshold REAL DEFAULT 0.45,
    pull_pct REAL DEFAULT 0.5,
    adaptability INTEGER DEFAULT 50,
    leadership   INTEGER DEFAULT 50,   -- batter mental attribute. Stacks with `grit` in the RISP-pressure bonus so a low-eye/contact bench guy with elite leadership+grit can still tip a high-leverage AB (joker archetype).
    archetype             TEXT DEFAULT '',
    -- Canonical crew role (see o27v2/rotation.py): '' / HM / 1C / 2C / BO /
    -- SK / AN / PI (Helms, First/Second Change, Bosun, Skidder, Anchor,
    -- Pilot). Re-derived relative to the team at seed, season rollover, and
    -- after roster changes. '' = no crew role (legacy / pre-crew saves) →
    -- consumers fall back to live derivation.
    pitcher_role          TEXT DEFAULT '',
    -- Usage rank within a crew role (1 = primary; e.g. the two Helms
    -- alternate as slot 1 / slot 2). 0 for non-pitchers / unroled arms.
    rotation_slot         INTEGER DEFAULT 0,
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
    -- Bunting technique / bat control (0-100). Distinct from foot speed, so a
    -- slow contact hitter can still be an elite bunter. Drives the manager's
    -- bunt calls and execution. Seeded ~0.6*contact + 0.4*speed by default.
    bunt      INTEGER DEFAULT 50,
    command   INTEGER DEFAULT 50,
    movement  INTEGER DEFAULT 50,
    bats      TEXT DEFAULT 'R',
    throws    TEXT DEFAULT 'R',
    -- ISO 3166-1 alpha-2 country code (e.g. "IN", "PK", "MY"). Drives
    -- the flag emoji rendered next to the player's name in the UI.
    -- Empty string for legacy rows / pre-roster generation.
    country   TEXT DEFAULT '',
    -- Player-card flavor. hometown = birthplace city (rolled from
    -- data/names/hometowns.json by country); birthday = cosmetic "Mar 14"
    -- (no year — age is the engine clock); secondary_country = dual-
    -- nationality code for lineage-eligible players (flag flavor + the
    -- youth side's weak-nation talent steering).
    hometown          TEXT DEFAULT '',
    birthday          TEXT DEFAULT '',
    secondary_country TEXT DEFAULT '',
    -- Defense layer (range / glove / arm + per-position-group sub-ratings).
    defense           INTEGER DEFAULT 50,
    arm               INTEGER DEFAULT 50,
    defense_infield   INTEGER DEFAULT 50,
    defense_outfield  INTEGER DEFAULT 50,
    defense_catcher   INTEGER DEFAULT 50,
    -- Catcher pitch-calling — suppresses contact when this player is behind
    -- the plate (see o27/engine/prob._catcher_gc_shift). NOT framing.
    game_calling      INTEGER DEFAULT 50,
    -- Baserunning skill (reads, routes, slides) and aggressiveness
    -- (willingness to risk extra base). Independent of foot speed.
    baserunning         INTEGER DEFAULT 50,
    run_aggressiveness  INTEGER DEFAULT 50,
    -- Phase 5e — work-ethic / work-habits.
    --   work_ethic  (visible 20-80) is a season-long boost on every
    --     attribute. Re-rolled each off-season under age 30; locks
    --     at 30 (the value held at age 29 carries forward).
    --   work_habits (hidden 20-80) is a context-dependent multiplier.
    --     Re-rolled each off-season under age 27; locks at 27.
    --   habit_cup (0..1, defaults 0.5) is the in-season "cup" — fills
    --     with success, drains with failure. Modulates how strongly
    --     work_habits applies to today_condition: at cup=1.0 a high-
    --     habits player gets the full boost; at cup=0.0 a low-habits
    --     player takes the full penalty. Resets to 0.5 each off-season.
    work_ethic   INTEGER DEFAULT 50,
    work_habits  INTEGER DEFAULT 50,
    habit_cup    REAL    DEFAULT 0.5,
    -- Persisted salary in guilders (int). Seeded at league creation
    -- via o27v2.valuation. Default 0 lets older rows fall through to
    -- on-the-fly estimation in valuation.estimate_player_value.
    salary       INTEGER DEFAULT 0,
    -- Pitch-type activation: JSON-encoded list of repertoire entries
    -- ({"pitch_type", "quality", "usage_weight"}). NULL on legacy rows
    -- and on non-pitchers; the engine treats NULL as "no repertoire"
    -- and falls back to the aggregate Stuff/Command/Movement path.
    repertoire   TEXT    DEFAULT NULL,
    -- Release angle (0=submarine, 0.5=sidearm, 1.0=three-quarter).
    -- Drives which pitches a pitcher can throw well — see PITCH_CATALOG
    -- in o27/config.py for per-pitch release_optimal / max_release.
    release_angle  REAL  DEFAULT 0.5,
    -- Per-pitch quality jitter (static half-width around central
    -- Stuff/Command/Movement). High variance = max-effort, frayed
    -- mechanics arm; low variance = consistent. Default 0 = identity.
    pitch_variance REAL  DEFAULT 0.0,
    -- Pitcher fatigue resistance, bounded 0.25-0.75 in roster gen.
    -- 0.50 = identity (no fatigue ramp change). Also damps today_form
    -- per-game variance — high-grit arms swing less day-to-day.
    grit           REAL  DEFAULT 0.5,
    -- Performance streaks (see o27v2/streaks.py). A multi-week hot/cold run
    -- that ramps over weeks and reverts to the true rating when it ends.
    streak_state   INTEGER DEFAULT 0,   -- -1 cold / 0 none / +1 hot
    streak_weeks   INTEGER DEFAULT 0,   -- completed weekly ramp ticks
    streak_games   INTEGER DEFAULT 0,   -- games logged in the current week
    streak_heat    REAL    DEFAULT 0.0, -- rolling [-1,1] performance signal
    -- Substitution-economy role tags (see o27v2/archetypes.py). Stamped at
    -- generation, re-derived in development. `roster_slot` drives roster
    -- composition; `role_*` flags drive substitution candidate filtering.
    roster_slot    TEXT  DEFAULT '',
    role_hit       INTEGER DEFAULT 1,
    role_run       INTEGER DEFAULT 0,
    role_two_way   INTEGER DEFAULT 1,
    role_field_pos TEXT  DEFAULT ''
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
    cloud_tier       TEXT DEFAULT 'clear',
    -- Exact rolled temperature (°F) and first-pitch clock time. Start time
    -- is local to the home park; start_utc_offset carries the park's zone
    -- so the box score can label it. low_light = game runs into fading
    -- light (drives the K/error penalty). See o27/engine/gametime.py.
    temperature_f    INTEGER,
    start_minute     INTEGER,
    start_utc_offset INTEGER,
    low_light        INTEGER DEFAULT 0,
    -- Defensive-shift telemetry (per-team, per-game). Stamped from the
    -- engine at game end so the value of each manager's shift calls is
    -- visible at the game level — sums to season-level shift impact.
    home_shift_outs_added INTEGER DEFAULT 0,
    home_shift_hits_lost  INTEGER DEFAULT 0,
    away_shift_outs_added INTEGER DEFAULT 0,
    away_shift_hits_lost  INTEGER DEFAULT 0,
    -- Playoff hookup. NULL `series_id` ⇒ regular-season game.
    -- `is_playoff` is the cheap flag the UI / queries filter on.
    series_id        INTEGER REFERENCES playoff_series(id),
    is_playoff       INTEGER DEFAULT 0,
    -- Declared Seconds: home_bats_first is the pre-game choice; each side's
    -- declared_at and seconds_used capture the in-game decisions. NULL declared_at
    -- = no declaration; seconds_used > 0 = the team came back for a seconds
    -- inning. declare_context records the score state when each declared.
    home_bats_first              INTEGER DEFAULT NULL,
    away_declared_at             INTEGER DEFAULT NULL,
    home_declared_at             INTEGER DEFAULT NULL,
    away_seconds_used            INTEGER DEFAULT 0,
    home_seconds_used            INTEGER DEFAULT 0,
    away_declare_context         TEXT    DEFAULT NULL,
    home_declare_context         TEXT    DEFAULT NULL,
    away_declare_score_for       INTEGER DEFAULT NULL,
    away_declare_score_against   INTEGER DEFAULT NULL,
    home_declare_score_for       INTEGER DEFAULT NULL,
    home_declare_score_against   INTEGER DEFAULT NULL,
    seconds_outcome              TEXT    DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS game_batter_stats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    INTEGER NOT NULL REFERENCES games(id),
    team_id    INTEGER NOT NULL REFERENCES teams(id),
    player_id  INTEGER NOT NULL REFERENCES players(id),
    phase      INTEGER NOT NULL DEFAULT 0,
    -- Denormalized from games.is_playoff so the many season/career/leaderboard
    -- aggregations (which don't join games) can keep regular-season totals
    -- separate from postseason without double-counting. 0 = regular season.
    is_playoff INTEGER NOT NULL DEFAULT 0,
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
    -- Bunting (multi-type). sh = successful sacrifice bunts (PA, not AB);
    -- bunt_att = every bunt PA; bunt_hits = bunt singles (subset of hits);
    -- sqz / sqz_rbi = squeeze plays and the runs they drove in from third.
    sh         INTEGER DEFAULT 0,
    bunt_att   INTEGER DEFAULT 0,
    bunt_hits  INTEGER DEFAULT 0,
    sqz        INTEGER DEFAULT 0,
    sqz_rbi    INTEGER DEFAULT 0,
    stay_rbi   INTEGER DEFAULT 0,
    stay_hits  INTEGER DEFAULT 0,   -- hits credited on a 2C event (subset of hits)
    c2_strand_out INTEGER DEFAULT 0, -- AB ended in a batter-out after >=1 credited
                                     -- 2C this AB (advanced runners then made an out)
    walkback_runs INTEGER DEFAULT 0,  -- runs scored as a Walk-Back bonus runner
                                      -- (HR hitter driven in again); subset of runs
                                      -- and the per-hitter mirror of pitcher wb_runs
    -- 2C moved-runner stats (Apollo-style): per-base opportunities and
    -- successes. A successful "move" = post-stay base position is higher
    -- than pre-stay (or runner scored cleanly). Runner thrown out trying
    -- = opportunity but not move. Rate stat: c2_adv_X / c2_op_X.
    c2_op_1b   INTEGER DEFAULT 0,
    c2_adv_1b  INTEGER DEFAULT 0,
    c2_op_2b   INTEGER DEFAULT 0,
    c2_adv_2b  INTEGER DEFAULT 0,
    c2_op_3b   INTEGER DEFAULT 0,
    c2_adv_3b  INTEGER DEFAULT 0,
    -- Per-PA advancement: did this batter move the runner who started
    -- on each base during his PA? (Inclusive of 2C, run-chosen, BB-force,
    -- sac bunt.) Binary success conversion% = adv / op.
    adv_op_1b   INTEGER DEFAULT 0,
    adv_adv_1b  INTEGER DEFAULT 0,
    adv_op_2b   INTEGER DEFAULT 0,
    adv_adv_2b  INTEGER DEFAULT 0,
    adv_op_3b   INTEGER DEFAULT 0,
    adv_adv_3b  INTEGER DEFAULT 0,
    -- Runners Advanced (RAD) — graded per-base advancement. Counts the
    -- bases each runner gained (not binary success). Sum is the "total
    -- runner bases advanced" — MLB Total Bases concept applied to
    -- runner movement rather than batter movement.
    rad_1b      INTEGER DEFAULT 0,
    rad_2b      INTEGER DEFAULT 0,
    rad_3b      INTEGER DEFAULT 0,
    -- RISP (runners in scoring position — runner on 2B and/or 3B at the PA's
    -- start). Each is the subset of the matching counter accrued in a RISP
    -- situation, so a full RISP slash line + RISP RBI is recoverable. The
    -- recorded-outcome companion to the engine's RISP-pressure probability
    -- model: "how good is this bat at cashing runners in?"
    risp_pa     INTEGER DEFAULT 0,
    risp_ab     INTEGER DEFAULT 0,
    risp_h      INTEGER DEFAULT 0,
    risp_2b     INTEGER DEFAULT 0,
    risp_3b     INTEGER DEFAULT 0,
    risp_hr     INTEGER DEFAULT 0,
    risp_bb     INTEGER DEFAULT 0,
    risp_hbp    INTEGER DEFAULT 0,
    risp_rbi    INTEGER DEFAULT 0,
    -- Per-game fielding position. Distinct from `players.position` (the
    -- player's primary), this is the actual spot they played that day.
    -- Utility (UT) players land on a concrete slot at lineup build time;
    -- jokers stay "J". Mid-game defensive moves can extend (e.g. "SS-2B").
    game_position TEXT DEFAULT '',
    -- Box-score entry classification. "starter" / "PH" / "PR" / "DEF" /
    -- "joker" / "joker_field".
    entry_type TEXT DEFAULT 'starter',
    -- For PH / PR / DEF / joker_field rows: the player_id they came in
    -- for, used to indent the box-score row directly under the starter
    -- they replaced.
    replaced_player_id INTEGER DEFAULT NULL,
    -- Inning (1..9, derived as outs // 3 + 1) at which this row entered
    -- the game. 0 for starters. Footnote rendering reads this to emit
    -- "Pinch-hit for Skanes in the 5th." Once set, never overwritten
    -- (no-reentry — a removed player can't come back).
    entered_inning INTEGER DEFAULT 0,
    -- Exact team-out count when the row entered (0 = starter). Powers the
    -- defensive log's precise out-envelopes; entered_inning stays for footnotes.
    entered_outs INTEGER DEFAULT 0,
    -- Grounded into double / triple play counters.
    gidp INTEGER DEFAULT 0,
    gitp INTEGER DEFAULT 0,
    roe        INTEGER DEFAULT 0,   -- reached on error (NOT a hit; AB credited)
    -- Per-fielder defensive events (the player as a FIELDER, not as a batter).
    po         INTEGER DEFAULT 0,   -- putouts as primary fielder
    a          INTEGER DEFAULT 0,   -- assists (intermediate fielder on the play)
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
    rbi_credited  INTEGER NOT NULL DEFAULT 0,
    -- SABR analytics: pre/post game-state stamped per event so RE24 /
    -- leverage / WPA can be computed without engine replay. `bases_*` is
    -- a 3-bit mask (bit0=1B, bit1=2B, bit2=3B), `outs_*` is outs in the
    -- half (0..27 for regulation, may exceed in super-innings),
    -- `score_diff_*` is batting_score − fielding_score at the moment.
    -- NULL on legacy rows written before the stamping was added.
    outs_before       INTEGER DEFAULT NULL,
    bases_before      INTEGER DEFAULT NULL,
    score_diff_before INTEGER DEFAULT NULL,
    outs_after        INTEGER DEFAULT NULL,
    bases_after       INTEGER DEFAULT NULL,
    score_diff_after  INTEGER DEFAULT NULL,
    -- Pitch-type activation: the typed pitch selected for this PA from
    -- the pitcher's repertoire. NULL on legacy rows and on PAs against
    -- pitchers without a typed repertoire. Drives the per-pitcher pitch-
    -- mix aggregate stamped on game_pitcher_stats.
    pitch_type        TEXT    DEFAULT NULL,
    -- Batted-ball physics hybrid layer. Sampled per BIP from the
    -- (quality, hit_type, batter.power, pitch.hard_contact_shift)
    -- joint distribution and stamped here for downstream visualization
    -- (spray charts, EV/LA-banded Luck Ledger, xwOBA attribution).
    -- Flavor-only — does NOT drive engine fielding outcomes. Categorical
    -- hit_type remains the canonical engine output. NULL on non-BIP
    -- events (K / BB / HBP) and on legacy rows.
    exit_velocity     REAL    DEFAULT NULL,   -- mph
    launch_angle      REAL    DEFAULT NULL,   -- degrees, − = grounder
    spray_angle       REAL    DEFAULT NULL,   -- degrees, − = pull / + = oppo
    -- Engine-credited fielder (player_id) on outs/errors; NULL for hits, non-
    -- BIP events, and legacy rows. Powers exact (PO-consistent) Fielding OAA;
    -- hits still fall back to trajectory-zone attribution in the analytics.
    fielder_id        INTEGER DEFAULT NULL,
    -- Ball-strike count the batter put the ball in play on (pre-contact, the
    -- count the swing happened at). Powers outcome-by-count analysis — e.g.
    -- home-runs-by-count vs the MLB reference (docs/aar-hr-by-count-vs-mlb.md).
    -- NULL on legacy rows written before count stamping was added.
    balls             INTEGER DEFAULT NULL,
    strikes           INTEGER DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_pa_log_game ON game_pa_log(game_id);
CREATE INDEX IF NOT EXISTS idx_pa_log_batter ON game_pa_log(batter_id);

-- Per-bunt event log (manager-called bunts only). Kept out of game_pa_log
-- because bunts carry no contact-quality / batted-ball physics and would
-- pollute the BIP-keyed xwOBA / expected-stats aggregates. The pre/post
-- game-state stamps (bases_* = 3-bit mask bit0=1B/bit1=2B/bit2=3B, outs_* =
-- outs in the half) let analytics value each bunt against the RE24-O27
-- matrix: run value = RE(after) − RE(before) + runs_scored.
CREATE TABLE IF NOT EXISTS game_bunt_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       INTEGER NOT NULL REFERENCES games(id),
    team_id       INTEGER NOT NULL REFERENCES teams(id),
    batter_id     INTEGER NOT NULL REFERENCES players(id),
    pitcher_id    INTEGER REFERENCES players(id),
    phase         INTEGER NOT NULL DEFAULT 0,
    is_playoff    INTEGER NOT NULL DEFAULT 0,   -- denormalized from games
    bunt_type     TEXT,                          -- sac | drag | suicide | safety
    outcome       TEXT,                          -- hit | sacrifice | squeeze_* | ...
    runs_scored   INTEGER NOT NULL DEFAULT 0,
    outs_before   INTEGER,
    bases_before  INTEGER,
    outs_after    INTEGER,
    bases_after   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bunt_log_game ON game_bunt_log(game_id);

-- Pesäpallo-style scoring events log. One row per run that crosses the
-- plate: the batter at bat when it happened, the runner who scored, the
-- starting base of that runner at the PA's start, and the score after.
-- Produces the "Inn / Batter / Runner / Situation" log seen on the
-- Finnish pesistulokset.fi event listings.
CREATE TABLE IF NOT EXISTS game_scoring_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,        -- order within the game (0-indexed)
    half            TEXT NOT NULL,           -- "top" | "bottom" | "super_top" | "super_bottom"
    outs_before     INTEGER NOT NULL,        -- outs in this half BEFORE the scoring play
    batter_id       INTEGER NOT NULL REFERENCES players(id),
    runner_id       INTEGER NOT NULL REFERENCES players(id),
    runner_from_base INTEGER NOT NULL,        -- 0 = 1B, 1 = 2B, 2 = 3B (where the runner started the PA); 3 = batter's own run (HR)
    visitors_score  INTEGER NOT NULL,        -- score after this run
    home_score      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scoring_game   ON game_scoring_events(game_id);
CREATE INDEX IF NOT EXISTS idx_scoring_batter ON game_scoring_events(batter_id);
CREATE INDEX IF NOT EXISTS idx_scoring_runner ON game_scoring_events(runner_id);

-- Full text play-by-play, one blob per game. Stored in its own table
-- (not a games column) so SELECT * on games stays lean. Written by
-- o27v2/sim.py from the engine's rendered log; surfaced read-only at
-- /game/<id>/pbp. Legacy games simulated before this landed have no row.
CREATE TABLE IF NOT EXISTS game_pbp (
    game_id   INTEGER PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
    pbp_text  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_pitcher_stats (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id        INTEGER NOT NULL REFERENCES games(id),
    team_id        INTEGER NOT NULL REFERENCES teams(id),
    player_id      INTEGER NOT NULL REFERENCES players(id),
    phase          INTEGER NOT NULL DEFAULT 0,
    -- Denormalized from games.is_playoff (see game_batter_stats). 0 = reg season.
    is_playoff     INTEGER NOT NULL DEFAULT 0,
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
    -- Times-through-the-order buckets (1st / 2nd / 3rd+ look a batter has had
    -- at this pitcher in the game). Powers K%-by-look splits and the
    -- Deception decay stat (familiarity axis, vs Decay's fatigue axis).
    k_tto1         INTEGER DEFAULT 0,
    k_tto2         INTEGER DEFAULT 0,
    k_tto3         INTEGER DEFAULT 0,
    fo_tto1        INTEGER DEFAULT 0,
    fo_tto2        INTEGER DEFAULT 0,
    fo_tto3        INTEGER DEFAULT 0,
    bf_tto1        INTEGER DEFAULT 0,
    bf_tto2        INTEGER DEFAULT 0,
    bf_tto3        INTEGER DEFAULT 0,
    is_starter     INTEGER DEFAULT 0,   -- 1 if this pitcher started the game
    -- Walk-Back rule (post-HR rule-placed runner). wb_faced = PAs this
    -- pitcher pitched with a Walk-Back runner pending. wb_runs = subset
    -- where the runner scored (always unearned). Walk-Back Stop% =
    -- (wb_faced - wb_runs) / wb_faced. See docs/stats-reference.md.
    wb_faced       INTEGER DEFAULT 0,
    wb_runs        INTEGER DEFAULT 0,
    -- Inherited runners: how many were on base when this reliever entered
    -- (ir_inherited) and how many scored against him (ir_scored). Powers
    -- IR-Stop% = (ir_inherited - ir_scored) / ir_inherited.
    ir_inherited   INTEGER DEFAULT 0,
    ir_scored      INTEGER DEFAULT 0,
    -- Finisher stats. terminal_outs = outs recorded in a spell entered with a
    -- lead that was never relinquished and finished the game. quality_finish =
    -- count of 9+-out finishes never trailing. lead_entries / lead_held drive
    -- Lead-Retention% (lead_held / lead_entries).
    terminal_outs  INTEGER DEFAULT 0,
    quality_finish INTEGER DEFAULT 0,
    lead_entries   INTEGER DEFAULT 0,
    lead_held      INTEGER DEFAULT 0,
    -- xRA v3 — per-pitcher hit-type breakdown allowed. Lets each
    -- pitcher's xRA reflect their own batted-ball mix rather than the
    -- league average. Sums tabulated from the PA log post-game.
    singles_allowed INTEGER DEFAULT 0,
    doubles_allowed INTEGER DEFAULT 0,
    triples_allowed INTEGER DEFAULT 0,
    -- Pitch-type usage (per-game averages of fastball / breaking /
    -- offspeed share among typed pitches). Aggregated to season-level
    -- in the web layer for the Arsenal panel and pitch-mix leaderboards.
    fastball_pct   REAL    DEFAULT 0.0,
    breaking_pct   REAL    DEFAULT 0.0,
    offspeed_pct   REAL    DEFAULT 0.0,
    primary_pitch  TEXT    DEFAULT '',
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

-- Power Play (optional rule) per-game stat rack. Only written for games where
-- the rule was on (power_play_on(state) true), so leagues that never enable it
-- have an empty table. One row per (game, team, player) carrying that player's
-- power-play contribution that game. Two complementary roles share the table:
--   * DEFENSE  — the nickel fielder: deployments he started (pp_deploys),
--     outs the team's windows covered (pp_outs), extra-base hits he held to
--     singles (pp_xbh_held) and shallow hits he ran down (pp_hits_converted),
--     plus his PO/A/E AS the nickel (already in game_batter_stats too, mirrored
--     here for the power-play leaderboards).
--   * OFFENSE  — short-handed batting: PA/AB/H taken while the OPPOSING defense
--     had its nickel deployed (sh_pa / sh_ab / sh_hits). SH-AVG = sh_hits/sh_ab.
-- A player can have both roles in different games; the columns for the role
-- that didn't apply stay 0.
CREATE TABLE IF NOT EXISTS game_power_play_stats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    INTEGER NOT NULL REFERENCES games(id),
    team_id    INTEGER NOT NULL REFERENCES teams(id),
    player_id  INTEGER NOT NULL REFERENCES players(id),
    -- Defense (nickel) line.
    pp_deploys        INTEGER DEFAULT 0,  -- windows this player started as nickel
    pp_outs           INTEGER DEFAULT 0,  -- outs his deployment windows covered
    pp_start_outs     TEXT DEFAULT '',    -- CSV of the team-out number each window opened at (e.g. "14,25")
    pp_xbh_held       INTEGER DEFAULT 0,  -- XBH cut to singles while he patrolled
    pp_hits_converted INTEGER DEFAULT 0,  -- shallow hits run down for outs
    nickel_po         INTEGER DEFAULT 0,  -- putouts recorded as the nickel
    nickel_a          INTEGER DEFAULT 0,  -- assists recorded as the nickel
    nickel_e          INTEGER DEFAULT 0,  -- errors as the nickel
    -- Short-handed offense line.
    sh_pa      INTEGER DEFAULT 0,
    sh_ab      INTEGER DEFAULT 0,
    sh_hits    INTEGER DEFAULT 0,
    -- Power Play PITCHING — the pitcher with the nickel deployed behind him.
    -- K/BB are defense-independent (they never reach the extra fielder); BIP
    -- outcomes and the saves reflect the loaded defense. ppp_tot_* span the
    -- WHOLE game (window or not) so the BABIP split (with-nickel vs without) is
    -- derivable.
    ppp_bf        INTEGER DEFAULT 0,  -- batters faced while the nickel was deployed
    ppp_outs      INTEGER DEFAULT 0,  -- outs recorded during those windows
    ppp_k         INTEGER DEFAULT 0,  -- strikeouts during windows (his own)
    ppp_bb        INTEGER DEFAULT 0,  -- walks during windows (his own)
    ppp_bip       INTEGER DEFAULT 0,  -- balls in play during windows
    ppp_bip_hits  INTEGER DEFAULT 0,  -- hits on those balls in play (BABIP numerator)
    ppp_tot_bip      INTEGER DEFAULT 0,  -- total balls in play all game
    ppp_tot_bip_hits INTEGER DEFAULT 0,  -- total hits on balls in play all game
    ppp_hits_saved INTEGER DEFAULT 0, -- singles the nickel ran down behind him
    ppp_xbh_saved  INTEGER DEFAULT 0, -- extra-base hits the nickel held behind him
    UNIQUE(player_id, game_id)
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

-- Per-team, per-season franchise honors. Captured at season archive (while
-- the playoff bracket still exists — playoff_series is wiped at rollover) so
-- the team page can show a banner of accolades with the years they were won.
-- Division titles and the overall champion are also reconstructable from
-- season_standings / seasons for already-archived seasons (see
-- season_archive.backfill_team_honors); pennants and wild-card berths are not,
-- so for pre-existing seasons those stay 0.
CREATE TABLE IF NOT EXISTS season_team_honors (
    season_id       INTEGER NOT NULL REFERENCES seasons(id),
    season_number   INTEGER,
    year            INTEGER,
    team_id         INTEGER,           -- stable franchise id (NULL if unresolved)
    team_abbrev     TEXT,
    league          TEXT,
    division        TEXT,
    division_title  INTEGER DEFAULT 0, -- finished 1st in its division
    wild_card       INTEGER DEFAULT 0, -- made the playoff field as a non-winner
    league_champion INTEGER DEFAULT 0, -- won its league final ('championship')
    series_champion INTEGER DEFAULT 0, -- overall champion (World Series / lone league)
    PRIMARY KEY (season_id, team_abbrev)
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
    -- Park-adjusted league-relative offense + win probability.
    -- Added in the GSc-normalization batch; legacy archive rows have
    -- 100 / 0 / 0 defaults so display code doesn't need to NULL-check.
    wrc_plus    REAL DEFAULT 100,
    wpa         REAL DEFAULT 0,
    li_avg      REAL DEFAULT 0,
    -- OPS+ (OPS relative to league, 100 = avg). Built from box-score OPS,
    -- so unlike wRC+ (wOBA weights from game_pa_log) it stays correct in
    -- fast-sim archives.
    ops_plus    REAL DEFAULT 100,
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
    -- Park-adjusted + z-score normalized pitching + win probability.
    -- Same defaults convention as the batting variant.
    wera_plus   REAL DEFAULT 100,
    gsc_index   REAL DEFAULT 100,
    wpa         REAL DEFAULT 0,
    li_avg      REAL DEFAULT 0,
    -- Outs-based DIPS rates (IP = outs/3). Correct even in fast-sim
    -- archives, where game_pa_log (and thus WPA / wOBA weights) is absent.
    fip_dips    REAL DEFAULT 0,   -- true FIP on the ERA scale
    kbb         REAL DEFAULT 0,   -- strikeout-to-walk ratio
    whip_v      REAL DEFAULT 0,   -- (H + BB) / IP
    k9          REAL DEFAULT 0,   -- strikeouts per 9 IP
    PRIMARY KEY (season_id, category, rank)
);

-- Full per-player season lines, persisted at archive time so career
-- (multi-season) leaderboards can aggregate by a stable player_id. Unlike
-- season_*_leaders (top-10 by name only), this stores every player who
-- recorded a PA / out, keyed by the player row that persists across
-- history-mode seasons. Raw game stats are wiped on each season reset, so
-- these snapshots are the only durable per-player record.
CREATE TABLE IF NOT EXISTS season_player_batting (
    season_id   INTEGER NOT NULL REFERENCES seasons(id),
    player_id   INTEGER NOT NULL,
    player_name TEXT,
    team_abbrev TEXT,
    league      TEXT,
    g           INTEGER DEFAULT 0,
    pa          INTEGER DEFAULT 0,
    ab          INTEGER DEFAULT 0,
    r           INTEGER DEFAULT 0,
    h           INTEGER DEFAULT 0,
    doubles     INTEGER DEFAULT 0,
    triples     INTEGER DEFAULT 0,
    hr          INTEGER DEFAULT 0,
    rbi         INTEGER DEFAULT 0,
    bb          INTEGER DEFAULT 0,
    k           INTEGER DEFAULT 0,
    sb          INTEGER DEFAULT 0,
    hbp         INTEGER DEFAULT 0,
    -- RISP component sums + bunting (so career rates/totals aggregate across
    -- seasons; rates are recomputed on read, never averaged).
    risp_pa     INTEGER DEFAULT 0,
    risp_ab     INTEGER DEFAULT 0,
    risp_h      INTEGER DEFAULT 0,
    risp_2b     INTEGER DEFAULT 0,
    risp_3b     INTEGER DEFAULT 0,
    risp_hr     INTEGER DEFAULT 0,
    risp_bb     INTEGER DEFAULT 0,
    risp_hbp    INTEGER DEFAULT 0,
    risp_rbi    INTEGER DEFAULT 0,
    sh          INTEGER DEFAULT 0,
    bunt_att    INTEGER DEFAULT 0,
    bunt_hits   INTEGER DEFAULT 0,
    sqz         INTEGER DEFAULT 0,
    sqz_rbi     INTEGER DEFAULT 0,
    PRIMARY KEY (season_id, player_id)
);

CREATE TABLE IF NOT EXISTS season_player_pitching (
    season_id   INTEGER NOT NULL REFERENCES seasons(id),
    player_id   INTEGER NOT NULL,
    player_name TEXT,
    team_abbrev TEXT,
    league      TEXT,
    g           INTEGER DEFAULT 0,
    gs          INTEGER DEFAULT 0,
    w           INTEGER DEFAULT 0,
    l           INTEGER DEFAULT 0,
    outs        INTEGER DEFAULT 0,
    h           INTEGER DEFAULT 0,
    r           INTEGER DEFAULT 0,
    er          INTEGER DEFAULT 0,
    bb          INTEGER DEFAULT 0,
    k           INTEGER DEFAULT 0,
    hr          INTEGER DEFAULT 0,
    -- Relief / finisher component sums (career IR-Stop% / LRA recomputed on
    -- read; terminal_outs / quality_finish are career counting totals).
    ir_inherited   INTEGER DEFAULT 0,
    ir_scored      INTEGER DEFAULT 0,
    terminal_outs  INTEGER DEFAULT 0,
    quality_finish INTEGER DEFAULT 0,
    lead_entries   INTEGER DEFAULT 0,
    lead_held      INTEGER DEFAULT 0,
    PRIMARY KEY (season_id, player_id)
);

-- round"). Created in waves as each round's pairings are determined.
-- best_of is the series length; high_wins/low_wins track the standings;
-- winner_team_id is set once one side hits ceil(best_of / 2) wins.
CREATE TABLE IF NOT EXISTS playoff_series (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    season              INTEGER DEFAULT 1,
    round_idx           INTEGER NOT NULL,        -- 0 = first round, increases toward final
    rounds_to_final     INTEGER NOT NULL,        -- 0 = the final itself (within its bracket)
    bracket_position    INTEGER NOT NULL,        -- pairing slot within the round
    -- Which league's bracket this series belongs to. Each league runs its own
    -- bracket; the interleague final (World Series) carries league = '' (no
    -- single league owns it). See o27v2/playoffs.py.
    league              TEXT    DEFAULT '',
    -- Round flavour for display + series-length lookup:
    -- 'wild_card' | 'division' | 'championship' (league final) | 'world_series'.
    series_kind         TEXT    DEFAULT '',
    high_seed           INTEGER NOT NULL,        -- numeric seed (1..N) within its league
    low_seed            INTEGER,                 -- NULL when bye
    high_seed_team_id   INTEGER NOT NULL REFERENCES teams(id),
    low_seed_team_id    INTEGER          REFERENCES teams(id),  -- NULL on bye
    best_of             INTEGER NOT NULL,        -- 3, 5, 7, 9
    high_wins           INTEGER DEFAULT 0,
    low_wins            INTEGER DEFAULT 0,
    winner_team_id      INTEGER REFERENCES teams(id),
    started_at          TEXT,
    ended_at            TEXT
);

-- Phase E: snapshot of the per-season transactions log (auction signs,
-- FA signings, college sign-throughs, manual assigns, post-auction
-- reconciliation trades, in-season trades). Mirrors the live
-- `transactions` table — keyed to seasons.id so it survives the
-- offseason wipe. Player + team identity denormalised so the row
-- stays meaningful after roster shuffles. Filled by
-- season_archive._snapshot_transactions at archive time.
CREATE TABLE IF NOT EXISTS season_transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id     INTEGER NOT NULL REFERENCES seasons(id),
    game_date     TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    team_id       INTEGER REFERENCES teams(id),
    team_abbrev   TEXT,
    player_id     INTEGER REFERENCES players(id),
    player_name   TEXT,
    detail        TEXT NOT NULL DEFAULT ''
);

-- Phase E: snapshot of the per-season auction lot ledger. Mirrors
-- auction_results — keyed to seasons.id. Denormalises winner /
-- traded-to abbrev + player name so an archived season's auction
-- page still renders after roster/team churn.
CREATE TABLE IF NOT EXISTS season_auction_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id           INTEGER NOT NULL REFERENCES seasons(id),
    lot_order           INTEGER,
    player_id           INTEGER REFERENCES players(id),
    player_name         TEXT,
    player_position     TEXT,
    player_overall      INTEGER,
    winner_team_id      INTEGER REFERENCES teams(id),
    winner_abbrev       TEXT,
    winning_bid         INTEGER,
    second_bid          INTEGER,
    price               INTEGER,
    traded_to_team_id   INTEGER REFERENCES teams(id),
    traded_to_abbrev    TEXT,
    trade_price         INTEGER
);

-- Phase 4: regular-season + WS-MVP awards. One row per (category, season)
-- so a fresh league can re-award without colliding with prior seasons'
-- archived rows. Player ID denormalised to name/abbrev so the row
-- survives roster wipes between seasons.
CREATE TABLE IF NOT EXISTS season_awards (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    season        INTEGER DEFAULT 1,
    category      TEXT NOT NULL,        -- mvp / cy_young / roy / ws_mvp
    league        TEXT,                 -- AL / NL / MLB / "" — for split awards
    player_id     INTEGER REFERENCES players(id),
    player_name   TEXT,
    team_abbrev   TEXT,
    headline_stat TEXT,                 -- one-line stat blurb for the UI
    awarded_at    TEXT
);

-- BBWAA-style per-voter ballots. Each award has N synthetic voters; each
-- voter submits a top-10 ranked ballot. Winner is the player with the
-- highest BBWAA-weighted point total (1st=14, 2nd=9, 3rd=8, …, 10th=1).
-- The 1st-place row gets a parallel `season_awards` insert for back-compat
-- with code/templates that only look at the single winner.
CREATE TABLE IF NOT EXISTS award_ballots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    season        INTEGER NOT NULL,
    category      TEXT    NOT NULL,    -- mvp / cy_young / roy / ws_mvp
    voter_id      INTEGER NOT NULL,    -- synthetic voter index, 1..N
    rank          INTEGER NOT NULL,    -- 1..10
    player_id     INTEGER REFERENCES players(id),
    player_name   TEXT,
    team_abbrev   TEXT,
    headline_stat TEXT,
    UNIQUE(season, category, voter_id, rank)
);
CREATE INDEX IF NOT EXISTS idx_ballots_season_cat
    ON award_ballots(season, category);

-- Hall of Fame (Task: player-hall-of-fame).
--
-- Per-game stats are wiped at every offseason rollover
-- (_reset_for_next_history_season) and only the top-10 leader rows survive,
-- so there is no surviving source of full career totals. player_career_lines
-- fixes that: archive_current_season snapshots EVERY qualified player's full
-- season batting + pitching line here BEFORE the per-game stats are cleared.
-- Career totals = SUM over this table; black/gray ink, awards, rings, and
-- sustained-excellence all derive from it plus the season_* archive tables.
--
-- These three tables are tied to live player ids (stable across a single
-- continuous franchise but meaningless after a reseed), so unlike the
-- season_* archive tables they ARE dropped by drop_all() — a fresh universe
-- starts the Hall over. _reset_for_next_history_season does NOT touch them,
-- so they accumulate across the carry-forward season lineage.
CREATE TABLE IF NOT EXISTS player_career_lines (
    season_id     INTEGER NOT NULL REFERENCES seasons(id),
    season_number INTEGER,
    year          INTEGER,
    player_id     INTEGER NOT NULL,
    player_name   TEXT,
    team_abbrev   TEXT,
    is_pitcher    INTEGER DEFAULT 0,
    position      TEXT DEFAULT '',
    age           INTEGER,
    -- batting line
    g     INTEGER DEFAULT 0,
    pa    INTEGER DEFAULT 0,
    ab    INTEGER DEFAULT 0,
    h     INTEGER DEFAULT 0,
    d2    INTEGER DEFAULT 0,
    d3    INTEGER DEFAULT 0,
    hr    INTEGER DEFAULT 0,
    r     INTEGER DEFAULT 0,
    rbi   INTEGER DEFAULT 0,
    bb    INTEGER DEFAULT 0,
    k     INTEGER DEFAULT 0,
    sb    INTEGER DEFAULT 0,
    avg       REAL DEFAULT 0,
    obp       REAL DEFAULT 0,
    slg       REAL DEFAULT 0,
    ops       REAL DEFAULT 0,
    wrc_plus  REAL DEFAULT 100,
    -- pitching line
    p_g    INTEGER DEFAULT 0,
    w      INTEGER DEFAULT 0,
    l      INTEGER DEFAULT 0,
    outs   INTEGER DEFAULT 0,
    er     INTEGER DEFAULT 0,
    p_k    INTEGER DEFAULT 0,
    p_bb   INTEGER DEFAULT 0,
    p_h    INTEGER DEFAULT 0,
    wera       REAL DEFAULT 0,
    whip       REAL DEFAULT 0,
    wera_plus  REAL DEFAULT 100,
    PRIMARY KEY (season_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_career_lines_player
    ON player_career_lines(player_id);

-- League Hall of Fame — gated, automatic (LPGA-style points threshold +
-- longevity/age eligibility). One row per enshrined player.
CREATE TABLE IF NOT EXISTS hof_inductees (
    player_id              INTEGER PRIMARY KEY,
    player_name            TEXT,
    primary_team_abbrev    TEXT,
    is_pitcher             INTEGER DEFAULT 0,
    position               TEXT DEFAULT '',
    inducted_season_number INTEGER,
    inducted_year          INTEGER,
    hof_points             REAL DEFAULT 0,
    seasons_played         INTEGER DEFAULT 0,
    career_summary         TEXT DEFAULT '',
    inducted_at            TEXT
);

-- Team Halls of Fame — a lower, franchise-scoped bar. Players land here
-- either by meeting the team criteria automatically (method='criteria') or
-- by a manual induction from the team HOF page (method='manual'). A player
-- can be in several team halls (e.g. a franchise legend traded late).
CREATE TABLE IF NOT EXISTS team_hof_inductees (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id                INTEGER,
    team_abbrev            TEXT,
    player_id              INTEGER,
    player_name            TEXT,
    is_pitcher             INTEGER DEFAULT 0,
    position               TEXT DEFAULT '',
    inducted_season_number INTEGER,
    inducted_year          INTEGER,
    team_points            REAL DEFAULT 0,
    seasons_with_team      INTEGER DEFAULT 0,
    method                 TEXT DEFAULT 'criteria',
    career_summary         TEXT DEFAULT '',
    inducted_at            TEXT,
    UNIQUE(team_id, player_id)
);

-- ── Performance indexes ───────────────────────────────────────────────────
-- The per-game stat tables and the games table are read constantly (Scores,
-- Standings, Leaders, team & player pages) and grow without bound as seasons
-- accumulate. Without these, those reads are full table scans that get slower
-- every season. CREATE INDEX IF NOT EXISTS runs idempotently inside init_db's
-- executescript(SCHEMA), so existing live DBs pick them up on next boot.
CREATE INDEX IF NOT EXISTS idx_bstats_player ON game_batter_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_bstats_game   ON game_batter_stats(game_id);
CREATE INDEX IF NOT EXISTS idx_bstats_team   ON game_batter_stats(team_id);
CREATE INDEX IF NOT EXISTS idx_pstats_player ON game_pitcher_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_pstats_game   ON game_pitcher_stats(game_id);
CREATE INDEX IF NOT EXISTS idx_pstats_team   ON game_pitcher_stats(team_id);
-- Composite to support _PSTATS_DEDUP_SQL's ROW_NUMBER() OVER
-- (PARTITION BY game_id, player_id ORDER BY outs_recorded DESC). That dedup is
-- the hottest pitching query (Leaders, Scores, baselines all wrap it); this
-- lets SQLite read in partition order instead of scanning + sorting the whole
-- table. The single biggest lever for the 25s Leaders page.
CREATE INDEX IF NOT EXISTS idx_pstats_dedup ON game_pitcher_stats(game_id, player_id, outs_recorded DESC);
CREATE INDEX IF NOT EXISTS idx_games_date      ON games(game_date);
CREATE INDEX IF NOT EXISTS idx_games_played    ON games(played, game_date);
CREATE INDEX IF NOT EXISTS idx_games_home_date ON games(home_team_id, game_date);
CREATE INDEX IF NOT EXISTS idx_games_away_date ON games(away_team_id, game_date);
CREATE INDEX IF NOT EXISTS idx_players_team ON players(team_id);
"""


def _wipe_if_stale() -> None:
    """
    Detect a genuinely pre-Phase-8 database and wipe it so seed_league() can
    reseed with the current schema.

    Signal: a populated players table where NO player carries an archetype.
    Pre-Phase-8 rosters predate the archetype column (added as '' by the
    ALTER migration above), whereas every modern seed classifies its batters
    (and jokers) with non-empty archetypes. A DB with players but zero
    archetypes is therefore stale.

    NOTE: the previous signal (blank pitcher_role) is no longer valid —
    Task #65 stopped persisting pitcher_role entirely (roles are derived live
    at game time), so it is '' for every pitcher in a healthy modern league.
    Using it here caused init_db() — which runs on every server boot — to
    drop_all() perfectly good leagues, i.e. "data disappears on its own".

    A fresh empty DB (tables don't exist yet, or no players) is left alone.
    """
    try:
        total = fetchone("SELECT COUNT(*) AS n FROM players")
        if not total or total["n"] == 0:
            return
        archetyped = fetchone(
            "SELECT COUNT(*) AS n FROM players WHERE COALESCE(archetype, '') != ''"
        )
        if archetyped and archetyped["n"] == 0:
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
    db_dir = os.path.dirname(_resolve_path())
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    # WAL mode is a persistent, on-disk setting — set it once here. It lets
    # readers and the writer proceed without blocking each other and makes
    # commits cheap (paired with synchronous=NORMAL in get_conn). Big win
    # for bulk simulation, which commits per game across many connections.
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")

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
        # Canonical pitching-roles column (see o27v2/rotation.py). Starter
        # rotation order; 0 for relievers / legacy rows.
        rotation_int = [("rotation_slot", "0")]

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
        for col, defval in phase9_int + task65_int + rotation_int:
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass

        # Phase 4: playoff hookup on games. Older DBs need the columns
        # added without losing data; new DBs get them via SCHEMA below.
        for col, sql_type, defval in [
            ("series_id",  "INTEGER", "NULL"),
            ("is_playoff", "INTEGER", "0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE games ADD COLUMN {col} {sql_type} DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass

        # Per-league playoff brackets: each league runs its own bracket and the
        # interleague final (World Series) is a separate series. Older DBs that
        # predate the per-league model gain the columns without losing data; the
        # CREATE TABLE below carries them for fresh DBs.
        for col, sql_type, defval in [
            ("league",      "TEXT", "''"),
            ("series_kind", "TEXT", "''"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE playoff_series ADD COLUMN {col} {sql_type} DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass

        # Denormalized is_playoff on the per-game stat tables so regular-season
        # aggregations (which mostly don't join games) keep postseason out of
        # the totals. Add the column, then backfill it from games for any rows
        # written before the column existed. Idempotent: the UPDATE only touches
        # rows whose flag disagrees with their game.
        for tbl in ("game_batter_stats", "game_pitcher_stats"):
            try:
                conn.execute(
                    f"ALTER TABLE {tbl} ADD COLUMN is_playoff INTEGER NOT NULL DEFAULT 0")
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(
                    f"""UPDATE {tbl}
                        SET is_playoff = COALESCE(
                            (SELECT g.is_playoff FROM games g WHERE g.id = {tbl}.game_id), 0)
                        WHERE is_playoff IS NOT COALESCE(
                            (SELECT g.is_playoff FROM games g WHERE g.id = {tbl}.game_id), 0)""")
                conn.commit()
            except Exception:
                pass

        # Weather/start-time: exact rolled °F + first-pitch clock time on
        # `games`. Older DBs gain the columns without losing data.
        for col, sql_type, defval in [
            ("temperature_f",    "INTEGER", "NULL"),
            ("start_minute",     "INTEGER", "NULL"),
            ("start_utc_offset", "INTEGER", "NULL"),
            ("low_light",        "INTEGER", "0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE games ADD COLUMN {col} {sql_type} DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass

        # Declared Seconds: per-game decision telemetry on `games`.
        # Idempotent — re-running init_db on a migrated DB is a no-op.
        for col, sql_type, defval in [
            ("home_bats_first",             "INTEGER", "NULL"),
            ("away_declared_at",            "INTEGER", "NULL"),
            ("home_declared_at",            "INTEGER", "NULL"),
            ("away_seconds_used",           "INTEGER", "0"),
            ("home_seconds_used",           "INTEGER", "0"),
            ("away_declare_context",        "TEXT",    "NULL"),
            ("home_declare_context",        "TEXT",    "NULL"),
            ("away_declare_score_for",      "INTEGER", "NULL"),
            ("away_declare_score_against",  "INTEGER", "NULL"),
            ("home_declare_score_for",      "INTEGER", "NULL"),
            ("home_declare_score_against",  "INTEGER", "NULL"),
            ("seconds_outcome",             "TEXT",    "NULL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE games ADD COLUMN {col} {sql_type} DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass

        # Phase 5e: work-ethic / work-habits / habit-cup columns on
        # players. Idempotent.
        for col, sql_type, defval in [
            ("work_ethic",  "INTEGER", "50"),
            ("work_habits", "INTEGER", "50"),
            ("habit_cup",   "REAL",    "0.5"),
        ]:
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} {sql_type} DEFAULT {defval}")
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

        # Country code: ISO 3166-1 alpha-2 (e.g. "IN", "PK", "MY"). Drives
        # the flag emoji rendered next to the player's name in roster /
        # player / box-score views. Empty default keeps pre-migration
        # rosters rendering as flag-less without breaking the templates.
        try:
            conn.execute("ALTER TABLE players ADD COLUMN country TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass

        # Player-card flavor: hometown (birthplace city), birthday (cosmetic
        # "Mar 14" — no year, age is the engine's clock), and a secondary
        # nationality code for dual-eligible players. Empty defaults keep
        # legacy rows rendering cleanly.
        for col in ("hometown", "birthday", "secondary_country"):
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} TEXT DEFAULT ''")
                conn.commit()
            except Exception:
                pass

        # Home-market coordinates — drives nearest-city weather + division
        # placement. NULL default so legacy rows fall back to name lookup.
        for col in ("lat", "lon"):
            try:
                conn.execute(f"ALTER TABLE teams ADD COLUMN {col} REAL")
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
        # Distinctive ballpark name (generated at seed time). Empty
        # default keeps legacy rows working — the UI falls back to
        # "<city> ballpark" when missing.
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN park_name TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN park_dimensions TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass
        for col in ("park_shape", "park_quirks"):
            try:
                conn.execute(f"ALTER TABLE teams ADD COLUMN {col} TEXT DEFAULT ''")
                conn.commit()
            except Exception:
                pass
        # Manager name (rolled at seed time using the league's regional
        # name picker). Empty default keeps legacy rows working — the UI
        # falls back to "(unknown skipper)" when missing.
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN manager_name TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass

        # Org-strength: 20-95 scout-grade team attribute that drives the
        # additive shift applied to every player attribute roll for the
        # team. Persisted on the team so it can be displayed, sorted on,
        # and consumed by future draft / signing logic — the team_shift
        # is no longer a hidden Gaussian rolled at seed time.
        # Default 50 = league-average org (no shift).
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN org_strength INTEGER DEFAULT 50")
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
                    "mgr_run_game", "mgr_bench_usage",
                    "mgr_declare_aggression", "mgr_bat_first_pref",
                    "mgr_flip_aggression"):
            try:
                conn.execute(f"ALTER TABLE teams ADD COLUMN {col} REAL DEFAULT 0.5")
                conn.commit()
            except Exception:
                pass

        # Front-office persona columns (see o27v2/front_office.py). Drives
        # trade behavior; re-rolled on reseed; drifts year-over-year via
        # development.run_offseason -> front_office.drift_fo_strategies.
        for col, sql_type, defval in (
            ("fo_strategy",        "TEXT",    "'balanced'"),
            ("fo_archetype_bias",  "TEXT",    "''"),
            ("fo_last_trade_date", "TEXT",    "''"),
        ):
            try:
                conn.execute(
                    f"ALTER TABLE teams ADD COLUMN {col} {sql_type} DEFAULT {defval}"
                )
                conn.commit()
            except Exception:
                pass
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN fo_aggression REAL DEFAULT 0.5")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN fo_losing_streak INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN style_profile TEXT DEFAULT ''")
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

        # Persisted salary in guilders. 0 = unpopulated; valuation.py
        # falls back to on-the-fly estimation in that case. Backfill via
        # `python o27v2/manage.py backfill_salaries`.
        try:
            conn.execute("ALTER TABLE players ADD COLUMN salary INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass

        # Bunt rating (bat control). New column → seed existing rosters from
        # contact + speed so legacy players bunt sensibly. The UPDATE runs only
        # when the ALTER just succeeded (a fresh add); on re-runs the ALTER
        # raises and we skip, preserving any hand-set values.
        try:
            conn.execute("ALTER TABLE players ADD COLUMN bunt INTEGER DEFAULT 50")
            conn.commit()
            conn.execute(
                "UPDATE players SET bunt = CAST(ROUND("
                "0.6 * COALESCE(contact, 50) + 0.4 * COALESCE(speed, 50)"
                ") AS INTEGER)"
            )
            conn.commit()
        except Exception:
            pass

        # Defensive-shift layer: per-batter spray rating + per-manager
        # shift-aggression tendency. Defaults of 0.5 keep legacy rosters
        # shift-immune (neutral spray = never shifted; neutral manager
        # combined with neutral batter = zero shift probability).
        try:
            conn.execute("ALTER TABLE players ADD COLUMN pull_pct REAL DEFAULT 0.5")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN mgr_shift_aggression REAL DEFAULT 0.5")
            conn.commit()
        except Exception:
            pass
        # Intentional-walk persona dimension — added with the joker decay
        # / IBB rebalance pass. Legacy rows default to 0.5 (neutral).
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN mgr_ibb_aggression REAL DEFAULT 0.5")
            conn.commit()
        except Exception:
            pass
        # Power Play (optional rule) — per-league opt-in set at league
        # creation. Stamped onto every team in a league whose creator ticked
        # the box, read by sim.py into state.power_play_enabled per game.
        # Legacy rows default to 0 (rule off), so existing leagues are
        # byte-for-byte unchanged.
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN power_play_enabled INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        # Cricket Batting Order (optional rule) — per-league opt-in set at
        # league creation (or flipped on an existing league via /league/edit).
        # Stamped onto every team in the league, read by sim.py into
        # team.cricket_order_enabled per game. Legacy rows default to 0 (rule
        # off), so existing leagues are byte-for-byte unchanged.
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN cricket_order_enabled INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        # Power Play PITCHING columns — added to game_power_play_stats after the
        # initial defense/offense version of the table. No-op on fresh DBs (the
        # CREATE TABLE already has them) and on DBs without the table yet.
        for col in ("ppp_bf", "ppp_outs", "ppp_k", "ppp_bb", "ppp_bip",
                    "ppp_bip_hits", "ppp_tot_bip", "ppp_tot_bip_hits",
                    "ppp_hits_saved", "ppp_xbh_saved"):
            try:
                conn.execute(
                    f"ALTER TABLE game_power_play_stats ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass
        # Per-window start-out (the team-out number each nickel window opened
        # at) — added so the box-score Powerplays note can say WHEN it deployed.
        try:
            conn.execute(
                "ALTER TABLE game_power_play_stats ADD COLUMN pp_start_outs TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass
        # Adaptability — batter rating that erodes shift effectiveness
        # when the manager keeps the same alignment for multiple consecutive
        # ABs against this batter. 20-80 scale like other ratings.
        try:
            conn.execute("ALTER TABLE players ADD COLUMN adaptability INTEGER DEFAULT 50")
            conn.commit()
        except Exception:
            pass
        # Leadership — batter mental rating. Stacks with grit in the
        # RISP-pressure bonus so high-mental bench guys can still tip
        # high-leverage ABs even without elite hard skills. Independent
        # 20-80 roll at seed time.
        try:
            conn.execute("ALTER TABLE players ADD COLUMN leadership INTEGER DEFAULT 50")
            conn.commit()
        except Exception:
            pass
        # Per-game shift telemetry (defense's outs gained / hits lost via
        # the shift call). Aggregates roll up per-team-season at query time.
        for col in (
            "home_shift_outs_added", "home_shift_hits_lost",
            "away_shift_outs_added", "away_shift_hits_lost",
        ):
            try:
                conn.execute(f"ALTER TABLE games ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass

        # Defense layer columns. Defaults of 50 = neutral.
        # Per-position sub-ratings (infield / outfield / catcher) let a
        # player be a true specialist (elite at one group, replacement
        # elsewhere) or a legit utility guy (decent across groups).
        for col in ("defense", "arm", "defense_infield",
                    "defense_outfield", "defense_catcher", "game_calling"):
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT 50")
                conn.commit()
            except Exception:
                pass

        # Counting-stat columns persisted post-realism (Stage 1 of stats expansion).
        # Defaults of 0 leave pre-existing rows neutral; new games populate fully.
        for col in ("hbp", "sb", "cs", "fo", "multi_hit_abs",
                    "sh", "bunt_att", "bunt_hits", "sqz", "sqz_rbi",
                    "stay_rbi", "stay_hits", "c2_strand_out",
                    "c2_op_1b", "c2_adv_1b", "c2_op_2b", "c2_adv_2b", "c2_op_3b", "c2_adv_3b",
                    "adv_op_1b", "adv_adv_1b", "adv_op_2b", "adv_adv_2b",
                    "adv_op_3b", "adv_adv_3b",
                    "rad_1b", "rad_2b", "rad_3b",
                    "risp_pa", "risp_ab", "risp_h", "risp_2b", "risp_3b",
                    "risp_hr", "risp_bb", "risp_hbp", "risp_rbi", "walkback_runs"):
            try:
                conn.execute(f"ALTER TABLE game_batter_stats ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass
        try:
            conn.execute("ALTER TABLE game_batter_stats ADD COLUMN game_position TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE game_batter_stats ADD COLUMN entry_type TEXT DEFAULT 'starter'")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE game_batter_stats ADD COLUMN replaced_player_id INTEGER DEFAULT NULL")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE game_batter_stats ADD COLUMN entered_inning INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE game_batter_stats ADD COLUMN entered_outs INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
        for col in ("gidp", "gitp"):
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
        # Plus is_starter for GS.
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

        # Times-through-the-order buckets (1st / 2nd / 3rd+ look). Powers
        # K%-by-look splits + the Deception decay stat. Default 0 on legacy
        # rows (familiarity stats simply read empty for pre-migration games).
        _tto_cols = (
            "k_tto1",  "k_tto2",  "k_tto3",
            "fo_tto1", "fo_tto2", "fo_tto3",
            "bf_tto1", "bf_tto2", "bf_tto3",
        )
        for col in _tto_cols:
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

        # Substitution-economy role tags (see o27v2/archetypes.py and the
        # substitution_economy AAR). Stamped at generation, re-derived on
        # off-season development. `roster_slot` is the deployment slot
        # (bat_first / glove_first / two_way / pitcher / joker /
        # pr_specialist / ph_specialist). `role_field_pos` is the comma-
        # joined list of positions a player can defend (e.g., "2B,SS,3B").
        # Defaults are NULL/empty so legacy rows fall through to "any
        # deployment" semantics rather than being silently excluded.
        for col, sql_type, defval in (
            ("roster_slot",    "TEXT",    "''"),
            ("role_hit",       "INTEGER", "1"),
            ("role_run",       "INTEGER", "0"),
            ("role_two_way",   "INTEGER", "1"),
            ("role_field_pos", "TEXT",    "''"),
        ):
            try:
                conn.execute(
                    f"ALTER TABLE players ADD COLUMN {col} {sql_type} DEFAULT {defval}"
                )
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

        # GSc-normalization batch: park-adjusted + z-score-normalized
        # stats persisted into season archive. Idempotent — silently
        # no-ops on already-migrated DBs.
        for (table, col, default) in (
            ("season_batting_leaders",  "wrc_plus",   100),
            ("season_batting_leaders",  "wpa",        0),
            ("season_batting_leaders",  "li_avg",     0),
            ("season_batting_leaders",  "ops_plus",   100),
            ("season_pitching_leaders", "wera_plus",  100),
            ("season_pitching_leaders", "gsc_index",  100),
            ("season_pitching_leaders", "wpa",        0),
            ("season_pitching_leaders", "li_avg",     0),
            # Robust, outs-based (IP = outs/3) rate stats that stay correct
            # in fast-sim archives — surfaced in place of the pa_log-derived
            # WPA leaderboard, which is dead when detail="lite".
            ("season_pitching_leaders", "fip_dips",   0),
            ("season_pitching_leaders", "kbb",        0),
            ("season_pitching_leaders", "whip_v",     0),
            ("season_pitching_leaders", "k9",         0),
        ):
            try:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col} REAL DEFAULT {default}"
                )
                conn.commit()
            except Exception:
                pass
        # Task #62: add year column to existing seasons table.
        try:
            conn.execute("ALTER TABLE seasons ADD COLUMN year INTEGER")
            conn.commit()
        except Exception:
            pass

        # SABR analytics: per-event game-state stamps on game_pa_log
        # (outs / bases-mask / score-diff before & after). Idempotent —
        # silently no-op if columns already exist or table absent.
        for col in ("outs_before", "bases_before", "score_diff_before",
                    "outs_after",  "bases_after",  "score_diff_after"):
            try:
                conn.execute(f"ALTER TABLE game_pa_log ADD COLUMN {col} INTEGER DEFAULT NULL")
                conn.commit()
            except Exception:
                pass

        # Pitch-type activation: repertoire JSON on players, pitch_type on
        # game_pa_log, per-game pitch-mix + hit-shape on game_pitcher_stats,
        # assists on game_batter_stats.
        for col, sql_type, defval in (
            ("repertoire",     "TEXT",    "NULL"),
            ("release_angle",  "REAL",    "0.5"),
            ("pitch_variance", "REAL",    "0.0"),
            ("grit",           "REAL",    "0.5"),
        ):
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} {sql_type} DEFAULT {defval}")
                conn.commit()
            except Exception:
                pass
        try:
            conn.execute("ALTER TABLE game_pa_log ADD COLUMN pitch_type TEXT DEFAULT NULL")
            conn.commit()
        except Exception:
            pass
        # Batted-ball physics hybrid layer (EV / LA / spray). NULL on
        # non-BIP events and legacy rows.
        for col in ("exit_velocity", "launch_angle", "spray_angle"):
            try:
                conn.execute(f"ALTER TABLE game_pa_log ADD COLUMN {col} REAL DEFAULT NULL")
                conn.commit()
            except Exception:
                pass
        # Per-event fielder attribution (engine-credited fielder on outs).
        try:
            conn.execute("ALTER TABLE game_pa_log ADD COLUMN fielder_id INTEGER DEFAULT NULL")
            conn.commit()
        except Exception:
            pass
        # Ball-strike count at the moment the ball was put in play (pre-contact).
        # Powers outcome-by-count analysis. NULL on legacy rows.
        for col in ("balls", "strikes"):
            try:
                conn.execute(f"ALTER TABLE game_pa_log ADD COLUMN {col} INTEGER DEFAULT NULL")
                conn.commit()
            except Exception:
                pass
        for col in ("singles_allowed", "doubles_allowed", "triples_allowed"):
            try:
                conn.execute(f"ALTER TABLE game_pitcher_stats ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass
        for col in ("fastball_pct", "breaking_pct", "offspeed_pct"):
            try:
                conn.execute(f"ALTER TABLE game_pitcher_stats ADD COLUMN {col} REAL DEFAULT 0.0")
                conn.commit()
            except Exception:
                pass
        try:
            conn.execute("ALTER TABLE game_pitcher_stats ADD COLUMN primary_pitch TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            pass
        # Walk-Back rule columns — added when the rule landed. Historical
        # rows get 0 (no Walk-Back PAs existed before the rule).
        for col in ("wb_faced", "wb_runs", "ir_inherited", "ir_scored",
                    "terminal_outs", "quality_finish", "lead_entries", "lead_held"):
            try:
                conn.execute(f"ALTER TABLE game_pitcher_stats ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass
        # Cross-season snapshot tables — RISP / bunting / relief-finisher
        # component columns so career (multi-season) leaderboards can aggregate
        # them. Existing archives backfill to 0 (no data before this).
        for col in ("risp_pa", "risp_ab", "risp_h", "risp_2b", "risp_3b",
                    "risp_hr", "risp_bb", "risp_hbp", "risp_rbi",
                    "sh", "bunt_att", "bunt_hits", "sqz", "sqz_rbi"):
            try:
                conn.execute(f"ALTER TABLE season_player_batting ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass
        for col in ("ir_inherited", "ir_scored", "terminal_outs",
                    "quality_finish", "lead_entries", "lead_held"):
            try:
                conn.execute(f"ALTER TABLE season_player_pitching ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass
        try:
            conn.execute("ALTER TABLE game_batter_stats ADD COLUMN a INTEGER DEFAULT 0")
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

        # Performance streaks (see o27v2/streaks.py). Per-player hot/cold
        # streak that ramps over weeks like an illness — slow to start, then
        # accelerating — and reverts to the player's true rating when it ends.
        #   streak_state : -1 cold / 0 none / +1 hot
        #   streak_weeks : completed weekly ramp ticks (drives the magnitude)
        #   streak_games : games logged in the current week (ticks a week at 6)
        #   streak_heat  : rolling performance signal in [-1, 1] that ignites
        #                  or breaks a streak (good play pushes +, bad pushes -)
        for col in ("streak_state", "streak_weeks", "streak_games"):
            try:
                conn.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass
        try:
            conn.execute("ALTER TABLE players ADD COLUMN streak_heat REAL DEFAULT 0.0")
            conn.commit()
        except Exception:
            pass
        # Team-wide streak overlay — a club catching fire (or going cold)
        # together, lighter than the per-player swing. Same column semantics.
        for col in ("streak_state", "streak_weeks", "streak_games"):
            try:
                conn.execute(f"ALTER TABLE teams ADD COLUMN {col} INTEGER DEFAULT 0")
                conn.commit()
            except Exception:
                pass
        try:
            conn.execute("ALTER TABLE teams ADD COLUMN streak_heat REAL DEFAULT 0.0")
            conn.commit()
        except Exception:
            pass

    # Step 2: wipe stale pre-Phase-8 data
    _wipe_if_stale()

    # Step 3: (re)create any missing tables
    with get_conn() as conn:
        conn.executescript(SCHEMA)

    # Step 4: one-shot backfill — recompute batter PA on rows generated
    # before the renderer's PA semantics were corrected. The old code
    # counted every contact event (including intermediate stays) as a
    # separate PA; the corrected definition matches MLB
    # (PA == AB + BB + HBP). Newly-generated rows already satisfy this
    # identity, so the UPDATE is a no-op on those. Idempotent.
    try:
        with get_conn() as conn:
            n_bad = conn.execute(
                "SELECT COUNT(*) FROM game_batter_stats "
                "WHERE pa != COALESCE(ab,0) + COALESCE(bb,0) + COALESCE(hbp,0)"
            ).fetchone()[0]
            if n_bad:
                conn.execute(
                    "UPDATE game_batter_stats "
                    "SET pa = COALESCE(ab,0) + COALESCE(bb,0) + COALESCE(hbp,0) "
                    "WHERE pa != COALESCE(ab,0) + COALESCE(bb,0) + COALESCE(hbp,0)"
                )
                conn.commit()
    except Exception:
        pass  # game_batter_stats may not exist on a fresh DB

    # Fill any missing team coordinates from the city gazetteer. The root
    # cause behind "GMT" first-pitch labels was teams with no lat/lon;
    # filling them here also sharpens weather and sunset-based low-light.
    try:
        fill_missing_team_coords()
    except Exception:
        pass  # teams table may be absent on a fresh DB

    # One-time backfill: heal first-pitch time-zone offsets stamped before
    # the gazetteer fallback existed. Teams without coordinates defaulted to
    # UTC+0 ("GMT"); recompute each game's offset from its home park's
    # location (longitude, or the city gazetteer). Touches only the zone
    # label — never the rolled start time or any result.
    try:
        with get_conn() as conn:
            done = conn.execute(
                "SELECT value FROM sim_meta WHERE key = 'start_tz_backfilled'"
            ).fetchone()
            if not done:
                from o27.engine.gametime import utc_offset_for
                rows = conn.execute(
                    "SELECT g.id AS id, t.city AS city, t.lon AS lon "
                    "FROM games g JOIN teams t ON g.home_team_id = t.id "
                    "WHERE g.start_minute IS NOT NULL"
                ).fetchall()
                for r in rows:
                    off = utc_offset_for(r["city"] or "", r["lon"])
                    conn.execute(
                        "UPDATE games SET start_utc_offset = ? WHERE id = ?",
                        (off, r["id"]),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO sim_meta (key, value) "
                    "VALUES ('start_tz_backfilled', '1')"
                )
                conn.commit()
    except Exception:
        pass  # games / teams / sim_meta may be absent on a fresh DB

    # Joker identity backfill. MLB-league jokers are tagged by
    # roster_slot='joker' but were historically inserted with is_joker=0
    # (the players INSERT omitted the column), while youth/college jokers
    # set is_joker=1. Box-score display ('J' / trailing group),
    # trade-eligibility exclusion, and other consumers key on is_joker, so
    # normalize the flag from the authoritative role tag. Idempotent
    # (only flips 0→1) and cheap.
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE players SET is_joker = 1 "
                "WHERE roster_slot = 'joker' AND COALESCE(is_joker, 0) = 0"
            )
            conn.commit()
    except Exception:
        pass  # players / roster_slot may be absent on a very old DB


def fill_missing_team_coords() -> int:
    """Fill lat/lon for teams that have none, from the weather city
    gazetteer (matched by city name). Coordinates drive weather archetype
    resolution, sunset-based low-light, and first-pitch time zones — so a
    team with a recognizable city but no coordinates gets all three for
    free. Idempotent: only rows missing a coordinate are touched, and a
    city the gazetteer doesn't know is left alone. Returns count filled.
    """
    try:
        from o27.engine.weather import coords_for_city
    except Exception:
        return 0
    n = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, city FROM teams WHERE lat IS NULL OR lon IS NULL"
        ).fetchall()
        for r in rows:
            c = coords_for_city(r["city"] or "")
            if c is None:
                continue
            conn.execute("UPDATE teams SET lat = ?, lon = ? WHERE id = ?",
                         (c[0], c[1], r["id"]))
            n += 1
        if n:
            conn.commit()
    return n


def drop_all() -> None:
    """Drop all tables (for re-seeding)."""
    with get_conn() as conn:
        # FKs off for the duration of the drop: auction_*, and any other
        # module-owned tables that reference teams/players, would otherwise
        # raise FOREIGN KEY constraint failed and abort the reset
        # mid-script — which leaves the DB in a half-dropped state where
        # every subsequent request 500s with "no such table: sim_meta".
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.executescript("""
                DROP TABLE IF EXISTS auction_lot_bids;
                DROP TABLE IF EXISTS auction_results;
                DROP TABLE IF EXISTS auction_keepers;
                DROP TABLE IF EXISTS transactions;
                DROP TABLE IF EXISTS game_pa_log;
                DROP TABLE IF EXISTS game_bunt_log;
                DROP TABLE IF EXISTS game_pitcher_stats;
                DROP TABLE IF EXISTS game_batter_stats;
                DROP TABLE IF EXISTS team_phase_outs;
                DROP TABLE IF EXISTS sim_meta;
                DROP TABLE IF EXISTS award_ballots;
                DROP TABLE IF EXISTS season_awards;
                DROP TABLE IF EXISTS team_hof_inductees;
                DROP TABLE IF EXISTS hof_inductees;
                DROP TABLE IF EXISTS player_career_lines;
                DROP TABLE IF EXISTS games;
                DROP TABLE IF EXISTS playoff_series;
                DROP TABLE IF EXISTS players;
                DROP TABLE IF EXISTS teams;
            """)
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys = ON")


def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def fetchone(sql: str, params: tuple = ()) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def child_tables_of(parent: str) -> list[str]:
    """Tables that hold a FOREIGN KEY referencing ``parent``.

    Used to delete child rows before the parent so FK enforcement (which the
    app turns on) doesn't reject the parent delete. Discovered dynamically from
    the schema so a newly-added per-game child table is handled automatically
    — a hardcoded list is exactly what let `seed_schedule` ship an incomplete
    wipe and crash on a populated save.
    """
    with get_conn() as conn:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        out = []
        for t in tables:
            for fk in conn.execute(f"PRAGMA foreign_key_list({t})"):
                if fk["table"] == parent:
                    out.append(t)
                    break
    return out


def execute(sql: str, params: tuple = ()) -> int:
    """Execute a DML statement; returns lastrowid."""
    def _run() -> int:
        with get_conn() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid
    return _retry_on_locked(_run)


def executemany(sql: str, param_list: list[tuple]) -> None:
    def _run() -> None:
        with get_conn() as conn:
            conn.executemany(sql, param_list)
            conn.commit()
    _retry_on_locked(_run)
