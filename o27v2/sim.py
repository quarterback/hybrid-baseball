"""
Game simulation for O27v2.

simulate_game(game_id) runs a complete O27 game for the given DB game_id,
stores results back to the database, and returns a result summary dict.

Phase 9 additions:
  - Active roster filtering: injured players are excluded from the lineup.
  - Post-game injury draws fire after each game.
  - Trade deadline and in-season trade checks fire after each game.
  - Waiver claims fire when bullpen drops below threshold.
  - All roster moves are logged to the transactions table.
"""
from __future__ import annotations
import random
import sys
import os

_workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

from o27.engine.state import GameState, Team, Player
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer

from o27v2 import db
import o27v2.config as v2cfg
from o27v2 import scout as _scout


# ---------------------------------------------------------------------------
# DB ↔ engine type converters
# ---------------------------------------------------------------------------

def _db_team_to_engine(
    team_row: dict,
    players: list[dict],
    team_role: str,
    rotation_index: int = 0,
    recently_used_pitcher_ids: set[int] | None = None,
) -> Team:
    """
    Convert a DB team row + player rows into an O27 engine Team object.

    Task #65 changes:
      - roster:  ALL active healthy players (34: 12 fielders + 3 DH + 19 P).
      - lineup:  8 starting fielders + today's SP + 3 DH = 12 batters.
      - Today's SP is the highest-Stamina active arm that did NOT pitch in
        the last few sim days (`recently_used_pitcher_ids`). No rotation
        index is used — role is derived live from current attributes so
        aging arms naturally drift out of the rotation.

    `recently_used_pitcher_ids` is a set of DB player_ids who appeared in
    the previous ~4 sim days; supply via the caller. None / empty is
    treated as "everyone is rested".

    `rotation_index` is accepted for back-compat with older callers but
    is no longer consulted.
    """
    _ = rotation_index  # unused — kept for back-compat
    rest_excluded = recently_used_pitcher_ids or set()

    engine_players: list[Player] = []
    fielders: list[Player] = []
    pitchers: list[Player] = []
    dhs:      list[Player] = []
    jokers:   list[Player] = []

    # Map engine player_id (str) → original DB id (int) so we can apply
    # the rest filter.
    engine_to_db_id: dict[str, int] = {}

    for p in players:
        home_bonus = (
            v2cfg.HOME_ADVANTAGE_SKILL
            if team_role == "home"
            else 0.0
        )
        stamina_grade = p.get("stamina")
        if stamina_grade is None or stamina_grade == 0:
            stamina_grade = p.get("pitcher_skill", 50)
        player = Player(
            player_id=str(p["id"]),
            name=p["name"],
            is_pitcher=bool(p["is_pitcher"]),
            skill=_scout.to_unit(p["skill"]) + home_bonus,
            speed=_scout.to_unit(p["speed"]),
            pitcher_skill=_scout.to_unit(p["pitcher_skill"]),
            stamina=_scout.to_unit(stamina_grade),
            stay_aggressiveness=float(p["stay_aggressiveness"]),
            contact_quality_threshold=float(p["contact_quality_threshold"]),
            archetype=str(p.get("archetype") or ""),
            pitcher_role=str(p.get("pitcher_role") or ""),
            hard_contact_delta=float(p.get("hard_contact_delta") or 0.0),
            hr_weight_bonus=float(p.get("hr_weight_bonus") or 0.0),
            # Realism layer — defaults of 50 / 'R' produce engine-identical
            # behavior on legacy DB rows that predate these columns.
            contact=_scout.to_unit(p.get("contact") or 50),
            power=_scout.to_unit(p.get("power") or 50),
            eye=_scout.to_unit(p.get("eye") or 50),
            command=_scout.to_unit(p.get("command") or 50),
            movement=_scout.to_unit(p.get("movement") or 50),
            # Legacy DB rows pre-realism return '' / 'R' from the column
            # default; only treat seeded 'L'/'R'/'S' as platoon-applicable.
            bats=str(p.get("bats") or ""),
            throws=str(p.get("throws") or ""),
        )
        engine_players.append(player)
        engine_to_db_id[player.player_id] = int(p["id"])
        if bool(p.get("is_joker")):
            jokers.append(player)
        elif player.is_pitcher:
            pitchers.append(player)
        elif p.get("position") == "DH":
            dhs.append(player)
        else:
            fielders.append(player)

    # ---- Pick today's SP: highest Stamina among rested active arms ----
    todays_sp: list[Player] = []
    if pitchers:
        rested = [
            p for p in pitchers
            if engine_to_db_id.get(p.player_id) not in rest_excluded
        ]
        # Fall back to the full pitcher pool if every arm is "tired".
        candidate_pool = rested or pitchers
        sp = max(candidate_pool, key=lambda pl: pl.stamina)
        todays_sp = [sp]

    # Cap fielders at the canonical 8 starting positions; remaining ones
    # are bench depth that lives in the roster but does not bat. Cap DHs
    # at 3 batting slots so the lineup stays at 12 (8 fielders + SP + 3 DH)
    # for engine compatibility.
    starting_fielders = fielders[:8]
    starting_dhs      = dhs[:3]

    lineup = list(starting_fielders) + todays_sp + list(starting_dhs)

    return Team(
        team_id=team_role,
        name=team_row["name"],
        roster=engine_players,
        lineup=lineup,
        jokers_available=list(jokers),
        park_hr=float(team_row.get("park_hr") or 1.0),
        park_hits=float(team_row.get("park_hits") or 1.0),
    )


