"""
o27/stats_site/blueprint.py — Read-only stats-browsing Blueprint.

All routes are mounted under /stats on the main Flask app.
Imports o27.data directly — no HTTP roundtrip.
"""

from __future__ import annotations

import os

from flask import Blueprint, render_template, request, redirect, url_for

import o27.data as data

_here = os.path.dirname(os.path.abspath(__file__))

stats_bp = Blueprint(
    "stats_site",
    __name__,
    url_prefix="/stats",
    template_folder=os.path.join(_here, "templates"),
    static_folder=os.path.join(_here, "static"),
    static_url_path="/stats/static",
)


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

@stats_bp.route("")
@stats_bp.route("/")
def home():
    any_data = data.has_data()
    total_games = data.total_games()
    standings = data.get_standings()[:8] if any_data else []
    recent_games = data.get_schedule(10) if any_data else []
    bat_leaders = data.get_leaders("hits", 5) if any_data else []
    teams = data.load_teams()
    avg_v, avg_h = data.avg_scores() if any_data else (0, 0)

    return render_template(
        "stats_site/home.html",
        section="home",
        any_data=any_data,
        total_games=total_games,
        team_count=len(teams),
        avg_v_score=avg_v,
        avg_h_score=avg_h,
        standings=standings,
        recent_games=recent_games,
        bat_leaders=bat_leaders,
    )


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

@stats_bp.route("/standings")
def standings():
    rows = data.get_standings()
    return render_template(
        "stats_site/standings.html",
        section="standings",
        rows=rows,
        total_games=data.total_games(),
    )


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

@stats_bp.route("/schedule")
def schedule():
    team_filter = request.args.get("team", "").strip().upper()
    all_teams = data.load_teams()
    games = data.get_schedule(60, team=team_filter)
    return render_template(
        "stats_site/schedule.html",
        section="boxscores",
        games=games,
        team_filter=team_filter,
        all_teams=all_teams,
    )


# ---------------------------------------------------------------------------
# Box Score
# ---------------------------------------------------------------------------

@stats_bp.route("/game/<game_id>")
def game(game_id):
    g = data.get_game(game_id)
    if not g:
        return redirect(url_for("stats_site.schedule"))

    v_batting = g.get("v_batting", [])
    h_batting = g.get("h_batting", [])
    v_pitching = g.get("v_pitching", [])
    h_pitching = g.get("h_pitching", [])
    v_hits = sum(r.get("hits", 0) for r in v_batting)
    h_hits = sum(r.get("hits", 0) for r in h_batting)

    # Prev / next game navigation. recent_game_ids() returns newest → oldest,
    # so "previous game" (older) is at idx+1 and "next game" (newer) is at idx-1.
    recent = data.recent_game_ids()
    try:
        idx = recent.index(game_id)
    except ValueError:
        idx = -1
    prev_game_id = recent[idx + 1] if idx >= 0 and idx + 1 < len(recent) else None
    next_game_id = recent[idx - 1] if idx > 0 else None

    return render_template(
        "stats_site/game.html",
        section="boxscores",
        game_id=game_id,
        seed=g.get("seed", 0),
        visitors_name=g["visitors_name"],
        home_name=g["home_name"],
        visitors_abbrev=g["visitors_abbrev"],
        home_abbrev=g["home_abbrev"],
        visitors_score=g["v_score"],
        home_score=g["h_score"],
        winner_id=g.get("winner_id", ""),
        winner_pitcher=g.get("winner_pitcher", "—"),
        loser_pitcher=g.get("loser_pitcher", "—"),
        super_flag=g.get("super_flag", False),
        v_batting=v_batting,
        h_batting=h_batting,
        v_pitching=v_pitching,
        h_pitching=h_pitching,
        v_hits=v_hits,
        h_hits=h_hits,
        prev_game_id=prev_game_id,
        next_game_id=next_game_id,
    )


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------

