"""
O27v2 Flask web application.

Routes:
  GET  /                  Dashboard — recent games, quick standings
  GET  /standings         Full division standings
  GET  /schedule          Full schedule (filter: team, unplayed/played)
  GET  /game/<id>         Box score for a completed game
  GET  /teams             Team roster list
  GET  /team/<id>         Single team roster + season stats
  GET  /new-league        League-creation screen (pick preset config)
  POST /new-league        Apply the chosen config (reset DB + reseed)
  POST /api/sim           Simulate the next N games (JSON response)
"""
from __future__ import annotations
import os
import sys

_workspace = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

from flask import Flask, render_template, request, redirect, url_for, jsonify, abort

from o27v2 import db
from o27v2.sim import simulate_game, simulate_next_n
from o27v2.league import get_league_configs

app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = "o27v2-dev-key"


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _divisions() -> dict[str, list[dict]]:
    """Return teams grouped by division, sorted by win pct."""
    teams = db.fetchall("SELECT * FROM teams ORDER BY division, wins DESC, losses ASC")
    divs: dict[str, list[dict]] = {}
    for t in teams:
        divs.setdefault(t["division"], []).append(t)
    return divs


def _win_pct(t: dict) -> str:
    total = t["wins"] + t["losses"]
    if total == 0:
        return ".000"
    return f".{int(t['wins'] / total * 1000):03d}"


def _gb(leader: dict, team: dict) -> str:
    diff = (leader["wins"] - team["wins"] + team["losses"] - leader["losses"]) / 2
    if diff == 0:
        return "—"
    return f"{diff:.1f}"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    recent = db.fetchall(
        """SELECT g.*,
                  ht.name as home_name, ht.abbrev as home_abbrev,
                  at.name as away_name, at.abbrev as away_abbrev,
                  wt.abbrev as winner_abbrev
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           LEFT JOIN teams wt ON g.winner_id = wt.id
           WHERE g.played = 1
           ORDER BY g.game_date DESC, g.id DESC
           LIMIT 10"""
    )
    divs = _divisions()
    stats = db.fetchone(
        "SELECT COUNT(*) as total, SUM(played) as played FROM games"
    )
    return render_template("index.html",
                           recent=recent,
                           divisions=divs,
                           stats=stats,
                           win_pct=_win_pct,
                           gb=_gb)


@app.route("/standings")
def standings():
    divs = _divisions()
    return render_template("standings.html",
                           divisions=divs,
                           win_pct=_win_pct,
                           gb=_gb)


@app.route("/schedule")
def schedule():
    team_id = request.args.get("team", type=int)
    status  = request.args.get("status", "all")

    sql = """
        SELECT g.*,
               ht.name as home_name, ht.abbrev as home_abbrev,
               at.name as away_name, at.abbrev as away_abbrev,
               wt.abbrev as winner_abbrev
        FROM games g
        JOIN teams ht ON g.home_team_id = ht.id
        JOIN teams at ON g.away_team_id = at.id
        LEFT JOIN teams wt ON g.winner_id = wt.id
    """
    where_clauses = []
    params: list = []

    if team_id:
        where_clauses.append("(g.home_team_id = ? OR g.away_team_id = ?)")
        params += [team_id, team_id]
    if status == "played":
        where_clauses.append("g.played = 1")
    elif status == "unplayed":
        where_clauses.append("g.played = 0")

    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += " ORDER BY g.game_date, g.id LIMIT 200"

    games       = db.fetchall(sql, tuple(params))
    teams       = db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name")
    selected_team = None
    if team_id:
        selected_team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))

    return render_template("schedule.html",
                           games=games,
                           teams=teams,
                           selected_team=selected_team,
                           status=status)


