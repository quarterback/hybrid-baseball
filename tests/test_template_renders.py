"""Verify every modified template renders without Jinja errors against
the tiny fixture DB. Catches missing fields, typos, and {% if %} bugs."""
import os
import tempfile

import pytest


@pytest.fixture(scope="module")
def tiny_db_app():
    """Build a tiny DB and yield a Flask test client."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    os.environ["O27V2_DB_PATH"] = path
    import importlib
    import o27v2.db as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()

    with db_mod.get_conn() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO teams(name, abbrev, city, division, league, park_hr, park_hits)"
            " VALUES ('Astros','HOU','Houston','AL West','AL', 1.05, 1.02)"
        )
        c.execute(
            "INSERT INTO teams(name, abbrev, city, division, league, park_hr, park_hits)"
            " VALUES ('Tigers','DET','Detroit','AL Central','AL', 0.92, 0.97)"
        )
        c.execute("INSERT INTO players(team_id, name, position, is_pitcher) VALUES (1, 'A. Bat', 'SS', 0)")
        c.execute("INSERT INTO players(team_id, name, position, is_pitcher) VALUES (1, 'P. Cher', 'P', 1)")
        c.execute("INSERT INTO players(team_id, name, position, is_pitcher) VALUES (2, 'D. Bat', 'OF', 0)")
        c.execute("INSERT INTO players(team_id, name, position, is_pitcher) VALUES (2, 'E. Cher', 'P', 1)")

        for i in range(3):
            c.execute(
                "INSERT INTO games(season, game_date, home_team_id, away_team_id, home_score, away_score, winner_id, played)"
                " VALUES (?, ?, 2, 1, 2, 5, 1, 1)",
                (1, f"2026-04-{i+1:02d}"),
            )
        for gid in (1, 2, 3):
            c.execute(
                "INSERT INTO game_pitcher_stats(game_id, team_id, player_id, phase, batters_faced, outs_recorded, hits_allowed, runs_allowed, er, bb, k, hr_allowed, fo_induced, hbp_allowed, unearned_runs, is_starter)"
                " VALUES (?, 1, 2, 0, 30, 27, 5, 2, 2, 1, 8, 1, 1, 0, 0, 1)",
                (gid,),
            )
            c.execute(
                "INSERT INTO game_pitcher_stats(game_id, team_id, player_id, phase, batters_faced, outs_recorded, hits_allowed, runs_allowed, er, bb, k, hr_allowed, fo_induced, hbp_allowed, unearned_runs, is_starter)"
                " VALUES (?, 2, 4, 0, 33, 27, 8, 5, 5, 3, 5, 1, 0, 0, 0, 1)",
                (gid,),
            )
            c.execute(
                "INSERT INTO game_batter_stats(game_id, team_id, player_id, phase, pa, ab, runs, hits, doubles, hr, rbi, bb, k)"
                " VALUES (?, 1, 1, 0, 5, 5, 1, 2, 1, 0, 1, 0, 1)",
                (gid,),
            )
            c.execute(
                "INSERT INTO game_batter_stats(game_id, team_id, player_id, phase, pa, ab, hits)"
                " VALUES (?, 2, 3, 0, 3, 3, 0)",
                (gid,),
            )
        conn.commit()

    # Re-import so the app picks up the new DB.
    import o27v2.web.app as web_app
    importlib.reload(web_app)
    web_app.app.config["TESTING"] = True
    client = web_app.app.test_client()
    yield client

    os.unlink(path)
    os.environ.pop("O27V2_DB_PATH", None)


def test_leaders_page_renders(tiny_db_app):
    r = tiny_db_app.get("/leaders")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "wRC+" in body
    assert "wERA+" in body
    assert "GSc Index" in body or "GSc Idx" in body
    assert "WPA" in body
    assert "Hit Streak" in body or "Streak" in body


def test_player_page_renders_for_pitcher(tiny_db_app):
    r = tiny_db_app.get("/player/2")  # P. Cher
    assert r.status_code == 200, r.get_data(as_text=True)[:500]
    body = r.get_data(as_text=True)
    assert "wERA+" in body
    assert "GSc Idx" in body


def test_player_page_renders_for_batter(tiny_db_app):
    r = tiny_db_app.get("/player/1")  # A. Bat
    assert r.status_code == 200, r.get_data(as_text=True)[:500]
    body = r.get_data(as_text=True)
    assert "wRC+" in body


def test_team_page_renders(tiny_db_app):
    r = tiny_db_app.get("/team/1")
    assert r.status_code == 200, r.get_data(as_text=True)[:500]
    body = r.get_data(as_text=True)
    assert "wRC+" in body
    assert "wERA+" in body


def test_stats_browse_renders(tiny_db_app):
    for view in ("default", "advanced", "all"):
        r = tiny_db_app.get(f"/stats?view={view}")
        assert r.status_code == 200, f"view={view}: {r.get_data(as_text=True)[:500]}"


def test_compare_page_renders(tiny_db_app):
    r = tiny_db_app.get("/compare?ids=1,2")
    assert r.status_code == 200, r.get_data(as_text=True)[:500]
    body = r.get_data(as_text=True)
    assert "wRC+" in body
    assert "wERA+" in body


def test_season_archive_schema_has_new_columns(tiny_db_app):
    """The migration on init_db must add the new columns to existing
    season_batting_leaders / season_pitching_leaders rows, even on
    pre-existing DBs that were created before this batch.
    """
    from o27v2 import db
    bcols = {r["name"] for r in db.fetchall(
        "SELECT name FROM pragma_table_info('season_batting_leaders')"
    )}
    assert {"wrc_plus", "wpa", "li_avg"} <= bcols
    pcols = {r["name"] for r in db.fetchall(
        "SELECT name FROM pragma_table_info('season_pitching_leaders')"
    )}
    assert {"wera_plus", "gsc_index", "wpa", "li_avg"} <= pcols


def test_season_archive_writer_runs_end_to_end(tiny_db_app):
    """Snapshotting a season must not raise (regression: pre-fix code
    sorted pitching rows by `xfip`, which `_aggregate_pitcher_rows`
    never stamps — it stamps `xra`. The writer crashed with KeyError).
    """
    from o27v2 import db
    from o27v2 import season_archive
    db.execute(
        "INSERT INTO seasons(id, season_number, year) VALUES (?, ?, ?)",
        (1, 1, 2026),
    )
    # Should not raise.
    season_archive._snapshot_leaders(season_id=1)

    bat = db.fetchall("SELECT * FROM season_batting_leaders WHERE season_id = 1")
    pit = db.fetchall("SELECT * FROM season_pitching_leaders WHERE season_id = 1")
    assert bat, "writer produced no batting leaders"
    assert pit, "writer produced no pitching leaders"
    # New sort categories are present.
    bat_cats = {r["category"] for r in bat}
    pit_cats = {r["category"] for r in pit}
    assert "wrc_plus" in bat_cats
    assert "wera_plus" in pit_cats
    assert "gsc_index" in pit_cats
    assert "xra"       in pit_cats   # renamed from "xfip"
    assert "xfip"     not in pit_cats
