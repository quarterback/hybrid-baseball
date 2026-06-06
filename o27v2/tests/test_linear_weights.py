"""
Tests for the linear weights derivation (o27v2/analytics/linear_weights.py).

Covers the stay-credit contamination fix: a STAY event is no longer
bucketed into 1B, so the empirical RV of a true single sits above the
walk RV (where it belongs).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest


@pytest.fixture()
def fresh_db(tmp_path):
    from o27v2 import db as _db
    path = str(tmp_path / "lw.db")
    original = _db._DB_PATH
    _db._DB_PATH = path
    _db.init_db()
    try:
        yield path
    finally:
        _db._DB_PATH = original


def test_classify_bip_buckets_2c_by_hit_type():
    from o27v2.analytics.linear_weights import _classify_bip
    # Since the 2C-through-the-hitting-engine rework, a credited 2C is a REAL
    # hit of its resolved type — bucketed by hit_type, NOT a separate STAY bucket.
    assert _classify_bip("single", 1, 1) == "1B"
    assert _classify_bip("double", 1, 1) == "2B"
    assert _classify_bip("triple", 1, 1) == "3B"
    # A valid stay that credited no hit (no runner moved) is an out-ish non-event.
    assert _classify_bip(None,     1, 0) == "out"
    # Real (non-stay) hits bucket by type as before.
    assert _classify_bip("single",          0, 0) == "1B"
    assert _classify_bip("infield_single",  0, 0) == "1B"
    assert _classify_bip("double",          0, 0) == "2B"
    assert _classify_bip("triple",          0, 0) == "3B"
    assert _classify_bip("home_run",        0, 0) == "HR"
    assert _classify_bip("hr",              0, 0) == "HR"
    assert _classify_bip("ground_out",      0, 0) == "out"


def test_no_stay_bucket_after_2c_rework(fresh_db):
    """The STAY bucket is gone — 2C hits are bucketed by their real type. On a
    fresh DB every real event RV still defaults to 0 and is safe to dereference."""
    from o27v2.analytics.linear_weights import derive_linear_weights
    r = derive_linear_weights()
    for k in ("1B", "2B", "3B", "HR"):
        assert k in r["rv"], f"missing rv[{k}] on empty DB"
        assert r["rv"][k] == 0.0
    for k in ("1B", "2B", "3B", "HR", "BB", "HBP"):
        assert k in r["woba_weights"]
    assert "STAY" not in r["woba_weights"]


def _seed_minimal_re_environment(db_path: str) -> None:
    """Insert two teams, one player, one game so the RE walk has a half
    to chew on."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO teams (name, abbrev, city, division, league) VALUES "
                 "('A','AAA','City','Div','L'), ('B','BBB','City','Div','L')")
    conn.execute(
        "INSERT INTO players (team_id, name, position, is_pitcher) "
        "VALUES (1, 'Test Hitter', 'CF', 0)"
    )
    conn.execute(
        "INSERT INTO games (game_date, home_team_id, away_team_id, played, super_inning) "
        "VALUES ('2024-04-01', 1, 2, 1, 0)"
    )
    conn.commit()
    conn.close()


