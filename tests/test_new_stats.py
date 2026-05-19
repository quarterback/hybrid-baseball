"""Regression tests for the new stat features:
- GSc Index (z-score-normalized Game Score)
- Streaks (hit streaks, no-hitters, perfect games)
- Game-log Top 10 (best outings / games of the season)
- Park-adjusted wRC+ and wERA+
- WPA + Leverage Index

Each test builds a tiny throwaway DB (env override via O27V2_DB_PATH)
and exercises the new code paths end-to-end. Numbers aren't compared to
fixed targets — empirical baselines drift with sample — but invariants
ARE asserted (perfect game detection, monotonic GSc ordering, etc.).
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(scope="module")
def tiny_db_path():
    """Create a fresh tiny DB at a tempfile path. Set O27V2_DB_PATH
    BEFORE importing o27v2 so the db module resolves to this path.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # init_db will create it
    os.environ["O27V2_DB_PATH"] = path

    # Force re-import so the module-level _DB_PATH picks up the env var.
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
        c.execute("INSERT INTO players(team_id, name, position, is_pitcher) VALUES (1, 'B. Bat', '3B', 0)")
        c.execute("INSERT INTO players(team_id, name, position, is_pitcher) VALUES (1, 'P. Cher', 'P', 1)")
        c.execute("INSERT INTO players(team_id, name, position, is_pitcher) VALUES (2, 'D. Bat', 'OF', 0)")
        c.execute("INSERT INTO players(team_id, name, position, is_pitcher) VALUES (2, 'E. Cher', 'P', 1)")

        # 3 HOU @ DET games, all won by HOU
        for i in range(3):
            c.execute(
                "INSERT INTO games(season, game_date, home_team_id, away_team_id, home_score, away_score, winner_id, played)"
                " VALUES (?, ?, 2, 1, 2, 5, 1, 1)",
                (1, f"2026-04-{i+1:02d}"),
            )

        # P.Cher (id 3): perfect game (1), no-hitter w/ BB (2), normal start (3)
        c.execute(
            "INSERT INTO game_pitcher_stats(game_id, team_id, player_id, phase, batters_faced, outs_recorded, hits_allowed, runs_allowed, er, bb, k, hr_allowed, fo_induced, hbp_allowed, unearned_runs, is_starter)"
            " VALUES (1, 1, 3, 0, 27, 27, 0, 0, 0, 0, 12, 0, 1, 0, 0, 1)"
        )
        c.execute(
            "INSERT INTO game_pitcher_stats(game_id, team_id, player_id, phase, batters_faced, outs_recorded, hits_allowed, runs_allowed, er, bb, k, hr_allowed, fo_induced, hbp_allowed, unearned_runs, is_starter)"
            " VALUES (2, 1, 3, 0, 29, 27, 0, 0, 0, 2, 10, 0, 0, 0, 0, 1)"
        )
        c.execute(
            "INSERT INTO game_pitcher_stats(game_id, team_id, player_id, phase, batters_faced, outs_recorded, hits_allowed, runs_allowed, er, bb, k, hr_allowed, fo_induced, hbp_allowed, unearned_runs, is_starter)"
            " VALUES (3, 1, 3, 0, 30, 27, 5, 2, 2, 1, 8, 1, 1, 0, 0, 1)"
        )
        for gid in (1, 2, 3):
            c.execute(
                "INSERT INTO game_pitcher_stats(game_id, team_id, player_id, phase, batters_faced, outs_recorded, hits_allowed, runs_allowed, er, bb, k, hr_allowed, fo_induced, hbp_allowed, unearned_runs, is_starter)"
                " VALUES (?, 2, 5, 0, 33, 27, 8, 5, 5, 3, 5, 1, 0, 0, 0, 1)",
                (gid,),
            )

        # Batter lines: A.Bat 3-game hit streak, B.Bat broken streak, D.Bat 0-fer
        for gid in (1, 2, 3):
            c.execute(
                "INSERT INTO game_batter_stats(game_id, team_id, player_id, phase, pa, ab, runs, hits, doubles, hr, rbi, bb, k)"
                " VALUES (?, 1, 1, 0, 5, 5, 1, 2, 1, 0, 1, 0, 1)",
                (gid,),
            )
        c.execute(
            "INSERT INTO game_batter_stats(game_id, team_id, player_id, phase, pa, ab, runs, hits, hr, rbi, bb, k)"
            " VALUES (1, 1, 2, 0, 4, 4, 1, 1, 0, 0, 0, 1)"
        )
        c.execute(
            "INSERT INTO game_batter_stats(game_id, team_id, player_id, phase, pa, ab, runs, hits, hr, rbi, bb, k)"
            " VALUES (2, 1, 2, 0, 4, 4, 0, 0, 0, 0, 0, 2)"
        )
        c.execute(
            "INSERT INTO game_batter_stats(game_id, team_id, player_id, phase, pa, ab, runs, hits, hr, rbi, bb, k)"
            " VALUES (3, 1, 2, 0, 5, 4, 2, 3, 2, 4, 1, 0)"
        )
        for gid in (1, 2, 3):
            c.execute(
                "INSERT INTO game_batter_stats(game_id, team_id, player_id, phase, pa, ab, hits)"
                " VALUES (?, 2, 4, 0, 3, 3, 0)",
                (gid,),
            )

        # PA log so WPA has events to chew on
        for gid in (1, 2, 3):
            c.execute(
                "INSERT INTO game_pa_log(game_id, team_id, batter_id, pitcher_id, phase, ab_seq, swing_idx, choice, was_stay, stay_credited, runs_scored, rbi_credited, outs_before, bases_before, score_diff_before, outs_after, bases_after, score_diff_after)"
                " VALUES (?, 1, 1, 5, 0, 1, 1, 'run', 0, 0, 1, 1, 0, 0, 0, 0, 0, 1)",
                (gid,),
            )
            c.execute(
                "INSERT INTO game_pa_log(game_id, team_id, batter_id, pitcher_id, phase, ab_seq, swing_idx, choice, was_stay, stay_credited, runs_scored, rbi_credited, outs_before, bases_before, score_diff_before, outs_after, bases_after, score_diff_after)"
                " VALUES (?, 2, 4, 3, 0, 1, 1, 'run', 0, 0, 0, 0, 0, 0, -5, 1, 0, -5)",
                (gid,),
            )
        conn.commit()

    yield path

    os.unlink(path)
    os.environ.pop("O27V2_DB_PATH", None)


