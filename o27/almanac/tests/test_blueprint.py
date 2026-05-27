"""
Smoke tests for the Flask blueprint. Mounts the almanac on a bare
Flask app pointed at the fixture dataset, then hits each route shape
once and asserts a 200 + a clue that the page rendered.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest
from flask import Flask

# The blueprint reads from `loader.load(source)`, which dispatches on
# whether the source looks like SQLite or JSON. The fixture module
# returns an in-memory dataset, so we monkeypatch loader.load to
# bypass file I/O entirely.
from o27.almanac import blueprint as bp_module
from o27.almanac import loader
from o27.almanac.tests.fixture import build_fixture


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["ALMANAC_SOURCE"] = "fixture://memory"
    # Bypass disk loading.
    fixture = build_fixture()
    original_load = loader.load
    loader.load = lambda source: fixture  # type: ignore[assignment]
    # Mtime cache key has to vary across tests; reset it.
    bp_module.invalidate_cache()
    # The mtime lookup will fail on the fake source — _cache_key handles
    # that by returning 0.0. That's fine; the cache will simply persist
    # for the duration of the test (which we reset above).
    app.register_blueprint(bp_module.almanac_bp)
    try:
        with app.test_client() as c:
            yield c
    finally:
        loader.load = original_load
        bp_module.invalidate_cache()


def _ok(client, path: str) -> bytes:
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} → {resp.status_code}"
    return resp.data


def test_home(client):
    body = _ok(client, "/almanac/")
    assert b"O27 Almanac" in body or b"Hybrid Baseball" in body


def test_top_level_pages(client):
    for p in ("/almanac/standings.html",
              "/almanac/schedule.html",
              "/almanac/awards.html",
              "/almanac/parks.html"):
        _ok(client, p)


def test_leader_boards(client):
    for which in ("batting", "pitching", "stays", "fielding",
                  "value", "situational"):
        _ok(client, f"/almanac/leaders/{which}.html")


def test_leader_bad_name(client):
    assert client.get("/almanac/leaders/bogus.html").status_code == 404


def test_teams_pages(client):
    _ok(client, "/almanac/teams/index.html")
    # Fixture has at least one team with an abbrev — find it.
    fixture = build_fixture()
    abb = fixture["teams"][0]["abbrev"]
    _ok(client, f"/almanac/teams/{abb.lower()}.html")


def test_team_not_found(client):
    assert client.get("/almanac/teams/zzz.html").status_code == 404


def test_players_index_and_detail(client):
    _ok(client, "/almanac/players/index.html")
    # Use the same slug computation render.py uses.
    from o27.almanac.render import _slugify
    fixture = build_fixture()
    teams_by_id = {t["id"]: t for t in fixture["teams"]}
    p = fixture["players"][0]
    team_abb = teams_by_id[p["team_id"]]["abbrev"]
    slug = _slugify(p["name"])
    _ok(client, f"/almanac/players/{team_abb}_{slug}.html")


def test_game_detail(client):
    fixture = build_fixture()
    gid = fixture["games"][0]["id"]
    _ok(client, f"/almanac/games/{gid}.html")


def test_game_not_found(client):
    assert client.get("/almanac/games/999999.html").status_code == 404


def test_exports_index(client):
    body = _ok(client, "/almanac/exports/index.html")
    assert b"standings.csv" in body or b"Export" in body or b"export" in body


def test_export_csv(client):
    body = _ok(client, "/almanac/exports/standings.csv")
    assert body.startswith(b"abbrev") or b"," in body


def test_export_bundle_zip(client):
    resp = client.get("/almanac/exports/season-bundle.zip")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    assert resp.data[:2] == b"PK"  # zip magic


def test_export_unknown_file(client):
    assert client.get("/almanac/exports/nope.csv").status_code == 404


def test_static_asset_passthrough(client):
    """Templates reference /almanac/static/almanac.css — make sure the
    passthrough route resolves to the on-disk asset."""
    resp = client.get("/almanac/static/almanac.css")
    assert resp.status_code == 200
    assert b"body" in resp.data or len(resp.data) > 0


# ---------------------------------------------------------------------------
# Empty-DB regression: when a fresh deploy seeds the league but no games
# have been played yet, every page must still render (don't 500).
# Caught one bug where pythag_summary's early-return path omitted
# `improvement_pct` that standings.html.j2 references.
# ---------------------------------------------------------------------------

@pytest.fixture()
def empty_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["ALMANAC_SOURCE"] = "fixture://empty"
    empty_dataset = {
        "teams": [], "players": [], "games": [],
        "batting": [], "pitching": [], "fielding": [],
        "scoring_events": [], "pa_log": [],
        "parks": [], "awards": [], "playoff_series": [],
        "seasons": [], "season": None,
    }
    original_load = loader.load
    loader.load = lambda source: empty_dataset  # type: ignore[assignment]
    bp_module.invalidate_cache()
    app.register_blueprint(bp_module.almanac_bp)
    try:
        with app.test_client() as c:
            yield c
    finally:
        loader.load = original_load
        bp_module.invalidate_cache()


# ---------------------------------------------------------------------------
# warm_cache: pre-computes the dataset+views off the request path (e.g. in a
# background thread after creating a new league) so the first real request
# renders from a warm cache instead of paying the cold-load aggregation.
# ---------------------------------------------------------------------------

def test_warm_cache_populates_cache(monkeypatch, tmp_path):
    fixture = build_fixture()
    calls = {"n": 0}

    def counting_load(source):
        calls["n"] += 1
        return fixture

    monkeypatch.setattr(loader, "load", counting_load)
    # Real file so _cache_key's mtime lookup is stable across the warm + read.
    src = tmp_path / "live.db"
    src.write_bytes(b"SQLite format 3\x00")
    bp_module.invalidate_cache()
    try:
        assert bp_module.warm_cache(str(src)) is True
        assert calls["n"] == 1
        # Second warm of the same source is a no-op hit — no recompute.
        assert bp_module.warm_cache(str(src)) is True
        assert calls["n"] == 1
        # A request resolving to the same source reads the warmed cache.
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["ALMANAC_SOURCE"] = str(src)
        app.register_blueprint(bp_module.almanac_bp)
        with app.test_client() as c:
            assert c.get("/almanac/standings.html").status_code == 200
        assert calls["n"] == 1  # served from warm cache, no extra load
    finally:
        bp_module.invalidate_cache()


def test_warm_cache_swallows_errors(monkeypatch):
    def boom(source):
        raise RuntimeError("db not ready")

    monkeypatch.setattr(loader, "load", boom)
    bp_module.invalidate_cache()
    try:
        # Best-effort: a failed warm must never raise into the caller.
        assert bp_module.warm_cache("anything") is False
        assert bp_module._CACHE["dataset"] is None
    finally:
        bp_module.invalidate_cache()


def test_empty_db_renders_every_top_level_page(empty_client):
    """Regression for the standings.html `improvement_pct` crash. Every
    non-detail page must render against an empty dataset."""
    for path in ("/almanac/",
                 "/almanac/standings.html",
                 "/almanac/schedule.html",
                 "/almanac/awards.html",
                 "/almanac/parks.html",
                 "/almanac/leaders/batting.html",
                 "/almanac/leaders/pitching.html",
                 "/almanac/leaders/stays.html",
                 "/almanac/leaders/fielding.html",
                 "/almanac/leaders/value.html",
                 "/almanac/leaders/situational.html",
                 "/almanac/teams/index.html",
                 "/almanac/players/index.html",
                 "/almanac/exports/index.html"):
        r = empty_client.get(path)
        assert r.status_code == 200, f"{path} crashed on empty DB ({r.status_code})"