def _seed_pa_events(db_path: str, events: list[dict]) -> None:
    """Insert pre-built PA-log rows + matching game_batter_stats so the
    OBP-scale factor has hit/AB totals to work with. `events` is a list
    of dicts shaped {hit_type, was_stay, stay_credited, bases_before,
    bases_after, outs_before, outs_after, runs_scored}."""
    conn = sqlite3.connect(db_path)
    for i, e in enumerate(events):
        choice = "stay" if e["was_stay"] else "run"
        conn.execute(
            """
            INSERT INTO game_pa_log
              (game_id, team_id, batter_id, phase, ab_seq, swing_idx, choice,
               outs_before, bases_before, score_diff_before,
               outs_after,  bases_after,  score_diff_after,
               runs_scored, hit_type, was_stay, stay_credited, quality)
            VALUES (1, 1, 1, 0, ?, 0, ?, ?, ?, 0, ?, ?, 0, ?, ?, ?, ?, 'medium')
            """,
            (i, choice,
             e["outs_before"], e["bases_before"],
             e["outs_after"], e["bases_after"],
             e["runs_scored"], e.get("hit_type"),
             e["was_stay"], e["stay_credited"]),
        )
    # Aggregate game_batter_stats so OBP / wOBA-scale denominators work.
    n = len(events)
    n_singles = sum(1 for e in events
                    if not e["was_stay"] and e.get("hit_type") in ("single", "infield_single"))
    n_doubles = sum(1 for e in events if not e["was_stay"] and e.get("hit_type") == "double")
    n_triples = sum(1 for e in events if not e["was_stay"] and e.get("hit_type") == "triple")
    n_hr      = sum(1 for e in events if not e["was_stay"] and e.get("hit_type") in ("hr", "home_run"))
    n_stay_h  = sum(1 for e in events if e["was_stay"] and e["stay_credited"])
    n_hits    = n_singles + n_doubles + n_triples + n_hr + n_stay_h
    # 1 PA per event for simplicity; BB/HBP set to 0 (separate from BIP).
    conn.execute(
        """
        INSERT INTO game_batter_stats
          (game_id, team_id, player_id, phase, pa, ab, hits, doubles, triples, hr,
           bb, hbp, stays, stay_hits)
        VALUES (1, 1, 1, 0, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
        """,
        (n, n, n_hits, n_doubles, n_triples, n_hr,
         sum(1 for e in events if e["was_stay"]), n_stay_h),
    )
    conn.commit()
    conn.close()


def _make_run_scoring_half() -> list[dict]:
    """A simple half: build a runner on 3rd, then alternate run-scoring
    singles vs no-op stay events. Outs accumulate at the end to terminate
    the half. Crafted so true singles average a positive RV and STAYs
    average ~0 (they don't advance or score)."""
    events = []
    # 6 sub-halves: ___ -> 1__ (single, no run), 1__ -> _2_ (single advances
    # to 2nd, runner from 1B scores would need 1_3 but engine drives this).
    # Simplify: each sub-half is single (___, 0 outs) -> __3 0 runs;
    # then single (__3, 0 outs) -> ___ scoring 1 run; then stay (___, 0 outs)
    # -> ___ scoring 0 runs.
    for _ in range(6):
        # Single: ___ -> __3, no out, no run (batter on 3rd)
        events.append({
            "hit_type": "single", "was_stay": 0, "stay_credited": 0,
            "bases_before": 0, "bases_after": 4,
            "outs_before": 0, "outs_after": 0,
            "runs_scored": 0,
        })
        # Single: __3 -> ___, scoring the runner from 3rd (bases empty after)
        events.append({
            "hit_type": "single", "was_stay": 0, "stay_credited": 0,
            "bases_before": 4, "bases_after": 0,
            "outs_before": 0, "outs_after": 0,
            "runs_scored": 1,
        })
        # Stay-credit: ___ -> ___, no advance, no run
        events.append({
            "hit_type": None, "was_stay": 1, "stay_credited": 1,
            "bases_before": 0, "bases_after": 0,
            "outs_before": 0, "outs_after": 0,
            "runs_scored": 0,
        })
    # Burn outs to terminate the half cleanly (3 outs).
    for o in range(3):
        events.append({
            "hit_type": "ground_out", "was_stay": 0, "stay_credited": 0,
            "bases_before": 0, "bases_after": 0,
            "outs_before": o, "outs_after": o + 1,
            "runs_scored": 0,
        })
    return events


def test_no_stay_bucket_in_rv_or_weights(fresh_db):
    """Since the 2C-through-the-hitting-engine rework there is NO separate STAY
    bucket: a credited 2C is a real hit of its type and lands in 1B/2B/3B. The
    rv map and woba_weights must not carry a STAY key, and the standard hit
    ordering (RV(1B) ≥ RV(BB)) must still hold."""
    from o27v2.analytics.linear_weights import derive_linear_weights
    _seed_minimal_re_environment(fresh_db)
    _seed_pa_events(fresh_db, _make_run_scoring_half())

    r = derive_linear_weights()
    assert "STAY" not in r["woba_weights"]
    assert "STAY" not in r["rv"]
    assert r["rv"]["1B"] >= r["rv"]["BB"], (
        f"RV(1B) {r['rv']['1B']:.3f} should be ≥ RV(BB) {r['rv']['BB']:.3f}"
    )
