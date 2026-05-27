"""
o27.almanac.blueprint — Flask blueprint that serves the almanac live
from the o27v2 SQLite DB.

Mounted at /almanac on the o27v2 web app. Every URL the static
renderer writes is mirrored here, so the same Jinja templates render
in both modes without modification — `base_path` is set to "/almanac/"
in this mode (vs "" / "../" for static) and all in-page links resolve
under the blueprint mount.

Routes:
  /almanac/                              → home
  /almanac/index.html                    → home (alias)
  /almanac/standings.html
  /almanac/schedule.html
  /almanac/awards.html
  /almanac/parks.html
  /almanac/leaders/<which>.html          → batting | pitching | fielding |
                                           value | situational | stays
  /almanac/teams/index.html
  /almanac/teams/<abbrev>.html
  /almanac/players/index.html
  /almanac/players/<slug>.html
  /almanac/games/<game_id>.html
  /almanac/exports/index.html
  /almanac/exports/<file>                → CSV / JSON / ZIP

Data freshness: views are computed once per (db_path, db_mtime) tuple
so identical requests don't re-aggregate. A fresh sim bumps mtime and
the next request re-loads automatically.
"""
from __future__ import annotations

import datetime as _dt
import io
import logging
import os
import threading
from typing import Any

from flask import (
    Blueprint, abort, current_app, redirect, render_template,
    request, send_file, url_for,
)

from . import compute, export, loader
from .compute import MIN_PA_QUALIFIED, MIN_OUTS_QUALIFIED, team_label
from .render import _attribute_panel, _box_row, _format_weather, _slugify


_HERE = os.path.dirname(os.path.abspath(__file__))

almanac_bp = Blueprint(
    "almanac",
    __name__,
    url_prefix="/almanac",
    template_folder=os.path.join(_HERE, "templates"),
    static_folder=os.path.join(_HERE, "static"),
    static_url_path="/almanac/_static",
)

# Expose the city-deduplicating team label to all blueprint templates (the
# standalone static exporter registers the same global in render.py).
almanac_bp.add_app_template_global(team_label, "team_label")
almanac_bp.add_app_template_global(_slugify, "slugify")
almanac_bp.add_app_template_filter(_slugify, "slugify")


# ---------------------------------------------------------------------------
# Cached dataset / views (keyed by source-file mtime)
# ---------------------------------------------------------------------------

_LOG = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"key": None, "dataset": None, "views": None,
                          "loaded_at": None}


def _cache_key(source: str) -> tuple[str, float]:
    try:
        mtime = os.path.getmtime(source)
    except OSError:
        mtime = 0.0
    return (source, mtime)


def _resolve_source() -> str:
    """Where to read from. App-level config wins over env var wins over
    the loader's default (<repo>/o27v2/o27v2.db)."""
    app_cfg = current_app.config.get("ALMANAC_SOURCE")
    if app_cfg:
        return app_cfg
    env = os.environ.get("ALMANAC_SOURCE")
    if env:
        return env
    return loader._default_db_path()


def _compute_and_store(source: str, key: tuple[str, float]) -> tuple[dict, compute.Views]:
    # Compute outside the lock — only one request will race to populate,
    # but we'd rather not block the world during a 1-2s aggregation.
    dataset = loader.load(source)
    views   = compute.compute_views(dataset)
    with _CACHE_LOCK:
        _CACHE.update(key=key, dataset=dataset, views=views,
                      loaded_at=_dt.datetime.now())
    return dataset, views


def _get_dataset_and_views() -> tuple[dict, compute.Views]:
    source = _resolve_source()
    key = _cache_key(source)
    with _CACHE_LOCK:
        if _CACHE["key"] == key and _CACHE["dataset"] is not None:
            return _CACHE["dataset"], _CACHE["views"]
    return _compute_and_store(source, key)


