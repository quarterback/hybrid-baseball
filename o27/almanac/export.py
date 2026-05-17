"""
o27.almanac.export — CSV / JSON / bundle writers.

Produces three things, all written under {out}/exports/:

  * One CSV per logical dataset (standings, schedule, batting_season,
    pitching_season, league_totals, plus per-team batting/pitching).
  * A round-trippable JSON bundle (`season-bundle.json`) that matches the
    loader's expected shape so a future almanac build can ingest it.
  * A zip archive (`season-bundle.zip`) containing all of the above plus
    a copy of the source DB if available.

The JSON bundle is the wiring contract — drop it into a fresh almanac
build with `--source path/to/season-bundle.json` and the site rebuilds
identically.
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import os
import shutil
import zipfile
from typing import Any, Iterable

from .compute import Views


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _write_csv(path: str, rows: Iterable[dict], cols: list[str] | None = None) -> None:
    rows = list(rows)
    if not rows:
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(cols or [])
        return
    if cols is None:
        seen, cols = set(), []
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    cols.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _csv_safe(r.get(k)) for k in cols})


def _csv_safe(v: Any) -> Any:
    if isinstance(v, float):
        if v != v:  # NaN
            return ""
        return round(v, 6)
    if v is None:
        return ""
    return v


# ---------------------------------------------------------------------------
# Bundle writer
# ---------------------------------------------------------------------------

def write_exports(views: Views, dataset: dict, out_dir: str) -> dict[str, str]:
    """Write every CSV + the JSON bundle + the zip. Returns a manifest
    mapping dataset name → relative path (for links on the exports page)."""
    exp_dir = os.path.join(out_dir, "exports")
    os.makedirs(exp_dir, exist_ok=True)

    manifest: dict[str, str] = {}

    # ---- Per-dataset CSVs ------------------------------------------------
    _write_csv(os.path.join(exp_dir, "standings.csv"),       views.standings)
    manifest["standings"] = "exports/standings.csv"

    _write_csv(os.path.join(exp_dir, "schedule.csv"),        views.schedule)
    manifest["schedule"] = "exports/schedule.csv"

    _write_csv(os.path.join(exp_dir, "teams.csv"),           views.teams)
    manifest["teams"] = "exports/teams.csv"

    _write_csv(os.path.join(exp_dir, "players.csv"),         views.players)
    manifest["players"] = "exports/players.csv"

    _write_csv(os.path.join(exp_dir, "batting_season.csv"),  views.batting_season)
    manifest["batting_season"] = "exports/batting_season.csv"

    _write_csv(os.path.join(exp_dir, "pitching_season.csv"), views.pitching_season)
    manifest["pitching_season"] = "exports/pitching_season.csv"

    _write_csv(os.path.join(exp_dir, "fielding_season.csv"), views.fielding_season)
    manifest["fielding_season"] = "exports/fielding_season.csv"

    _write_csv(os.path.join(exp_dir, "team_pythag.csv"),
               [{"abbrev": k, **v} for k, v in views.team_pythag.items()])
    manifest["team_pythag"] = "exports/team_pythag.csv"

    _write_csv(os.path.join(exp_dir, "monthly_splits.csv"), views.monthly_splits)
    manifest["monthly_splits"] = "exports/monthly_splits.csv"

    _write_csv(os.path.join(exp_dir, "scoring_events.csv"), views.scoring_events)
    manifest["scoring_events"] = "exports/scoring_events.csv"

    _write_csv(os.path.join(exp_dir, "pa_log.csv"), views.pa_log)
    manifest["pa_log"] = "exports/pa_log.csv"

    if views.playoff_series:
        _write_csv(os.path.join(exp_dir, "playoff_series.csv"), views.playoff_series)
        manifest["playoff_series"] = "exports/playoff_series.csv"

    _write_csv(os.path.join(exp_dir, "team_totals_batting.csv"),
               list(views.team_totals_bat.values()))
    manifest["team_totals_batting"] = "exports/team_totals_batting.csv"

    _write_csv(os.path.join(exp_dir, "team_totals_pitching.csv"),
               list(views.team_totals_pit.values()))
    manifest["team_totals_pitching"] = "exports/team_totals_pitching.csv"

    _write_csv(os.path.join(exp_dir, "league_totals.csv"),
               [views.league_totals])
    manifest["league_totals"] = "exports/league_totals.csv"

    # Raw per-game lines (the data behind every aggregate).
    _write_csv(os.path.join(exp_dir, "batting_game_log.csv"),
               dataset.get("batting") or [])
    manifest["batting_game_log"] = "exports/batting_game_log.csv"

    _write_csv(os.path.join(exp_dir, "pitching_game_log.csv"),
               dataset.get("pitching") or [])
    manifest["pitching_game_log"] = "exports/pitching_game_log.csv"

    _write_csv(os.path.join(exp_dir, "games.csv"),
               dataset.get("games") or [])
    manifest["games"] = "exports/games.csv"

    if views.awards:
        _write_csv(os.path.join(exp_dir, "awards.csv"), views.awards)
        manifest["awards"] = "exports/awards.csv"

    # ---- JSON bundle (round-trip ingestion contract) --------------------
    bundle_path = os.path.join(exp_dir, "season-bundle.json")
    bundle = {
        "meta": {
            **dict(dataset.get("meta") or {}),
            "exported_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        },
        "teams":    dataset.get("teams")    or [],
        "players":  dataset.get("players")  or [],
        "games":    dataset.get("games")    or [],
        "batting":  dataset.get("batting")  or [],
        "pitching": dataset.get("pitching") or [],
        "seasons":  dataset.get("seasons")  or [],
        "awards":   dataset.get("awards")   or [],
        "scoring_events":  dataset.get("scoring_events")  or [],
        "pa_log":          dataset.get("pa_log")          or [],
        "team_phase_outs": dataset.get("team_phase_outs") or [],
        "playoff_series":  dataset.get("playoff_series")  or [],
        "award_ballots":   dataset.get("award_ballots")   or [],
        "season_standings_archive":  dataset.get("season_standings_archive")  or [],
        "season_batting_leaders_archive":  dataset.get("season_batting_leaders_archive")  or [],
        "season_pitching_leaders_archive": dataset.get("season_pitching_leaders_archive") or [],
    }
    with open(bundle_path, "w") as f:
        json.dump(bundle, f, indent=2, default=str)
    manifest["json_bundle"] = "exports/season-bundle.json"

    # ---- Zip everything --------------------------------------------------
    zip_path = os.path.join(exp_dir, "season-bundle.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in os.listdir(exp_dir):
            full = os.path.join(exp_dir, name)
            if name == "season-bundle.zip":
                continue
            if os.path.isfile(full):
                z.write(full, arcname=name)
        # Include the source DB if it's a sqlite path the user pointed at.
        src = (dataset.get("meta") or {}).get("source", "")
        if isinstance(src, str) and src.endswith((".db", ".sqlite", ".sqlite3")) and os.path.exists(src):
            z.write(src, arcname=os.path.basename(src))
    manifest["zip_bundle"] = "exports/season-bundle.zip"

    return manifest
