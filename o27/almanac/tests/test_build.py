"""End-to-end smoke test: fixture → compute → export → render.

Verifies the almanac pipeline produces every expected page, that exports
round-trip (re-loading the JSON bundle produces the same dataset shape),
and that key derived stats land in plausible numeric ranges.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

import pytest

from o27.almanac import compute, export, loader, render
from o27.almanac.tests.fixture import build_fixture


@pytest.fixture
def site(tmp_path):
    dataset = build_fixture()
    views = compute.compute_views(dataset)
    out = tmp_path / "site"
    export.write_exports(views, dataset, str(out))
    render.render_site(views, dataset, str(out),
                       site_title="Test Almanac",
                       subtitle="fixture")
    return {"dataset": dataset, "views": views, "out": out}


def test_pages_written(site):
    out = site["out"]
    must_exist = [
        "index.html", "standings.html", "schedule.html",
        "leaders/batting.html", "leaders/pitching.html", "leaders/stays.html",
        "teams/index.html", "players/index.html",
        "exports/index.html", "exports/season-bundle.json",
        "exports/season-bundle.zip",
        "exports/standings.csv", "exports/batting_season.csv",
        "exports/pitching_season.csv",
        "static/almanac.css", "static/almanac.js",
    ]
    for rel in must_exist:
        assert (out / rel).exists(), f"missing {rel}"


def test_per_team_and_player_pages(site):
    views = site["views"]
    out = site["out"]
    for t in views.teams:
        assert (out / "teams" / f"{t['abbrev'].lower()}.html").exists()
    for p in views.players:
        team_abbrev = next(
            (t["abbrev"] for t in views.teams if t["id"] == p["team_id"]),
            "FA",
        )
        slug = p["name"].lower().replace(" ", "_").replace(".", "")
        f = out / "players" / f"{team_abbrev.lower()}_{slug}.html"
        assert f.exists(), f"missing player page for {p['name']}"


def test_per_game_box_score(site):
    out = site["out"]
    dataset = site["dataset"]
    for g in dataset["games"]:
        assert (out / "games" / f"{g['id']}.html").exists()


def test_round_trip_bundle(site):
    out = site["out"]
    bundle = out / "exports" / "season-bundle.json"
    reloaded = loader.load(str(bundle))
    assert reloaded["meta"]["team_count"]   == site["dataset"]["meta"]["team_count"]
    assert reloaded["meta"]["game_count"]   == site["dataset"]["meta"]["game_count"]
    assert reloaded["meta"]["player_count"] == site["dataset"]["meta"]["player_count"]
    # And the views computed from the round-trip should match shape-wise.
    v2 = compute.compute_views(reloaded)
    v1 = site["views"]
    assert len(v2.batting_season)  == len(v1.batting_season)
    assert len(v2.pitching_season) == len(v1.pitching_season)
    assert len(v2.standings)       == len(v1.standings)


def test_league_totals_are_sane(site):
    lt = site["views"].league_totals
    # League batting averages should be in a real-world neighbourhood.
    assert 0.150 < lt["pavg"] < 0.400, f"PAVG out of band: {lt['pavg']:.3f}"
    assert lt["obp"] >= lt["pavg"], "OBP must be >= AVG"
    assert lt["slg"] >= lt["pavg"], "SLG must be >= AVG when there are XBH"
    assert lt["ops"] == pytest.approx(lt["obp"] + lt["slg"])
    # League ERA should be positive and not blow up.
    assert 0.5 < lt["era"] < 25.0, f"ERA out of band: {lt['era']:.2f}"
    assert lt["whip"] > 0


def test_standings_have_no_ties_on_pct_sort(site):
    rows = site["views"].standings
    for i in range(len(rows) - 1):
        # within a division group, pct should be non-increasing
        if rows[i]["division"] == rows[i + 1]["division"]:
            assert rows[i]["pct"] >= rows[i + 1]["pct"] - 1e-9


def test_qualified_filter_works(site):
    rows = site["views"].batting_season
    quals = [r for r in rows if r["qualified"]]
    nonq  = [r for r in rows if not r["qualified"]]
    # With ~75 PA over a 60-game season, most starters should qualify.
    if quals:
        assert min(r["pa"] for r in quals) >= compute.MIN_PA_QUALIFIED
    if nonq:
        assert max(r["pa"] for r in nonq) < compute.MIN_PA_QUALIFIED


def test_csv_exports_have_headers(site):
    import csv
    out = site["out"]
    for name in ("standings.csv", "batting_season.csv", "pitching_season.csv",
                 "schedule.csv", "team_totals_batting.csv"):
        path = out / "exports" / name
        with open(path) as f:
            reader = csv.reader(f)
            header = next(reader)
            assert len(header) > 1, f"{name} has no columns"
            rows = list(reader)
            assert len(rows) > 0, f"{name} has no data rows"
