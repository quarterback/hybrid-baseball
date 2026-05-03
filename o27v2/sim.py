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
) -> Team:
    """
    Convert a DB team row + player rows into an O27 engine Team object.

    Phase 10:
      - roster:  ALL players (8 fielders + 4 SP + 4 RP + 3 jokers).
      - lineup:  8 fielders + today's starting pitcher + 3 jokers (12 total).
        Today's starter is chosen by `rotation_index % n_starters`. The other
        3 SPs and all 4 RPs are bullpen-only and do NOT bat.
      - jokers_available: the 3 jokers (joker insertion is engine-managed).

    team_role: "visitors" | "home"
    rotation_index: integer used to pick today's SP from the rotation.
    """
    engine_players: list[Player] = []
    fielders: list[Player] = []
    starters: list[Player] = []
    relievers: list[Player] = []
    other_pitchers: list[Player] = []  # legacy "workhorse"/"committee" DBs
    jokers: list[Player] = []

    for p in players:
        home_bonus = (
            v2cfg.HOME_ADVANTAGE_SKILL
            if team_role == "home"
            else 0.0
        )
        player = Player(
            player_id=str(p["id"]),
            name=p["name"],
            is_pitcher=bool(p["is_pitcher"]),
            skill=_scout.to_unit(p["skill"]) + home_bonus,
            speed=_scout.to_unit(p["speed"]),
            pitcher_skill=_scout.to_unit(p["pitcher_skill"]),
            stay_aggressiveness=float(p["stay_aggressiveness"]),
            contact_quality_threshold=float(p["contact_quality_threshold"]),
            archetype=str(p.get("archetype") or ""),
            pitcher_role=str(p.get("pitcher_role") or ""),
            hard_contact_delta=float(p.get("hard_contact_delta") or 0.0),
            hr_weight_bonus=float(p.get("hr_weight_bonus") or 0.0),
        )
        engine_players.append(player)
        # Engine Player has no is_joker field; consult the source dict instead.
        # (v2 dropped jokers in Phase 10 — list stays empty in current builds.)
        if bool(p.get("is_joker")):
            jokers.append(player)
        elif player.is_pitcher:
            role = player.pitcher_role
            if role == "starter":
                starters.append(player)
            elif role == "reliever":
                relievers.append(player)
            else:
                other_pitchers.append(player)
        else:
            fielders.append(player)

    # ---- Build the 12-player batting lineup ----
    # 8 fielders (or however many exist) + today's SP + jokers.
    todays_sp: list[Player] = []
    if starters:
        idx = rotation_index % len(starters)
        todays_sp = [starters[idx]]
    elif other_pitchers:
        # Legacy DB fallback (workhorse/committee players)
        todays_sp = [other_pitchers[0]]

    lineup = list(fielders) + todays_sp + list(jokers)

    # Roster includes everyone (so pick_new_pitcher can see all relievers)
    return Team(
        team_id=team_role,
        name=team_row["name"],
        roster=engine_players,
        lineup=lineup,
        jokers_available=list(jokers),
    )


# ---------------------------------------------------------------------------
# Stat extraction from Renderer
# ---------------------------------------------------------------------------

def _extract_batter_stats(renderer: Renderer, team_id: int, players: list[dict]) -> list[dict]:
    """Extract per-batter stats from Renderer._batter_stats for DB insertion."""
    team_player_ids: set[int] = {p["id"] for p in players}
    rows: list[dict] = []
    for engine_pid, bstat in renderer._batter_stats.items():
        try:
            db_pid = int(engine_pid)
        except (ValueError, TypeError):
            continue
        if db_pid not in team_player_ids:
            continue
        rows.append({
            "team_id": team_id,
            "player_id": db_pid,
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
    """Extract pitcher stats from spell_log for DB insertion."""
    from o27.stats.pitcher import PitcherStats
    team_player_ids: set[int] = {p["id"] for p in players}

    pitcher_engine_ids: set[str] = {rec.pitcher_id for rec in state.spell_log}
    rows: list[dict] = []
    for pid_str in pitcher_engine_ids:
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
        ps = PitcherStats.from_spell_log(state.spell_log, pid_str, player.name)
        rows.append({
            "team_id": team_id,
            "player_id": db_pid,
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


# ---------------------------------------------------------------------------
# Active roster helpers (Phase 9)
# ---------------------------------------------------------------------------

def _get_active_players(team_id: int, game_date: str) -> list[dict]:
    """Return healthy (non-injured) players; falls back to full roster if too few."""
    from o27v2.injuries import get_active_players
    return get_active_players(team_id, game_date)


def _promote_pitcher_role(players: list[dict]) -> list[dict]:
    """
    Phase 10: ensure at least one usable starter exists.

    Modern leagues seed dedicated starters/relievers, but legacy DBs may
    still contain workhorse/committee or no pitcher at all (after injuries).
    Falls back gracefully in that case so the engine never lacks a pitcher.
    """
    has_starter = any(
        p.get("pitcher_role") in ("starter", "workhorse") and p.get("is_pitcher")
        for p in players
    )
    if has_starter:
        return players

    # Try a reliever if no starter is healthy
    relievers = [p for p in players
                 if p.get("pitcher_role") == "reliever" and p.get("is_pitcher")]
    if relievers:
        best = max(relievers, key=lambda p: float(p.get("pitcher_skill", 0.0)))
        return [dict(p, pitcher_role="starter") if p["id"] == best["id"] else p
                for p in players]

    # Legacy committee fallback (Phase 8 DBs)
    committee = [p for p in players if p.get("pitcher_role") == "committee"]
    if committee:
        best = max(committee, key=lambda p: float(p.get("pitcher_skill", 0.0)))
        return [dict(p, pitcher_role="starter", is_pitcher=1)
                if p["id"] == best["id"] else p
                for p in players]

    # Last resort: promote the highest-pitcher_skill non-joker player.
    pool = [p for p in players if not p.get("is_joker")]
    if not pool:
        return players
    best = max(pool, key=lambda p: float(p.get("pitcher_skill", 0.0)))
    return [dict(p, pitcher_role="starter", is_pitcher=1)
            if p["id"] == best["id"] else p
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
    home_rotation_idx = game_id
    away_rotation_idx = game_id

    visitors_team = _db_team_to_engine(away_row,  away_players,  "visitors", away_rotation_idx)
    home_team     = _db_team_to_engine(home_row, home_players, "home",     home_rotation_idx)

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
        for r in away_bstats + home_bstats:
            conn.execute(
                """INSERT INTO game_batter_stats
                   (game_id, team_id, player_id, pa, ab, runs, hits, doubles,
                    triples, hr, rbi, bb, k, stays)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (game_id, r["team_id"], r["player_id"], r["pa"], r["ab"],
                 r["runs"], r["hits"], r["doubles"], r["triples"], r["hr"],
                 r["rbi"], r["bb"], r["k"], r["stays"]),
            )
        for r in away_pstats + home_pstats:
            conn.execute(
                """INSERT INTO game_pitcher_stats
                   (game_id, team_id, player_id, batters_faced, outs_recorded,
                    hits_allowed, runs_allowed, er, bb, k, hr_allowed, pitches)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (game_id, r["team_id"], r["player_id"], r["batters_faced"],
                 r["outs_recorded"], r["hits_allowed"], r["runs_allowed"],
                 r.get("er", r["runs_allowed"]),
                 r["bb"], r["k"],
                 r.get("hr_allowed", 0), r.get("pitches", 0)),
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