# ---------------------------------------------------------------------------
# Stat extraction from Renderer
# ---------------------------------------------------------------------------

def _extract_batter_stats(renderer: Renderer, team_id: int, players: list[dict]) -> list[dict]:
    """Extract per-phase batter stats from the Renderer's per-phase snapshots.

    Task #58: yields one row per (player, phase) tuple, where phase 0 is
    regulation and phase N >= 1 is super-inning round N. Players with no
    activity in a phase are omitted.
    """
    team_player_ids: set[int] = {p["id"] for p in players}
    rows: list[dict] = []
    phases = renderer.phases_seen()
    if not phases:
        # Engine never called end_phase (legacy code path) — fall back to
        # writing the cumulative stats as a single phase-0 row so older
        # tests / callers still get something.
        phases = [0]
        per_phase = {0: dict(renderer._batter_stats)}
    else:
        per_phase = {p: renderer.batter_stats_for_phase(p) for p in phases}

    for phase in phases:
        for engine_pid, bstat in per_phase.get(phase, {}).items():
            try:
                db_pid = int(engine_pid)
            except (ValueError, TypeError):
                continue
            if db_pid not in team_player_ids:
                continue
            rows.append({
                "team_id": team_id,
                "player_id": db_pid,
                "phase": phase,
                "pa": bstat.pa,
                "ab": bstat.ab,
                "runs": bstat.runs,
                "hits": bstat.hits,
                "doubles": bstat.doubles,
                "triples": bstat.triples,
                "hr": bstat.hr,
                "rbi": bstat.rbi,
                "bb": bstat.bb,
                "k": bstat.k,
                "stays": bstat.sty,
                "outs_recorded": bstat.outs_recorded,
            })
    return rows


