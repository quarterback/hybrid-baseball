"""
O27 Web Interface — Flask + Jinja2.

Routes:
  GET  /                  Home dashboard
  GET  /sim               Game setup + simulate
  GET  /game              Run game (?seed=N&visitors=ABB&home=ABB) → results
  GET  /game/<game_id>    View stored game result
  GET  /random            Redirect to /game with random teams + seed
  GET  /standings         League standings
  GET  /schedule          Schedule / results
  GET  /stats             Batting leaders
  GET  /teams             Team list
  GET  /team/<abbrev>     Team page + roster
  GET  /players           Player browser
  GET  /api/health        Health check
"""

from __future__ import annotations

import json
import os
import random
import sys

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

from flask import Flask, render_template, request, redirect, url_for, jsonify

from o27.engine.state import GameState, Team, Player
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer
from o27.main import make_foxes, make_bears
import o27.data as data

app = Flask(__name__, template_folder="templates")


# ---------------------------------------------------------------------------
# Team → engine object converter
# ---------------------------------------------------------------------------

def _team_obj(team_data: dict, team_id: str) -> Team:
    roster: list[Player] = []
    for i, p in enumerate(team_data["players"]):
        pid = f"{team_id}_{team_data['abbrev']}{i}"
        roster.append(Player(
            player_id=pid,
            name=p["name"],
            skill=p["skill"],
            speed=p["speed"],
            pitcher_skill=p["pitcher_skill"],
            stay_aggressiveness=p["stay_aggressiveness"],
            contact_quality_threshold=p["contact_quality_threshold"],
            is_pitcher=p["is_pitcher"],
            is_joker=p["is_joker"],
        ))
    jokers = [p for p in roster if p.is_joker]
    full_name = f"{team_data['city']} {team_data['name']}".strip()
    return Team(
        team_id=team_id,
        name=full_name,
        roster=roster,
        lineup=list(roster),
        jokers_available=list(jokers),
    )


# ---------------------------------------------------------------------------
# Game runner
# ---------------------------------------------------------------------------

def _run(seed: int, visitors_abbrev: str | None, home_abbrev: str | None):
    rng = random.Random(seed)
    provider = ProbabilisticProvider(rng)
    renderer = Renderer()

    v_data = data.get_team(visitors_abbrev) if visitors_abbrev else None
    h_data = data.get_team(home_abbrev)     if home_abbrev     else None

    visitors = _team_obj(v_data, "visitors") if v_data else make_foxes()
    home     = _team_obj(h_data, "home")     if h_data else make_bears()

    state = GameState(visitors=visitors, home=home)
    final_state, log_lines = run_game(state, provider, renderer)
    return final_state, log_lines, renderer


# ---------------------------------------------------------------------------
# Log splitter
# ---------------------------------------------------------------------------

def _split_log(lines: list[str]) -> dict:
    halves: list[dict] = []
    current_half: dict | None = None
    box_lines: list[str] = []
    part_lines: list[str] = []
    spell_lines: list[str] = []
    super_lines: list[str] = []

    in_box = in_part = in_spell = in_super = False

    for line in lines:
        s = line.strip()

        if s.startswith("─" * 10):
            if current_half and current_half["header"]:
                halves.append(current_half)
            in_box = in_part = in_spell = in_super = False
            current_half = {"header": "", "lines": []}
            continue

        if s.startswith("═" * 10):
            if current_half and current_half["header"]:
                halves.append(current_half)
                current_half = None
            in_box = not in_box if not in_part and not in_spell else False
            continue

        if "BOX SCORE" in s or ("BATTING" in s and "PA" in s):
            if current_half and current_half["header"]:
                halves.append(current_half)
                current_half = None
            in_box = True; in_part = in_spell = in_super = False

        if "PARTNERSHIP LOG" in s:
            in_box = False; in_part = True; in_spell = in_super = False

        if "PITCHER SPELL LOG" in s or "SPELL LOG" in s:
            in_part = False; in_spell = True; in_box = in_super = False

        if "SUPER-INNING" in s and "TIEBREAKER" in s:
            in_spell = False; in_super = True; in_box = in_part = False

        if "GAME OVER" in s:
            in_box = in_part = in_spell = in_super = False
            current_half = None
            continue

        if in_super:
            super_lines.append(line)
        elif in_spell:
            spell_lines.append(line)
        elif in_part:
            part_lines.append(line)
        elif in_box:
            box_lines.append(line)
        elif current_half is not None:
            if not current_half["header"] and s and not s.startswith("─"):
                current_half["header"] = s
            else:
                current_half["lines"].append(line)

    if current_half and current_half["header"]:
        halves.append(current_half)

    return {
        "halves": halves,
        "box_score": box_lines,
        "partnerships": part_lines,
        "spells": spell_lines,
        "super": super_lines,
    }


