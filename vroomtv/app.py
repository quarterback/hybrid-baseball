"""Unassociated Press Sports Wire — cross-sport aggregator hub."""
from __future__ import annotations
import os
from datetime import datetime
from flask import Flask, render_template, abort, request
from adapters import baseball, viperball, tennis

app = Flask(__name__)

_DAYS   = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
_MONTHS = ["January","February","March","April","May","June",
           "July","August","September","October","November","December"]

@app.context_processor
def inject_globals():
    now = datetime.now()
    wire_date = f"{_DAYS[now.weekday()]}, {_MONTHS[now.month-1]} {now.day}, {now.year}"
    return {"wire_date": wire_date}


@app.route("/")
def index():
    return render_template(
        "index.html",
        baseball_scores=baseball.get_recent_scores(),
        viperball_scores=viperball.get_recent_scores(),
        tennis_scores=tennis.get_recent_scores(),
        baseball_configured=bool(os.environ.get("BASEBALL_DB")),
        viperball_configured=bool(os.environ.get("VIPERBALL_DB")),
        tennis_configured=bool(os.environ.get("TENNIS_DB")),
    )


@app.route("/standings")
def standings():
    return render_template(
        "standings.html",
        baseball_standings=baseball.get_standings(),
        viperball_standings=viperball.get_standings(),
        tennis_standings=tennis.get_standings(),
    )


@app.route("/leaders")
def leaders():
    return render_template(
        "leaders.html",
        baseball_batting=baseball.get_batting_leaders(),
        baseball_pitching=baseball.get_pitching_leaders(),
        viperball_leaders=viperball.get_stat_leaders(),
        tennis_leaders=tennis.get_stat_leaders(),
    )


@app.route("/game/baseball/<int:game_id>")
def game_baseball(game_id: int):
    detail = baseball.get_game_detail(game_id)
    if not detail:
        abort(404)
    return render_template("game_baseball.html", **detail)


@app.route("/game/viperball/<save_key>/<int:week>/<path:matchup_key>")
def game_viperball(save_key: str, week: int, matchup_key: str):
    detail = viperball.get_game_detail(save_key, week, matchup_key)
    if not detail:
        abort(404)
    return render_template("game_viperball.html", **detail)


@app.route("/game/tennis/<source>/<int:dual_id>")
def game_tennis(source: str, dual_id: int):
    if source not in ("gtt", "ncaa"):
        abort(404)
    detail = tennis.get_game_detail(source, dual_id)
    if not detail:
        abort(404)
    return render_template("game_tennis.html", **detail)
