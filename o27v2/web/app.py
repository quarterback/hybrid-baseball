"""
O27v2 Flask web application.

Routes:
  GET  /                  Scores dashboard — today's games, recent finals, division leaders, top-5 leaders
  GET  /standings         Full standings — one wide table per league, sortable
  GET  /schedule          Full schedule (filter: team, status)
  GET  /game/<id>         Box score for a completed game
  GET  /players           Browseable player index (server-paginated, sortable, filterable)
  GET  /player/<id>       Single player season + game log
  GET  /teams             Team list
  GET  /team/<id>         Team header + batting roster + pitching roster + last 10 games
  GET  /leaders           Season-to-date leaderboards (replaces /stats; /stats redirects here)
  GET  /transactions      League transaction log (filterable by team / type)
  GET  /new-league        League-creation screen
  POST /new-league        Apply the chosen config (reset DB + reseed)
  POST /api/sim           Simulate the next N games (JSON response)
"""
from __future__ import annotations
import math
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
    last = get_last_scheduled_date()
    if last is None:
        return date_str
    last_plus_one = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat()
    return min(date_str, last_plus_one)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _divisions() -> dict[str, list[dict]]:
    teams = db.fetchall("SELECT * FROM teams ORDER BY division, wins DESC, losses ASC")
    divs: dict[str, list[dict]] = {}
    for t in teams:
        divs.setdefault(t["division"], []).append(t)
    return divs


def _leagues_with_divisions() -> dict[str, dict[str, list[dict]]]:
    """Return {league_name: {division_name: [team, ...]}} sorted by win pct."""
    teams = db.fetchall(
        "SELECT * FROM teams ORDER BY league, division, wins DESC, losses ASC"
    )
    out: dict[str, dict[str, list[dict]]] = {}
    for t in teams:
        out.setdefault(t["league"], {}).setdefault(t["division"], []).append(t)
    return out


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


# Dedup subquery: collapse duplicate (player_id, game_id) rows in
# game_pitcher_stats (Task #57 audit — pre-#58 the engine could re-insert a
# pitcher's line if they appeared in multiple half-innings, inflating BF/K/G).
# We pick ONE real row per (game_id, player_id) — the row with the most outs,
# breaking ties by lowest rowid (earliest appearance). This avoids the
# "Frankenstein" totals you get from MAX-per-column, which can mix maxima from
# different duplicate rows and overstate stats. Task #58 will add a UNIQUE
# constraint on (player_id, game_id, phase) so this subquery becomes a no-op.
_PSTATS_DEDUP_SQL = """(
    SELECT game_id, player_id, team_id, batters_faced, outs_recorded,
           hits_allowed, runs_allowed, er, bb, k, hr_allowed
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY game_id, player_id
                   ORDER BY outs_recorded DESC, rowid ASC
               ) AS _rn
        FROM game_pitcher_stats
    )
    WHERE _rn = 1
)"""


def _pitcher_wl_map() -> dict[int, dict[str, int]]:
    """For each pitcher, count W/L in games where they were that team's
    workhorse for the day (recorded the most outs of any pitcher on their
    team for that game). This approximates a starter's decision.

    Tie-breaker: when two pitchers on the same team tie on MAX(outs_recorded)
    for a game, only the first one returned by the join (lowest game_pitcher_stats
    rowid, i.e. earliest appearance) gets credit, via the (game_id, team_id)
    `seen` set below."""
    rows = db.fetchall(
        """SELECT ps.player_id, ps.team_id, ps.game_id, g.winner_id
           FROM game_pitcher_stats ps
           JOIN games g ON g.id = ps.game_id
           JOIN (SELECT game_id, team_id, MAX(outs_recorded) AS mo
                 FROM game_pitcher_stats
                 GROUP BY game_id, team_id) m
             ON m.game_id = ps.game_id
            AND m.team_id = ps.team_id
            AND m.mo = ps.outs_recorded
           WHERE g.played = 1
           ORDER BY ps.game_id, ps.team_id, ps.rowid"""
    )
    out: dict[int, dict[str, int]] = {}
    seen: set[tuple[int, int]] = set()  # (game_id, team_id) — only first ranked pitcher
    for r in rows:
        key = (r["game_id"], r["team_id"])
        if key in seen:
            continue
        seen.add(key)
        pid = r["player_id"]
        rec = out.setdefault(pid, {"w": 0, "l": 0})
        if r["winner_id"] == r["team_id"]:
            rec["w"] += 1
        elif r["winner_id"] is not None:
            rec["l"] += 1
    return out


def _attach_hits(games: list[dict]) -> None:
    """Sum hits per (game_id, team_id) from game_batter_stats and attach
    home_hits / away_hits to each game row. Pure roll-up of sim output —
    nothing is invented; if a game wasn't played, both hit totals are None."""
    if not games:
        return
    ids = [g["id"] for g in games]
    ph = ",".join("?" * len(ids))
    rows = db.fetchall(
        f"""SELECT game_id, team_id, SUM(hits) AS h
            FROM game_batter_stats
            WHERE game_id IN ({ph})
            GROUP BY game_id, team_id""",
        tuple(ids),
    )
    by_game: dict[int, dict[int, int]] = {}
    for r in rows:
        by_game.setdefault(r["game_id"], {})[r["team_id"]] = r["h"] or 0
    for g in games:
        team_hits = by_game.get(g["id"], {})
        g["home_hits"] = team_hits.get(g["home_team_id"]) if g.get("played") else None
        g["away_hits"] = team_hits.get(g["away_team_id"]) if g.get("played") else None