def _extract_pitcher_stats(state: GameState, team_id: int, players: list[dict]) -> list[dict]:
    """Extract per-phase pitcher stats from state.spell_log for DB insertion.

    Task #58: each SpellRecord already carries super_inning_number, so a
    pitcher who pitched in regulation AND in SI round 1 produces TWO
    rows (phase=0 and phase=1) instead of one combined row.
    """
    from o27.stats.pitcher import PitcherStats
    team_player_ids: set[int] = {p["id"] for p in players}

    # Group spells by (pitcher_id, phase).
    by_phase_pid: dict[tuple[str, int], list] = {}
    for rec in state.spell_log:
        phase = int(getattr(rec, "super_inning_number", 0) or 0)
        by_phase_pid.setdefault((rec.pitcher_id, phase), []).append(rec)

    rows: list[dict] = []
    for (pid_str, phase), spells in by_phase_pid.items():
        try:
            db_pid = int(pid_str)
        except (ValueError, TypeError):
            continue
        if db_pid not in team_player_ids:
            continue
        player = (state.visitors.get_player(pid_str) or
                  state.home.get_player(pid_str))
        if player is None:
            continue
        ps = PitcherStats.from_spell_log(spells, pid_str, player.name)
        rows.append({
            "team_id": team_id,
            "player_id": db_pid,
            "phase": phase,
            "batters_faced": ps.batters_faced,
            "outs_recorded": ps.outs_recorded,
            "hits_allowed": ps.hits_allowed,
            "runs_allowed": ps.runs_allowed,
            # Task #48: ER = runs_allowed - passed-ball-charged unearned runs.
            "er": max(0, ps.runs_allowed - getattr(ps, "unearned_runs", 0)),
            "bb": ps.bb,
            "k": ps.k,
            "hr_allowed": ps.hr_allowed,
            "pitches": ps.pitches_thrown,
        })
    return rows


def _compute_team_phase_outs(
    away_bstats: list[dict],
    home_bstats: list[dict],
    away_pstats: list[dict],
    home_pstats: list[dict],
    home_team_id: int,
    away_team_id: int,
) -> list[dict]:
    """Per (team, phase): unattributed outs = team_outs - sum(batter_outs).

    team_outs come from the OPPOSING side's pitcher rows (a pitcher's
    outs_recorded counts the outs the batting team made against him).
    The batter side's outs_recorded reflects only outs the engine could
    charge to a specific batter (CS / FC / pickoff handled). The
    difference is logged so the box-score Game Notes section can show
    "X outs unattributed" instead of silently dropping rows or padding
    a fake "[Caught Stealing/FC]" patch row.
    """
    def _per_phase_outs(rows: list[dict], key: str = "outs_recorded") -> dict[int, int]:
        out: dict[int, int] = {}
        for r in rows:
            out[r["phase"]] = out.get(r["phase"], 0) + (r[key] or 0)
        return out

    away_batter_outs  = _per_phase_outs(away_bstats)
    home_batter_outs  = _per_phase_outs(home_bstats)
    away_pitcher_outs = _per_phase_outs(away_pstats)  # outs vs HOME
    home_pitcher_outs = _per_phase_outs(home_pstats)  # outs vs AWAY

    rows: list[dict] = []
    all_phases = set(away_batter_outs) | set(home_batter_outs) | \
                 set(away_pitcher_outs) | set(home_pitcher_outs)
    for phase in sorted(all_phases):
        # Outs by the AWAY team's batters = total outs HOME's pitchers recorded.
        away_team_total_outs = home_pitcher_outs.get(phase, 0)
        home_team_total_outs = away_pitcher_outs.get(phase, 0)
        away_unattr = max(0, away_team_total_outs - away_batter_outs.get(phase, 0))
        home_unattr = max(0, home_team_total_outs - home_batter_outs.get(phase, 0))
        if away_unattr:
            rows.append({"team_id": away_team_id, "phase": phase,
                         "unattributed_outs": away_unattr})
        if home_unattr:
            rows.append({"team_id": home_team_id, "phase": phase,
                         "unattributed_outs": home_unattr})
    return rows


# ---------------------------------------------------------------------------
# Active roster helpers (Phase 9)
# ---------------------------------------------------------------------------

def _get_active_players(team_id: int, game_date: str) -> list[dict]:
    """Return today's playable roster: healthy is_active=1 players, topped
    up from the reserve pool (is_active=0) when injuries thin out a slot.

    Reserve promotion is ephemeral — the DB flags are not flipped, the
    reserves are only added to today's lineup pool.
    """
    from o27v2.injuries import get_active_players
    return get_active_players(team_id, game_date)


def _recently_used_pitcher_ids(
    team_id: int, game_date: str, days_back: int = 4
) -> set[int]:
    """DB ids of pitchers who appeared for `team_id` in the last
    `days_back` sim days (used to keep today's SP rested)."""
    rows = db.fetchall(
        """SELECT DISTINCT ps.player_id
             FROM game_pitcher_stats ps
             JOIN games g ON g.id = ps.game_id
            WHERE ps.team_id = ?
              AND g.played = 1
              AND g.game_date >= date(?, ?)
              AND g.game_date <  ?""",
        (team_id, game_date, f"-{days_back} days", game_date),
    )
    return {int(r["player_id"]) for r in rows}