def test_hit_streak_detection(tiny_db_path):
    from o27v2.analytics.streaks import longest_hit_streaks
    streaks = longest_hit_streaks(top_n=10)
    # A.Bat had hits in all 3 games — should be the top streak
    top = streaks[0]
    assert top["player_name"] == "A. Bat"
    assert top["length"] == 3
    assert top["active"] is True


def test_no_hitter_and_perfect_game_detection(tiny_db_path):
    from o27v2.analytics.streaks import no_hitters_and_perfect_games
    data = no_hitters_and_perfect_games()
    no_hitters = data["no_hitters"]
    perfect = data["perfect_games"]

    # P.Cher pitched two no-hitters (games 1 + 2); only game 1 is perfect
    # (game 2 had 2 walks).
    assert len(no_hitters) == 2
    assert all(n["player_name"] == "P. Cher" for n in no_hitters)
    assert len(perfect) == 1
    assert perfect[0]["player_name"] == "P. Cher"
    assert perfect[0]["bb"] == 0 and perfect[0]["hbp"] == 0


def test_top_pitcher_outings_orders_by_gsc(tiny_db_path):
    from o27v2.web.app import _top_pitcher_outings
    outings = _top_pitcher_outings(top_n=10)
    # All 3 P.Cher outings + 3 E.Cher outings = 6 rows
    assert len(outings) == 6
    # Sorted descending by GSc
    gscs = [o["gsc"] for o in outings]
    assert gscs == sorted(gscs, reverse=True)
    # Top outing should be the perfect game (game 1)
    assert outings[0]["player_name"] == "P. Cher"
    assert outings[0]["h"] == 0


def test_top_batter_games_orders_by_bgsc(tiny_db_path):
    from o27v2.web.app import _top_batter_games
    games = _top_batter_games(top_n=10)
    # 3 (A.Bat) + 3 (B.Bat) + 3 (D.Bat) = 9 rows
    assert len(games) == 9
    bgscs = [g["bgsc"] for g in games]
    assert bgscs == sorted(bgscs, reverse=True)
    # B.Bat's 3-for-4 with 2 HR / 4 RBI should beat A.Bat's quieter days
    assert games[0]["player_name"] == "B. Bat"
    assert games[0]["hr"] == 2


def test_park_factor_lookup(tiny_db_path):
    from o27v2.web.app import _team_park_map
    pf = _team_park_map()
    # HOU (1.05 HR, 1.02 hits) → home_pf = 1.035, player_pf = (1.035 + 1)/2 = 1.0175
    assert abs(pf[1] - 1.0175) < 1e-4
    # DET (0.92, 0.97) → 0.945 → (0.945 + 1)/2 = 0.9725
    assert abs(pf[2] - 0.9725) < 1e-4


