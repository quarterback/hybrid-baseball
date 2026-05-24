"""
Hall of Fame points + induction tests.

Builds a tiny synthetic universe directly in a temp DB (career lines, leaders,
awards, champions) and asserts the points math, the league gate, the team
criteria path, and manual induction.
"""
import pytest

import o27v2.db as db
from o27v2 import hof


@pytest.fixture()
def fresh_db(tmp_path):
    """A clean, schema-initialised DB pointed at a temp file."""
    orig = db._DB_PATH
    db._DB_PATH = str(tmp_path / "hof.db")
    try:
        db.init_db()
        yield db
    finally:
        db._DB_PATH = orig


def _team(abbrev="BOS", name="Boston", tid=1):
    db.execute(
        "INSERT INTO teams (id, name, abbrev, city, division, league) "
        "VALUES (?, ?, ?, ?, 'East', 'AL')",
        (tid, name, abbrev, name),
    )


def _player(pid, name, age, is_pitcher=0, position="CF", team_id=1):
    db.execute(
        "INSERT INTO players (id, name, position, is_pitcher, age, team_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (pid, name, position, is_pitcher, age, team_id),
    )


def _season(season_id, number, year, champion="BOS"):
    db.execute(
        "INSERT INTO seasons (id, season_number, year, champion_abbrev, games_played) "
        "VALUES (?, ?, ?, ?, 100)",
        (season_id, number, year, champion),
    )


def _bat_line(season_id, number, year, pid, name, team="BOS", *,
              age=30, hr=40, h=180, rbi=120, wrc_plus=150):
    db.execute(
        """INSERT INTO player_career_lines
           (season_id, season_number, year, player_id, player_name, team_abbrev,
            is_pitcher, position, age, g, pa, ab, h, hr, rbi, wrc_plus)
           VALUES (?, ?, ?, ?, ?, ?, 0, 'CF', ?, 150, 600, 550, ?, ?, ?, ?)""",
        (season_id, number, year, pid, name, team, age, h, hr, rbi, wrc_plus),
    )


def _bat_leader(season_id, category, rank, name, team="BOS"):
    db.execute(
        "INSERT INTO season_batting_leaders "
        "(season_id, category, rank, player_name, team_abbrev) "
        "VALUES (?, ?, ?, ?, ?)",
        (season_id, category, rank, name, team),
    )


def _award(season_number, category, pid, name, team="BOS"):
    db.execute(
        "INSERT INTO season_awards (season, category, player_id, player_name, team_abbrev) "
        "VALUES (?, ?, ?, ?, ?)",
        (season_number, category, pid, name, team),
    )


def test_empty_universe_has_no_candidates(fresh_db):
    assert hof.compute_all() == []
    assert hof.league_hof() == []


def test_dominant_batter_clears_league_gate(fresh_db):
    _team()
    _player(1, "Slugger Sam", age=36)
    # Eight elite seasons, leading the league in HR each year, on the champ.
    for i in range(1, 9):
        _season(i, i, 2026 + i, champion="BOS")
        _bat_line(i, i, 2026 + i, 1, "Slugger Sam", wrc_plus=150)
        _bat_leader(i, "hr", 1, "Slugger Sam")   # black ink
        _bat_leader(i, "rbi", 3, "Slugger Sam")  # gray ink
    _award(8, "mvp", 1, "Slugger Sam")

    cards = hof.compute_all()
    assert len(cards) == 1
    c = cards[0]
    assert c["seasons_played"] == 8
    assert c["black_ink"] == 8
    assert c["gray_ink"] == 8
    assert c["rings"] == 8
    assert c["elite_seasons"] == 8          # wrc_plus 150 >= ELITE_PLUS
    assert c["awards"]["mvp"] == 1
    # 8*black(3) + 8*gray(.5) + 8*ring(2) + 8*elite(1) + 8*longevity(.25) + MVP(5)
    expected = 8 * (hof.PTS_BLACK_INK + hof.PTS_GRAY_INK + hof.PTS_RING
                    + hof.PTS_ELITE_SEASON + hof.PTS_PER_SEASON) + hof.PTS_MVP
    assert c["hof_points"] == pytest.approx(round(expected, 2))
    assert c["league_eligible"] is True

    result = hof.run_inductions(8, 2034)
    assert len(result["league"]) == 1
    members = hof.league_hof()
    assert len(members) == 1
    assert members[0]["player_name"] == "Slugger Sam"
    # Idempotent — re-running inducts nobody new.
    assert hof.run_league_inductions(8, 2034) == []