@stats_bp.route("/team/<abbrev>")
def team(abbrev):
    t = data.get_team(abbrev)
    if not t:
        return redirect(url_for("stats_site.standings"))

    standings_ = {s["abbrev"]: s for s in data.get_standings()}
    rec = standings_.get(abbrev, {
        "w": 0, "l": 0, "gp": 0, "pct": 0.0,
        "r_for": 0, "r_against": 0, "gb": "—",
        "streak": "—", "l10_w": 0, "l10_l": 0,
    })

    recent_games = data.get_schedule(40, team=abbrev)[:10]
    team_batting = data.get_team_batting(abbrev)
    team_pitching = data.get_team_pitching(abbrev)
    tab = request.args.get("tab", "roster")

    return render_template(
        "stats_site/team.html",
        section="standings",
        team=t,
        record=rec,
        recent_games=recent_games,
        team_batting=team_batting,
        team_pitching=team_pitching,
        tab=tab,
    )


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

@stats_bp.route("/player/<player_id>")
def player(player_id):
    result = data.get_player(player_id)
    if not result:
        return redirect(url_for("stats_site.players"))

    team = result["team"]
    p = result["player"]
    stats = data.get_player_stats(team["abbrev"], p["name"])

    # Build batting game log
    game_log = []
    for g in data.get_schedule(100, team=team["abbrev"]):
        side = "v" if g.get("visitors_abbrev") == team["abbrev"] else "h"
        for row in g.get(f"{side}_batting", []):
            if row["name"] == p["name"] and row.get("pa", 0) > 0:
                game_log.append({
                    "game_id": g["game_id"],
                    "opp": g["home_abbrev"] if side == "v" else g["visitors_abbrev"],
                    "ha": "@" if side == "v" else "vs",
                    **row,
                })
                break
        if len(game_log) >= 10:
            break

    pitcher_game_log = (
        data.get_pitcher_game_log(team["abbrev"], p["name"], 10)
        if p.get("is_pitcher") else []
    )

    return render_template(
        "stats_site/player.html",
        section="players",
        team=team,
        player=p,
        stats=stats,
        game_log=game_log,
        pitcher_game_log=pitcher_game_log,
    )


# ---------------------------------------------------------------------------
# Players browser
# ---------------------------------------------------------------------------

@stats_bp.route("/players")
def players():
    q = request.args.get("q", "").strip().lower()
    filter_team = request.args.get("team", "")
    all_teams = data.load_teams()

    rows = []
    for t in all_teams:
        if filter_team and t["abbrev"] != filter_team:
            continue
        for p in t["players"]:
            if q and q not in p["name"].lower():
                continue
            slug = p["name"].lower().replace(" ", "_").replace(".", "")
            rows.append({"team": t, "player": p, "slug": slug})
            if len(rows) >= 200:
                break
        if len(rows) >= 200:
            break

    total = sum(len(t["players"]) for t in all_teams)
    return render_template(
        "stats_site/players.html",
        section="players",
        players=rows,
        total=total,
        team_count=len(all_teams),
        all_teams=all_teams,
        q=q,
        filter_team=filter_team,
    )


# ---------------------------------------------------------------------------
# League Leaders
# ---------------------------------------------------------------------------

@stats_bp.route("/leaders")
def leaders():
    any_data = data.has_data()
    return render_template(
        "stats_site/leaders.html",
        section="leaders",
        any_data=any_data,
        by_avg=data.get_leaders("avg", 10),
        by_hits=data.get_leaders("hits", 10),
        by_rbi=data.get_leaders("rbi", 10),
        by_bb=data.get_leaders("bb", 10),
        by_sty=data.get_stays_leaders(10),
        by_pit_era=data.get_pitching_leaders("era", 10),
        by_pit_w=data.get_wins_leaders(10),
        by_pit_k=data.get_pitching_leaders("k", 10),
        by_pit_whip=data.get_pitching_leaders("whip", 10),
    )