def warm_cache(source: str | None = None) -> bool:
    """Pre-compute and cache the dataset+views so the next request renders
    from a warm cache instead of paying the cold-load cost (seconds on a
    fresh league). Best-effort: returns True on success or an existing warm
    cache, False on failure (logged, never raised). Safe to call from a
    background thread — `source` defaults to the active save's DB path, which
    resolves without app/request context."""
    try:
        if source is None:
            source = loader._default_db_path()
        key = _cache_key(source)
        with _CACHE_LOCK:
            if _CACHE["key"] == key and _CACHE["dataset"] is not None:
                return True
        _compute_and_store(source, key)
        return True
    except Exception:
        _LOG.exception("almanac warm_cache failed for source=%s", source)
        return False


def _base_ctx() -> dict:
    """Common context every page receives — mirrors render.py's setup
    so the templates render identically."""
    _, views = _get_dataset_and_views()
    prefix = (request.script_root or "") + "/almanac/"
    return {
        "site_title":   current_app.config.get("ALMANAC_TITLE", "O27 Almanac"),
        "subtitle":     current_app.config.get("ALMANAC_SUBTITLE",
                                               "Hybrid Baseball — Live"),
        "generated_at": _CACHE["loaded_at"].strftime("%Y-%m-%d %H:%M")
                        if _CACHE["loaded_at"] else "",
        "source_label": "live DB",
        "views":        views,
        "base_path":    prefix,
    }


def invalidate_cache() -> None:
    """Drop the cached dataset+views. Useful in tests or post-sim hooks."""
    with _CACHE_LOCK:
        _CACHE.update(key=None, dataset=None, views=None, loaded_at=None)


# ---------------------------------------------------------------------------
# Static assets — Flask's auto-static lives at /almanac/_static; map the
# template-expected /almanac/static/<f> there too so unchanged hrefs work.
# ---------------------------------------------------------------------------

@almanac_bp.route("/static/<path:fname>")
def static_passthrough(fname: str):
    from flask import send_from_directory
    return send_from_directory(os.path.join(_HERE, "static"), fname)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@almanac_bp.route("/")
@almanac_bp.route("/index.html")
def home():
    ctx = _base_ctx()
    views = ctx["views"]
    schedule_newest = list(reversed(views.schedule))
    top_woba = sorted([r for r in views.batting_season  if r.get("qualified")],
                      key=lambda r: (-r["woba_plus"], -r["woba"]))[:10]
    top_wera = sorted([r for r in views.pitching_season if r.get("qualified")],
                      key=lambda r: (-r["era_plus"], r["wera"]))[:10]
    return render_template("index.html.j2",
                           **ctx,
                           section="home",
                           recent_games=schedule_newest[:12],
                           top_woba=top_woba, top_wera=top_wera,
                           current_league=None,
                           leader_leagues=_leader_leagues(views))


@almanac_bp.route("/standings.html")
def standings():
    return render_template("standings.html.j2", **_base_ctx(),
                           section="standings")


@almanac_bp.route("/schedule.html")
def schedule():
    return render_template("schedule.html.j2", **_base_ctx(),
                           section="schedule")


@almanac_bp.route("/awards.html")
def awards():
    return render_template("awards.html.j2", **_base_ctx(),
                           section="awards")


@almanac_bp.route("/parks.html")
def parks():
    return render_template("parks.html.j2", **_base_ctx(),
                           section="parks")


@almanac_bp.route("/career.html")
def career():
    ctx = _base_ctx()
    return render_template("career.html.j2", **ctx, section="career",
                           career=ctx["views"].career)


# ---- leaders ---------------------------------------------------------------

_LEADER_TEMPLATES = {
    "batting":     ("leaders_batting.html.j2",     {"min_pa":   MIN_PA_QUALIFIED}),
    "pitching":    ("leaders_pitching.html.j2",    {"min_outs": MIN_OUTS_QUALIFIED}),
    "stays":       ("leaders_stays.html.j2",       {}),
    "fielding":    ("leaders_fielding.html.j2",    {}),
    "value":       ("leaders_value.html.j2",       {}),
    "situational": ("leaders_situational.html.j2", {}),
}