def test_age_gate_blocks_young_phenom(fresh_db):
    _team()
    _player(2, "Kid Phenom", age=27)  # too young
    for i in range(1, 9):
        _season(i, i, 2026 + i)
        _bat_line(i, i, 2026 + i, 2, "Kid Phenom", wrc_plus=150)
        _bat_leader(i, "hr", 1, "Kid Phenom")

    c = hof.compute_all()[0]
    assert c["hof_points"] >= hof.LEAGUE_THRESHOLD   # has the resume
    assert c["league_eligible"] is False             # but not the age/career
    assert hof.run_league_inductions(9, 2035) == []


def test_short_career_blocks_induction(fresh_db):
    _team()
    _player(3, "Flash Inthepan", age=38)
    for i in range(1, 4):  # only 3 seasons
        _season(i, i, 2026 + i)
        _bat_line(i, i, 2026 + i, 3, "Flash Inthepan", wrc_plus=200)
        _bat_leader(i, "hr", 1, "Flash Inthepan")
    c = hof.compute_all()[0]
    assert c["seasons_played"] == 3
    assert c["league_eligible"] is False
    assert hof.run_league_inductions(3, 2029) == []


def test_team_criteria_and_manual_induction(fresh_db):
    _team(abbrev="BOS", name="Boston", tid=1)
    _team(abbrev="NYC", name="New York", tid=2)
    _player(4, "Franchise Frank", age=35)
    # Five strong seasons with BOS — a franchise great, but too short a career
    # (5 < league min seasons) to reach the gated league Hall.
    for i in range(1, 6):
        _season(i, i, 2026 + i, champion="NYC")  # never won a ring
        _bat_line(i, i, 2026 + i, 4, "Franchise Frank", team="BOS",
                  wrc_plus=125, hr=42)
        _bat_leader(i, "hr", 1, "Franchise Frank", team="BOS")  # black ink

    c = hof.compute_all()[0]
    tinfo = c["teams"]["BOS"]
    assert tinfo["seasons"] == 5
    # Clears the team bar; career too short for the league gate.
    assert c["league_eligible"] is False
    assert c["hof_points"] < hof.LEAGUE_THRESHOLD
    assert tinfo["points"] >= hof.TEAM_THRESHOLD

    hof.run_team_inductions(5, 2031)
    bos = hof.team_hof(1)
    assert len(bos) == 1
    assert bos[0]["player_name"] == "Franchise Frank"
    assert bos[0]["method"] == "criteria"
    # Not added to a team he never played for.
    assert hof.team_hof(2) == []

    # Manual induction into NYC succeeds even without meeting criteria.
    assert hof.induct_into_team_manual(2, 4, 5, 2031) is True
    nyc = hof.team_hof(2)
    assert len(nyc) == 1
    assert nyc[0]["method"] == "manual"
    # Duplicate manual induction is rejected.
    assert hof.induct_into_team_manual(2, 4, 5, 2031) is False

    # Removal works.
    hof.remove_from_team(2, 4)
    assert hof.team_hof(2) == []


def test_pitcher_uses_wera_plus_for_excellence(fresh_db):
    _team()
    _player(5, "Ace Adams", age=37, is_pitcher=1, position="P")
    for i in range(1, 9):
        _season(i, i, 2026 + i, champion="BOS")
        db.execute(
            """INSERT INTO player_career_lines
               (season_id, season_number, year, player_id, player_name,
                team_abbrev, is_pitcher, position, age, p_g, w, l, outs,
                er, p_k, wera, wera_plus)
               VALUES (?, ?, ?, ?, 'Ace Adams', 'BOS', 1, 'P', ?, 32, 18, 6,
                       540, 60, 200, 2.40, 160)""",
            (i, i, 2026 + i, 5, 30 + i),
        )
        db.execute(
            "INSERT INTO season_pitching_leaders "
            "(season_id, category, rank, player_name, team_abbrev) "
            "VALUES (?, 'w', 1, 'Ace Adams', 'BOS')",
            (i,),
        )
    _award(8, "cy_young", 5, "Ace Adams")

    c = hof.compute_all()[0]
    assert c["is_pitcher"] is True
    assert c["elite_seasons"] == 8          # wera_plus 160 >= ELITE_PLUS
    assert c["black_ink"] == 8              # led league in wins
    assert c["awards"]["cy_young"] == 1
    assert "W" in c["career_summary"]
    assert c["league_eligible"] is True
    assert len(hof.run_league_inductions(8, 2034)) == 1