def _aggregate_batter_rows(rows: list[dict]) -> None:
    """Mutates rows in place to add avg/obp/slg/ops keys."""
    for b in rows:
        ab = b.get("ab") or 0
        h = b.get("h") or 0
        bb = b.get("bb") or 0
        pa = b.get("pa") or 0
        d2 = b.get("d2") or 0
        d3 = b.get("d3") or 0
        hr = b.get("hr") or 0
        b["avg"] = (h / ab) if ab else 0.0
        b["obp"] = ((h + bb) / pa) if pa else 0.0
        singles = h - d2 - d3 - hr
        tb = singles + 2 * d2 + 3 * d3 + 4 * hr
        b["slg"] = (tb / ab) if ab else 0.0
        b["ops"] = b["obp"] + b["slg"]


def _league_fip_const() -> float:
    """Compute the FIP constant for the per-27-outs stat model.

    Standard MLB FIP = (13*HR + 3*BB - 2*K) / IP * 9 + C, where C is set so
    that league FIP equals league ERA. In the O27 per-27-outs model we use
    27/outs in place of 9/IP, and C is re-fit each time against the live
    league totals so FIP stays anchored to ERA across calibration cycles.

    Falls back to 3.10 (a reasonable per-game baseline) if no games yet.
    """
    row = db.fetchone(
        f"""SELECT COALESCE(SUM(hr_allowed),0) as hr,
                   COALESCE(SUM(bb),0)         as bb,
                   COALESCE(SUM(k),0)          as k,
                   COALESCE(SUM(er),0)         as er,
                   COALESCE(SUM(outs_recorded),0) as outs
            FROM {_PSTATS_DEDUP_SQL} ps"""
    )
    outs = (row or {}).get("outs") or 0
    if not outs:
        return 3.10
    league_era = (row["er"] * 27.0) / outs
    raw_fip    = ((13 * row["hr"]) + (3 * row["bb"]) - (2 * row["k"])) * 27.0 / outs
    return league_era - raw_fip