def _leader_leagues(views) -> list[str]:
    """Distinct leagues for the per-league leader nav; empty if single-league."""
    leagues = sorted({r.get("league") for r in views.standings if r.get("league")})
    return leagues if len(leagues) > 1 else []


def _render_leaders(which: str, league: str | None):
    if which not in _LEADER_TEMPLATES:
        abort(404)
    tpl, extra = _LEADER_TEMPLATES[which]
    ctx = _base_ctx()
    extra_ctx = dict(extra)
    views = ctx["views"]
    leagues = _leader_leagues(views)
    if league is not None:
        # Match the slug back to a real league name; 404 on unknown.
        league = next((lg for lg in leagues if _slugify(lg) == league), None)
        if league is None:
            abort(404)
    if which == "batting":
        extra_ctx["always_show_all"] = not any(r.get("qualified")
                                               for r in views.batting_season)
    elif which == "pitching":
        extra_ctx["always_show_all"] = not any(r.get("qualified")
                                               for r in views.pitching_season)
    return render_template(tpl, **ctx, section="leaders",
                           current_league=league, leader_leagues=leagues,
                           leader_page=which, **extra_ctx)


@almanac_bp.route("/leaders/<which>.html")
def leaders(which: str):
    return _render_leaders(which, None)


@almanac_bp.route("/leaders/<league>/<which>.html")
def leaders_by_league(league: str, which: str):
    return _render_leaders(which, league)


# ---- teams -----------------------------------------------------------------

@almanac_bp.route("/teams/")
@almanac_bp.route("/teams/index.html")
def teams_index():
    return render_template("teams_index.html.j2", **_base_ctx(),
                           section="teams")


@almanac_bp.route("/teams/<abbrev>.html")
def team_detail(abbrev: str):
    ctx = _base_ctx()
    views = ctx["views"]
    abb = abbrev.upper()
    t = next((tt for tt in views.teams if tt.get("abbrev") == abb), None)
    if not t:
        abort(404)
    standings_by_abb = {r["abbrev"]: r for r in views.standings}
    schedule_by_abb: dict[str, list] = {}
    for g in reversed(views.schedule):
        schedule_by_abb.setdefault(g["away_abbrev"], []).append(g)
        schedule_by_abb.setdefault(g["home_abbrev"], []).append(g)
    team_row = {
        "abbrev":       abb,
        "city":         t.get("city", ""),
        "name":         t.get("name", ""),
        "league":       t.get("league", ""),
        "division":     t.get("division", ""),
        "park_name":    t.get("park_name", ""),
        "manager_name": t.get("manager_name", ""),
    }
    record = standings_by_abb.get(abb, {
        "w": 0, "l": 0, "pct": 0.0, "rs": 0, "ra": 0, "rd": 0,
        "streak": "—", "l10_w": 0, "l10_l": 0,
    })
    return render_template("team.html.j2", **ctx,
                           section="teams",
                           team_row=team_row, record=record,
                           batters=views.batting_by_team.get(abb, []),
                           pitchers=views.pitching_by_team.get(abb, []),
                           totals_bat=views.team_totals_bat.get(abb),
                           totals_pit=views.team_totals_pit.get(abb),
                           recent_games=schedule_by_abb.get(abb, [])[:12])


# ---- players ---------------------------------------------------------------

@almanac_bp.route("/players/")
@almanac_bp.route("/players/index.html")
def players_index():
    return _render_players_index(None)


@almanac_bp.route("/players/<league>/")
@almanac_bp.route("/players/<league>/index.html")
def players_index_by_league(league: str):
    return _render_players_index(league)


