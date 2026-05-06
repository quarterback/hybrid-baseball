"""
O27 Web Interface — Flask + Jinja2.

Routes:
  GET       /                    Home dashboard
  GET/POST  /sim                 Game setup (GET) + redirect (POST)
  GET       /game                Run game (?seed=N&visitors=ABB&home=ABB)
  GET       /game/<game_id>      View stored game result
  GET       /random              Redirect to /game with random teams + seed
  GET       /standings           League standings
  GET       /schedule            Schedule / results (?team=ABB to filter)
  GET       /leaders             Batting + pitching leaders (operational view)
  GET       /stats-leaders       Alias → /leaders
  GET       /stats               O27 stats-browsing site (Blueprint)
  GET       /teams               Team list
  GET       /team/<abbrev>       Team page (Roster|Stats|Pitching|Schedule tabs)
  GET       /player/<team>/<slug> Player detail page
  GET       /players             Player browser
  GET       /my-team             My Team placeholder
  GET       /roster              Roster placeholder
  GET       /lineups             Lineups placeholder
  GET       /trade               Trade placeholder
  GET       /manager             Manager placeholder
  GET       /api/health          Health check
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import random
import sys

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, flash, get_flashed_messages, send_from_directory,
)

from o27.engine.state import GameState, Team, Player, PitchEntry
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer
from o27.main import make_foxes, make_bears
import o27.data as data

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "o27-dev-secret-key")

from o27.stats_site.blueprint import stats_bp
app.register_blueprint(stats_bp)

_SEASON_START = _dt.date(2026, 4, 1)


@app.template_filter("to_grade")
def to_grade(v: float) -> int:
    """Convert 0.0–1.0 float attribute to 20–80 scouting integer (50 = avg)."""
    try:
        return max(20, min(80, round(20 + float(v) * 60)))
    except (TypeError, ValueError):
        return 50


@app.template_filter("pitch_consistency")
def pitch_consistency(pitch_variance: float) -> float:
    """Convert pitch_variance to a 0.0–1.0 Consistency score (inverted, clamped)."""
    from o27 import config as _c
    try:
        return max(0.0, min(1.0, 1.0 - float(pitch_variance) / _c.PITCH_VARIANCE_MAX))
    except (TypeError, ValueError, ZeroDivisionError):
        return 1.0


@app.template_filter("release_label")
def release_label(v: float) -> str:
    """Convert release_angle float to display string."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "Sidearm"
    if v <= 0.25:
        return "Submarine"
    if v <= 0.45:
        return "Low Sidearm"
    if v <= 0.62:
        return "Sidearm"
    if v <= 0.80:
        return "High Sidearm"
    return "Three-Quarter"


# ---------------------------------------------------------------------------
# Context processor — inject globals into every template
# ---------------------------------------------------------------------------

