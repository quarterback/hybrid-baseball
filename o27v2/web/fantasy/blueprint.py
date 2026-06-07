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
import threading
import time

from flask import Blueprint, jsonify, render_template, request

from o27v2 import currency, db
from . import data as slate_data
from . import contests as dfs
from . import streak as streakgame
from . import sluggers as sluggergame
from . import pitching as pilotgame
from . import categories as catgame
from . import sportsbook as book
from . import bestball as bbgame
from . import wallet

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
            try:
                # Fast single-row read — do NOT settle inline (that blocked the
                # page render for ~15 s). A background pass keeps it current.
                blob["WALLET"] = wallet.balance()
            except Exception:
                _LOG.exception("CapSpace wallet read failed")
        _kick_settle()
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


_ACTIVITY_LABEL = {"sluggers": "Sluggers", "pilots": "Pilots",
                   "categories": "Category League", "bestball": "Best Ball"}


@capspace_bp.route("/api/activity")
def api_activity():
    """Everything the user has live or settled across ALL games — DFS lineups,
    Sportsbook bets, and every game's buy-ins — in one feed."""
    items = []
    # Settle in the background; render the feed off what's already persisted so
    # this endpoint stays fast (it used to block ~26 s).
    _kick_settle()
    try:
        for r in db.fetchall(
            "SELECT e.fee_paid fp, e.settled s, e.payout po, e.slate_date sd, c.name cn "
            "FROM dfs_entries e JOIN dfs_contests c ON e.contest_id = c.id ORDER BY e.id DESC"):
            st = "won" if (r["s"] and r["po"] > 0) else ("lost" if r["s"] else "live")
            items.append({"game": "Daily Slate", "title": r["cn"], "sub": r["sd"],
                          "stake": r["fp"] or 0, "status": st, "payout": r["po"] or 0})
    except Exception:
        _LOG.exception("activity dfs")
    try:
        sb = book.status()
        for b in sb.get("open", []):
            items.append({"game": "Sportsbook", "title": b["desc"], "sub": b["matchup"],
                          "stake": b["stake"], "status": "open", "payout": 0})
        for b in sb.get("settled", []):
            items.append({"game": "Sportsbook", "title": b["desc"],
                          "sub": b["matchup"] + (f" ({b['score']})" if b.get("score") else ""),
                          "stake": b["stake"], "status": b["status"], "payout": b["payout"]})
    except Exception:
        _LOG.exception("activity sportsbook")
    try:
        for r in db.fetchall("SELECT game, ekey, fee, settled, payout FROM cs_buyins ORDER BY rowid DESC"):
            lbl = _ACTIVITY_LABEL.get(r["game"], r["game"])
            st = "won" if (r["settled"] and r["payout"] > 0) else ("lost" if r["settled"] else "live")
            ek = str(r["ekey"])
            items.append({"game": lbl, "title": ek if "-" in ek else ek.upper(),
                          "sub": "buy-in", "stake": r["fee"], "status": st, "payout": r["payout"] or 0})
    except Exception:
        _LOG.exception("activity buyins")
    return jsonify({"items": items})


def _settle_all() -> int:
    """Settle every game that pays into the wallet, then return the balance —
    so one bankroll reflects DFS contests and Sportsbook bets alike.

    This is the SLOW path (it rebuilds slates and grades synthetic fields), so it
    must never run inline on a page render or a wallet read — call _kick_settle()
    instead. Kept callable directly for tests / explicit settle triggers."""
    for fn in (dfs.settle_entries, book.settle_bets, sluggergame.settle,
               pilotgame.settle, catgame.settle, bbgame.settle, streakgame.settle):
        try:
            fn()
        except Exception:  # pragma: no cover
            _LOG.exception("CapSpace settle failed: %s", getattr(fn, "__name__", fn))
    return wallet.balance()


# Settling is the dominant cost behind the fantasy app's lag — it re-derives the
# slate pool and grades a synthetic field for every game type. It used to run
# inline on home(), /api/wallet, /api/slate and /api/activity, so the document
# and the wallet call each blocked ~15 s and the page read as "won't load".
# Settling only needs to credit newly-final results, not gate the UI, so it now
# runs in a debounced background thread: requests return immediately off the
# already-persisted wallet, and winnings appear on the next poll a few seconds
# later.
_SETTLE_LOCK = threading.Lock()
_SETTLE_LAST = [0.0]
_SETTLE_MIN_INTERVAL = 6.0  # seconds between background settle passes


