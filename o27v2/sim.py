"""
Game simulation for O27v2.

simulate_game(game_id) runs a complete O27 game for the given DB game_id,
stores results back to the database, and returns a result summary dict.

Uses the O27 probabilistic engine from o27/engine/ — the 9-inning loop from the
Baseball-Simulation fork is replaced entirely by O27's 1-inning/27-out structure.
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


# ---------------------------------------------------------------------------
# DB ↔ engine type converters
# ---------------------------------------------------------------------------

def _db_team_to_engine(team_row: dict, players: list[dict], team_role: str) -> Team:
    """
    Convert a DB team row + player rows into an O27 engine Team object.

    team_role: "visitors" | "home"
    """
    roster: list[Player] = []
    jokers: list[Player] = []

    for p in players:
        # Home-field advantage: all non-joker players on the home team receive a
        # small batting skill bonus, consistent with batch.py / generate_players().
        home_bonus = (
            v2cfg.HOME_ADVANTAGE_SKILL
            if team_role == "home" and not p.get("is_joker")
            else 0.0
        )
        player = Player(
            player_id=str(p["id"]),
            name=p["name"],
            is_pitcher=bool(p["is_pitcher"]),
            is_joker=bool(p["is_joker"]),
            skill=float(p["skill"]) + home_bonus,
            speed=float(p["speed"]),
            pitcher_skill=float(p["pitcher_skill"]),
            stay_aggressiveness=float(p["stay_aggressiveness"]),
            contact_quality_threshold=float(p["contact_quality_threshold"]),
            archetype=str(p.get("archetype") or ""),
            pitcher_role=str(p.get("pitcher_role") or ""),
            hard_contact_delta=float(p.get("hard_contact_delta") or 0.0),
            hr_weight_bonus=float(p.get("hr_weight_bonus") or 0.0),
        )
        roster.append(player)
        if player.is_joker:
            jokers.append(player)

    return Team(
        team_id=team_role,
        name=team_row["name"],
        roster=roster,
        lineup=list(roster),
        jokers_available=list(jokers),
    )


# ---------------------------------------------------------------------------
# Stat extraction from Renderer
# ---------------------------------------------------------------------------

def _extract_batter_stats(renderer: Renderer, team_id: int, players: list[dict]) -> list[dict]:
    """
    Extract per-batter stats from Renderer._batter_stats for DB insertion.

    Engine Player.player_id is set to str(db_player.id), so we convert directly
    to int to get the DB primary key — no name-based matching needed.
    Only rows whose player_id belongs to this team's player set are included.
    """
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
        })
    return rows


def _extract_pitcher_stats(state: GameState, team_id: int, players: list[dict]) -> list[dict]:
    """
    Extract pitcher stats from spell_log for DB insertion.

    Engine pitcher_id is str(db_player.id); convert to int for direct lookup.
    Filter by membership in this team's player ID set to avoid cross-team
    misattribution.
    """
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
            "bb": ps.bb,
            "k": ps.k,
        })
    return rows


# ---------------------------------------------------------------------------
# Main simulation entry point
# ---------------------------------------------------------------------------

def simulate_game(game_id: int, seed: int | None = None) -> dict:
    """
    Run an O27 game for the given DB game_id.

    - Loads team and player data from the DB.
    - Runs the O27 probabilistic engine.
    - Stores score, winner, and per-player stats back to DB.
    - Returns a summary dict.
    """
    game = db.fetchone("SELECT * FROM games WHERE id = ?", (game_id,))
    if game is None:
        raise ValueError(f"Game {game_id} not found")
    if game["played"]:
        raise ValueError(f"Game {game_id} has already been played")

    home_row  = db.fetchone("SELECT * FROM teams WHERE id = ?", (game["home_team_id"],))
    away_row  = db.fetchone("SELECT * FROM teams WHERE id = ?", (game["away_team_id"],))
    home_players = db.fetchall("SELECT * FROM players WHERE team_id = ? ORDER BY id", (game["home_team_id"],))
    away_players = db.fetchall("SELECT * FROM players WHERE team_id = ? ORDER BY id", (game["away_team_id"],))

    if seed is None:
        seed = random.randint(0, 999_999)

    rng = random.Random(seed)

    visitors_team = _db_team_to_engine(away_row,  away_players,  "visitors")
    home_team     = _db_team_to_engine(home_row, home_players, "home")

    state = GameState(visitors=visitors_team, home=home_team)
    # Set the starting pitcher for each team
    state.current_pitcher_id = _find_pitcher_id(home_team)

    renderer = Renderer()
    provider = ProbabilisticProvider(rng)

    final_state, _log = run_game(state, provider, renderer)

    away_score = final_state.score["visitors"]
    home_score = final_state.score["home"]
    winner_team_id: int | None = None
    if final_state.winner == "visitors":
        winner_team_id = game["away_team_id"]
    elif final_state.winner == "home":
        winner_team_id = game["home_team_id"]

    # Update game row
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE games SET home_score=?, away_score=?, winner_id=?,
               super_inning=?, played=1, seed=? WHERE id=?""",
            (home_score, away_score, winner_team_id,
             final_state.super_inning_number, seed, game_id),
        )
        conn.commit()

    # Update team wins/losses
    if winner_team_id == game["home_team_id"]:
        loser_id = game["away_team_id"]
    else:
        loser_id = game["home_team_id"]

    with db.get_conn() as conn:
        conn.execute("UPDATE teams SET wins = wins + 1 WHERE id = ?", (winner_team_id,))
        conn.execute("UPDATE teams SET losses = losses + 1 WHERE id = ?", (loser_id,))
        conn.commit()

    # Store batter stats
    away_bstats = _extract_batter_stats(renderer, game["away_team_id"], away_players)
    home_bstats = _extract_batter_stats(renderer, game["home_team_id"], home_players)

    _insert_batter_stats(game_id, away_bstats + home_bstats)

    # Store pitcher stats
    away_pstats = _extract_pitcher_stats(final_state, game["away_team_id"], away_players)
    home_pstats = _extract_pitcher_stats(final_state, game["home_team_id"], home_players)
    _insert_pitcher_stats(game_id, away_pstats + home_pstats)

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


def _find_pitcher_id(team: Team) -> str | None:
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
            hr, rbi, bb, k, stays)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(game_id, r["team_id"], r["player_id"], r["pa"], r["ab"], r["runs"],
          r["hits"], r["doubles"], r["triples"], r["hr"], r["rbi"],
          r["bb"], r["k"], r["stays"]) for r in rows],
    )


def _insert_pitcher_stats(game_id: int, rows: list[dict]) -> None:
    if not rows:
        return
    db.executemany(
        """INSERT INTO game_pitcher_stats
           (game_id, team_id, player_id, batters_faced, outs_recorded,
            hits_allowed, runs_allowed, bb, k)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [(game_id, r["team_id"], r["player_id"], r["batters_faced"],
          r["outs_recorded"], r["hits_allowed"], r["runs_allowed"],
          r["bb"], r["k"]) for r in rows],
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