def _render_players_index(league: str | None):
    ctx = _base_ctx()
    views = ctx["views"]
    leagues = _leader_leagues(views)
    if league is not None:
        league = next((lg for lg in leagues if _slugify(lg) == league), None)
        if league is None:
            abort(404)
    teams_by_id = {t["id"]: t for t in views.teams}
    rows: list[dict] = []
    for p in views.players:
        tid = p.get("team_id")
        t = teams_by_id.get(tid) if tid else None
        team_abb = (t or {}).get("abbrev", "FA")
        slug = _slugify(p.get("name") or str(p["id"]))
        row = {
            "name":       p.get("name", "?"),
            "team":       team_abb,
            "slug":       slug,
            "position":   p.get("position", ""),
            "is_pitcher": bool(p.get("is_pitcher")),
            "is_joker":   bool(p.get("is_joker")),
            "age":        p.get("age"),
            "bats":       p.get("bats", "R"),
            "throws":     p.get("throws", "R"),
            "league":     (t or {}).get("league", ""),
            "division":   (t or {}).get("division", ""),
        }
        if p.get("is_pitcher"):
            ps = views.pitching_by_player.get(p["id"])
            if ps:
                row.update(g=ps["g"], ip_disp=ps["ip_disp"],
                           era=ps["era"], whip=ps["whip"], k=ps["k"])
        else:
            bs = views.batting_by_player.get(p["id"])
            if bs:
                row.update(g=bs["g"], pa=bs["pa"], avg=bs["pavg"],
                           ops=bs["ops"], hr=bs["hr"])
        rows.append(row)
    rows.sort(key=lambda r: (r["team"], r["name"]))
    return render_template("players_index.html.j2", **ctx,
                           section="players", players=rows,
                           current_league=league, leader_leagues=leagues)


@almanac_bp.route("/players/<slug>.html")
def player_detail(slug: str):
    """slug is `<team_abbrev>_<player_slug>` to match the static URL shape."""
    ctx = _base_ctx()
    views = ctx["views"]
    teams_by_id = {t["id"]: t for t in views.teams}
    target_team, _, target_slug = slug.partition("_")
    target_team = target_team.upper()

    match = None
    for p in views.players:
        tid = p.get("team_id")
        t = teams_by_id.get(tid) if tid else None
        team_abb = (t or {}).get("abbrev", "FA")
        if team_abb.upper() != target_team:
            continue
        if _slugify(p.get("name") or str(p["id"])) == target_slug:
            match = (p, t, team_abb)
            break
    if not match:
        abort(404)
    p, t, team_abb = match

    if p.get("is_pitcher"):
        season = views.pitching_by_player.get(p["id"])
    else:
        season = views.batting_by_player.get(p["id"])
    player_ctx = {
        "name":       p.get("name", "?"),
        "team":       team_abb,
        "slug":       _slugify(p.get("name") or str(p["id"])),
        "position":   p.get("position", "P" if p.get("is_pitcher") else ""),
        "is_pitcher": bool(p.get("is_pitcher")),
        "is_joker":   bool(p.get("is_joker")),
        "age":        p.get("age"),
        "bats":       p.get("bats", "R"),
        "throws":     p.get("throws", "R"),
        "country":    p.get("country", ""),
        "team_name":  team_label(t),
        "archetype":  p.get("archetype", ""),
    }
    return render_template("player.html.j2", **ctx,
                           section="players",
                           player=player_ctx, season=season,
                           fielding=views.fielding_by_player.get(p["id"]),
                           attrs=_attribute_panel(p),
                           game_log=views.game_logs_batter.get(p["id"], []),
                           pitcher_log=views.game_logs_pitcher.get(p["id"], []))


# ---- games -----------------------------------------------------------------