def _kick_settle() -> None:
    """Run _settle_all() once in the background — debounced and non-overlapping —
    so settling NEVER blocks a request."""
    if time.time() - _SETTLE_LAST[0] < _SETTLE_MIN_INTERVAL:
        return
    if not _SETTLE_LOCK.acquire(blocking=False):
        return  # a pass is already running

    def _run():
        try:
            _SETTLE_LAST[0] = time.time()
            _settle_all()
        except Exception:  # pragma: no cover
            _LOG.exception("CapSpace background settle failed")
        finally:
            _SETTLE_LOCK.release()

    threading.Thread(target=_run, name="capspace-settle", daemon=True).start()


@capspace_bp.route("/api/wallet")
def api_wallet():
    """The save's live wallet balance + career records + onboarding state.

    Fast path only: persisted balance + records (a handful of single-row reads).
    Settling runs in the background (kicked here); best_streak is read from the
    persisted record the streak settle keeps current, not recomputed inline."""
    try:
        _kick_settle()
        rec = wallet.records()
        try:
            rec["best_streak"] = wallet.rec_get("best_streak")
        except Exception:
            rec["best_streak"] = 0
        return jsonify({"balance": wallet.balance(), "records": rec,
                        "started": wallet.started(), "personas": wallet.PERSONAS})
    except Exception:  # pragma: no cover
        _LOG.exception("CapSpace wallet failed")
        return jsonify({"balance": 0, "records": {}, "started": True, "personas": wallet.PERSONAS})


def _reset_run() -> None:
    """Wipe all CapSpace play state for a fresh run (keeps the league/save)."""
    conn = db.get_conn()
    for t in ("cap_wallet", "cap_records", "cap_profile", "sb_bets", "sb_lines",
              "dfs_entries", "cs_buyins", "slugger_picks", "pilot_picks",
              "cat_rosters", "bb_roster", "streak_picks"):
        try:
            conn.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    conn.commit()


@capspace_bp.route("/api/onboard", methods=["POST"])
def api_onboard():
    body = request.get_json(silent=True) or {}
    if body.get("reset"):
        _reset_run()
    res = wallet.start(body.get("persona"))
    return jsonify(res), (200 if res.get("ok") else 400)


@capspace_bp.route("/api/wallet/restart", methods=["POST"])
def api_wallet_restart():
    res = wallet.restart()
    return jsonify(res), (200 if res.get("ok") else 400)


@capspace_bp.route("/api/player/<int:player_id>")
def api_player(player_id):
    """Full player-card payload — stats, ratings, logs — used by the drawer
    from every game screen."""
    try:
        card = slate_data.player_card(player_id)
    except Exception:  # pragma: no cover
        _LOG.exception("CapSpace player_card failed")
        card = None
    return (jsonify(card), 200) if card else (jsonify({}), 404)


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


# ---- Sluggers (Walk-Back home-run game) ---------------------------------

@capspace_bp.route("/api/sluggers")
def api_sluggers():
    try:
        return jsonify(sluggergame.status())
    except Exception:  # pragma: no cover - never 500 the app
        _LOG.exception("CapSpace sluggers status failed")
        return jsonify({"slate_date": None, "season": 0, "max": sluggergame.MAX_PICKS,
                        "picked": 0, "your_slate": None, "pool": [], "history": []})


@capspace_bp.route("/api/sluggers/pick", methods=["POST"])
def api_sluggers_pick():
    body = request.get_json(silent=True) or {}
    pid = body.get("player_id")
    if pid is None:
        return jsonify({"ok": False, "error": "No player chosen."}), 400
    res = sluggergame.pick(pid)
    return jsonify(res), (200 if res.get("ok") else 400)


@capspace_bp.route("/api/sluggers/remove", methods=["POST"])
def api_sluggers_remove():
    body = request.get_json(silent=True) or {}
    pid = body.get("player_id")
    if pid is None:
        return jsonify({"ok": False, "error": "No player chosen."}), 400
    res = sluggergame.remove(pid)
    return jsonify(res), (200 if res.get("ok") else 400)


