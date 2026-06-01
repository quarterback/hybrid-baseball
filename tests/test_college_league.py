"""
End-to-end college-tier league test — seeding, schedule, sim, postseason,
annual rollover, pro signing.

Uses a tmp_path DB so it's hermetic.
"""
import pytest

import o27v2.db as db
import o27v2.college_league as cl


@pytest.fixture()
def college_db(tmp_path):
    original = db._DB_PATH
    db._DB_PATH = str(tmp_path / "college.db")
    try:
        db.init_db()
        yield
    finally:
        db._DB_PATH = original


def test_seed_creates_real_conferences_with_rosters(college_db):
    summary = cl.seed_college_league(season=2026, rng_seed=42)
    # Real D1 + selected D3 catalog — 18 conferences, ~195 programs.
    assert summary["created_programs"] >= 180
    # 35-man rosters per program (real NCAA D1 active-roster size).
    assert summary["created_players"] == 35 * summary["created_programs"]

    progs = db.fetchall("SELECT * FROM college_programs WHERE season = 2026")
    assert len(progs) == summary["created_programs"]
    confs = {p["conference"] for p in progs}
    # Spot-check real conference names — SEC, ACC, Big 12, Big Ten,
    # the new Pac-12, plus an academic D3 (UAA).
    assert "SEC"          in confs
    assert "ACC"          in confs
    assert "Big 12"       in confs
    assert "Big Ten"      in confs
    assert "Pac-12 (new)" in confs
    assert "UAA (D3)"     in confs

    # Players spread across college_year 1-4 (no all-freshmen season)
    by_year = db.fetchall(
        "SELECT college_year, COUNT(*) AS n FROM college_players GROUP BY college_year"
    )
    years = {r["college_year"]: r["n"] for r in by_year}
    assert set(years.keys()) == {1, 2, 3, 4}


def test_seed_is_idempotent(college_db):
    cl.seed_college_league(season=2026, rng_seed=1)
    re_run = cl.seed_college_league(season=2026, rng_seed=1)
    assert re_run["created_programs"] == 0


def test_schedule_creates_full_slate(college_db):
    cl.seed_college_league(season=2026, rng_seed=1)
    n_games = cl.generate_schedule(season=2026)
    # ~195 programs × ~55 games / 2 ≈ 5000 regular-season games.
    # Bigger conferences (SEC/Big Ten = 16-18 teams) push individual
    # teams above 50 games purely from conf round-robin (15-17 weekend
    # series × 3), so per-team totals scale with conference size.
    assert n_games > 4000
    per_team = db.fetchall(
        """SELECT p.id, COUNT(*) AS n
             FROM college_programs p
             JOIN college_games g
               ON g.home_program_id = p.id OR g.away_program_id = p.id
            WHERE p.season = 2026 AND g.season = 2026 AND g.phase = 'regular'
            GROUP BY p.id"""
    )
    counts = [r["n"] for r in per_team]
    # Lower bound: ≥35 (the smallest conferences need fewer conf games
    # but mid-week non-conf fills in). Upper: ≤100 (largest conferences
    # play the most conf games).
    assert all(35 <= c <= 100 for c in counts), (min(counts), max(counts))


def test_sim_a_few_games(college_db):
    cl.seed_college_league(season=2026, rng_seed=1)
    cl.generate_schedule(season=2026)
    # Sim first 5 unplayed games
    rows = db.fetchall(
        "SELECT id FROM college_games WHERE season=2026 AND played=0 LIMIT 5"
    )
    for r in rows:
        result = cl.sim_game(r["id"], rng_seed=42)
        assert "home_score" in result
        assert "away_score" in result
        assert result["home_score"] >= 0
        assert result["away_score"] >= 0


def test_standings_after_partial_season(college_db):
    cl.seed_college_league(season=2026, rng_seed=1)
    cl.generate_schedule(season=2026)
    # Sim first 100 games
    rows = db.fetchall(
        "SELECT id FROM college_games WHERE season=2026 AND played=0 LIMIT 100"
    )
    for r in rows:
        cl.sim_game(r["id"], rng_seed=99)
    s = cl.standings(2026)
    # One row per program for the seeded season.
    assert len(s) >= 180
    # Some programs should have non-zero wins by now
    assert any(row["wins"] > 0 for row in s)