def _promote_pitcher_role(players: list[dict]) -> list[dict]:
    """Task #65: roles are derived live (no `pitcher_role` is read), so
    this helper only needs to guarantee at least one `is_pitcher` arm
    exists. If the active staff is somehow empty (legacy/edge case),
    promote the highest-pitcher_skill non-joker as an emergency starter.
    """
    has_pitcher = any(p.get("is_pitcher") for p in players)
    if has_pitcher:
        return players
    pool = [p for p in players if not p.get("is_joker")]
    if not pool:
        return players
    best = max(pool, key=lambda p: float(p.get("pitcher_skill", 0.0)))
    return [dict(p, is_pitcher=1) if p["id"] == best["id"] else p
            for p in players]


# ---------------------------------------------------------------------------
# Main simulation entry point
# ---------------------------------------------------------------------------

def simulate_game(game_id: int, seed: int | None = None) -> dict:
    """
    Run an O27 game for the given DB game_id.

    - Loads active (healthy) players only (Phase 9).
    - Runs the O27 probabilistic engine.
    - Stores score, winner, and per-player stats back to DB.
    - Fires post-game injury draws, deadline trade checks, and waiver claims.
    - Returns a summary dict.
    """
    game = db.fetchone("SELECT * FROM games WHERE id = ?", (game_id,))
    if game is None:
        raise ValueError(f"Game {game_id} not found")
    if game["played"]:
        raise ValueError(f"Game {game_id} has already been played")

    game_date    = game["game_date"]
    home_team_id = game["home_team_id"]
    away_team_id = game["away_team_id"]

    home_row  = db.fetchone("SELECT * FROM teams WHERE id = ?", (home_team_id,))
    away_row  = db.fetchone("SELECT * FROM teams WHERE id = ?", (away_team_id,))

    # Phase 9: use active (non-injured) roster
    home_players = _promote_pitcher_role(_get_active_players(home_team_id, game_date))
    away_players = _promote_pitcher_role(_get_active_players(away_team_id, game_date))

    if seed is None:
        seed = random.randint(0, 999_999)

    rng = random.Random(seed)

    # Phase 10: rotate starting pitcher per game. Use game_id so the
    # rotation is deterministic and each SP gets a near-equal share of
    # the season's starts (162 / 4 ≈ 40 starts per SP per team).
    # Task #65: today's SP is picked live by Stamina + rest, so the only
    # rotation state we need is the set of arms that pitched recently.
    home_rest = _recently_used_pitcher_ids(home_team_id, game_date)
    away_rest = _recently_used_pitcher_ids(away_team_id, game_date)

    visitors_team = _db_team_to_engine(
        away_row, away_players, "visitors",
        recently_used_pitcher_ids=away_rest,
    )
    home_team = _db_team_to_engine(
        home_row, home_players, "home",
        recently_used_pitcher_ids=home_rest,
    )

    state = GameState(visitors=visitors_team, home=home_team)
    state.current_pitcher_id = _find_pitcher_id(home_team)

    renderer = Renderer()
    provider = ProbabilisticProvider(rng)

    final_state, _log = run_game(state, provider, renderer)

    away_score = final_state.score["visitors"]
    home_score = final_state.score["home"]
    winner_team_id: int | None = None
    if final_state.winner == "visitors":
        winner_team_id = away_team_id
    elif final_state.winner == "home":
        winner_team_id = home_team_id

    # ----------------------------------------------------------------
    # Phase 10: extract stats BEFORE marking the game played, so a
    # mid-flow exception leaves the game retryable instead of orphaning
    # it as played-with-no-stats. This was the root cause of the ~1108
    # missing-stats games observed in the previous full sim.
    # ----------------------------------------------------------------
    all_home_players = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? ORDER BY id", (home_team_id,)
    )
    all_away_players = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? ORDER BY id", (away_team_id,)
    )
    away_bstats = _extract_batter_stats(renderer, away_team_id, all_away_players)
    home_bstats = _extract_batter_stats(renderer, home_team_id, all_home_players)
    away_pstats = _extract_pitcher_stats(final_state, away_team_id, all_away_players)
    home_pstats = _extract_pitcher_stats(final_state, home_team_id, all_home_players)
    team_phase_outs = _compute_team_phase_outs(
        away_bstats, home_bstats, away_pstats, home_pstats,
        home_team_id, away_team_id,
    )

    # Atomic write: game row + team W/L + per-player stats in one txn.
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE games SET home_score=?, away_score=?, winner_id=?,
               super_inning=?, played=1, seed=? WHERE id=?""",
            (home_score, away_score, winner_team_id,
             final_state.super_inning_number, seed, game_id),
        )
        if winner_team_id is not None:
            loser_id = away_team_id if winner_team_id == home_team_id else home_team_id
            conn.execute("UPDATE teams SET wins = wins + 1 WHERE id = ?", (winner_team_id,))
            conn.execute("UPDATE teams SET losses = losses + 1 WHERE id = ?", (loser_id,))
        # Inline inserts inside the same connection so it's all one txn.
        # Task #58: writes per-phase rows; outs_recorded is now included
        # for batters (the prior inline INSERT silently dropped it, which
        # is why every historical batter row has OR=0).
        for r in away_bstats + home_bstats:
            conn.execute(
                """INSERT INTO game_batter_stats
                   (game_id, team_id, player_id, phase, pa, ab, runs, hits,
                    doubles, triples, hr, rbi, bb, k, stays, outs_recorded)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (game_id, r["team_id"], r["player_id"], r["phase"],
                 r["pa"], r["ab"], r["runs"], r["hits"], r["doubles"],
                 r["triples"], r["hr"], r["rbi"], r["bb"], r["k"],
                 r["stays"], r.get("outs_recorded", 0)),
            )
        for r in away_pstats + home_pstats:
            conn.execute(
                """INSERT INTO game_pitcher_stats
                   (game_id, team_id, player_id, phase, batters_faced,
                    outs_recorded, hits_allowed, runs_allowed, er, bb, k,
                    hr_allowed, pitches)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (game_id, r["team_id"], r["player_id"], r["phase"],
                 r["batters_faced"], r["outs_recorded"], r["hits_allowed"],
                 r["runs_allowed"], r.get("er", r["runs_allowed"]),
                 r["bb"], r["k"],
                 r.get("hr_allowed", 0), r.get("pitches", 0)),
            )
        for r in team_phase_outs:
            conn.execute(
                """INSERT OR REPLACE INTO team_phase_outs
                   (game_id, team_id, phase, unattributed_outs)
                   VALUES (?,?,?,?)""",
                (game_id, r["team_id"], r["phase"], r["unattributed_outs"]),
            )
        conn.commit()

    # -----------------------------------------------------------------------
    # Phase 9: Post-game injury draws + transaction logging
    # -----------------------------------------------------------------------
    _post_game_roster_processing(game_id, game_date, home_team_id, away_team_id, rng, seed)

    return {
        "game_id": game_id,
        "away_team": away_row["name"],
        "home_team": home_row["name"],
        "away_score": away_score,
        "home_score": home_score,
        "winner": final_state.winner,
        "super_inning": final_state.super_inning_number,
        "seed": seed,
    }


def _post_game_roster_processing(
    game_id: int,
    game_date: str,
    home_team_id: int,
    away_team_id: int,
    rng: random.Random,
    seed: int,
) -> None:
    """
    Run all Phase 9 post-game roster events:
      1. Process player returns (expired injuries).
      2. Draw new injuries for players in this game.
      3. Check for waiver claims (depleted bullpen).
      4. Check deadline / in-season trade triggers.
    All events are logged to the transactions table.
    """
    from o27v2.injuries import process_returns, process_post_game_injuries, check_waiver_claims
    from o27v2.trades import check_deadline_and_trades
    from o27v2.transactions import log_many

    games_played = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")
    n_played = games_played["n"] if games_played else 0
    season = 1

    all_events: list[dict] = []

    # Player returns
    all_events.extend(process_returns(game_date))

    # Injury draws
    inj_rng = random.Random(seed + game_id * 31337)
    all_events.extend(
        process_post_game_injuries(game_id, game_date, home_team_id, away_team_id, inj_rng)
    )

    # Waiver claims
    all_events.extend(check_waiver_claims(game_date))

    # Trades (deadline or in-season)
    all_events.extend(check_deadline_and_trades(game_date, n_played))

    log_many(season, game_date, all_events)


def _find_pitcher_id(team: Team) -> str | None:
    """Phase 10: return the player_id of today's starter (in the lineup)."""
    # Today's SP is the lone pitcher in the batting lineup (slot 9).
    for p in team.lineup:
        if p.is_pitcher and p.pitcher_role in ("starter", "workhorse"):
            return p.player_id
    for p in team.lineup:
        if p.is_pitcher:
            return p.player_id
    # Fallback to roster (should not happen with Phase 10 setup)
    for p in team.roster:
        if p.is_pitcher:
            return p.player_id
    return team.roster[0].player_id if team.roster else None


def _insert_batter_stats(game_id: int, rows: list[dict]) -> None:
    if not rows:
        return
    db.executemany(
        """INSERT INTO game_batter_stats
           (game_id, team_id, player_id, pa, ab, runs, hits, doubles, triples,
            hr, rbi, bb, k, stays, outs_recorded)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(game_id, r["team_id"], r["player_id"], r["pa"], r["ab"], r["runs"],
          r["hits"], r["doubles"], r["triples"], r["hr"], r["rbi"],
          r["bb"], r["k"], r["stays"], r.get("outs_recorded", 0))
         for r in rows],
    )


def _insert_pitcher_stats(game_id: int, rows: list[dict]) -> None:
    if not rows:
        return
    db.executemany(
        """INSERT INTO game_pitcher_stats
           (game_id, team_id, player_id, batters_faced, outs_recorded,
            hits_allowed, runs_allowed, er, bb, k, hr_allowed, pitches)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(game_id, r["team_id"], r["player_id"], r["batters_faced"],
          r["outs_recorded"], r["hits_allowed"], r["runs_allowed"],
          r.get("er", r["runs_allowed"]),
          r["bb"], r["k"], r.get("hr_allowed", 0), r.get("pitches", 0))
         for r in rows],
    )


