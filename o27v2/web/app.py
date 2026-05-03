"""
O27v2 Flask web application.

Routes:
  GET  /                  Dashboard — recent games, quick standings
  GET  /standings         Full division standings
  GET  /schedule          Full schedule (filter: team, unplayed/played)
  GET  /game/<id>         Box score for a completed game
  GET  /teams             Team roster list
  GET  /team/<id>         Single team roster + season stats
  GET  /transactions      League transaction log (filterable by team / type)
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
from o27v2.sim import (
    simulate_game,
    simulate_next_n,
    simulate_date,
    simulate_through,
    get_current_sim_date,
    get_last_scheduled_date,
    get_all_star_date,
    is_season_complete,
    advance_sim_clock,
    resync_sim_clock,
)
from o27v2.league import get_league_configs

import datetime as _dt

app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = "o27v2-dev-key"


def _scout(val) -> int:
    """Render a stored attribute as a 20–80 scout grade.
    Task #47 stores grades natively as ints in [20, 80]; legacy float values
    in [0.0, 1.0] are converted on the fly via the 0.15 / 0.50 / 0.85 anchors."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return 50
    if v > 1.0:  # already a grade (int storage from Task #47)
        return max(20, min(80, int(round(v))))
    grade = 20 + (v - 0.15) / 0.70 * 60
    return max(20, min(80, int(round(grade))))


app.jinja_env.filters["scout"] = _scout


@app.context_processor
def inject_sim_state():
    """Make current sim date + All-Star date available to every template.
    Note: we do NOT resync on render — that would skip past off-days the user
    intentionally landed on via Sim Today. Resync is invoked explicitly after
    legacy /api/sim and single-game sim endpoints instead."""
    return {"sim": {
        "current_date":   get_current_sim_date(),
        "all_star_date":  get_all_star_date(),
        "last_date":      get_last_scheduled_date(),
        "season_complete": is_season_complete(),
    }}


def _end_of_month(d: _dt.date) -> _dt.date:
    if d.month == 12:
        return _dt.date(d.year, 12, 31)
    return _dt.date(d.year, d.month + 1, 1) - _dt.timedelta(days=1)


def _sim_response(from_date: str | None, to_date: str | None, results: list) -> dict:
    return {
        "simulated":       len(results),
        "from_date":       from_date,
        "to_date":         to_date,
        "current_date":    get_current_sim_date(),
        "season_complete": is_season_complete(),
    }


def _clamp_to_last(date_str: str) -> str:
    """Don't let the clock run past last_scheduled_date + 1 day."""
    last = get_last_scheduled_date()
    if last is None:
        return date_str
    last_plus_one = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat()
    return min(date_str, last_plus_one)


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
    team_count = db.fetchone("SELECT COUNT(*) as n FROM teams")
    if not team_count or team_count["n"] == 0:
        return redirect(url_for("new_league_get"))

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
    # Lean snapshot — only the division leaders.
    snapshot = {div_name: [teams_[0]] for div_name, teams_ in divs.items() if teams_}
    stats = db.fetchone(
        "SELECT COUNT(*) as total, SUM(played) as played FROM games"
    )
    today = get_current_sim_date()
    today_games = []
    if today:
        today_games = db.fetchall(
            """SELECT g.*,
                      ht.name as home_name, ht.abbrev as home_abbrev,
                      at.name as away_name, at.abbrev as away_abbrev,
                      wt.abbrev as winner_abbrev
               FROM games g
               JOIN teams ht ON g.home_team_id = ht.id
               JOIN teams at ON g.away_team_id = at.id
               LEFT JOIN teams wt ON g.winner_id = wt.id
               WHERE g.game_date = ?
               ORDER BY g.id""",
            (today,),
        )
    return render_template("index.html",
                           recent=recent,
                           divisions=snapshot,
                           today_games=today_games,
                           today=today,
                           stats=stats,
                           win_pct=_win_pct,
                           gb=_gb)


