"""
Youth league inherits the pro substitution-economy roster shape.

Guards three things that broke (or were missing) when the youth league
predated the substitution economy:

  1. Seeding produces the 48-player / 42-active shape with the drafted
     specialist minimums (1 PR + 2 PH), 3 jokers and 17 pitchers.
  2. The youth game simulator populates ``Team.bench`` so pinch-hit /
     pinch-run / defensive substitutions actually fire (the one-way
     invariant must hold).
  3. The off-season roll (develop → graduate → refill) keeps every team
     pinned at exactly 48/42 instead of drifting upward.
"""
import random

import pytest

import o27v2.db as db
import o27v2.youth as youth
import o27v2.youth_sim as youth_sim


@pytest.fixture()
def youth_db(tmp_path):
    original = db._DB_PATH
    db._DB_PATH = str(tmp_path / "youth.db")
    try:
        db.init_db()
        youth.init_youth_schema()
        youth.seed_youth_league(rng_seed=7, seed_year=1)
        yield
    finally:
        db._DB_PATH = original


def _team_ids():
    return [r["id"] for r in db.fetchall("SELECT id FROM youth_teams ORDER BY id")]


def _shape(team_id):
    def n(where):
        return db.fetchone(
            f"SELECT COUNT(*) c FROM youth_players WHERE youth_team_id = ? AND {where}",
            (team_id,),
        )["c"]
    return {
        "total":    n("1=1"),
        "active":   n("is_active = 1"),
        "pitchers": n("is_active = 1 AND is_pitcher = 1"),
        "jokers":   n("is_active = 1 AND is_joker = 1"),
        "pr":       n("is_active = 1 AND roster_slot = 'pr_specialist'"),
        "ph":       n("is_active = 1 AND roster_slot = 'ph_specialist'"),
        "reserves": n("is_active = 0"),
    }


def _assert_canonical_shape(team_id):
    s = _shape(team_id)
    assert s["total"] == 48, s
    assert s["active"] == 42, s
    assert s["pitchers"] == 17, s
    assert s["jokers"] == 3, s
    assert s["reserves"] == 6, s
    assert s["pr"] >= 1, s          # >= because the classifier may surface
    assert s["ph"] >= 2, s          # organic specialists on top of drafted ones


def test_seed_roster_shape(youth_db):
    ids = _team_ids()
    assert len(ids) == 48
    for tid in ids:
        _assert_canonical_shape(tid)


def test_bench_populated_and_subs_fire(youth_db):
    from o27.engine.state import GameState
    from o27.engine.game import run_game
    from o27.engine.prob import ProbabilisticProvider

    t1, t2 = _team_ids()[:2]
    kinds = set()
    violations = 0
    games = 8
    for s in range(games):
        rng = random.Random(500 + s)
        home, _, _ = youth_sim._build_youth_engine_team(t1, "home", 1, rng)
        away, _, _ = youth_sim._build_youth_engine_team(t2, "visitors", 1, rng)
        # Bench must be non-empty or no positional sub can ever happen.
        assert home.bench, "youth home bench is empty — substitution economy is inert"
        final, _ = run_game(GameState(visitors=away, home=home),
                            ProbabilisticProvider(rng))
        log = getattr(final, "substitution_log", [])
        for x in log:
            kinds.add(x.kind)
        # One-way invariant, order-aware, per team.
        for team_id in {x.team_id for x in log}:
            gone = set()
            for x in [e for e in log if e.team_id == team_id]:
                if x.in_player_id in gone:
                    violations += 1
                gone.add(x.out_player_id)
    assert violations == 0, "one-way substitution invariant violated"
    # Over 8 games at least one positional substitution should have fired.
    assert kinds & {"pinch_hit", "pinch_run", "pinch_field"}, (
        "no positional substitutions fired across the sample — bench unused"
    )


def test_offseason_roll_keeps_shape_pinned(youth_db):
    for yr in range(2, 5):
        youth.advance_youth_year(rng_seed=yr, new_season_year=yr)
        for tid in _team_ids():
            _assert_canonical_shape(tid)