@app.route("/game/<int:game_id>")
def game_detail(game_id: int):
    game = db.fetchone(
        """SELECT g.*,
                  ht.name as home_name, ht.abbrev as home_abbrev,
                  at.name as away_name, at.abbrev as away_abbrev,
                  wt.name as winner_name
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           LEFT JOIN teams wt ON g.winner_id = wt.id
           WHERE g.id = ?""", (game_id,)
    )
    if not game:
        abort(404)

    away_batting = db.fetchall(
        """SELECT bs.*, p.name as player_name, p.position
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           WHERE bs.game_id = ? AND bs.team_id = ?
           ORDER BY bs.id""",
        (game_id, game["away_team_id"])
    )
    home_batting = db.fetchall(
        """SELECT bs.*, p.name as player_name, p.position
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           WHERE bs.game_id = ? AND bs.team_id = ?
           ORDER BY bs.id""",
        (game_id, game["home_team_id"])
    )
    away_pitching = db.fetchall(
        """SELECT ps.*, p.name as player_name
           FROM game_pitcher_stats ps
           JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ?
           ORDER BY ps.id""",
        (game_id, game["away_team_id"])
    )
    home_pitching = db.fetchall(
        """SELECT ps.*, p.name as player_name
           FROM game_pitcher_stats ps
           JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ?
           ORDER BY ps.id""",
        (game_id, game["home_team_id"])
    )

    def _avg(bs: list[dict]) -> str:
        hits = sum(r["hits"] for r in bs)
        ab   = sum(r["ab"]   for r in bs)
        if ab == 0:
            return ".000"
        return f".{int(hits / ab * 1000):03d}"

    return render_template("game.html",
                           game=game,
                           away_batting=away_batting,
                           home_batting=home_batting,
                           away_pitching=away_pitching,
                           home_pitching=home_pitching,
                           avg=_avg)


@app.route("/teams")
def teams():
    teams_list = db.fetchall(
        """SELECT t.*,
                  COUNT(p.id) as player_count
           FROM teams t
           LEFT JOIN players p ON p.team_id = t.id
           GROUP BY t.id
           ORDER BY t.league, t.division, t.name"""
    )
    return render_template("teams.html", teams=teams_list, win_pct=_win_pct)


@app.route("/team/<int:team_id>")
def team_detail(team_id: int):
    team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))
    if not team:
        abort(404)
    players = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? ORDER BY is_joker, is_pitcher, id",
        (team_id,)
    )
    recent = db.fetchall(
        """SELECT g.*,
                  ht.name as home_name, ht.abbrev as home_abbrev,
                  at.name as away_name, at.abbrev as away_abbrev,
                  wt.abbrev as winner_abbrev
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           LEFT JOIN teams wt ON g.winner_id = wt.id
           WHERE g.played = 1 AND (g.home_team_id = ? OR g.away_team_id = ?)
           ORDER BY g.game_date DESC LIMIT 10""",
        (team_id, team_id)
    )
    return render_template("team.html",
                           team=team,
                           players=players,
                           recent=recent,
                           win_pct=_win_pct)


@app.route("/new-league", methods=["GET"])
def new_league_get():
    configs = get_league_configs()
    current_team_count = db.fetchone("SELECT COUNT(*) as n FROM teams")
    current_n = current_team_count["n"] if current_team_count else 0
    return render_template("new_league.html",
                           configs=configs,
                           current_team_count=current_n)


@app.route("/new-league", methods=["POST"])
def new_league_post():
    from o27v2.league import seed_league
    from o27v2.schedule import seed_schedule

    config_id  = request.form.get("config_id", "30teams")
    rng_seed   = int(request.form.get("rng_seed", 42))

    configs = get_league_configs()
    if config_id not in configs:
        abort(400, f"Unknown config: {config_id}")

    db.drop_all()
    db.init_db()
    seed_league(rng_seed=rng_seed, config_id=config_id)
    seed_schedule(config_id=config_id, rng_seed=rng_seed)

    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.route("/api/sim", methods=["POST"])
def api_sim():
    """Simulate the next N unplayed games. POST body: {n: int, seed_base: int|null}"""
    data      = request.get_json(silent=True) or {}
    n         = int(data.get("n", 5))
    n         = max(1, min(n, 50))
    seed_base = data.get("seed_base")
    results   = simulate_next_n(n, seed_base=seed_base)
    return jsonify({"simulated": len(results), "results": results})


@app.route("/api/sim/<int:game_id>", methods=["POST"])
def api_sim_game(game_id: int):
    """Simulate a specific game by ID."""
    data = request.get_json(silent=True) or {}
    seed = data.get("seed")
    try:
        result = simulate_game(game_id, seed=seed)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/league-configs")
def api_league_configs():
    """Return all available league configs as JSON."""
    return jsonify(list(get_league_configs().values()))