@app.route("/standings")
def standings():
    divs = _divisions()

    # Per-team last-10 record, current streak, runs scored / allowed, diff.
    extras: dict[int, dict] = {}
    teams = db.fetchall("SELECT id FROM teams")
    for t in teams:
        tid = t["id"]
        # All played games for this team in chronological order.
        played = db.fetchall(
            """SELECT g.id, g.game_date, g.home_team_id, g.away_team_id,
                      g.home_score, g.away_score, g.winner_id
               FROM games g
               WHERE g.played = 1 AND (g.home_team_id = ? OR g.away_team_id = ?)
               ORDER BY g.game_date, g.id""",
            (tid, tid),
        )
        rs = ra = w10 = l10 = 0
        for g in played:
            if g["home_team_id"] == tid:
                rs += g["home_score"] or 0
                ra += g["away_score"] or 0
            else:
                rs += g["away_score"] or 0
                ra += g["home_score"] or 0
        for g in played[-10:]:
            if g["winner_id"] == tid:
                w10 += 1
            else:
                l10 += 1
        # Streak: walk backwards.
        streak = ""
        if played:
            last_won = (played[-1]["winner_id"] == tid)
            count = 0
            for g in reversed(played):
                if (g["winner_id"] == tid) == last_won:
                    count += 1
                else:
                    break
            streak = ("W" if last_won else "L") + str(count)
        extras[tid] = {
            "l10":    f"{w10}-{l10}",
            "streak": streak,
            "rs":     rs,
            "ra":     ra,
            "diff":   rs - ra,
        }

    return render_template("standings.html",
                           divisions=divs,
                           extras=extras,
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

    # Prev/Next played games in (game_date, id) order.
    prev_game = db.fetchone(
        """SELECT id FROM games
           WHERE played = 1
             AND (game_date < ? OR (game_date = ? AND id < ?))
           ORDER BY game_date DESC, id DESC LIMIT 1""",
        (game["game_date"], game["game_date"], game_id),
    )
    next_game = db.fetchone(
        """SELECT id FROM games
           WHERE played = 1
             AND (game_date > ? OR (game_date = ? AND id > ?))
           ORDER BY game_date ASC, id ASC LIMIT 1""",
        (game["game_date"], game["game_date"], game_id),
    )

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
                           prev_game_id=(prev_game["id"] if prev_game else None),
                           next_game_id=(next_game["id"] if next_game else None),
                           avg=_avg)