# ---------------------------------------------------------------------------
# Build structured batting / pitching rows for HTML tables
# ---------------------------------------------------------------------------

def _structured_stats(final_state, renderer: Renderer) -> tuple[list, list, list, list]:
    """Return (v_batting, h_batting, v_pitching, h_pitching) as plain dicts."""
    bs = renderer.batter_stats      # dict player_id → BatterStats

    pos_labels = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C", "P"]

    def _batting_rows(team):
        rows = []
        pos_idx = 0
        for p in team.roster:
            pos = pos_labels[pos_idx] if not p.is_joker and pos_idx < len(pos_labels) else ""
            if not p.is_joker:
                pos_idx += 1
            if p.is_joker:
                archetype = "Power" if p.speed < 0.50 else ("Speed" if p.speed > 0.65 else "Contact")
                pos = f"JKR-{archetype[:3]}"
            else:
                archetype = ""
            s = bs.get(p.player_id)
            ab   = s.ab   if s else 0
            hits = s.hits if s else 0
            rows.append({
                "name": p.name, "pos": pos,
                "is_joker": p.is_joker, "archetype": archetype,
                "pa":      s.pa      if s else 0,
                "ab":      ab,
                "runs":    s.runs    if s else 0,
                "hits":    hits,
                "doubles": s.doubles if s else 0,
                "triples": s.triples if s else 0,
                "hr":      s.hr      if s else 0,
                "rbi":     s.rbi     if s else 0,
                "bb":      s.bb      if s else 0,
                "k":       s.k       if s else 0,
                "hbp":     s.hbp     if s else 0,
                "sty":     s.sty     if s else 0,
                "avg":     f"{hits/ab:.3f}" if ab > 0 else ".000",
            })
        return rows

    # Pitcher aggregates from spell_log
    pitcher_map: dict[str, dict] = {}
    for spell in final_state.spell_log:
        pid = spell.pitcher_id
        if pid not in pitcher_map:
            pitcher_map[pid] = {
                "name": spell.pitcher_name,
                "bf": 0, "outs": 0, "r": 0, "h": 0, "bb": 0, "k": 0, "hbp": 0,
            }
        ps = pitcher_map[pid]
        ps["bf"]   += spell.batters_faced
        ps["outs"] += spell.outs_recorded
        ps["r"]    += spell.runs_allowed
        ps["h"]    += spell.hits_allowed
        ps["bb"]   += spell.bb
        ps["k"]    += spell.k
        ps["hbp"]  += spell.hbp

    v_pids = {p.player_id for p in final_state.visitors.roster}
    h_pids = {p.player_id for p in final_state.home.roster}

    v_batting  = _batting_rows(final_state.visitors)
    h_batting  = _batting_rows(final_state.home)
    v_pitching = [v for pid, v in pitcher_map.items() if pid in v_pids]
    h_pitching = [v for pid, v in pitcher_map.items() if pid in h_pids]
    return v_batting, h_batting, v_pitching, h_pitching


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    teams   = data.load_teams()
    recent  = list(reversed(data.get_schedule(10)))
    stgs    = data.get_standings()
    leaders = data.get_leaders("hits", 5)
    quick_v = teams[0]["abbrev"] if teams else "FOX"
    quick_h = teams[1]["abbrev"] if len(teams) > 1 else "BEA"
    return render_template("index.html",
        recent=recent, standings=stgs, leaders=leaders,
        quick_v=quick_v, quick_h=quick_h,
    )