def test_gsc_index_and_wera_plus_present(tiny_db_path):
    """End-to-end: aggregate pitcher rows and verify the new fields appear."""
    from o27v2.web.app import _league_baselines, _aggregate_pitcher_rows
    from o27v2 import db
    baselines = _league_baselines()
    # baselines must include both new keys
    assert "gsc_std" in baselines
    assert "runs_per_pa" in baselines
    assert baselines["gsc_std"] > 0

    rows = db.fetchall(
        """SELECT p.id AS player_id, p.name AS player_name, t.id AS team_id,
                  COUNT(ps.game_id) AS g,
                  SUM(ps.batters_faced) AS bf, SUM(ps.outs_recorded) AS outs,
                  SUM(ps.hits_allowed) AS h, SUM(ps.runs_allowed) AS r,
                  SUM(ps.er) AS er, SUM(ps.bb) AS bb, SUM(ps.k) AS k,
                  SUM(ps.hr_allowed) AS hr_allowed,
                  COALESCE(SUM(ps.hbp_allowed),0) AS hbp_allowed,
                  COALESCE(SUM(ps.unearned_runs),0) AS uer,
                  COALESCE(SUM(ps.fo_induced),0) AS fo_induced,
                  COALESCE(SUM(ps.pitches),0) AS pitches,
                  COALESCE(SUM(ps.er_arc1),0) AS er_arc1,
                  COALESCE(SUM(ps.er_arc2),0) AS er_arc2,
                  COALESCE(SUM(ps.er_arc3),0) AS er_arc3,
                  COALESCE(SUM(ps.k_arc1),0)  AS k_arc1,
                  COALESCE(SUM(ps.k_arc2),0)  AS k_arc2,
                  COALESCE(SUM(ps.k_arc3),0)  AS k_arc3,
                  COALESCE(SUM(ps.fo_arc1),0) AS fo_arc1,
                  COALESCE(SUM(ps.fo_arc2),0) AS fo_arc2,
                  COALESCE(SUM(ps.fo_arc3),0) AS fo_arc3,
                  COALESCE(SUM(ps.bf_arc1),0) AS bf_arc1,
                  COALESCE(SUM(ps.bf_arc2),0) AS bf_arc2,
                  COALESCE(SUM(ps.bf_arc3),0) AS bf_arc3,
                  COALESCE(SUM(ps.is_starter),0) AS gs,
                  COALESCE(SUM(ps.singles_allowed),0) AS singles_allowed,
                  COALESCE(SUM(ps.doubles_allowed),0) AS doubles_allowed,
                  COALESCE(SUM(ps.triples_allowed),0) AS triples_allowed
           FROM game_pitcher_stats ps
           JOIN players p ON ps.player_id = p.id
           JOIN teams   t ON ps.team_id   = t.id
           GROUP BY p.id"""
    )
    _aggregate_pitcher_rows(rows, baselines=baselines)
    for p in rows:
        assert "gsc_index" in p
        assert "wera_plus" in p
        assert "park_factor" in p
        # The better pitcher (P. Cher, id 3) should land above the worse
        # one (E. Cher, id 5) on GSc Index.
    by_id = {r["player_id"]: r for r in rows}
    assert by_id[3]["gsc_index"] > by_id[5]["gsc_index"]
    # And on GSc+ for sanity.
    assert by_id[3]["gsc_plus"]  > by_id[5]["gsc_plus"]


def test_wrc_plus_present_for_batters(tiny_db_path):
    from o27v2.web.app import _league_baselines, _aggregate_batter_rows
    from o27v2 import db
    baselines = _league_baselines()
    rows = db.fetchall(
        """SELECT p.id AS player_id, p.name AS player_name, p.position,
                  t.id AS team_id,
                  COUNT(bs.game_id) AS g,
                  SUM(bs.pa) AS pa, SUM(bs.ab) AS ab, SUM(bs.hits) AS h,
                  SUM(bs.doubles) AS d2, SUM(bs.triples) AS d3, SUM(bs.hr) AS hr,
                  SUM(bs.runs) AS r, SUM(bs.rbi) AS rbi,
                  SUM(bs.bb) AS bb, SUM(bs.k) AS k, SUM(bs.stays) AS stays,
                  COALESCE(SUM(bs.hbp),0) AS hbp
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           JOIN teams   t ON bs.team_id   = t.id
           GROUP BY p.id"""
    )
    _aggregate_batter_rows(rows, baselines=baselines)
    for r in rows:
        assert "wrc_plus" in r
        assert "park_factor" in r


def test_wpa_module_builds_without_crashing(tiny_db_path):
    """WPA needs more games than this fixture provides to produce
    populated per-player rows, but the builders must not crash and
    must return well-formed dicts even on tiny samples.
    """
    from o27v2.analytics.wpa import build_wp_table, build_player_wpa
    table = build_wp_table()
    assert "wp" in table
    assert "wp_margin" in table
    assert table["n_pas"] == 6  # 2 PAs per game × 3 games

    result = build_player_wpa()
    # With min_n=8 and only 6 PAs total, no lookups will resolve, so
    # per-player aggregates may be empty. But the result shape must be
    # stable.
    assert "by_batter" in result
    assert "by_pitcher" in result
    assert "top_pa" in result
