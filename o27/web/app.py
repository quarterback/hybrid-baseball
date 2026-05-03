"""
O27 Web Interface — Flask + Jinja2.

Routes:
  GET  /        Game setup page — pick teams, seed, simulate
  GET  /game    Run a game (?seed=N&visitors=ABBREV&home=ABBREV) → results
  GET  /random  Redirect to /game with a random seed
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

from flask import Flask, render_template, request, redirect, url_for

from o27.engine.state import GameState, Team, Player
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer
from o27.main import make_foxes, make_bears
from o27 import config as cfg

app = Flask(__name__, template_folder="templates")

_RECENT: list[dict] = []          # [{seed, visitors, home, v_score, h_score}]
_MAX_RECENT = 8

# ---------------------------------------------------------------------------
# Team / roster loader
# ---------------------------------------------------------------------------

_TEAMS_DB_PATH = os.path.join(
    _root, "o27v2", "data", "teams_database.json"
)
_NAMES_DIR = os.path.join(_root, "o27v2", "data", "names")

_teams_cache: list[dict] | None = None   # list of {abbrev, name, city, level, players:[dict]}


def _load_name_pools() -> dict:
    pools: dict = {}
    for region in ("usa", "latin", "japan_korea", "other"):
        p = os.path.join(_NAMES_DIR, f"{region}.json")
        if os.path.exists(p):
            with open(p) as fh:
                pools[region] = json.load(fh)
    return pools


_name_pools_cache: dict | None = None


def _name_pools() -> dict:
    global _name_pools_cache
    if _name_pools_cache is None:
        _name_pools_cache = _load_name_pools()
    return _name_pools_cache


_REGION_WEIGHTS = [("usa", 0.50), ("latin", 0.30), ("japan_korea", 0.10), ("other", 0.10)]
_POSITIONS = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C", "P"]
_JOKER_LABELS = ["Power", "Speed", "Contact"]


def _weighted_region(rng: random.Random) -> str:
    r = rng.random()
    cum = 0.0
    for region, w in _REGION_WEIGHTS:
        cum += w
        if r < cum:
            return region
    return "usa"


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _generate_roster(team_seed: int) -> list[dict]:
    """Generate a reproducible 12-player roster (9 position + 3 jokers)."""
    rng = random.Random(team_seed)
    pools = _name_pools()
    used: set[str] = set()

    def _name() -> str:
        for _ in range(200):
            region = _weighted_region(rng)
            pool = pools.get(region, {})
            firsts = pool.get("first_names", ["J."])
            lasts = pool.get("last_names", ["Smith"])
            n = f"{rng.choice(firsts)} {rng.choice(lasts)}"
            if n not in used:
                used.add(n)
                return n
        return f"Player {rng.randint(100, 999)}"

    profile = team_seed % 5
    skill_base = [0.52, 0.50, 0.54, 0.48, 0.51][profile]
    speed_base = [0.52, 0.60, 0.48, 0.55, 0.50][profile]
    pitcher_base = [0.54, 0.48, 0.52, 0.56, 0.50][profile]

    players = []
    for pos in _POSITIONS:
        is_p = pos == "P"
        skill = _clamp(rng.gauss(skill_base, 0.10))
        speed = _clamp(rng.gauss(speed_base, 0.12))
        pskill = _clamp(rng.gauss(pitcher_base, 0.12)) if is_p else _clamp(rng.gauss(0.35, 0.08))
        stay_a = _clamp(rng.gauss(0.40, 0.12))
        cqt = _clamp(rng.gauss(0.45, 0.08))
        players.append({
            "name": _name(),
            "position": pos,
            "is_pitcher": is_p,
            "is_joker": False,
            "skill": round(skill, 3),
            "speed": round(speed, 3),
            "pitcher_skill": round(pskill, 3),
            "stay_aggressiveness": round(stay_a, 3),
            "contact_quality_threshold": round(cqt, 3),
        })

    joker_archetypes = [
        {"label": "Power",   "skill_mu": 0.68, "speed_mu": 0.42, "stay_mu": 0.25},
        {"label": "Speed",   "skill_mu": 0.62, "speed_mu": 0.78, "stay_mu": 0.55},
        {"label": "Contact", "skill_mu": 0.65, "speed_mu": 0.58, "stay_mu": 0.65},
    ]
    for arch in joker_archetypes:
        skill = _clamp(rng.gauss(arch["skill_mu"], 0.07))
        speed = _clamp(rng.gauss(arch["speed_mu"], 0.08))
        stay_a = _clamp(rng.gauss(arch["stay_mu"], 0.08))
        cqt = _clamp(rng.gauss(0.40, 0.07))
        players.append({
            "name": _name(),
            "position": f"JKR-{arch['label'][:3]}",
            "is_pitcher": False,
            "is_joker": True,
            "joker_archetype": arch["label"],
            "skill": round(skill, 3),
            "speed": round(speed, 3),
            "pitcher_skill": round(rng.gauss(0.38, 0.08), 3),
            "stay_aggressiveness": round(stay_a, 3),
            "contact_quality_threshold": round(cqt, 3),
        })
    return players


def _player_to_dict(p: "Player", position: str = "") -> dict:
    """Convert an engine Player to the team-dict player format."""
    archetype = ""
    pos_label = position
    if p.is_joker:
        archetype = "Power" if p.speed < 0.50 else ("Speed" if p.speed > 0.65 else "Contact")
        pos_label = f"JKR-{archetype[:3]}"
    elif p.is_pitcher:
        pos_label = "P"
    return {
        "name": p.name,
        "position": pos_label,
        "is_pitcher": p.is_pitcher,
        "is_joker": p.is_joker,
        "joker_archetype": archetype,
        "skill": round(p.skill, 3),
        "speed": round(p.speed, 3),
        "pitcher_skill": round(p.pitcher_skill, 3),
        "stay_aggressiveness": round(p.stay_aggressiveness, 3),
        "contact_quality_threshold": round(p.contact_quality_threshold, 3),
    }


def _team_to_dict(team: "Team", abbrev: str, city: str = "", level: str = "") -> dict:
    """Convert an engine Team to the load_teams dict format."""
    pos_labels = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C", "P"]
    players = []
    pos_idx = 0
    for p in team.roster:
        pos = pos_labels[pos_idx] if not p.is_joker and pos_idx < len(pos_labels) else ""
        if not p.is_joker:
            pos_idx += 1
        players.append(_player_to_dict(p, pos))
    full_name = f"{city} {team.name}".strip() if city else team.name
    return {
        "abbrev": abbrev,
        "name": team.name,
        "city": city,
        "level": level or "Classic",
        "display": full_name,
        "players": players,
    }


def load_teams() -> list[dict]:
    """Load all teams with generated rosters. Cached after first call."""
    global _teams_cache
    if _teams_cache is not None:
        return _teams_cache

    if not os.path.exists(_TEAMS_DB_PATH):
        foxes = make_foxes()
        bears = make_bears()
        _teams_cache = [
            _team_to_dict(foxes, "FOX", "", "Classic"),
            _team_to_dict(bears, "BEA", "", "Classic"),
        ]
        return _teams_cache

    with open(_TEAMS_DB_PATH) as fh:
        raw = json.load(fh)

    teams = []
    for i, t in enumerate(raw):
        abbrev = t.get("abbreviation") or t.get("abbrev", f"T{i:02d}")
        key = (abbrev + t.get("name", "")).encode()
        team_seed = int(hashlib.sha256(key).hexdigest(), 16) & 0xFFFFFFFF
        teams.append({
            "abbrev": abbrev,
            "name": t.get("name", abbrev),
            "city": t.get("city", ""),
            "level": t.get("level", "MLB"),
            "display": f"{t.get('city', '')} {t.get('name', abbrev)}".strip(),
            "players": _generate_roster(team_seed),
        })

    _teams_cache = teams
    return teams


def _find_team(abbrev: str) -> dict | None:
    for t in load_teams():
        if t["abbrev"] == abbrev:
            return t
    return None


def _team_obj(team_data: dict, team_id: str) -> Team:
    """Convert a team dict (from load_teams) into a GameState Team object."""
    roster: list[Player] = []
    for i, p in enumerate(team_data["players"]):
        pid = f"{team_data['abbrev']}{i}"
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


def _run(seed: int, visitors_abbrev: str | None, home_abbrev: str | None):
    """Run a game. Uses DB teams when available, falls back to hardcoded."""
    rng = random.Random(seed)
    provider = ProbabilisticProvider(rng)
    renderer = Renderer()

    v_data = _find_team(visitors_abbrev) if visitors_abbrev else None
    h_data = _find_team(home_abbrev) if home_abbrev else None

    visitors = _team_obj(v_data, "visitors") if v_data else make_foxes()
    home = _team_obj(h_data, "home") if h_data else make_bears()

    state = GameState(visitors=visitors, home=home)
    final_state, log_lines = run_game(state, provider, renderer)
    return final_state, log_lines, renderer


# ---------------------------------------------------------------------------
# Log splitter (unchanged from original)
# ---------------------------------------------------------------------------

def _split_log(lines: list[str]) -> dict:
    halves: list[dict] = []
    current_half: dict | None = None
    box_score_lines: list[str] = []
    partnership_lines: list[str] = []
    spell_lines: list[str] = []
    super_lines: list[str] = []
    game_over_lines: list[str] = []

    in_box = in_part = in_spell = in_super = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("─" * 10):
            if current_half and current_half["header"]:
                halves.append(current_half)
            in_box = in_part = in_spell = in_super = False
            current_half = {"header": "", "lines": []}
            continue

        if stripped.startswith("═" * 10):
            if current_half and current_half["header"]:
                halves.append(current_half)
                current_half = None
            in_box = not in_box if not in_part and not in_spell else False
            continue

        if "BOX SCORE" in stripped or ("BATTING" in stripped and "PA" in stripped):
            if current_half and current_half["header"]:
                halves.append(current_half)
                current_half = None
            in_box = True
            in_part = in_spell = in_super = False

        if "PARTNERSHIP LOG" in stripped:
            in_box = False; in_part = True; in_spell = in_super = False

        if "PITCHER SPELL LOG" in stripped or "SPELL LOG" in stripped:
            in_part = False; in_spell = True; in_box = in_super = False

        if "SUPER-INNING" in stripped and "TIEBREAKER" in stripped:
            in_spell = False; in_super = True; in_box = in_part = False

        if "GAME OVER" in stripped:
            in_box = in_part = in_spell = in_super = False
            current_half = None
            game_over_lines.append(line)
            continue

        if in_super:
            super_lines.append(line)
        elif in_spell:
            spell_lines.append(line)
        elif in_part:
            partnership_lines.append(line)
        elif in_box:
            box_score_lines.append(line)
        elif current_half is not None:
            if not current_half["header"] and stripped and not stripped.startswith("─"):
                current_half["header"] = stripped
            else:
                current_half["lines"].append(line)

    if current_half and current_half["header"]:
        halves.append(current_half)

    return {
        "halves": halves,
        "box_score": box_score_lines,
        "partnerships": partnership_lines,
        "spells": spell_lines,
        "super": super_lines,
        "game_over": game_over_lines,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    teams = load_teams()
    # Build a compact JSON blob for the JS roster-preview switcher
    roster_map = {
        t["abbrev"]: {
            "display": t["display"],
            "players": [
                {
                    "name":         p["name"],
                    "pos":          p["position"],
                    "skill":        p["skill"],
                    "speed":        p["speed"],
                    "pitcher_skill": p["pitcher_skill"],
                    "is_joker":     p["is_joker"],
                    "archetype":    p.get("joker_archetype", ""),
                }
                for p in t["players"]
            ],
        }
        for t in teams
    }
    default_v = teams[0]["abbrev"] if teams else "FOX"
    default_h = teams[1]["abbrev"] if len(teams) > 1 else "BEA"
    return render_template(
        "index.html",
        teams=teams,
        roster_map_json=json.dumps(roster_map),
        default_v=default_v,
        default_h=default_h,
        recent=list(reversed(_RECENT)),
        has_db=len(teams) > 0,
    )


@app.route("/game")
def game():
    try:
        seed = int(request.args.get("seed", 0))
    except (TypeError, ValueError):
        seed = 0

    visitors_abbrev = request.args.get("visitors") or None
    home_abbrev = request.args.get("home") or None

    final_state, log_lines, renderer = _run(seed, visitors_abbrev, home_abbrev)

    entry = {
        "seed": seed,
        "visitors": final_state.visitors.name,
        "home": final_state.home.name,
        "v_score": final_state.score.get("visitors", 0),
        "h_score": final_state.score.get("home", 0),
        "visitors_abbrev": visitors_abbrev or "FOX",
        "home_abbrev": home_abbrev or "BEA",
    }
    _RECENT[:] = [r for r in _RECENT if r["seed"] != seed]
    _RECENT.append(entry)
    if len(_RECENT) > _MAX_RECENT:
        _RECENT.pop(0)

    sections = _split_log(log_lines)
    v_score = final_state.score.get("visitors", 0)
    h_score = final_state.score.get("home", 0)
    winner_id = final_state.winner
    winner_name = ""
    if winner_id == "visitors":
        winner_name = final_state.visitors.name
    elif winner_id == "home":
        winner_name = final_state.home.name

    return render_template(
        "game.html",
        seed=seed,
        visitors_name=final_state.visitors.name,
        home_name=final_state.home.name,
        visitors_abbrev=visitors_abbrev or "",
        home_abbrev=home_abbrev or "",
        visitors_score=v_score,
        home_score=h_score,
        winner_name=winner_name,
        super_flag=final_state.super_inning_number > 0,
        log_lines=log_lines,
        sections=sections,
        prev_seed=seed - 1,
        next_seed=seed + 1,
    )


@app.route("/random")
def random_game():
    teams = load_teams()
    seed = random.randint(0, 9999)
    if len(teams) >= 2:
        pair = random.sample(teams, 2)
        return redirect(url_for("game", seed=seed,
                                visitors=pair[0]["abbrev"],
                                home=pair[1]["abbrev"]))
    return redirect(url_for("game", seed=seed))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