@app.route("/sim")
def sim():
    teams = data.load_teams()
    roster_map = {
        t["abbrev"]: {
            "display": t["display"],
            "players": [
                {
                    "name":          p["name"],
                    "pos":           p["position"],
                    "skill":         p["skill"],
                    "speed":         p["speed"],
                    "pitcher_skill": p["pitcher_skill"],
                    "is_joker":      p["is_joker"],
                    "archetype":     p.get("joker_archetype", ""),
                }
                for p in t["players"]
            ],
        }
        for t in teams
    }
    default_v = teams[0]["abbrev"] if teams else "FOX"
    default_h = teams[1]["abbrev"] if len(teams) > 1 else "BEA"
    return render_template("sim.html",
        teams=teams,
        roster_map_json=json.dumps(roster_map),
        default_v=default_v,
        default_h=default_h,
    )


@app.route("/game")
def game():
    try:
        seed = int(request.args.get("seed", 0))
    except (TypeError, ValueError):
        seed = 0

    visitors_abbrev = request.args.get("visitors") or request.args.get("visitors_team") or None
    home_abbrev     = request.args.get("home")     or request.args.get("home_team")     or None

    final_state, log_lines, renderer = _run(seed, visitors_abbrev, home_abbrev)

    v_score   = final_state.score.get("visitors", 0)
    h_score   = final_state.score.get("home", 0)
    winner_id = final_state.winner or ""
    v_abbrev  = visitors_abbrev or "FOX"
    h_abbrev  = home_abbrev     or "BEA"

    game_id = data.make_game_id(seed, v_abbrev, h_abbrev)

    # Structured batting / pitching data for HTML tables
    v_batting, h_batting, v_pitching, h_pitching = _structured_stats(final_state, renderer)

    # Store result
    data.store_game(game_id, {
        "game_id":       game_id,
        "seed":          seed,
        "visitors_abbrev": v_abbrev,
        "home_abbrev":     h_abbrev,
        "visitors_name": final_state.visitors.name,
        "home_name":     final_state.home.name,
        "v_score":       v_score,
        "h_score":       h_score,
        "winner_id":     winner_id,
        "super_flag":    final_state.super_inning_number > 0,
        "v_batting":     v_batting,
        "h_batting":     h_batting,
        "v_pitching":    v_pitching,
        "h_pitching":    h_pitching,
    })

    sections = _split_log(log_lines)

    return render_template("game.html",
        seed=seed,
        game_id=game_id,
        visitors_name=final_state.visitors.name,
        home_name=final_state.home.name,
        visitors_abbrev=v_abbrev,
        home_abbrev=h_abbrev,
        visitors_score=v_score,
        home_score=h_score,
        winner_id=winner_id,
        winner_name=(final_state.visitors.name if winner_id == "visitors" else final_state.home.name),
        super_flag=final_state.super_inning_number > 0,
        log_lines=log_lines,
        sections=sections,
        prev_seed=seed - 1,
        next_seed=seed + 1,
        v_batting=v_batting,
        h_batting=h_batting,
        v_pitching=v_pitching,
        h_pitching=h_pitching,
    )