# ---------------------------------------------------------------------------
# Batch simulation helper
# ---------------------------------------------------------------------------

def simulate_next_n(n: int = 10, seed_base: int | None = None) -> list[dict]:
    """
    Simulate the next N unplayed games in schedule order.
    Returns list of result dicts.
    """
    games = db.fetchall(
        "SELECT id FROM games WHERE played = 0 ORDER BY game_date, id LIMIT ?", (n,)
    )
    results = []
    for i, g in enumerate(games):
        seed = None if seed_base is None else seed_base + i
        try:
            r = simulate_game(g["id"], seed=seed)
            results.append(r)
        except Exception as e:
            results.append({"game_id": g["id"], "error": str(e)})
    return results


# ---------------------------------------------------------------------------
# Date-based simulation helpers
# ---------------------------------------------------------------------------

import datetime as _dt


def get_first_scheduled_date() -> str | None:
    row = db.fetchone("SELECT MIN(game_date) as d FROM games")
    return row["d"] if row and row["d"] else None


def get_last_scheduled_date() -> str | None:
    row = db.fetchone("SELECT MAX(game_date) as d FROM games")
    return row["d"] if row and row["d"] else None


def get_earliest_unplayed_date() -> str | None:
    row = db.fetchone("SELECT MIN(game_date) as d FROM games WHERE played = 0")
    return row["d"] if row and row["d"] else None


