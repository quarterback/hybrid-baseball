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