def _aggregate_pitcher_rows(
    rows: list[dict],
    wl: dict[int, dict[str, int]] | None = None,
    fip_const: float | None = None,
) -> None:
    if fip_const is None:
        fip_const = _league_fip_const()
    for p in rows:
        outs = p.get("outs") or 0
        ip = outs / 3.0
        h = p.get("h") or 0
        bb = p.get("bb") or 0
        r = p.get("r") or 0
        er = p.get("er") or 0
        k = p.get("k") or 0
        hr = p.get("hr_allowed") or p.get("hra") or 0
        p["ip"] = ip
        # ERA uses earned runs and the per-27-outs denominator (Task #48).
        p["era"]   = (er * 27.0 / outs) if outs else 0.0
        # WHIP / K / BB are now per-27 outs (one full O27 game) instead of per-9 IP.
        p["whip"]  = ((bb + h) * 27.0 / outs) if outs else 0.0
        p["k27"]   = (k  * 27.0 / outs) if outs else 0.0
        p["bb27"]  = (bb * 27.0 / outs) if outs else 0.0
        # Kept under the legacy keys so older templates still render sensibly.
        p["k9"]    = p["k27"]
        p["bb9"]   = p["bb27"]
        p["so_bb"] = (k / bb) if bb else (k * 1.0)
        # FIP, fit against league ERA each batch (Task #50 calibration).
        if outs:
            p["fip"] = ((13 * hr) + (3 * bb) - (2 * k)) * 27.0 / outs + fip_const
        else:
            p["fip"] = 0.0
        if wl is not None:
            pid = p.get("player_id") or p.get("id")
            d = wl.get(pid, {"w": 0, "l": 0})
            p["w"] = d["w"]
            p["l"] = d["l"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    team_count = db.fetchone("SELECT COUNT(*) as n FROM teams")
    if not team_count or team_count["n"] == 0:
        return redirect(url_for("new_league_get"))

    today = get_current_sim_date()
    today_games = []
    if today:
        today_games = db.fetchall(
            """SELECT g.*,
                      ht.name as home_name, ht.abbrev as home_abbrev,
                      at.name as away_name, at.abbrev as away_abbrev
               FROM games g
               JOIN teams ht ON g.home_team_id = ht.id
               JOIN teams at ON g.away_team_id = at.id
               WHERE g.game_date = ?
               ORDER BY g.id""",
            (today,),
        )
        _attach_hits(today_games)

    # Yesterday's finals = the most recent date < today with played=1 games.
    yesterday = None
    yesterday_games: list[dict] = []
    last_played = db.fetchone(
        "SELECT MAX(game_date) AS d FROM games WHERE played = 1"
        + (" AND game_date < ?" if today else ""),
        (today,) if today else (),
    )
    if last_played and last_played["d"]:
        yesterday = last_played["d"]
        yesterday_games = db.fetchall(
            """SELECT g.*,
                      ht.name as home_name, ht.abbrev as home_abbrev,
                      at.name as away_name, at.abbrev as away_abbrev
               FROM games g
               JOIN teams ht ON g.home_team_id = ht.id
               JOIN teams at ON g.away_team_id = at.id
               WHERE g.played = 1 AND g.game_date = ?
               ORDER BY g.id""",
            (yesterday,),
        )
        _attach_hits(yesterday_games)

    divs = _divisions()

    # Top-5 leaders for AVG / HR / RBI / W / ERA / K
    games_played_row = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")
    games_played = games_played_row["n"] if games_played_row else 0
    min_pa = max(20, games_played // 30 * 8)
    min_outs = max(9, games_played // 30 * 5)

    top = {"avg": [], "hr": [], "rbi": [], "w": [], "era": [], "k": []}
    if games_played > 0:
        batting = db.fetchall(
            """SELECT p.id as player_id, p.name as player_name,
                      t.id as team_id, t.abbrev as team_abbrev,
                      SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                      SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                      SUM(bs.rbi) as rbi, SUM(bs.bb) as bb
               FROM game_batter_stats bs
               JOIN players p ON bs.player_id = p.id
               JOIN teams   t ON bs.team_id = t.id
               GROUP BY p.id
               HAVING SUM(bs.pa) >= ?""",
            (min_pa,),
        )
        _aggregate_batter_rows(batting)
        top["avg"] = sorted(batting, key=lambda x: x["avg"], reverse=True)[:5]
        top["hr"]  = sorted(batting, key=lambda x: x["hr"] or 0, reverse=True)[:5]
        top["rbi"] = sorted(batting, key=lambda x: x["rbi"] or 0, reverse=True)[:5]

        pitching = db.fetchall(
            f"""SELECT p.id as player_id, p.name as player_name,
                      t.id as team_id, t.abbrev as team_abbrev,
                      SUM(ps.outs_recorded) as outs,
                      SUM(ps.hits_allowed) as h, SUM(ps.runs_allowed) as r,
                      SUM(ps.er) as er,
                      SUM(ps.bb) as bb, SUM(ps.k) as k,
                      SUM(ps.hr_allowed) as hr_allowed
               FROM {_PSTATS_DEDUP_SQL} ps
               JOIN players p ON ps.player_id = p.id
               JOIN teams   t ON ps.team_id = t.id
               GROUP BY p.id
               HAVING SUM(ps.outs_recorded) >= ?""",
            (min_outs,),
        )
        wl = _pitcher_wl_map()
        _aggregate_pitcher_rows(pitching, wl)
        top["w"]   = sorted(pitching, key=lambda x: x["w"], reverse=True)[:5]
        top["era"] = sorted(pitching, key=lambda x: x["era"])[:5]
        top["k"]   = sorted(pitching, key=lambda x: x["k"] or 0, reverse=True)[:5]

    return render_template("index.html",
                           today=today,
                           today_games=today_games,
                           yesterday=yesterday,
                           yesterday_games=yesterday_games,
                           divisions=divs,
                           top=top,
                           win_pct=_win_pct,
                           gb=_gb)


@app.route("/standings")
def standings():
    leagues = _leagues_with_divisions()

    extras: dict[int, dict] = {}
    teams = db.fetchall("SELECT id FROM teams")
    for t in teams:
        tid = t["id"]
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
        last5 = [("w" if g["winner_id"] == tid else "l") for g in played[-5:]]
        extras[tid] = {
            "l10":    f"{w10}-{l10}",
            "streak": streak,
            "rs":     rs,
            "ra":     ra,
            "diff":   rs - ra,
            "last5":  last5,
        }

    return render_template("standings.html",
                           leagues=leagues,
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

    # Task #58: pull per-phase rows and group them. Phase 0 = regulation;
    # phase N>=1 = super-inning round N. We also build per-phase totals
    # rows (suitable for the Game Totals section in the template).
    away_batting_rows = db.fetchall(
        """SELECT bs.*, p.name as player_name, p.position
           FROM game_batter_stats bs JOIN players p ON bs.player_id = p.id
           WHERE bs.game_id = ? AND bs.team_id = ? ORDER BY bs.phase, bs.id""",
        (game_id, game["away_team_id"]))
    home_batting_rows = db.fetchall(
        """SELECT bs.*, p.name as player_name, p.position
           FROM game_batter_stats bs JOIN players p ON bs.player_id = p.id
           WHERE bs.game_id = ? AND bs.team_id = ? ORDER BY bs.phase, bs.id""",
        (game_id, game["home_team_id"]))
    away_pitching_rows = db.fetchall(
        """SELECT ps.*, p.name as player_name
           FROM game_pitcher_stats ps JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ? ORDER BY ps.phase, ps.id""",
        (game_id, game["away_team_id"]))
    home_pitching_rows = db.fetchall(
        """SELECT ps.*, p.name as player_name
           FROM game_pitcher_stats ps JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ? ORDER BY ps.phase, ps.id""",
        (game_id, game["home_team_id"]))

    team_phase_outs_rows = db.fetchall(
        """SELECT team_id, phase, unattributed_outs FROM team_phase_outs
           WHERE game_id = ?""", (game_id,))

    # Legacy data (pre-Task-#58) often has duplicate rows for the same
    # (player_id, game_id) because the schema lacked a UNIQUE constraint
    # and re-sims of the same game inserted parallel copies. New rows
    # are unique on (player_id, game_id, phase). Aggregate duplicates
    # here so the box score never shows the same player twice in one
    # phase or double-counts totals.
    _BAT_NUM = ("pa", "ab", "runs", "hits", "doubles", "triples",
                "hr", "rbi", "bb", "k", "stays", "outs_recorded")
    _PIT_NUM = ("batters_faced", "outs_recorded", "hits_allowed",
                "runs_allowed", "er", "bb", "k")

    def _dedup_by_player_phase(rows: list, num_fields: tuple) -> list:
        merged: dict[tuple, dict] = {}
        order: list[tuple] = []
        for r in rows:
            key = (r["phase"] or 0, r["player_id"])
            if key not in merged:
                merged[key] = dict(r)
                order.append(key)
            else:
                acc = merged[key]
                for f in num_fields:
                    acc[f] = (acc.get(f) or 0) + (r[f] or 0)
        return [merged[k] for k in order]

    def _group_by_phase(rows: list) -> dict:
        out: dict[int, list] = {}
        for r in rows:
            out.setdefault(r["phase"] or 0, []).append(r)
        return out

    def _aggregate_batting(rows: list) -> dict:
        agg = {f: 0 for f in ("pa", "ab", "runs", "hits", "doubles", "triples",
                              "hr", "rbi", "bb", "k", "stays", "outs_recorded")}
        for r in rows:
            for f in agg:
                agg[f] += (r[f] or 0)
        return agg

    def _aggregate_pitching(rows: list) -> dict:
        agg = {f: 0 for f in ("batters_faced", "outs_recorded", "hits_allowed",
                              "runs_allowed", "er", "bb", "k")}
        for r in rows:
            for f in agg:
                agg[f] += (r[f] or 0)
        return agg

    away_batting_rows = _dedup_by_player_phase(away_batting_rows, _BAT_NUM)
    home_batting_rows = _dedup_by_player_phase(home_batting_rows, _BAT_NUM)
    away_pitching_rows = _dedup_by_player_phase(away_pitching_rows, _PIT_NUM)
    home_pitching_rows = _dedup_by_player_phase(home_pitching_rows, _PIT_NUM)

    # Per-player Game Totals (one row per player across all phases).
    # Spec: "Game Totals — one consolidated row per player across all
    # phases." Distinct from the team-totals row at the bottom of each
    # per-phase table.
    def _consolidate_per_player(rows: list, num_fields: tuple) -> list:
        merged: dict[int, dict] = {}
        order: list[int] = []
        for r in rows:
            pid = r["player_id"]
            if pid not in merged:
                base = dict(r)
                base["phase"] = None  # consolidated row spans phases
                merged[pid] = base
                order.append(pid)
            else:
                acc = merged[pid]
                for f in num_fields:
                    acc[f] = (acc.get(f) or 0) + (r[f] or 0)
        return [merged[k] for k in order]

    away_batting_consolidated = _consolidate_per_player(away_batting_rows, _BAT_NUM)
    home_batting_consolidated = _consolidate_per_player(home_batting_rows, _BAT_NUM)
    away_pitching_consolidated = _consolidate_per_player(away_pitching_rows, _PIT_NUM)
    home_pitching_consolidated = _consolidate_per_player(home_pitching_rows, _PIT_NUM)

    away_batting_by_phase = _group_by_phase(away_batting_rows)
    home_batting_by_phase = _group_by_phase(home_batting_rows)
    away_pitching_by_phase = _group_by_phase(away_pitching_rows)
    home_pitching_by_phase = _group_by_phase(home_pitching_rows)

    # Determine which phases to render. Always include 0; include N>=1
    # only if any side actually played that phase (super-inning round).
    all_phases: set[int] = {0}
    for d in (away_batting_by_phase, home_batting_by_phase,
              away_pitching_by_phase, home_pitching_by_phase):
        all_phases.update(d.keys())
    phases = sorted(all_phases)
    si_rounds = max(0, max(phases) if phases else 0)

    # Line score: runs per phase, plus H and "team errors" placeholder.
    def _line_score(b_by_phase: dict) -> dict:
        runs_per = {ph: sum(r["runs"] or 0 for r in rows)
                    for ph, rows in b_by_phase.items()}
        hits_per = {ph: sum(r["hits"] or 0 for r in rows)
                    for ph, rows in b_by_phase.items()}
        return {
            "runs":  runs_per,
            "hits":  hits_per,
            "total_r": sum(runs_per.values()),
            "total_h": sum(hits_per.values()),
        }

    away_line = _line_score(away_batting_by_phase)
    home_line = _line_score(home_batting_by_phase)

    # Game Notes: per-side unattributed outs by phase (CS / FC / pickoff
    # outs the engine couldn't charge to a specific batter).
    notes: list[dict] = []
    team_name_by_id = {
        game["away_team_id"]: game["away_name"],
        game["home_team_id"]: game["home_name"],
    }
    for r in team_phase_outs_rows:
        if (r["unattributed_outs"] or 0) <= 0:
            continue
        phase_label = "Regulation" if r["phase"] == 0 else f"SI Round {r['phase']}"
        notes.append({
            "team":  team_name_by_id.get(r["team_id"], "?"),
            "phase": r["phase"],
            "phase_label": phase_label,
            "outs":  r["unattributed_outs"],
        })

    return render_template(
        "game.html",
        game=game,
        phases=phases,
        si_rounds=si_rounds,
        away_batting_by_phase=away_batting_by_phase,
        home_batting_by_phase=home_batting_by_phase,
        away_pitching_by_phase=away_pitching_by_phase,
        home_pitching_by_phase=home_pitching_by_phase,
        away_batting_total=_aggregate_batting(away_batting_rows),
        home_batting_total=_aggregate_batting(home_batting_rows),
        away_pitching_total=_aggregate_pitching(away_pitching_rows),
        home_pitching_total=_aggregate_pitching(home_pitching_rows),
        away_batting_consolidated=away_batting_consolidated,
        home_batting_consolidated=home_batting_consolidated,
        away_pitching_consolidated=away_pitching_consolidated,
        home_pitching_consolidated=home_pitching_consolidated,
        away_line=away_line,
        home_line=home_line,
        game_notes=notes,
        prev_game_id=(prev_game["id"] if prev_game else None),
        next_game_id=(next_game["id"] if next_game else None),
    )


# ---------------------------------------------------------------------------
# Players index (NEW) + leaders (renamed from /stats)
# ---------------------------------------------------------------------------

@app.route("/players")
def players():
    kind = request.args.get("kind", "batters")
    if kind not in ("batters", "pitchers", "both"):
        kind = "batters"
    selected_team_id = request.args.get("team", type=int)
    selected_pos = request.args.get("pos", "") or ""
    q = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 50

    where = []
    params: list = []
    if selected_team_id:
        where.append("p.team_id = ?")
        params.append(selected_team_id)
    if selected_pos:
        where.append("p.position = ?")
        params.append(selected_pos)
    if q:
        where.append("LOWER(p.name) LIKE ?")
        params.append(f"%{q.lower()}%")
    if kind == "batters":
        where.append("p.is_pitcher = 0")
    elif kind == "pitchers":
        where.append("p.is_pitcher = 1")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    total_row = db.fetchone(f"SELECT COUNT(*) AS n FROM players p{where_sql}", tuple(params))
    total = total_row["n"] if total_row else 0
    pages = max(1, math.ceil(total / per_page))
    if page > pages:
        page = pages
    offset = (page - 1) * per_page

    base = db.fetchall(
        f"""SELECT p.id, p.name, p.team_id, p.position, p.age, p.is_pitcher, p.is_joker, p.pitcher_role,
                   t.abbrev AS team_abbrev
            FROM players p JOIN teams t ON p.team_id = t.id
            {where_sql}
            ORDER BY p.name
            LIMIT ? OFFSET ?""",
        tuple(params) + (per_page, offset),
    )
    page_ids = [p["id"] for p in base]
    if not page_ids:
        return render_template(
            "players.html",
            kind=kind, batters=[], pitchers=[],
            all_teams=db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name"),
            all_positions=[r["position"] for r in db.fetchall("SELECT DISTINCT position FROM players ORDER BY position")],
            selected_team_id=selected_team_id, selected_pos=selected_pos, q=q,
            total=total, page=page, pages=pages,
        )

    ph = ",".join("?" * len(page_ids))

    batter_rows = []
    pitcher_rows = []

    if kind in ("batters", "both"):
        bstats = {
            r["player_id"]: r for r in db.fetchall(
                f"""SELECT bs.player_id,
                           COUNT(bs.game_id) AS gp,
                           SUM(bs.pa) AS pa, SUM(bs.ab) AS ab, SUM(bs.hits) AS h,
                           SUM(bs.doubles) AS d2, SUM(bs.triples) AS d3, SUM(bs.hr) AS hr,
                           SUM(bs.runs) AS r, SUM(bs.rbi) AS rbi,
                           SUM(bs.bb) AS bb, SUM(bs.k) AS k
                    FROM game_batter_stats bs
                    WHERE bs.player_id IN ({ph})
                    GROUP BY bs.player_id""",
                tuple(page_ids),
            )
        }
        for p in base:
            if p["is_pitcher"] and kind == "both":
                continue
            row = dict(p)
            s = bstats.get(p["id"], {})
            row.update(s)
            _aggregate_batter_rows([row])
            batter_rows.append(row)

    if kind in ("pitchers", "both"):
        pstats = {
            r["player_id"]: r for r in db.fetchall(
                f"""SELECT ps.player_id,
                           COUNT(ps.game_id) AS gp,
                           SUM(ps.outs_recorded) AS outs,
                           SUM(ps.hits_allowed) AS h, SUM(ps.runs_allowed) AS r,
                           SUM(ps.er) AS er,
                           SUM(ps.bb) AS bb, SUM(ps.k) AS k,
                           SUM(ps.hr_allowed) AS hr_allowed
                    FROM {_PSTATS_DEDUP_SQL} ps
                    WHERE ps.player_id IN ({ph})
                    GROUP BY ps.player_id""",
                tuple(page_ids),
            )
        }
        wl = _pitcher_wl_map()
        for p in base:
            if not p["is_pitcher"] and kind == "both":
                continue
            row = dict(p)
            s = pstats.get(p["id"], {})
            row.update(s)
            _aggregate_pitcher_rows([row], wl)
            pitcher_rows.append(row)

    return render_template(
        "players.html",
        kind=kind,
        batters=batter_rows,
        pitchers=pitcher_rows,
        all_teams=db.fetchall("SELECT id, name, abbrev FROM teams ORDER BY name"),
        all_positions=[r["position"] for r in db.fetchall("SELECT DISTINCT position FROM players ORDER BY position")],
        selected_team_id=selected_team_id,
        selected_pos=selected_pos,
        q=q,
        total=total, page=page, pages=pages,
    )


@app.route("/stats")
def stats_redirect():
    return redirect(url_for("leaders"), code=302)


@app.route("/leaders")
def leaders():
    games_played = db.fetchone("SELECT COUNT(*) as n FROM games WHERE played = 1")["n"]
    if games_played == 0:
        return render_template("leaders.html",
                               games_played=0, batting=[], pitching=[],
                               min_pa=0, min_outs=0)

    # Scale qualifying minimums by games-per-team, not by total league games.
    # MLB rule of thumb: 3.1 PA/team-game qualifies for batting title; here we
    # use ~1× games/team for batting and ~1× games/team in outs for pitching,
    # so leaders are visible from week one and grow naturally with the season.
    num_teams = db.fetchone("SELECT COUNT(*) as n FROM teams")["n"] or 2
    games_per_team = max(1, (games_played * 2) // num_teams)
    min_pa   = max(3, games_per_team)        # ~1 PA/team-game
    min_outs = max(3, games_per_team)        # ~1 out/team-game (very lenient)

    batting = db.fetchall(
        """SELECT p.id as player_id, p.name as player_name, p.position,
                  t.abbrev as team_abbrev, t.id as team_id,
                  COUNT(bs.game_id) as g,
                  SUM(bs.pa) as pa, SUM(bs.ab) as ab, SUM(bs.hits) as h,
                  SUM(bs.doubles) as d2, SUM(bs.triples) as d3, SUM(bs.hr) as hr,
                  SUM(bs.runs) as r, SUM(bs.rbi) as rbi,
                  SUM(bs.bb) as bb, SUM(bs.k) as k, SUM(bs.stays) as stays
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           JOIN teams   t ON bs.team_id = t.id
           GROUP BY p.id
           HAVING SUM(bs.pa) >= ?""",
        (min_pa,),
    )
    _aggregate_batter_rows(batting)
    # SB doesn't exist in schema — set to 0 so leaders.html can list it without errors.
    for b in batting:
        b.setdefault("sb", 0)

    pitching = db.fetchall(
        f"""SELECT p.id as player_id, p.name as player_name,
                  t.abbrev as team_abbrev, t.id as team_id,
                  COUNT(ps.game_id) as g,
                  SUM(ps.batters_faced)  as bf,
                  SUM(ps.outs_recorded)  as outs,
                  SUM(ps.hits_allowed)   as h,
                  SUM(ps.runs_allowed)   as r,
                  SUM(ps.er)             as er,
                  SUM(ps.bb)             as bb,
                  SUM(ps.k)              as k,
                  SUM(ps.hr_allowed)     as hr_allowed
           FROM {_PSTATS_DEDUP_SQL} ps
           JOIN players p ON ps.player_id = p.id
           JOIN teams   t ON ps.team_id = t.id
           GROUP BY p.id
           HAVING SUM(ps.outs_recorded) >= ?""",
        (min_outs,),
    )
    # Shared helper now produces era/whip/k27/bb27/fip directly (Task #50).
    wl = _pitcher_wl_map()
    _aggregate_pitcher_rows(pitching, wl)
    for p in pitching:
        outs = p["outs"] or 0
        # Extra rate stats only the leaders page surfaces.
        p["ra27"]   = (p["r"]  * 27.0 / outs) if outs else 0.0
        p["k_pct"]  = (p["k"]  / p["bf"]) if p["bf"] else 0.0
        p["bb_pct"] = (p["bb"] / p["bf"]) if p["bf"] else 0.0
        # OS% = share of a complete game (27 outs) recorded per appearance.
        p["os_pct"] = (outs / (27.0 * p["g"])) if p["g"] else 0.0

    return render_template(
        "leaders.html",
        games_played=games_played,
        min_pa=min_pa, min_outs=min_outs,
        batting=batting, pitching=pitching,
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
           ORDER BY g.game_date DESC, g.id DESC LIMIT 50""",
        (player_id,),
    )
    # Dedup pitching log to one row per game appearance (Task #57 audit).
    pitching_log = db.fetchall(
        f"""SELECT ps.*, g.game_date, g.id as game_id, g.home_team_id, g.away_team_id,
                  ht.abbrev as home_abbrev, at.abbrev as away_abbrev
           FROM {_PSTATS_DEDUP_SQL} ps
           JOIN games g ON ps.game_id = g.id
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE ps.player_id = ?
           ORDER BY g.game_date DESC, g.id DESC LIMIT 50""",
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
        f"""SELECT COUNT(*) as g, SUM(batters_faced) as bf, SUM(outs_recorded) as outs,
                   SUM(hits_allowed) as h, SUM(runs_allowed) as r,
                   SUM(er) as er,
                   SUM(bb) as bb, SUM(k) as k,
                   SUM(hr_allowed) as hr_allowed
            FROM {_PSTATS_DEDUP_SQL} ps WHERE ps.player_id = ?""",
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
        # Run through the shared helper so ERA/WHIP/K27/BB27/FIP all use the
        # same per-27-outs definitions (and the freshly-fit FIP constant).
        _aggregate_pitcher_rows([pt_totals])
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
        """SELECT t.*, COUNT(p.id) as player_count
           FROM teams t LEFT JOIN players p ON p.team_id = t.id
           GROUP BY t.id
           ORDER BY t.league, t.division, t.name"""
    )
    return render_template("teams.html", teams=teams_list, win_pct=_win_pct)


@app.route("/team/<int:team_id>")
def team_detail(team_id: int):
    team = db.fetchone("SELECT * FROM teams WHERE id = ?", (team_id,))
    if not team:
        abort(404)

    roster = db.fetchall(
        "SELECT * FROM players WHERE team_id = ? ORDER BY is_pitcher, position, id",
        (team_id,),
    )
    ids = [p["id"] for p in roster]
    bstats: dict[int, dict] = {}
    pstats: dict[int, dict] = {}
    if ids:
        ph = ",".join("?" * len(ids))
        for r in db.fetchall(
            f"""SELECT player_id,
                       COUNT(game_id) AS gp,
                       SUM(pa) AS pa, SUM(ab) AS ab, SUM(hits) AS h,
                       SUM(doubles) AS d2, SUM(triples) AS d3, SUM(hr) AS hr,
                       SUM(runs) AS r, SUM(rbi) AS rbi,
                       SUM(bb) AS bb, SUM(k) AS k
                FROM game_batter_stats
                WHERE player_id IN ({ph}) GROUP BY player_id""",
            tuple(ids),
        ):
            bstats[r["player_id"]] = r
        for r in db.fetchall(
            f"""SELECT ps.player_id,
                       COUNT(ps.game_id) AS gp,
                       SUM(ps.outs_recorded) AS outs,
                       SUM(ps.hits_allowed) AS h, SUM(ps.runs_allowed) AS r, SUM(ps.er) AS er,
                       SUM(ps.bb) AS bb, SUM(ps.k) AS k,
                       SUM(ps.hr_allowed) AS hr_allowed
                FROM {_PSTATS_DEDUP_SQL} ps
                WHERE ps.player_id IN ({ph}) GROUP BY ps.player_id""",
            tuple(ids),
        ):
            pstats[r["player_id"]] = r

    wl = _pitcher_wl_map()
    batters: list[dict] = []
    pitchers: list[dict] = []
    for p in roster:
        if p["is_pitcher"]:
            row = dict(p)
            row.update(pstats.get(p["id"], {}))
            _aggregate_pitcher_rows([row], wl)
            pitchers.append(row)
        else:
            row = dict(p)
            row.update(bstats.get(p["id"], {}))
            _aggregate_batter_rows([row])
            batters.append(row)

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
        (team_id, team_id),
    )
    return render_template("team.html",
                           team=team,
                           batters=batters,
                           pitchers=pitchers,
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

    from o27v2.season_archive import set_active_league_meta
    db.drop_all()
    db.init_db()
    seed_league(rng_seed=rng_seed, config_id=config_id)
    seed_schedule(config_id=config_id, rng_seed=rng_seed)
    set_active_league_meta(rng_seed, config_id)

    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.route("/api/sim", methods=["POST"])
def api_sim():
    data      = request.get_json(silent=True) or {}
    n         = int(data.get("n", 5))
    n         = max(1, min(n, 50))
    seed_base = data.get("seed_base")
    results   = simulate_next_n(n, seed_base=seed_base)
    resync_sim_clock()
    return jsonify({"simulated": len(results), "results": results})


@app.route("/api/sim/today", methods=["POST"])
def api_sim_today():
    if is_season_complete():
        return jsonify(_sim_response(None, None, []))
    current = get_current_sim_date()
    results = simulate_date(current)
    next_day = (_dt.date.fromisoformat(current) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(_clamp_to_last(next_day))
    return jsonify(_sim_response(current, current, results))


@app.route("/api/sim/week", methods=["POST"])
def api_sim_week():
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
    from o27v2.season_archive import archive_current_season
    if is_season_complete():
        # Already complete — archive if we haven't snapshotted this season yet.
        sid = archive_current_season(run_invariants=True)
        resp = _sim_response(None, None, [])
        resp["archived_season_id"] = sid
        return jsonify(resp)
    current = get_current_sim_date()
    last    = get_last_scheduled_date()
    results = simulate_through(last)
    next_day = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat()
    advance_sim_clock(next_day)
    # Auto-archive on completion: simulating through the last scheduled date
    # finishes the season, so snapshot leaders/standings/invariants now.
    archived_id = None
    if is_season_complete():
        try:
            archived_id = archive_current_season(run_invariants=True)
        except Exception as e:
            archived_id = None
            app.logger.exception("auto-archive after /api/sim/season failed: %s", e)
    resp = _sim_response(current, last, results)
    resp["archived_season_id"] = archived_id
    return jsonify(resp)


# ---------------------------------------------------------------------------
# Task #62: season lifecycle (reset + multi-season + history)
# ---------------------------------------------------------------------------

@app.route("/api/sim/multi-season", methods=["POST"])
def api_sim_multi_season():
    """Start an N-season run in the background. Returns 202; the dashboard
    polls /api/sim/multi-season/status for progress (current season number,
    games simmed) and redirects to /seasons when the run completes."""
    from o27v2.season_archive import start_multi_season
    data = request.get_json(silent=True) or {}
    n         = int(data.get("n", 3))
    base_seed = int(data.get("seed", 42))
    config_id = (data.get("config_id") or "30teams").strip()
    if config_id not in get_league_configs():
        return jsonify({"ok": False, "error": f"unknown config: {config_id}"}), 400
    started, msg = start_multi_season(n, base_seed=base_seed, config_id=config_id)
    return jsonify({"ok": started, "message": msg}), (202 if started else 409)


@app.route("/api/sim/multi-season/status")
def api_sim_multi_season_status():
    from o27v2.season_archive import multi_season_status
    return jsonify(multi_season_status())


@app.route("/api/season/archive", methods=["POST"])
def api_season_archive():
    """Snapshot the current DB into the seasons history (no reset)."""
    from o27v2.season_archive import archive_current_season
    sid = archive_current_season(run_invariants=True)
    if sid is None:
        return jsonify({"ok": False, "message": "Nothing to archive (no played games)."}), 400
    return jsonify({"ok": True, "season_id": sid})


@app.route("/api/season/reset", methods=["POST"])
def api_season_reset():
    """One-click 'New season' — optionally archive first, then drop+reseed.

    Body: {archive: bool, config_id: str, rng_seed: int}
    """
    from o27v2.league import seed_league
    from o27v2.schedule import seed_schedule
    from o27v2.season_archive import archive_current_season, set_active_league_meta

    data = request.get_json(silent=True) or {}
    new_config_id = (data.get("config_id") or "30teams").strip()
    new_rng_seed  = int(data.get("rng_seed", 42))
    do_archive    = bool(data.get("archive", True))

    if new_config_id not in get_league_configs():
        return jsonify({"ok": False, "error": f"unknown config: {new_config_id}"}), 400

    # Archive the *current* season FIRST, attributed to the seed/config that
    # actually produced it (read from sim_meta inside archive_current_season).
    # Do not pass the new seed/config — that would mislabel the archived row.
    archived_id = None
    if do_archive:
        try:
            archived_id = archive_current_season(run_invariants=True)
        except Exception as e:
            return jsonify({"ok": False, "error": f"archive failed: {e}"}), 500

    # Now drop + reseed for the new season, and record the new meta so the
    # *next* archive will be attributed correctly.
    db.drop_all()
    db.init_db()
    seed_league(rng_seed=new_rng_seed, config_id=new_config_id)
    seed_schedule(config_id=new_config_id, rng_seed=new_rng_seed)
    set_active_league_meta(new_rng_seed, new_config_id)
    resync_sim_clock()
    return jsonify({"ok": True, "archived_season_id": archived_id})


@app.route("/seasons")
def seasons_index():
    from o27v2.season_archive import compute_live_season
    rows = db.fetchall(
        "SELECT * FROM seasons ORDER BY season_number DESC"
    )
    live = compute_live_season()
    return render_template("seasons.html", seasons=rows, live=live)


@app.route("/seasons/<int:season_id>")
def season_detail(season_id: int):
    season = db.fetchone("SELECT * FROM seasons WHERE id = ?", (season_id,))
    if not season:
        abort(404)
    standings = db.fetchall(
        """SELECT * FROM season_standings
            WHERE season_id = ?
            ORDER BY league, division,
                     (wins * 1.0 / NULLIF(wins+losses,0)) DESC,
                     wins DESC""",
        (season_id,),
    )
    bat = db.fetchall(
        """SELECT * FROM season_batting_leaders
            WHERE season_id = ? ORDER BY category, rank""",
        (season_id,),
    )
    pit = db.fetchall(
        """SELECT * FROM season_pitching_leaders
            WHERE season_id = ? ORDER BY category, rank""",
        (season_id,),
    )
    bat_by_cat: dict[str, list[dict]] = {}
    for r in bat:
        bat_by_cat.setdefault(r["category"], []).append(r)
    pit_by_cat: dict[str, list[dict]] = {}
    for r in pit:
        pit_by_cat.setdefault(r["category"], []).append(r)

    # Group standings by league/division
    leagues: dict[str, dict[str, list[dict]]] = {}
    for r in standings:
        leagues.setdefault(r["league"] or "—", {}).setdefault(
            r["division"] or "—", []
        ).append(r)

    return render_template(
        "season_detail.html",
        season=season,
        leagues=leagues,
        batting=bat_by_cat,
        pitching=pit_by_cat,
    )


@app.route("/api/sim/<int:game_id>", methods=["POST"])
def api_sim_game(game_id: int):
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
    return jsonify(list(get_league_configs().values()))


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})
