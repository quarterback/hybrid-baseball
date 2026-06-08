"""Bunting-rate analytics — o27v2/analytics/bunting.build_bunting_rates.

Exercises the league bunt barometer against a synthetic DB (no flask): the
derived rates (bunt%PA, bunt-hit rate, sac/squeeze share, productive%), the
pitcher-vs-position split (O27 has no DH), and the per-team breakdown.

Run:  python -m pytest o27v2/tests/test_bunting_rates.py -v
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from o27v2 import db
from o27v2.analytics.bunting import (
    build_bunting_rates, build_bunt_run_value, _rates, _re_lookup,
)


# ---------------------------------------------------------------------------
# Pure-math helper — no DB.
# ---------------------------------------------------------------------------

def test_rates_division_safe_on_zeros():
    r = _rates({})
    for k in ("bunt_pct_pa", "bunt_hit_rate", "sac_share", "sqz_share",
              "productive_pct"):
        assert r[k] == 0.0


def test_rates_math():
    r = _rates({"pa": 100, "bunt_att": 10, "bunt_hits": 3, "sh": 4,
                "sqz": 2, "sqz_rbi": 1})
    assert r["bunt_pct_pa"] == pytest.approx(0.10)
    assert r["bunt_hit_rate"] == pytest.approx(0.30)
    assert r["sac_share"] == pytest.approx(0.40)
    assert r["sqz_share"] == pytest.approx(0.20)
    assert r["productive_pct"] == pytest.approx(0.70)  # (3 + 4) / 10


# ---------------------------------------------------------------------------
# DB-backed end-to-end.
# ---------------------------------------------------------------------------

@pytest.fixture()
def synth_db():
    path = tempfile.mktemp(suffix=".db")
    db._DB_PATH = path
    db._DB_PATH_OVERRIDDEN = True
    db.init_db()

    ex = db.execute
    ex("INSERT INTO teams(id,name,abbrev,city,division,league) "
       "VALUES (1,'Home Nine','HOM','Hometown','E','AL')")
    ex("INSERT INTO teams(id,name,abbrev,city,division,league) "
       "VALUES (2,'Away Crew','AWY','Awaycity','E','AL')")
    # A position player and a pitcher on each team.
    for pid, name, pos, tid, isp in [(10, 'Bunter Bob', 'OF', 1, 0),
                                     (11, 'Hurler Hank', 'P', 1, 1),
                                     (20, 'Slap Sully', '2B', 2, 0),
                                     (21, 'Arm Arnold', 'P', 2, 1)]:
        ex("INSERT INTO players(id,name,position,team_id,is_pitcher) "
           "VALUES (?,?,?,?,?)", (pid, name, pos, tid, isp))
    ex("INSERT INTO games(id,game_date,home_team_id,away_team_id,played,"
       "home_score,away_score) VALUES (1,'2026-04-01',1,2,1,5,3)")
    # A second (playoff) game to host the must-be-excluded noise row, since
    # game_batter_stats is UNIQUE on (player_id, game_id, phase).
    ex("INSERT INTO games(id,game_date,home_team_id,away_team_id,played,"
       "home_score,away_score,is_playoff) VALUES (2,'2026-10-01',1,2,1,5,3,1)")

    def bat(pid, tid, *, pa, bunt_att, bunt_hits, sh, sqz, sqz_rbi,
            game_id=1, is_playoff=0, phase=0):
        ex("""INSERT INTO game_batter_stats(game_id,team_id,player_id,phase,
              is_playoff,pa,bunt_att,bunt_hits,sh,sqz,sqz_rbi)
              VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
           (game_id, tid, pid, phase, is_playoff, pa, bunt_att, bunt_hits,
            sh, sqz, sqz_rbi))

    # Team 1: position player drags (reaches base), pitcher sacrifices.
    bat(10, 1, pa=50, bunt_att=8, bunt_hits=4, sh=2, sqz=1, sqz_rbi=1)
    bat(11, 1, pa=10, bunt_att=4, bunt_hits=0, sh=4, sqz=0, sqz_rbi=0)
    # Team 2: position player, pitcher.
    bat(20, 2, pa=40, bunt_att=4, bunt_hits=2, sh=1, sqz=1, sqz_rbi=0)
    bat(21, 2, pa=8,  bunt_att=2, bunt_hits=0, sh=2, sqz=0, sqz_rbi=0)
    # Noise that must be excluded: a playoff row (game 2) and a
    # non-regulation phase (game 1, phase 1).
    bat(10, 1, pa=99, bunt_att=99, bunt_hits=99, sh=99, sqz=99, sqz_rbi=99,
        game_id=2, is_playoff=1)
    bat(20, 2, pa=99, bunt_att=99, bunt_hits=99, sh=99, sqz=99, sqz_rbi=99,
        phase=1)

    yield path


def test_league_totals_exclude_playoff_and_nonreg(synth_db):
    r = build_bunting_rates()
    L = r["league"]
    # Regulation regular-season bunts only: 8 + 4 + 4 + 2 = 18.
    assert L["bunt_att"] == 18
    assert L["pa"] == 50 + 10 + 40 + 8        # 108
    assert L["bunt_hits"] == 6                # 4 + 0 + 2 + 0
    assert L["bunt_pct_pa"] == pytest.approx(18 / 108)
    assert L["bunt_hit_rate"] == pytest.approx(6 / 18)


