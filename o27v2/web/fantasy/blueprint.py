"""CapSpace — O27's daily-fantasy front-end, served at ``/fantasy``.

A standalone app with its own design system (like ``/almanac``): a React
prototype shipped verbatim as static assets, fed real data from the active
save. The page injects ``window.O27_RATES`` (the engine's currency snapshot)
and ``window.__CAPSPACE_DATA__`` (tonight's real slate + player pool +
contests); the bundled JS falls back to its mock data when the blob is absent.

Contests, entries, and the live leaderboard are served by the JSON API below,
backed by :mod:`contests` (computed field + par; persisted user entries).
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, render_template, request

from o27v2 import currency
from . import data as slate_data
from . import contests as dfs
from . import streak as streakgame

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOG = logging.getLogger(__name__)

capspace_bp = Blueprint(
    "capspace",
    __name__,
    url_prefix="/fantasy",
    template_folder=os.path.join(_HERE, "templates"),
    static_folder=os.path.join(_HERE, "static"),
    # Flask concatenates url_prefix + static_url_path, so this resolves to
    # /fantasy/_static/… (a leading "/fantasy" here would double it).
    static_url_path="/_static",
)


def _contest_cards(slate_date: str) -> list[dict]:
    """Map persisted contest rows into the design's lobby-card shape."""
    cards = []
    for c in dfs.list_contests(slate_date):
        cards.append({
            "id": c["id"], "name": c["name"], "kind": c["kind"],
            "color": c["color"], "badge": c["badge"],
            "fee": c["fee"], "prize": c["prize_pool"], "top": c["top_prize"],
            "entries": c["entries"], "cap": c["field_size"],
        })
    return cards


def _safe_slate() -> dict | None:
    """Build the real slate (+ contests), degrading to mock (None) on any
    error so the page always renders."""
    try:
        blob = slate_data.build_slate_data()
        if blob and blob.get("SLATE_DATE"):
            try:
                blob["CONTESTS"] = _contest_cards(blob["SLATE_DATE"])
            except Exception:
                _LOG.exception("CapSpace contest build failed; using mock contests")
        return blob
    except Exception:  # pragma: no cover - defensive; never 500 the app
        _LOG.exception("CapSpace slate build failed; falling back to mock data")
        return None


@capspace_bp.route("/")
def home():
    return render_template(
        "capspace.html",
        capspace_data=_safe_slate(),
        currency_rates=currency.rates_for_js(),
    )


@capspace_bp.route("/api/slate")
def api_slate():
    """JSON slate endpoint — the same blob injected into the page (or null)."""
    return jsonify(_safe_slate())


@capspace_bp.route("/api/enter", methods=["POST"])
def api_enter():
    """Persist a built lineup against a contest. Body: {contest_id, player_ids[]}."""
    body = request.get_json(silent=True) or {}
    try:
        contest_id = int(body.get("contest_id"))
        player_ids = list(body.get("player_ids") or [])
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Bad request."}), 400
    result = dfs.enter(contest_id, player_ids)
    return jsonify(result), (200 if result.get("ok") else 400)


@capspace_bp.route("/api/contest/<int:contest_id>")
def api_contest(contest_id: int):
    """Live board for a contest: your score, the computed field, par, rank."""
    res = dfs.contest_results(contest_id)
    if res is None:
        return jsonify({"error": "No results (slate has no pool yet)."}), 404
    return jsonify(res)


@capspace_bp.route("/api/entries")
def api_entries():
    """The user's entries with live rank/points."""
    return jsonify(dfs.list_user_entries())


# ---- Go Streaking (hit-streak survivor) ---------------------------------

@capspace_bp.route("/api/streak")
def api_streak():
    try:
        return jsonify(streakgame.status())
    except Exception:  # pragma: no cover - never 500 the app
        _LOG.exception("CapSpace streak status failed")
        return jsonify({"current": 0, "best": 0, "slate_date": None,
                        "today_pick": None, "pool": [], "history": []})


@capspace_bp.route("/api/streak/pick", methods=["POST"])
def api_streak_pick():
    body = request.get_json(silent=True) or {}
    pid = body.get("player_id")
    if pid is None:
        return jsonify({"ok": False, "error": "No player chosen."}), 400
    res = streakgame.make_pick(pid)
    return jsonify(res), (200 if res.get("ok") else 400)
