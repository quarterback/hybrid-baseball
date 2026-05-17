"""
o27.almanac.render — Jinja2 page emitter.

Walks the computed Views and writes the full HTML site under `out_dir`.
No Flask runtime — Jinja2 is invoked directly with a FileSystemLoader.

Page layout written:

    out/
      index.html
      standings.html
      schedule.html
      static/almanac.css
      static/almanac.js
      leaders/batting.html
      leaders/pitching.html
      leaders/stays.html
      teams/index.html
      teams/{abbrev}.html
      players/index.html
      players/{abbrev}_{slug}.html
      games/{game_id}.html
      exports/index.html
      exports/*.csv  (written by export.py)
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .compute import Views, MIN_PA_QUALIFIED, MIN_OUTS_QUALIFIED


_HERE = os.path.dirname(os.path.abspath(__file__))


def render_site(
    views: Views,
    dataset: dict[str, Any],
    out_dir: str,
    *,
    site_title: str = "O27 Almanac",
    subtitle: str = "Hybrid Baseball — Season Stats",
    export_manifest: dict[str, str] | None = None,
) -> int:
    """Render the full site. Returns the count of HTML pages written."""
    os.makedirs(out_dir, exist_ok=True)
    _copy_static(out_dir)

    env = Environment(
        loader=FileSystemLoader(os.path.join(_HERE, "templates")),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    generated_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    source_label = _source_label(views, dataset)

    base_ctx = dict(
        site_title=site_title,
        subtitle=subtitle,
        generated_at=generated_at,
        source_label=source_label,
        views=views,
    )

    pages_written = 0

    # ------- home -------
    schedule_newest = list(reversed(views.schedule))
    top_woba  = sorted([r for r in views.batting_season  if r.get("qualified")],
                       key=lambda r: -r["woba"])[:10]
    top_wera  = sorted([r for r in views.pitching_season if r.get("qualified")],
                       key=lambda r: r["wera"])[:10]
    _write(env, "index.html.j2", os.path.join(out_dir, "index.html"),
           {**base_ctx, "section": "home", "base_path": "",
            "recent_games": schedule_newest[:12],
            "top_woba": top_woba,
            "top_wera": top_wera})
    pages_written += 1

    # ------- standings / schedule -------
    _write(env, "standings.html.j2", os.path.join(out_dir, "standings.html"),
           {**base_ctx, "section": "standings", "base_path": ""})
    pages_written += 1

    _write(env, "schedule.html.j2", os.path.join(out_dir, "schedule.html"),
           {**base_ctx, "section": "schedule", "base_path": ""})
    pages_written += 1

    # ------- leaders -------
    leaders_dir = os.path.join(out_dir, "leaders")
    os.makedirs(leaders_dir, exist_ok=True)
    always_show = not any(r.get("qualified") for r in views.batting_season)
    _write(env, "leaders_batting.html.j2",
           os.path.join(leaders_dir, "batting.html"),
           {**base_ctx, "section": "leaders", "base_path": "../",
            "min_pa": MIN_PA_QUALIFIED, "always_show_all": always_show})
    always_show_p = not any(r.get("qualified") for r in views.pitching_season)
    _write(env, "leaders_pitching.html.j2",
           os.path.join(leaders_dir, "pitching.html"),
           {**base_ctx, "section": "leaders", "base_path": "../",
            "min_outs": MIN_OUTS_QUALIFIED, "always_show_all": always_show_p})
    _write(env, "leaders_stays.html.j2",
           os.path.join(leaders_dir, "stays.html"),
           {**base_ctx, "section": "leaders", "base_path": "../"})
    _write(env, "leaders_fielding.html.j2",
           os.path.join(leaders_dir, "fielding.html"),
           {**base_ctx, "section": "leaders", "base_path": "../"})
    _write(env, "leaders_value.html.j2",
           os.path.join(leaders_dir, "value.html"),
           {**base_ctx, "section": "leaders", "base_path": "../"})
    _write(env, "leaders_situational.html.j2",
           os.path.join(leaders_dir, "situational.html"),
           {**base_ctx, "section": "leaders", "base_path": "../"})
    pages_written += 6

    # ------- awards + parks (top-level pages) -------
    _write(env, "awards.html.j2", os.path.join(out_dir, "awards.html"),
           {**base_ctx, "section": "awards", "base_path": ""})
    _write(env, "parks.html.j2", os.path.join(out_dir, "parks.html"),
           {**base_ctx, "section": "parks", "base_path": ""})
    pages_written += 2

    # ------- teams -------
    teams_dir = os.path.join(out_dir, "teams")
    os.makedirs(teams_dir, exist_ok=True)
    _write(env, "teams_index.html.j2", os.path.join(teams_dir, "index.html"),
           {**base_ctx, "section": "teams", "base_path": "../"})
    pages_written += 1

    standings_by_abb = {r["abbrev"]: r for r in views.standings}
    schedule_by_abb: dict[str, list] = {}
    for g in schedule_newest:
        schedule_by_abb.setdefault(g["away_abbrev"], []).append(g)
        schedule_by_abb.setdefault(g["home_abbrev"], []).append(g)

    for t in views.teams:
        abb = t.get("abbrev")
        if not abb:
            continue
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
        _write(env, "team.html.j2",
               os.path.join(teams_dir, f"{abb.lower()}.html"),
               {**base_ctx, "section": "teams", "base_path": "../",
                "team_row": team_row, "record": record,
                "batters":  views.batting_by_team.get(abb, []),
                "pitchers": views.pitching_by_team.get(abb, []),
                "totals_bat": views.team_totals_bat.get(abb),
                "totals_pit": views.team_totals_pit.get(abb),
                "recent_games": schedule_by_abb.get(abb, [])[:12]})
        pages_written += 1

    # ------- players -------
    players_dir = os.path.join(out_dir, "players")
    os.makedirs(players_dir, exist_ok=True)

    teams_by_id = {t["id"]: t for t in views.teams}

    index_rows: list[dict] = []
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
        index_rows.append(row)

    index_rows.sort(key=lambda r: (r["team"], r["name"]))
    _write(env, "players_index.html.j2",
           os.path.join(players_dir, "index.html"),
           {**base_ctx, "section": "players", "base_path": "../",
            "players": index_rows})
    pages_written += 1

    # Per-player pages.
    for p in views.players:
        tid = p.get("team_id")
        t = teams_by_id.get(tid) if tid else None
        team_abb = (t or {}).get("abbrev", "FA")
        slug = _slugify(p.get("name") or str(p["id"]))
        if p.get("is_pitcher"):
            season = views.pitching_by_player.get(p["id"])
        else:
            season = views.batting_by_player.get(p["id"])
        player_ctx = {
            "name":       p.get("name", "?"),
            "team":       team_abb,
            "slug":       slug,
            "position":   p.get("position", "P" if p.get("is_pitcher") else ""),
            "is_pitcher": bool(p.get("is_pitcher")),
            "is_joker":   bool(p.get("is_joker")),
            "age":        p.get("age"),
            "bats":       p.get("bats", "R"),
            "throws":     p.get("throws", "R"),
            "team_name":  f"{(t or {}).get('city','')} {(t or {}).get('name','')}".strip(),
            "archetype":  p.get("archetype", ""),
        }
        bat_log = views.game_logs_batter.get(p["id"], [])
        pit_log = views.game_logs_pitcher.get(p["id"], [])
        fielding = views.fielding_by_player.get(p["id"])
        attrs = _attribute_panel(p)
        out_file = os.path.join(players_dir, f"{team_abb.lower()}_{slug}.html")
        _write(env, "player.html.j2", out_file,
               {**base_ctx, "section": "players", "base_path": "../",
                "player": player_ctx, "season": season,
                "fielding": fielding, "attrs": attrs,
                "game_log": bat_log, "pitcher_log": pit_log})
        pages_written += 1

    # ------- games -------
    games_dir = os.path.join(out_dir, "games")
    os.makedirs(games_dir, exist_ok=True)
    batting_by_game: dict[int, list] = {}
    for r in (dataset.get("batting") or []):
        batting_by_game.setdefault(r["game_id"], []).append(r)
    pitching_by_game: dict[int, list] = {}
    for r in (dataset.get("pitching") or []):
        pitching_by_game.setdefault(r["game_id"], []).append(r)
    players_by_id = {p["id"]: p for p in views.players}

    game_meta = {g["id"]: g for g in dataset.get("games") or []}

    for g in views.schedule:
        a_rows = batting_by_game.get(g["id"], [])
        p_rows = pitching_by_game.get(g["id"], [])
        away_batting  = [_box_row(r, players_by_id) for r in a_rows if r["team_id"] == g["away_id"]]
        home_batting  = [_box_row(r, players_by_id) for r in a_rows if r["team_id"] == g["home_id"]]
        away_pitching = [_box_row(r, players_by_id) for r in p_rows if r["team_id"] == g["away_id"]]
        home_pitching = [_box_row(r, players_by_id) for r in p_rows if r["team_id"] == g["home_id"]]
        # Order batting rows by team batting order if game_position is set;
        # otherwise PA desc.
        for lst in (away_batting, home_batting):
            lst.sort(key=lambda r: (-(r.get("pa") or 0), r.get("name") or ""))
        for lst in (away_pitching, home_pitching):
            lst.sort(key=lambda r: -(r.get("outs_recorded") or 0))

        # Scoring events with player names resolved.
        events = []
        for e in views.scoring_by_game.get(g["id"], []):
            ee = dict(e)
            ee["batter_name"] = (players_by_id.get(e["batter_id"]) or {}).get("name")
            ee["runner_name"] = (players_by_id.get(e["runner_id"]) or {}).get("name")
            events.append(ee)

        weather = _format_weather(game_meta.get(g["id"]) or {})

        _write(env, "game.html.j2",
               os.path.join(games_dir, f"{g['id']}.html"),
               {**base_ctx, "section": "schedule", "base_path": "../",
                "game": g,
                "away_batting":  away_batting,
                "home_batting":  home_batting,
                "away_pitching": away_pitching,
                "home_pitching": home_pitching,
                "scoring_events": events,
                "weather": weather})
        pages_written += 1

    # ------- exports index -------
    exports_dir = os.path.join(out_dir, "exports")
    os.makedirs(exports_dir, exist_ok=True)
    dataset_rows = _exports_index_rows(dataset, views, export_manifest)
    _write(env, "exports_index.html.j2",
           os.path.join(exports_dir, "index.html"),
           {**base_ctx, "section": "exports", "base_path": "../",
            "datasets": dataset_rows})
    pages_written += 1

    return pages_written


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(env: Environment, template: str, path: str, ctx: dict) -> None:
    tpl = env.get_template(template)
    html = tpl.render(**ctx)
    with open(path, "w") as f:
        f.write(html)


def _copy_static(out_dir: str) -> None:
    src = os.path.join(_HERE, "static")
    dst = os.path.join(out_dir, "static")
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        shutil.copy2(os.path.join(src, name), os.path.join(dst, name))


def _slugify(name: str) -> str:
    out = []
    for ch in (name or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    return "".join(out).strip("_") or "player"


def _box_row(r: dict, players_by_id: dict[int, dict]) -> dict:
    p = players_by_id.get(r["player_id"]) or {}
    return {
        **{k: v for k, v in r.items()},
        "name": p.get("name", f"#{r['player_id']}"),
        "slug": _slugify(p.get("name") or str(r["player_id"])),
    }


def _attribute_panel(p: dict) -> list[tuple[str, int]]:
    """Return the (label, value) tuples for the scout-grade panel.
    Skips fields that are missing or zero on the raw player row."""
    if p.get("is_pitcher"):
        keys = [
            ("Stuff",    "pitcher_skill"),
            ("Command",  "command"),
            ("Movement", "movement"),
            ("Stamina",  "stamina"),
            ("Grit",     "grit"),
            ("Defense",  "defense"),
            ("Arm",      "arm"),
        ]
    else:
        keys = [
            ("Skill",    "skill"),
            ("Contact",  "contact"),
            ("Power",    "power"),
            ("Eye",      "eye"),
            ("Speed",    "speed"),
            ("Baserun",  "baserunning"),
            ("Run Aggr", "run_aggressiveness"),
            ("Defense",  "defense"),
            ("Arm",      "arm"),
            ("Def·IF",   "defense_infield"),
            ("Def·OF",   "defense_outfield"),
            ("Def·C",    "defense_catcher"),
            ("Adapt",    "adaptability"),
            ("Leader",   "leadership"),
            ("WorkEth",  "work_ethic"),
        ]
    out: list[tuple[str, int]] = []
    for label, field in keys:
        v = p.get(field)
        if v is None:
            continue
        try:
            iv = int(round(float(v)))
        except (TypeError, ValueError):
            continue
        if iv <= 0:
            continue
        out.append((label, iv))
    return out


def _format_weather(g: dict) -> str:
    if not g:
        return ""
    bits = []
    for fld, label in (
        ("temperature_tier", "temp"),
        ("wind_tier", "wind"),
        ("humidity_tier", "humid"),
        ("precip_tier", "precip"),
        ("cloud_tier", "sky"),
    ):
        v = (g.get(fld) or "").strip()
        if v and v not in ("neutral", "normal", "none", "mild", "clear"):
            bits.append(f"{label} {v}")
    return " · ".join(bits)


def _source_label(views: Views, dataset: dict) -> str:
    meta = dataset.get("meta") or {}
    src  = meta.get("source") or ""
    kind = meta.get("source_kind") or "?"
    label = os.path.basename(src) if isinstance(src, str) else str(src)
    return f"{label} ({kind})"


def _exports_index_rows(dataset, views, manifest) -> list[dict]:
    manifest = manifest or {}
    rows = [
        ("Standings",            "standings",             "standings.csv",
         len(views.standings),   "Per-team W/L, RS/RA, GB, L10, streak"),
        ("Schedule",             "schedule",              "schedule.csv",
         len(views.schedule),    "Every played game (newest first)"),
        ("Teams",                "teams",                 "teams.csv",
         len(views.teams),       "Raw team metadata (parks, managers, …)"),
        ("Players",              "players",               "players.csv",
         len(views.players),     "Raw player attributes (the full roster)"),
        ("Batting · Season",     "batting_season",        "batting_season.csv",
         len(views.batting_season),  "Per-player season totals with derived stats"),
        ("Pitching · Season",    "pitching_season",       "pitching_season.csv",
         len(views.pitching_season), "Per-pitcher season totals with derived stats"),
        ("Team Totals · Batting","team_totals_batting",   "team_totals_batting.csv",
         len(views.team_totals_bat), "Aggregated per-team batting line"),
        ("Team Totals · Pitching","team_totals_pitching", "team_totals_pitching.csv",
         len(views.team_totals_pit), "Aggregated per-team pitching line"),
        ("League Totals",        "league_totals",         "league_totals.csv",
         1,                      "League-wide AVG/OBP/SLG/ERA/FIP/WHIP/wOBA"),
        ("Batting · Game Logs",  "batting_game_log",      "batting_game_log.csv",
         len(dataset.get("batting") or []),
                                 "Raw per-game per-player batting rows"),
        ("Pitching · Game Logs", "pitching_game_log",     "pitching_game_log.csv",
         len(dataset.get("pitching") or []),
                                 "Raw per-game per-player pitching rows"),
        ("Games",                "games",                 "games.csv",
         len(dataset.get("games") or []),
                                 "Raw game records (scores, weather, flags)"),
    ]
    if views.awards:
        rows.append(("Awards", "awards", "awards.csv", len(views.awards),
                     "Season awards (MVP, Cy Young, ROY, WS-MVP)"))
    return [
        {"name": name, "rows": rows_n, "file": file, "notes": notes}
        for (name, _key, file, rows_n, notes) in rows
    ]
