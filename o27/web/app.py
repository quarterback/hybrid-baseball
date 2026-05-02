"""
O27 Web Interface — Flask + Jinja2.

Routes:
  GET  /        Landing page with seed form + recent seeds list
  GET  /game    Run a game (?seed=N) and render full results
"""

from __future__ import annotations

import os
import random
import sys

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

from flask import Flask, render_template, request, redirect, url_for

from o27.engine.state import GameState
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer
from o27.main import make_foxes, make_bears

app = Flask(__name__, template_folder="templates")

_RECENT_SEEDS: list[int] = []
_MAX_RECENT = 10


def _run(seed: int):
    rng = random.Random(seed)
    provider = ProbabilisticProvider(rng)
    state = GameState(visitors=make_foxes(), home=make_bears())
    renderer = Renderer()
    final_state, log_lines = run_game(state, provider, renderer)
    return final_state, log_lines, renderer


def _split_log(lines: list[str]):
    """
    Split raw log lines into sections for the template.
    Returns dict with keys: halves, box_score, partnerships, spells, super.
    Each 'halves' entry: {"header": str, "lines": [str]}
    """
    halves = []
    current_half = None
    box_score_lines = []
    partnership_lines = []
    spell_lines = []
    super_lines = []
    game_over_lines = []

    in_box = False
    in_part = False
    in_spell = False
    in_super = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("─" * 10) and current_half is None:
            in_box = in_part = in_spell = in_super = False
            current_half = {"header": "", "lines": []}
            continue

        if stripped.startswith("─" * 10) and current_half is not None:
            if current_half["header"]:
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

        if "BOX SCORE" in stripped or "BATTING" in stripped and "PA" in stripped:
            if current_half and current_half["header"]:
                halves.append(current_half)
                current_half = None
            in_box = True
            in_part = in_spell = in_super = False

        if "PARTNERSHIP LOG" in stripped:
            in_box = False
            in_part = True
            in_spell = in_super = False

        if "PITCHER SPELL LOG" in stripped or "SPELL LOG" in stripped:
            in_part = False
            in_spell = True
            in_box = in_super = False

        if "SUPER-INNING" in stripped and "TIEBREAKER" in stripped:
            in_spell = False
            in_super = True
            in_box = in_part = False

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


def _box_score_tables(state):
    """Extract structured batter + pitcher rows from final state for HTML tables."""
    from o27.stats.batter import BatterStats
    from o27.stats.pitcher import PitcherStats

    return {
        "visitors_name": state.visitors.name,
        "home_name": state.home.name,
        "visitors_score": state.score.get("visitors", 0),
        "home_score": state.score.get("home", 0),
        "winner": state.winner,
    }


@app.route("/")
def index():
    return render_template("index.html", recent=list(reversed(_RECENT_SEEDS)))


@app.route("/game")
def game():
    try:
        seed = int(request.args.get("seed", 0))
    except (TypeError, ValueError):
        seed = 0

    final_state, log_lines, renderer = _run(seed)

    if seed not in _RECENT_SEEDS:
        _RECENT_SEEDS.append(seed)
    if len(_RECENT_SEEDS) > _MAX_RECENT:
        _RECENT_SEEDS.pop(0)

    sections = _split_log(log_lines)

    v_score = final_state.score.get("visitors", 0)
    h_score = final_state.score.get("home", 0)
    winner_id = final_state.winner
    winner_name = ""
    if winner_id == "visitors":
        winner_name = final_state.visitors.name
    elif winner_id == "home":
        winner_name = final_state.home.name

    super_flag = final_state.super_inning_number > 0

    return render_template(
        "game.html",
        seed=seed,
        visitors_name=final_state.visitors.name,
        home_name=final_state.home.name,
        visitors_score=v_score,
        home_score=h_score,
        winner_name=winner_name,
        super_flag=super_flag,
        log_lines=log_lines,
        sections=sections,
        prev_seed=seed - 1,
        next_seed=seed + 1,
    )


@app.route("/random")
def random_game():
    seed = random.randint(0, 9999)
    return redirect(url_for("game", seed=seed))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