@app.route("/stats")
def stats():
    """Season-to-date leaderboards — batting + pitching + team identity."""
    games_played = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")["n"]
    if games_played == 0:
        return render_template("stats.html",
                               games_played=0,
                               batting=[], pitching=[],
                               team_offense=[], team_defense=[])

    # Scale qualifying minimums by games-per-team, not by total league games.
    # MLB rule of thumb: 3.1 PA/team-game qualifies for batting title; here we
    # use ~1× games/team for batting and ~1× games/team in outs for pitching,
    # so leaders are visible from week one and grow naturally with the season.
    num_teams = db.fetchone("SELECT COUNT(*) as n FROM teams")["n"] or 2
    games_per_team = max(1, (games_played * 2) // num_teams)
    min_pa   = max(3, games_per_team)        # ~1 PA/team-game
    min_outs = max(3, games_per_team)        # ~1 out/team-game (very lenient)

    batting = db.fetchall(
        """SELECT p.id   as player_id,
                  p.name as player_name,
                  p.position,
                  t.abbrev as team_abbrev, t.id as team_id,
                  COUNT(bs.game_id) as g,
                  SUM(bs.pa)      as pa,
                  SUM(bs.ab)      as ab,
                  SUM(bs.hits)    as h,
                  SUM(bs.doubles) as d2,
                  SUM(bs.triples) as d3,
                  SUM(bs.hr)      as hr,
                  SUM(bs.runs)    as r,
                  SUM(bs.rbi)     as rbi,
                  SUM(bs.bb)      as bb,
                  SUM(bs.k)       as k,
                  SUM(bs.stays)   as stays
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           JOIN teams   t ON bs.team_id   = t.id
           GROUP BY p.id
           HAVING SUM(bs.pa) >= ?
           ORDER BY (CAST(SUM(bs.hits) AS REAL) / NULLIF(SUM(bs.ab), 0)) DESC""",
        (min_pa,),
    )
    for b in batting:
        b["avg"]    = (b["h"] / b["ab"]) if b["ab"] else 0.0
        b["h_ab"]   = (b["h"] / b["ab"]) if b["ab"] else 0.0
        b["obp"]    = ((b["h"] + b["bb"]) / b["pa"]) if b["pa"] else 0.0
        tb          = b["h"] - b["d2"] - b["d3"] - b["hr"] + 2 * b["d2"] + 3 * b["d3"] + 4 * b["hr"]
        b["slg"]    = (tb / b["ab"]) if b["ab"] else 0.0
        b["ops"]    = b["obp"] + b["slg"]

    pitching = db.fetchall(
        """SELECT p.id   as player_id,
                  p.name as player_name,
                  t.abbrev as team_abbrev, t.id as team_id,
                  COUNT(ps.game_id) as g,
                  SUM(ps.batters_faced)  as bf,
                  SUM(ps.outs_recorded)  as outs,
                  SUM(ps.hits_allowed)   as h,
                  SUM(ps.runs_allowed)   as r,
                  SUM(ps.bb)             as bb,
                  SUM(ps.k)              as k
           FROM game_pitcher_stats ps
           JOIN players p ON ps.player_id = p.id
           JOIN teams   t ON ps.team_id   = t.id
           GROUP BY p.id
           HAVING SUM(ps.outs_recorded) >= ?
           ORDER BY SUM(ps.outs_recorded) DESC""",
        (min_outs,),
    )
    for p in pitching:
        outs = p["outs"] or 0
        # O27 stats: per-27-outs ("per game") rather than per-9-IP.
        p["era"]    = (p["r"]  * 27.0 / outs) if outs else 0.0
        p["whip"]   = ((p["bb"] + p["h"]) * 27.0 / outs) if outs else 0.0
        p["k27"]    = (p["k"]  * 27.0 / outs) if outs else 0.0
        p["bb27"]   = (p["bb"] * 27.0 / outs) if outs else 0.0
        p["k_pct"]  = (p["k"]  / p["bf"]) if p["bf"] else 0.0
        p["bb_pct"] = (p["bb"] / p["bf"]) if p["bf"] else 0.0
        # OS% = share of a complete game (27 outs) recorded per appearance.
        p["os_pct"] = (outs / (27.0 * p["g"])) if p["g"] else 0.0

    team_offense = db.fetchall(
        """SELECT t.id, t.abbrev, t.name,
                  COUNT(DISTINCT g.id) as gp,
                  SUM(CASE WHEN g.home_team_id = t.id THEN g.home_score ELSE g.away_score END) as runs,
                  SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score ELSE g.home_score END) as runs_against
           FROM teams t
           JOIN games g ON g.played = 1 AND (g.home_team_id = t.id OR g.away_team_id = t.id)
           GROUP BY t.id
           ORDER BY (CAST(SUM(CASE WHEN g.home_team_id = t.id THEN g.home_score ELSE g.away_score END) AS REAL)
                     / NULLIF(COUNT(DISTINCT g.id), 0)) DESC"""
    )
    for to in team_offense:
        to["rpg"]  = (to["runs"] / to["gp"]) if to["gp"] else 0.0
        to["rapg"] = (to["runs_against"] / to["gp"]) if to["gp"] else 0.0
        to["diff"] = to["rpg"] - to["rapg"]

    team_defense = sorted(team_offense, key=lambda x: x["rapg"])

    return render_template(
        "stats.html",
        games_played=games_played,
        min_pa=min_pa,
        min_outs=min_outs,
        batting=batting,
        pitching=pitching,
        team_offense=team_offense,
        team_defense=team_defense,
    )


@app.route("/player/<int:player_id>")
def player_detail(player_id: int):
    player = db.fetchone(
        """SELECT p.*, t.abbrev as team_abbrev, t.name as team_name, t.id as team_id
           FROM players p JOIN teams t ON p.team_id = t.id
           WHERE p.id = ?""",
        (player_id,),
    )
    if not player:
        abort(404)

    batting_log = db.fetchall(
        """SELECT bs.*, g.game_date, g.id as game_id, g.home_team_id, g.away_team_id,
                  ht.abbrev as home_abbrev, at.abbrev as away_abbrev
           FROM game_batter_stats bs
           JOIN games g ON bs.game_id = g.id
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE bs.player_id = ?
           ORDER BY g.game_date DESC, g.id DESC
           LIMIT 50""",
        (player_id,),
    )
    pitching_log = db.fetchall(
        """SELECT ps.*, g.game_date, g.id as game_id, g.home_team_id, g.away_team_id,
                  ht.abbrev as home_abbrev, at.abbrev as away_abbrev
           FROM game_pitcher_stats ps
           JOIN games g ON ps.game_id = g.id
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE ps.player_id = ?
           ORDER BY g.game_date DESC, g.id DESC
           LIMIT 50""",
        (player_id,),
    )

    bt = db.fetchone(
        """SELECT COUNT(*) as g, SUM(pa) as pa, SUM(ab) as ab, SUM(hits) as h,
                  SUM(doubles) as d2, SUM(triples) as d3, SUM(hr) as hr,
                  SUM(runs) as r, SUM(rbi) as rbi, SUM(bb) as bb, SUM(k) as k,
                  SUM(stays) as stays
           FROM game_batter_stats WHERE player_id = ?""",
        (player_id,),
    )
    pt = db.fetchone(
        """SELECT COUNT(*) as g, SUM(batters_faced) as bf, SUM(outs_recorded) as outs,
                  SUM(hits_allowed) as h, SUM(runs_allowed) as r,
                  SUM(bb) as bb, SUM(k) as k
           FROM game_pitcher_stats WHERE player_id = ?""",
        (player_id,),
    )

    bt_totals = None
    if bt and bt["pa"]:
        ab = bt["ab"] or 0
        tb = (bt["h"] or 0) - (bt["d2"] or 0) - (bt["d3"] or 0) - (bt["hr"] or 0) \
             + 2 * (bt["d2"] or 0) + 3 * (bt["d3"] or 0) + 4 * (bt["hr"] or 0)
        bt_totals = dict(bt)
        bt_totals["avg"] = (bt["h"] / ab) if ab else 0.0
        bt_totals["obp"] = ((bt["h"] + bt["bb"]) / bt["pa"]) if bt["pa"] else 0.0
        bt_totals["slg"] = (tb / ab) if ab else 0.0
        bt_totals["ops"] = bt_totals["obp"] + bt_totals["slg"]

    pt_totals = None
    if pt and pt["outs"]:
        outs = pt["outs"] or 0
        pt_totals = dict(pt)
        pt_totals["era"]    = (pt["r"]  * 27.0 / outs) if outs else 0.0
        pt_totals["whip"]   = ((pt["bb"] + pt["h"]) * 27.0 / outs) if outs else 0.0
        pt_totals["k27"]    = (pt["k"]  * 27.0 / outs) if outs else 0.0
        pt_totals["bb27"]   = (pt["bb"] * 27.0 / outs) if outs else 0.0
        pt_totals["os_pct"] = (outs / (27.0 * pt["g"])) if pt["g"] else 0.0

    return render_template(
        "player.html",
        player=player,
        batting_log=batting_log,
        pitching_log=pitching_log,
        bt_totals=bt_totals,
        pt_totals=pt_totals,
    )


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
        "SELECT * FROM players WHERE team_id = ? ORDER BY is_pitcher, id",
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


@app.route("/transactions")
def transactions():
    from o27v2.transactions import get_transactions
    team_id    = request.args.get("team", type=int)
    event_type = request.args.get("type")

    txns  = get_transactions(team_id=team_id, event_type=event_type or None, limit=300)
    teams = db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name")

    event_types = ["injury", "return", "promotion", "penalty", "deadline_trade", "inseason_trade", "waiver"]
    selected_team = None
    if team_id:
        selected_team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))

    # Summary counts for the header
    counts = {et: 0 for et in event_types}
    all_txns = get_transactions(limit=50000)
    for tx in all_txns:
        et = tx.get("event_type", "")
        if et in counts:
            counts[et] += 1

    return render_template("transactions.html",
                           transactions=txns,
                           teams=teams,
                           selected_team=selected_team,
                           event_type=event_type or "",
                           event_types=event_types,
                           counts=counts)


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
    resync_sim_clock()
    return jsonify({"simulated": len(results), "results": results})


