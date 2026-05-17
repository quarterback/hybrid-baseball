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


def test_classify_bip_separates_stay_from_single():
    from o27v2.analytics.linear_weights import _classify_bip
    # Stay-credited is now its own bucket — NOT '1B'.
    assert _classify_bip(None,     1, 1) == "STAY"
    assert _classify_bip("single", 1, 1) == "STAY"   # was_stay wins
    # Stay without credit remains an out (no advance).
    assert _classify_bip(None,     1, 0) == "out"
    # Real singles still bucket to 1B.
    assert _classify_bip("single",          0, 0) == "1B"
    assert _classify_bip("infield_single",  0, 0) == "1B"
    assert _classify_bip("double",          0, 0) == "2B"
    assert _classify_bip("triple",          0, 0) == "3B"
    assert _classify_bip("home_run",        0, 0) == "HR"
    assert _classify_bip("hr",              0, 0) == "HR"
    assert _classify_bip("ground_out",      0, 0) == "out"


def test_empty_db_returns_zero_stay_weight(fresh_db):
    """On a fresh DB with no plays, every event RV defaults to 0
    (including STAY) — caller is safe to dereference STAY anywhere
    1B / 2B / 3B / HR are dereferenced."""
    from o27v2.analytics.linear_weights import derive_linear_weights
    r = derive_linear_weights()
    for k in ("1B", "2B", "3B", "HR", "STAY"):
        assert k in r["rv"], f"missing rv[{k}] on empty DB"
        assert r["rv"][k] == 0.0
    for k in ("1B", "2B", "3B", "HR", "BB", "HBP", "STAY"):
        assert k in r["woba_weights"]


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


def test_stay_separated_from_1b_in_rv(fresh_db):
    """Hand-craft a mini PA log where true singles advance / score
    runners and STAY events do not. The empirical RV(1B) should land
    strictly above RV(STAY) — exactly the inversion the dashboard
    explanation used to chalk up to 'contamination'."""
    from o27v2.analytics.linear_weights import derive_linear_weights
    _seed_minimal_re_environment(fresh_db)
    _seed_pa_events(fresh_db, _make_run_scoring_half())

    r = derive_linear_weights()
    rv = r["rv"]
    assert rv["1B"] > rv["STAY"], (
        f"RV(1B) {rv['1B']:.3f} should exceed RV(STAY) {rv['STAY']:.3f} — "
        f"stay-credit contamination has crept back in"
    )


def test_woba_weights_include_stay_separately(fresh_db):
    """woba_weights must expose a STAY entry distinct from 1B so
    consumers can credit stay-events with their own (lower) wOBA points
    instead of falsely lumping them into the single bucket."""
    from o27v2.analytics.linear_weights import derive_linear_weights
    _seed_minimal_re_environment(fresh_db)
    _seed_pa_events(fresh_db, _make_run_scoring_half())

    r = derive_linear_weights()
    ww = r["woba_weights"]
    assert "STAY" in ww and "1B" in ww
    assert ww["1B"] > ww["STAY"], (
        f"wOBA[1B] {ww['1B']:.3f} should exceed wOBA[STAY] {ww['STAY']:.3f}"
    )