def get_current_sim_date() -> str | None:
    """The simulator's calendar clock. Persists in sim_meta so the user can step through
    off-days via Sim Today, while staying anchored to the next unplayed game by default."""
    row = db.fetchone("SELECT value FROM sim_meta WHERE key = 'sim_date'")
    stored = row["value"] if row and row["value"] else None
    if stored is None:
        # Lazy-init: prefer the next unplayed date so existing leagues with progress
        # show the right date. If the league is already fully played, seed to
        # last_scheduled_date + 1 so is_season_complete() returns true. Final fallback
        # is the schedule's first day for a brand-new (unplayed) schedule.
        earliest = get_earliest_unplayed_date()
        if earliest is not None:
            seed = earliest
        else:
            last = get_last_scheduled_date()
            if last is not None:
                seed = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat()
            else:
                first = get_first_scheduled_date()
                if first is None:
                    return None
                seed = first
        db.execute(
            "INSERT OR REPLACE INTO sim_meta (key, value) VALUES ('sim_date', ?)",
            (seed,),
        )
        return seed
    return stored


def resync_sim_clock() -> str | None:
    """Bump the clock forward to the earliest unplayed date if it has fallen behind
    (e.g. games were simulated via legacy /api/sim or single-game endpoints).
    Never moves the clock backward, and never moves it past last_scheduled_date+1."""
    current = get_current_sim_date()
    earliest = get_earliest_unplayed_date()
    if earliest is None:
        # Season complete — push clock past the last game so is_season_complete() is true.
        last = get_last_scheduled_date()
        if last is not None:
            advance_sim_clock((_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat())
        return get_current_sim_date()
    if current is None or earliest > current:
        set_sim_date(earliest)
    return get_current_sim_date()


def set_sim_date(date: str | None) -> None:
    if date is None:
        db.execute("DELETE FROM sim_meta WHERE key = 'sim_date'")
    else:
        db.execute(
            "INSERT OR REPLACE INTO sim_meta (key, value) VALUES ('sim_date', ?)",
            (date,),
        )


def is_season_complete() -> bool:
    # Authoritative: no unplayed games left.
    if get_earliest_unplayed_date() is None:
        return True
    current = get_current_sim_date()
    last = get_last_scheduled_date()
    if current is None or last is None:
        return True
    return current > last


def get_all_star_date() -> str | None:
    """Midpoint of the schedule (derived from the games table — no DB column needed)."""
    first = get_first_scheduled_date()
    last = get_last_scheduled_date()
    if not first or not last:
        return None
    f = _dt.date.fromisoformat(first)
    l = _dt.date.fromisoformat(last)
    return (f + _dt.timedelta(days=(l - f).days // 2)).isoformat()


SIM_PER_REQUEST_GAME_CAP = 3000


def simulate_date(date: str, seed_base: int | None = None, max_games: int = SIM_PER_REQUEST_GAME_CAP) -> list[dict]:
    """Simulate every unplayed game whose game_date == `date`. Does NOT touch the clock."""
    games = db.fetchall(
        "SELECT id FROM games WHERE played = 0 AND game_date = ? ORDER BY id LIMIT ?",
        (date, max_games),
    )
    results = []
    for i, g in enumerate(games):
        seed = None if seed_base is None else seed_base + i
        try:
            results.append(simulate_game(g["id"], seed=seed))
        except Exception as e:
            results.append({"game_id": g["id"], "error": str(e)})
    return results


def simulate_through(target_date: str, seed_base: int | None = None, max_games: int = SIM_PER_REQUEST_GAME_CAP) -> list[dict]:
    """Simulate every unplayed game with game_date <= `target_date`. Does NOT touch the clock."""
    games = db.fetchall(
        "SELECT id FROM games WHERE played = 0 AND game_date <= ? ORDER BY game_date, id LIMIT ?",
        (target_date, max_games),
    )
    results = []
    for i, g in enumerate(games):
        seed = None if seed_base is None else seed_base + i
        try:
            results.append(simulate_game(g["id"], seed=seed))
        except Exception as e:
            results.append({"game_id": g["id"], "error": str(e)})
    return results


def advance_sim_clock(new_date: str) -> None:
    """Move the sim clock forward to `new_date` (never backward). Caller computes target."""
    current = get_current_sim_date()
    if current is None or new_date > current:
        set_sim_date(new_date)