@app.context_processor
def inject_globals():
    total = len(data._RECENT)
    # Advance season calendar: 2 games per day
    day_offset = total // 2
    season_date = _SEASON_START + _dt.timedelta(days=day_offset)
    week = (day_offset // 7) + 1
    return {
        "g_total":    total,
        "g_date":     season_date.strftime("%b %d"),
        "g_week":     week,
        "g_flashes":  get_flashed_messages(with_categories=True),
    }


# ---------------------------------------------------------------------------
# Team → engine object converter
# ---------------------------------------------------------------------------

def _team_obj(team_data: dict, team_id: str) -> Team:
    roster: list[Player] = []
    for i, p in enumerate(team_data["players"]):
        pid = f"{team_id}_{team_data['abbrev']}{i}"

        # Convert serialised repertoire dicts → PitchEntry objects.
        raw_rep = p.get("repertoire") or []
        repertoire = [
            PitchEntry(
                pitch_type=r["pitch_type"],
                quality=float(r.get("quality", 0.5)),
                usage_weight=float(r.get("usage_weight", 1.0)),
            )
            for r in raw_rep
        ]

        roster.append(Player(
            player_id=pid,
            name=p["name"],
            skill=p["skill"],
            speed=p["speed"],
            pitcher_skill=p["pitcher_skill"],
            command=float(p.get("command",  0.5)),
            movement=float(p.get("movement", 0.5)),
            stamina=float(p.get("stamina",   0.5)),
            grit=float(p.get("grit",         0.5)),
            pitch_variance=float(p.get("pitch_variance", 0.0)),
            release_angle=float(p.get("release_angle",  0.5)),
            pitcher_archetype=p.get("pitcher_archetype", ""),
            repertoire=repertoire,
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

def _fmt_ip(outs: int) -> str:
    """Format outs-recorded as innings-pitched string (e.g. 7 outs → '2.1')."""
    return f"{outs // 3}.{outs % 3}"


def _fmt_era(r: int, outs: int) -> str:
    if outs <= 0:
        return "—" if r == 0 else "∞"
    return f"{r / outs * 27:.2f}"


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
                "pa":      s.pa             if s else 0,
                "ab":      ab,
                "runs":    s.runs           if s else 0,
                "hits":    hits,
                "doubles": s.doubles        if s else 0,
                "triples": s.triples        if s else 0,
                "hr":      s.hr             if s else 0,
                "rbi":     s.rbi            if s else 0,
                "bb":      s.bb             if s else 0,
                "k":       s.k              if s else 0,
                "hbp":     s.hbp            if s else 0,
                "or_":     s.outs_recorded  if s else 0,
                "avg":     f"{hits/ab:.3f}" if ab > 0 else ".000",
                "h_ab":    f"{hits/ab:.3f}" if ab > 0 else ".000",
            })
        return rows

    # Pitcher aggregates from spell_log
    pitcher_map: dict[str, dict] = {}
    for spell in final_state.spell_log:
        pid = spell.pitcher_id
        if pid not in pitcher_map:
            pitcher_map[pid] = {
                "name": spell.pitcher_name,
                "bf": 0, "outs": 0, "r": 0, "er": 0,
                "h": 0, "bb": 0, "k": 0, "hbp": 0,
                "hr": 0, "p": 0, "out": 0,
            }
        ps = pitcher_map[pid]
        ps["bf"]   += spell.batters_faced
        ps["outs"] += spell.outs_recorded
        ps["r"]    += spell.runs_allowed
        ps["er"]   += spell.runs_allowed   # ER == R (no errors tracked in O27)
        ps["h"]    += spell.hits_allowed
        ps["bb"]   += spell.bb
        ps["k"]    += spell.k
        ps["hbp"]  += spell.hbp
        ps["hr"]   += spell.hr_allowed
        ps["p"]    += spell.pitches_thrown
        ps["out"]   = spell.out_when_pulled  # last spell's out count for this pitcher

    # Add derived OS% / ERA / WHIP to each pitcher row
    for ps in pitcher_map.values():
        outs = ps["outs"]
        ps["os_pct"] = f"{round(outs / 27 * 100)}%" if outs > 0 else "0%"
        ps["era"]    = _fmt_era(ps["r"], outs)
        ps["whip"]   = f"{(ps['bb'] + ps['h']) / outs * 27:.2f}" if outs > 0 else "—"

    v_pids = {p.player_id for p in final_state.visitors.roster}
    h_pids = {p.player_id for p in final_state.home.roster}

    v_batting  = _batting_rows(final_state.visitors)
    h_batting  = _batting_rows(final_state.home)
    v_pitching = [v for pid, v in pitcher_map.items() if pid in v_pids]
    h_pitching = [v for pid, v in pitcher_map.items() if pid in h_pids]
    return v_batting, h_batting, v_pitching, h_pitching


def _winner_loser_pitchers(winner_id: str, v_pitching: list, h_pitching: list) -> tuple[str, str]:
    """Return (winner_pitcher_name, loser_pitcher_name)."""
    w_pit = v_pitching if winner_id == "visitors" else h_pitching
    l_pit = h_pitching if winner_id == "visitors" else v_pitching
    wp = max(w_pit, key=lambda x: x["outs"])["name"] if w_pit else "—"
    lp = max(l_pit, key=lambda x: x["r"])["name"]    if l_pit else "—"
    return wp, lp


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    teams    = data.load_teams()
    recent   = data.get_schedule(10)
    stgs     = data.get_standings()
    leaders  = data.get_leaders("hits", 5)
    upcoming = data.get_upcoming(3)
    quick_v  = teams[0]["abbrev"] if teams else "FOX"
    quick_h  = teams[1]["abbrev"] if len(teams) > 1 else "BEA"
    return render_template("index.html",
        active="home",
        recent=recent, standings=stgs, leaders=leaders,
        upcoming=upcoming, quick_v=quick_v, quick_h=quick_h,
    )


@app.route("/sim", methods=["GET", "POST"])
def sim():
    if request.method == "POST":
        try:
            seed = int(request.form.get("seed", "") or random.randint(0, 9999))
        except (TypeError, ValueError):
            seed = random.randint(0, 9999)
        visitors = (request.form.get("visitors", "") or "").strip() or None
        home_    = (request.form.get("home",     "") or "").strip() or None
        # Auto-pick teams if not specified (topbar quick-sim)
        if not visitors or not home_:
            teams_list = data.load_teams()
            if len(teams_list) >= 2:
                pair = random.sample(teams_list, 2)
                visitors = visitors or pair[0]["abbrev"]
                home_    = home_    or pair[1]["abbrev"]

        # Run the game inline so we can flash the actual score
        final_state, log_lines, renderer = _run(seed, visitors, home_)
        v_score   = final_state.score.get("visitors", 0)
        h_score   = final_state.score.get("home", 0)
        winner_id = final_state.winner or ""
        v_abbrev  = visitors or "FOX"
        h_abbrev  = home_    or "BEA"
        game_id   = data.make_game_id(seed, v_abbrev, h_abbrev)

        v_batting, h_batting, v_pitching, h_pitching = _structured_stats(final_state, renderer)
        v_hits = sum(r.get("hits", 0) for r in v_batting)
        h_hits = sum(r.get("hits", 0) for r in h_batting)
        wp, lp = _winner_loser_pitchers(winner_id, v_pitching, h_pitching)

        data.store_game(game_id, {
            "game_id":        game_id, "seed": seed,
            "visitors_abbrev": v_abbrev, "home_abbrev": h_abbrev,
            "visitors_name":  final_state.visitors.name,
            "home_name":      final_state.home.name,
            "v_score": v_score, "h_score": h_score,
            "winner_id": winner_id,
            "super_flag": final_state.super_inning_number > 0,
            "v_batting": v_batting, "h_batting": h_batting,
            "v_pitching": v_pitching, "h_pitching": h_pitching,
            "winner_pitcher": wp, "loser_pitcher": lp,
        })

        flash(
            f"▶ {v_abbrev} {v_score}–{h_score} {h_abbrev} "
            f"(W: {wp}) — seed {seed}",
            "info",
        )

        # Redirect back to the originating page if one was provided
        next_url = (request.form.get("next", "") or "").strip()
        if next_url and next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect(url_for("view_game", game_id=game_id))

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
        active="sim",
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

    v_batting, h_batting, v_pitching, h_pitching = _structured_stats(final_state, renderer)

    v_hits = sum(r.get("hits", 0) for r in v_batting)
    h_hits = sum(r.get("hits", 0) for r in h_batting)

    wp, lp = _winner_loser_pitchers(winner_id, v_pitching, h_pitching)

    data.store_game(game_id, {
        "game_id":           game_id,
        "seed":              seed,
        "visitors_abbrev":   v_abbrev,
        "home_abbrev":       h_abbrev,
        "visitors_name":     final_state.visitors.name,
        "home_name":         final_state.home.name,
        "v_score":           v_score,
        "h_score":           h_score,
        "winner_id":         winner_id,
        "super_flag":        final_state.super_inning_number > 0,
        "v_batting":         v_batting,
        "h_batting":         h_batting,
        "v_pitching":        v_pitching,
        "h_pitching":        h_pitching,
        "winner_pitcher":    wp,
        "loser_pitcher":     lp,
    })

    sections = _split_log(log_lines)

    return render_template("game.html",
        active="boxscores",
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
        v_hits=v_hits,
        h_hits=h_hits,
    )


@app.route("/game/<game_id>")
def view_game(game_id):
    g = data.get_game(game_id)
    if not g:
        parts = game_id.split("_", 2)
        if len(parts) == 3:
            try:
                seed = int(parts[0])
                return redirect(url_for("game", seed=seed, visitors=parts[1], home=parts[2]))
            except ValueError:
                pass
        return redirect(url_for("index"))

    final_state, log_lines, renderer = _run(g["seed"], g["visitors_abbrev"], g["home_abbrev"])
    sections   = _split_log(log_lines)
    v_batting  = g.get("v_batting",  [])
    h_batting  = g.get("h_batting",  [])
    v_pitching = g.get("v_pitching", [])
    h_pitching = g.get("h_pitching", [])

    v_hits = sum(r.get("hits", 0) for r in v_batting)
    h_hits = sum(r.get("hits", 0) for r in h_batting)

    return render_template("game.html",
        active="boxscores",
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
        v_hits=v_hits,
        h_hits=h_hits,
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
        active="standings",
        rows=rows, total_games=len(data._RECENT))


@app.route("/schedule")
def schedule():
    team_filter = request.args.get("team", "").strip().upper()
    all_teams   = data.load_teams()
    games = data.get_schedule(60, team=team_filter)
    return render_template("schedule.html",
        active="schedule",
        games=games, team_filter=team_filter, all_teams=all_teams)


@app.route("/leaders")
@app.route("/stats-leaders")
def stats():
    any_data = bool(data._RECENT)
    return render_template("stats.html",
        active="stats",
        any_data=any_data,
        by_hits=data.get_leaders("hits"),
        by_avg= data.get_leaders("avg"),
        by_hr=  data.get_leaders("hr"),
        by_rbi= data.get_leaders("rbi"),
        by_or=  data.get_leaders("or_"),
        by_k=   data.get_leaders("k"),
        by_h_ab=data.get_leaders("h_ab"),
        by_pit_k=   data.get_pitching_leaders("k"),
        by_pit_era= data.get_pitching_leaders("era"),
        by_pit_os=  data.get_pitching_leaders("outs"),
    )


@app.route("/teams")
def teams_page():
    teams    = data.load_teams()
    standings_ = data.get_standings()
    records  = {s["abbrev"]: s for s in standings_}
    return render_template("teams.html", active="teams", teams=teams, records=records)


@app.route("/team/<abbrev>")
def team_page(abbrev):
    team = data.get_team(abbrev)
    if not team:
        return redirect(url_for("teams_page"))

    standings_ = {s["abbrev"]: s for s in data.get_standings()}
    rec = standings_.get(abbrev, {
        "w": 0, "l": 0, "gp": 0, "pct": 0.0,
        "r_for": 0, "r_against": 0, "gb": "—",
        "streak": "—", "l10_w": 0, "l10_l": 0,
    })

    recent_games   = data.get_schedule(40, team=abbrev)[:10]
    team_batting   = data.get_team_batting(abbrev)
    team_pitching  = data.get_team_pitching(abbrev)
    tab = request.args.get("tab", "roster")

    return render_template("team.html",
        active="teams",
        team=team, record=rec,
        recent_games=recent_games,
        team_batting=team_batting,
        team_pitching=team_pitching,
        tab=tab,
    )


def _player_game_log(team_abbrev: str, player_name: str, limit: int = 10) -> list[dict]:
    """Build a per-game batting log for one player."""
    game_log: list[dict] = []
    for g in data.get_schedule(100, team=team_abbrev):
        side = "v" if g.get("visitors_abbrev") == team_abbrev else "h"
        for row in g.get(f"{side}_batting", []):
            if row["name"] == player_name and row.get("pa", 0) > 0:
                game_log.append({
                    "game_id": g["game_id"],
                    "opp":     g["home_abbrev"] if side == "v" else g["visitors_abbrev"],
                    "ha":      "@" if side == "v" else "vs",
                    **row,
                })
                break
        if len(game_log) >= limit:
            break
    return game_log


@app.route("/player/<player_id>")
def player_page(player_id):
    """Primary player route — canonical ID e.g. NYY_Christopher_Almora."""
    result = data.get_player(player_id)
    if not result:
        return redirect(url_for("teams_page"))
    team   = result["team"]
    player = result["player"]
    stats            = data.get_player_stats(team["abbrev"], player["name"])
    game_log         = _player_game_log(team["abbrev"], player["name"])
    pitcher_game_log = (
        data.get_pitcher_game_log(team["abbrev"], player["name"])
        if player.get("is_pitcher") else []
    )
    return render_template("player.html",
        active="players",
        team=team, player=player, stats=stats,
        game_log=game_log, pitcher_game_log=pitcher_game_log)


@app.route("/player/<team_abbrev>/<slug>")
def player_page_alias(team_abbrev, slug):
    """Backward-compat alias — redirects to canonical /player/<player_id>."""
    result = data.get_player_by_team_slug(team_abbrev, slug)
    if result:
        return redirect(url_for("player_page", player_id=result["player_id"]))
    return redirect(url_for("team_page", abbrev=team_abbrev))


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
            slug = p["name"].lower().replace(" ", "_").replace(".", "")
            rows.append({"team": t, "player": p, "slug": slug})
            if len(rows) >= 200:
                break
        if len(rows) >= 200:
            break

    total = sum(len(t["players"]) for t in all_teams)
    return render_template("players.html",
        active="players",
        players=rows, total=total,
        team_count=len(all_teams),
        all_teams=all_teams,
        q=q, filter_team=filter_team,
    )


# ---------------------------------------------------------------------------
# Placeholder manage pages
# ---------------------------------------------------------------------------

@app.route("/my-team")
def my_team():
    return render_template("placeholder.html",
        active="my-team",
        title="My Team", icon="🏟",
        blurb="Full team management — lineups, depth charts, and stats — coming soon.")


@app.route("/roster")
def roster():
    return render_template("placeholder.html",
        active="roster",
        title="Roster", icon="📋",
        blurb="View and manage your full team roster, assign positions, and review player attributes.")


@app.route("/lineups")
def lineups():
    return render_template("placeholder.html",
        active="lineups",
        title="Lineups", icon="📋",
        blurb="Set your batting order, define joker deployment rules, and save lineup presets.")


@app.route("/trade")
def trade():
    return render_template("placeholder.html",
        active="trade",
        title="Trade Center", icon="🔄",
        blurb="Propose and evaluate trades with AI-driven value analysis.")


@app.route("/manager")
def manager():
    return render_template("placeholder.html",
        active="manager",
        title="Manager Settings", icon="⚙",
        blurb="Configure in-game AI strategy: aggression, joker timing, and pitching decisions.")


# ---------------------------------------------------------------------------
# API + misc
# ---------------------------------------------------------------------------

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/stats-site")
def stats_site_redirect():
    return redirect("/stats")


_STATIC = os.path.join(os.path.dirname(__file__), "static")


@app.route("/manifest.json")
def pwa_manifest():
    return send_from_directory(_STATIC, "manifest.json",
                               mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(_STATIC, "sw.js",
                               mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
