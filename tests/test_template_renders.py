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


# ---------------------------------------------------------------------------
# Substitution model — schema, renderer, and footnote tests
# ---------------------------------------------------------------------------

def test_entered_inning_column_migrated(tiny_db_app):
    """The substitution model adds entered_inning to game_batter_stats.
    Init must run the ALTER for existing DBs, not just CREATE."""
    from o27v2 import db
    cols = {r["name"] for r in db.fetchall(
        "SELECT name FROM pragma_table_info('game_batter_stats')"
    )}
    assert "entered_inning" in cols
    assert "entry_type" in cols
    assert "replaced_player_id" in cols


def _row(pid, name, pos, **kw):
    """Minimal batting row stub for renderer tests."""
    base = dict(
        player_id=pid, player_name=name, position=pos, box_position=pos,
        ab=0, runs=0, hits=0, doubles=0, triples=0, hr=0, rbi=0, bb=0,
        k=0, stays=0, pa=0, entry_type="starter", replaced_player_id=None,
        entered_inning=0,
    )
    base.update(kw)
    return base


def test_box_score_indents_ph_with_footnote():
    """A PH row indents under the replaced starter, shows position 'ph',
    gets a footnote letter prefix 'a-', and the footnote block emits
    the outcome (struck out / singled / walked) + replaced player + inning."""
    from o27v2.web.box_score import render_batting_table, _sub_footnotes_for
    rows = [
        _row(1, "Rodriguez", "3b", ab=3, hits=1, pa=3),
        _row(2, "Matsui", "3b", ab=1, k=1, pa=1,
             entry_type="PH", replaced_player_id=1, entered_inning=7),
        _row(3, "Sheffield", "rf", ab=4, hits=2, pa=4),
    ]
    out = render_batting_table("Yankees", rows)
    assert "Rodriguez" in out
    # PH row indented and labeled "ph", lettered 'a-'.
    assert "  a-Matsui" in out
    assert " ph " in out

    fn = _sub_footnotes_for(rows)
    assert "a-Struck out for Rodriguez in the 7th." in fn


def test_box_score_pr_row_labeled_and_footnoted():
    """A PR with 0 AB renders position 'pr' and the footnote reads
    'Ran for X in the Yth.' Sanity: lineup-order letter assignment."""
    from o27v2.web.box_score import render_batting_table, _sub_footnotes_for
    rows = [
        _row(10, "Skanes", "cf", ab=2, hits=1, pa=2),
        _row(11, "Vargas", "cf", ab=0, pa=0, sb=1,
             entry_type="PR", replaced_player_id=10, entered_inning=8),
    ]
    out = render_batting_table("PawSox", rows)
    assert "  a-Vargas" in out
    assert " pr " in out
    fn = _sub_footnotes_for(rows)
    assert fn == "  a-Ran for Skanes in the 8th."


def test_box_score_multiple_subs_get_sequential_letters():
    """Two subs on the same team get a-, b- in lineup order."""
    from o27v2.web.box_score import _sub_footnotes_for
    rows = [
        _row(1, "Jeter", "ss", ab=4, hits=2, pa=4),
        _row(2, "Rodriguez", "3b", ab=3, pa=3),
        _row(3, "Matsui", "3b", ab=1, hits=1, pa=1,
             entry_type="PH", replaced_player_id=2, entered_inning=7),
        _row(4, "Sheffield", "rf", ab=3, hits=1, pa=4),
        _row(5, "Posada", "c", ab=2, pa=2),
        _row(6, "Stinnett", "c", ab=0, pa=0,
             entry_type="DEF", replaced_player_id=5, entered_inning=9),
    ]
    fn = _sub_footnotes_for(rows)
    assert "a-Singled for Rodriguez in the 7th." in fn
    assert "b-Replaced Posada at C in the 9th." in fn


def test_pr_with_ab_but_no_pa_raises():
    """The PR=AB=0 invariant: a PR with ab>0 but pa==0 indicates a
    sim-side stat-accounting bug. Renderer must surface this loudly
    rather than silently emit a corrupt box score."""
    import pytest
    from o27v2.web.box_score import render_batting_table
    rows = [
        _row(10, "Skanes", "cf", ab=2, hits=1, pa=2),
        _row(11, "Vargas", "cf", ab=3, pa=0,  # corrupt: AB without PA
             entry_type="PR", replaced_player_id=10, entered_inning=8),
    ]
    with pytest.raises(AssertionError, match="ab=3 but pa=0"):
        render_batting_table("PawSox", rows)


def test_starter_with_no_entry_has_no_letter():
    """Starters never get a footnote letter. Subs without lineup
    indentation context (legacy rows missing replaced_player_id) still
    get assigned a letter so the indent-block reads consistently."""
    from o27v2.web.box_score import render_batting_table, _sub_footnotes_for
    rows = [
        _row(1, "Jeter", "ss", ab=4, hits=2, pa=4),
        _row(2, "Cano", "2b", ab=4, hits=1, pa=4),
    ]
    out = render_batting_table("Yankees", rows)
    # Starters should NOT have "a-" or "b-" prefixes.
    for line in out.splitlines():
        assert not line.lstrip().startswith("a-")
        assert not line.lstrip().startswith("b-")
    fn = _sub_footnotes_for(rows)
    assert fn == ""