@almanac_bp.route("/games/<int:game_id>.html")
def game_detail(game_id: int):
    ctx = _base_ctx()
    dataset, views = _get_dataset_and_views()
    g = next((gg for gg in views.schedule if gg["id"] == game_id), None)
    if not g:
        abort(404)
    players_by_id = {p["id"]: p for p in views.players}
    batting_by_game: dict[int, list] = {}
    for r in (dataset.get("batting") or []):
        batting_by_game.setdefault(r["game_id"], []).append(r)
    pitching_by_game: dict[int, list] = {}
    for r in (dataset.get("pitching") or []):
        pitching_by_game.setdefault(r["game_id"], []).append(r)
    a_rows = batting_by_game.get(g["id"], [])
    p_rows = pitching_by_game.get(g["id"], [])
    away_batting  = [_box_row(r, players_by_id) for r in a_rows if r["team_id"] == g["away_id"]]
    home_batting  = [_box_row(r, players_by_id) for r in a_rows if r["team_id"] == g["home_id"]]
    away_pitching = [_box_row(r, players_by_id) for r in p_rows if r["team_id"] == g["away_id"]]
    home_pitching = [_box_row(r, players_by_id) for r in p_rows if r["team_id"] == g["home_id"]]
    for lst in (away_batting, home_batting):
        lst.sort(key=lambda r: (-(r.get("pa") or 0), r.get("name") or ""))
    for lst in (away_pitching, home_pitching):
        lst.sort(key=lambda r: -(r.get("outs_recorded") or 0))
    events = []
    for e in views.scoring_by_game.get(g["id"], []):
        ee = dict(e)
        ee["batter_name"] = (players_by_id.get(e["batter_id"]) or {}).get("name")
        ee["runner_name"] = (players_by_id.get(e["runner_id"]) or {}).get("name")
        events.append(ee)
    game_meta = next((gg for gg in (dataset.get("games") or [])
                      if gg["id"] == game_id), {})
    weather = _format_weather(game_meta)
    return render_template("game.html.j2", **ctx,
                           section="schedule",
                           game=g,
                           away_batting=away_batting,
                           home_batting=home_batting,
                           away_pitching=away_pitching,
                           home_pitching=home_pitching,
                           scoring_events=events,
                           weather=weather)


# ---- exports ---------------------------------------------------------------

@almanac_bp.route("/exports/")
@almanac_bp.route("/exports/index.html")
def exports_index():
    """The exports landing page. The files themselves are generated on
    demand by /almanac/exports/<file>."""
    ctx = _base_ctx()
    dataset, views = _get_dataset_and_views()
    from .render import _exports_index_rows
    rows = _exports_index_rows(dataset, views, None)
    return render_template("exports_index.html.j2", **ctx,
                           section="exports", datasets=rows)


_EXPORT_FILES = {
    "standings.csv", "schedule.csv", "teams.csv", "players.csv",
    "batting_season.csv", "pitching_season.csv", "fielding_season.csv",
    "team_totals_batting.csv", "team_totals_pitching.csv",
    "team_pythag.csv", "monthly_splits.csv", "scoring_events.csv",
    "pa_log.csv", "league_totals.csv",
    "batting_game_log.csv", "pitching_game_log.csv",
    "games.csv", "awards.csv", "playoff_series.csv",
    "season-bundle.json", "season-bundle.zip",
}


@almanac_bp.route("/exports/<path:fname>")
def export_file(fname: str):
    if fname in ("", "index.html"):
        return redirect(url_for("almanac.exports_index"))
    if fname not in _EXPORT_FILES:
        abort(404)
    dataset, views = _get_dataset_and_views()
    import shutil, tempfile
    tmp = tempfile.mkdtemp(prefix="almanac_exp_")
    try:
        export.write_exports(views, dataset, tmp)
        path = os.path.join(tmp, "exports", fname)
        if not os.path.exists(path):
            abort(404)
        with open(path, "rb") as f:
            data = f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    mimetype = {
        ".csv":  "text/csv",
        ".json": "application/json",
        ".zip":  "application/zip",
    }.get(os.path.splitext(fname)[1], "application/octet-stream")
    return send_file(io.BytesIO(data), mimetype=mimetype,
                     as_attachment=fname.endswith((".zip", ".json")),
                     download_name=fname)