# ---- Pilots (pitching game) ---------------------------------------------

@capspace_bp.route("/api/pilots")
def api_pilots():
    try:
        return jsonify(pilotgame.status())
    except Exception:  # pragma: no cover - never 500 the app
        _LOG.exception("CapSpace pilots status failed")
        return jsonify({"slate_date": None, "season": 0, "max": pilotgame.MAX_PICKS,
                        "picked": 0, "your_slate": None, "pool": [], "history": []})


@capspace_bp.route("/api/pilots/pick", methods=["POST"])
def api_pilots_pick():
    body = request.get_json(silent=True) or {}
    pid = body.get("player_id")
    if pid is None:
        return jsonify({"ok": False, "error": "No player chosen."}), 400
    res = pilotgame.pick(pid)
    return jsonify(res), (200 if res.get("ok") else 400)


@capspace_bp.route("/api/pilots/remove", methods=["POST"])
def api_pilots_remove():
    body = request.get_json(silent=True) or {}
    pid = body.get("player_id")
    if pid is None:
        return jsonify({"ok": False, "error": "No player chosen."}), 400
    res = pilotgame.remove(pid)
    return jsonify(res), (200 if res.get("ok") else 400)


# ---- Category leagues (Roto engine) -------------------------------------

@capspace_bp.route("/api/categories")
def api_categories():
    fmt = request.args.get("format", "std5x5")
    try:
        return jsonify(catgame.state(fmt))
    except Exception:  # pragma: no cover - never 500 the app
        _LOG.exception("CapSpace categories state failed")
        return jsonify({"formats": [], "format": fmt, "slots": {"h": 0, "p": 0}, "roster": []})


@capspace_bp.route("/api/categories/pool")
def api_categories_pool():
    fmt = request.args.get("format", "std5x5")
    try:
        return jsonify(catgame.pool(fmt))
    except Exception:  # pragma: no cover
        _LOG.exception("CapSpace categories pool failed")
        return jsonify({"hitters": [], "pitchers": []})


@capspace_bp.route("/api/categories/draft", methods=["POST"])
def api_categories_draft():
    body = request.get_json(silent=True) or {}
    fmt = body.get("format", "std5x5")
    ids = body.get("player_ids") or []
    res = catgame.draft(fmt, ids)
    return jsonify(res), (200 if res.get("ok") else 400)


# ---- Sportsbook ---------------------------------------------------------

@capspace_bp.route("/api/sportsbook")
def api_sportsbook():
    try:
        return jsonify(book.status())
    except Exception:  # pragma: no cover - never 500 the app
        _LOG.exception("CapSpace sportsbook status failed")
        return jsonify({"bankroll": 0, "slate_date": None, "games": [], "open": [],
                        "settled": [], "at_risk": 0, "record": {"w": 0, "l": 0, "p": 0, "net": 0}})


@capspace_bp.route("/api/sportsbook/bet", methods=["POST"])
def api_sportsbook_bet():
    body = request.get_json(silent=True) or {}
    res = book.place(body.get("game_id"), body.get("market"), body.get("side"), body.get("stake"))
    return jsonify(res), (200 if res.get("ok") else 400)


# ---- Best Ball ----------------------------------------------------------

@capspace_bp.route("/api/bestball")
def api_bestball():
    try:
        return jsonify(bbgame.state())
    except Exception:  # pragma: no cover - never 500 the app
        _LOG.exception("CapSpace bestball state failed")
        return jsonify({"slots": {"h": bbgame.DRAFT_H, "p": bbgame.DRAFT_P},
                        "start": {"h": bbgame.START_H, "p": bbgame.START_P}, "roster": []})


@capspace_bp.route("/api/bestball/pool")
def api_bestball_pool():
    try:
        return jsonify(bbgame.pool())
    except Exception:  # pragma: no cover
        _LOG.exception("CapSpace bestball pool failed")
        return jsonify({"hitters": [], "pitchers": []})


@capspace_bp.route("/api/bestball/draft", methods=["POST"])
def api_bestball_draft():
    body = request.get_json(silent=True) or {}
    res = bbgame.draft(body.get("player_ids") or [])
    return jsonify(res), (200 if res.get("ok") else 400)