@app.route("/game/<game_id>")
def view_game(game_id):
    g = data.get_game(game_id)
    if not g:
        # Try to parse game_id as seed_VABB_HABB and re-run
        parts = game_id.split("_", 2)
        if len(parts) == 3:
            try:
                seed = int(parts[0])
                return redirect(url_for("game", seed=seed, visitors=parts[1], home=parts[2]))
            except ValueError:
                pass
        return redirect(url_for("index"))

    # Re-run the game to get fresh log (renderer state is ephemeral)
    final_state, log_lines, renderer = _run(g["seed"], g["visitors_abbrev"], g["home_abbrev"])
    sections = _split_log(log_lines)
    v_batting  = g.get("v_batting",  [])
    h_batting  = g.get("h_batting",  [])
    v_pitching = g.get("v_pitching", [])
    h_pitching = g.get("h_pitching", [])

    return render_template("game.html",
        seed=g["seed"],
        game_id=game_id,
        visitors_name=g["visitors_name"],
        home_name=g["home_name"],
        visitors_abbrev=g["visitors_abbrev"],
        home_abbrev=g["home_abbrev"],
        visitors_score=g["v_score"],
        home_score=g["h_score"],
        winner_id=g["winner_id"],
        winner_name=(g["visitors_name"] if g["winner_id"] == "visitors" else g["home_name"]),
        super_flag=g["super_flag"],
        log_lines=log_lines,
        sections=sections,
        prev_seed=g["seed"] - 1,
        next_seed=g["seed"] + 1,
        v_batting=v_batting,
        h_batting=h_batting,
        v_pitching=v_pitching,
        h_pitching=h_pitching,
    )


@app.route("/random")
def random_game():
    teams = data.load_teams()
    seed  = random.randint(0, 9999)
    if len(teams) >= 2:
        pair = random.sample(teams, 2)
        return redirect(url_for("game", seed=seed,
                                visitors=pair[0]["abbrev"],
                                home=pair[1]["abbrev"]))
    return redirect(url_for("game", seed=seed))


@app.route("/standings")
def standings():
    rows = data.get_standings()
    return render_template("standings.html",
        rows=rows, total_games=len(data._RECENT))


@app.route("/schedule")
def schedule():
    games = data.get_schedule(40)
    return render_template("schedule.html", games=games)


@app.route("/stats")
def stats():
    any_data = bool(data._RECENT)
    return render_template("stats.html",
        any_data=any_data,
        by_hits=data.get_leaders("hits"),
        by_avg= data.get_leaders("avg"),
        by_hr=  data.get_leaders("hr"),
        by_rbi= data.get_leaders("rbi"),
        by_sty= data.get_leaders("sty"),
        by_k=   data.get_leaders("k"),
    )


@app.route("/teams")
def teams_page():
    teams = data.load_teams()
    # Build per-team W/L from standings
    standings = data.get_standings()
    records = {s["abbrev"]: s for s in standings}
    return render_template("teams.html", teams=teams, records=records)


@app.route("/team/<abbrev>")
def team_page(abbrev):
    team = data.get_team(abbrev)
    if not team:
        return redirect(url_for("teams_page"))

    standings = {s["abbrev"]: s for s in data.get_standings()}
    rec = standings.get(abbrev, {"w": 0, "l": 0, "gp": 0, "pct": 0.0, "r_for": 0, "r_against": 0})

    # Recent games involving this team
    all_games = data.get_schedule(40)
    recent_games = [g for g in all_games
                    if g["visitors_abbrev"] == abbrev or g["home_abbrev"] == abbrev][:8]

    return render_template("team.html",
        team=team, record=rec, recent_games=recent_games)


@app.route("/players")
def players():
    q           = request.args.get("q", "").strip().lower()
    filter_team = request.args.get("team", "")
    all_teams   = data.load_teams()

    rows = []
    for t in all_teams:
        if filter_team and t["abbrev"] != filter_team:
            continue
        for p in t["players"]:
            if q and q not in p["name"].lower():
                continue
            rows.append({"team": t, "player": p})
            if len(rows) >= 200:
                break
        if len(rows) >= 200:
            break

    total = sum(len(t["players"]) for t in all_teams)
    return render_template("players.html",
        players=rows, total=total,
        team_count=len(all_teams),
        all_teams=all_teams,
        q=q, filter_team=filter_team,
    )


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "o27-web"})


@app.route("/stats-site")
def stats_site_redirect():
    return redirect("/stats")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