@app.route("/api/sim/today", methods=["POST"])
def api_sim_today():
    """Sim every game scheduled on the current sim date, then advance the clock by 1 day.
    On an off-day (no games), the clock still advances by 1 day so users can click through gaps."""
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    results = simulate_date(current)
    next_day = (_dt.date.fromisoformat(current) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(_clamp_to_last(next_day))
    return jsonify(_sim_response(current, current, results))


@app.route("/api/sim/week", methods=["POST"])
def api_sim_week():
    """Sim every unplayed game over the next 7 calendar days (current..current+6),
    then advance the clock to the day after."""
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    target  = (_dt.date.fromisoformat(current) + _dt.timedelta(days=6)).isoformat()
    results = simulate_through(target)
    next_day = (_dt.date.fromisoformat(target) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(_clamp_to_last(next_day))
    return jsonify(_sim_response(current, target, results))


@app.route("/api/sim/month", methods=["POST"])
def api_sim_month():
    """Sim through the end of the current calendar month, then advance the clock past it."""
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    target  = _end_of_month(_dt.date.fromisoformat(current)).isoformat()
    results = simulate_through(target)
    next_day = (_dt.date.fromisoformat(target) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(_clamp_to_last(next_day))
    return jsonify(_sim_response(current, target, results))


@app.route("/api/sim/all-star", methods=["POST"])
def api_sim_all_star():
    """Sim through the All-Star break (schedule midpoint)."""
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    target  = get_all_star_date()
    if target is None or current > target:
        return jsonify(_sim_response(current, target, []))
    results = simulate_through(target)
    next_day = (_dt.date.fromisoformat(target) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(_clamp_to_last(next_day))
    return jsonify(_sim_response(current, target, results))


@app.route("/api/sim/season", methods=["POST"])
def api_sim_season():
    """Sim every remaining unplayed game in the season."""
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    last    = get_last_scheduled_date()
    results = simulate_through(last)
    next_day = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(next_day)
    return jsonify(_sim_response(current, last, results))


@app.route("/api/sim/<int:game_id>", methods=["POST"])
def api_sim_game(game_id: int):
    """Simulate a specific game by ID."""
    data = request.get_json(silent=True) or {}
    seed = data.get("seed")
    try:
        result = simulate_game(game_id, seed=seed)
        resync_sim_clock()
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/league-configs")
def api_league_configs():
    """Return all available league configs as JSON."""
    return jsonify(list(get_league_configs().values()))


@app.route("/api/health")
def api_health():
    """Lightweight health probe for fly.io / load balancers. Cheap on purpose —
    no DB query so it can't fail during transient lock contention."""
    return jsonify({"status": "ok"})