def test_pitcher_vs_position_split(synth_db):
    r = build_bunting_rates()
    # Pitchers: 4 + 2 = 6 bunts, all sacrifices, zero hits.
    assert r["pitchers"]["bunt_att"] == 6
    assert r["pitchers"]["bunt_hits"] == 0
    assert r["pitchers"]["sh"] == 6
    assert r["pitchers"]["bunt_hit_rate"] == 0.0
    # Position players: 8 + 4 = 12 bunts, 6 hits.
    assert r["position"]["bunt_att"] == 12
    assert r["position"]["bunt_hits"] == 6
    assert r["position"]["bunt_hit_rate"] == pytest.approx(0.5)


def test_team_breakdown_sorted_by_bunt_rate(synth_db):
    r = build_bunting_rates()
    teams = r["teams"]
    assert len(teams) == 2
    # Team 1 bunts 12/60 = 20%; Team 2 bunts 6/48 = 12.5% -> team 1 first.
    assert teams[0]["team_abbrev"] == "HOM"
    assert teams[0]["bunt_att"] == 12
    assert teams[0]["bunt_pct_pa"] == pytest.approx(12 / 60)
    assert teams[1]["team_abbrev"] == "AWY"


def test_league_scope_filters_teams(synth_db):
    # Scope to team 1 only — team-2 bunts drop out of the league totals.
    r = build_bunting_rates(team_ids=[1])
    assert r["league"]["bunt_att"] == 12     # only team 1's 8 + 4
    assert len(r["teams"]) == 1
    assert r["teams"][0]["team_abbrev"] == "HOM"


def test_empty_db_is_all_zeros(tmp_path):
    db._DB_PATH = str(tmp_path / "empty.db")
    db._DB_PATH_OVERRIDDEN = True
    db.init_db()
    r = build_bunting_rates()
    assert r["league"]["bunt_att"] == 0
    assert r["teams"] == []
    assert r["pitchers"]["bunt_att"] == 0
    assert r["league"]["rv_per_100"] == 0.0


# ---------------------------------------------------------------------------
# RE24 run value.
# ---------------------------------------------------------------------------

def test_re_lookup_bucketing_and_half_end():
    # matrix[bases][outs_bucket] -> {n, re}. Outs bucket = outs // 3.
    matrix = {1: {0: {"n": 5, "re": 0.9}, 2: {"n": 5, "re": 0.4}}}
    assert _re_lookup(matrix, 1, 0) == 0.9      # bucket 0
    assert _re_lookup(matrix, 1, 7) == 0.4      # 7 // 3 = 2
    assert _re_lookup(matrix, 1, 27) == 0.0     # half over -> no future runs
    assert _re_lookup(matrix, None, 3) is None  # unusable
    # Sparse cell (bucket 1 missing) falls back to nearest populated bucket.
    assert _re_lookup(matrix, 1, 3) in (0.9, 0.4)


def _synth_matrix() -> dict:
    """re[bases][bucket] = 0.1 * bases, flat across outs buckets."""
    return {b: {ob: {"n": 1, "re": round(0.1 * b, 3)} for ob in range(9)}
            for b in range(8)}


def test_bunt_run_value(synth_db):
    ex = db.execute

    def bunt(team_id, *, runs, ob, bb, oa, ba, phase=0, is_playoff=0):
        ex("""INSERT INTO game_bunt_log(game_id,team_id,batter_id,phase,
              is_playoff,bunt_type,outcome,runs_scored,
              outs_before,bases_before,outs_after,bases_after)
              VALUES (1,?,10,?,?,'sac','sacrifice',?,?,?,?,?)""",
           (team_id, phase, is_playoff, runs, ob, bb, oa, ba))

    # rv = 0.1*bases_after - 0.1*bases_before + runs.
    bunt(1, runs=1, ob=3, bb=4, oa=4, ba=0)   # 0 - 0.4 + 1 = 0.6
    bunt(1, runs=0, ob=6, bb=1, oa=7, ba=2)   # 0.2 - 0.1 + 0 = 0.1
    # Noise: a playoff bunt and a non-regulation phase must be excluded.
    bunt(1, runs=9, ob=0, bb=0, oa=1, ba=0, is_playoff=1)
    bunt(1, runs=9, ob=0, bb=0, oa=1, ba=0, phase=1)

    rv = build_bunt_run_value(re_table={"matrix": _synth_matrix()})
    assert rv["league"]["n"] == 2
    assert rv["league"]["rv_total"] == pytest.approx(0.7)
    assert rv["league"]["rv_per_100"] == pytest.approx(35.0)
    assert rv["teams"][1]["n"] == 2
    assert rv["teams"][1]["rv_per_100"] == pytest.approx(35.0)


def test_rates_carry_run_value(synth_db):
    db.execute(
        """INSERT INTO game_bunt_log(game_id,team_id,batter_id,phase,is_playoff,
           bunt_type,outcome,runs_scored,outs_before,bases_before,
           outs_after,bases_after)
           VALUES (1,1,10,0,0,'sac','sacrifice',1,3,4,4,0)""")
    r = build_bunting_rates(re_table={"matrix": _synth_matrix()})
    assert r["league"]["rv_n"] == 1
    assert r["league"]["rv_per_100"] == pytest.approx(60.0)  # 0.6 * 100
    # Team 1 row carries its own RV.
    hom = next(t for t in r["teams"] if t["team_abbrev"] == "HOM")
    assert hom["rv_n"] == 1
    assert hom["rv_per_100"] == pytest.approx(60.0)
