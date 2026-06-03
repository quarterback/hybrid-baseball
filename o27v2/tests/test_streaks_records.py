"""Streaks & Records analytics — consecutive-game streaks, single-game
records, and cross-season (career / best-season) records.

These exercise o27v2/analytics/streaks.py (the box-line streak engine, NOT
the o27v2/streaks.py hot/cold overlay) and o27v2/analytics/records.py against
a synthetic DB. No flask needed — they build their own temp DB.

Run:  python -m pytest o27v2/tests/test_streaks_records.py -v
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from o27v2 import db


@pytest.fixture()
def synth_db():
    """Fresh temp DB seeded with a deterministic mini-universe."""
    path = tempfile.mktemp(suffix=".db")
    db._DB_PATH = path
    db._DB_PATH_OVERRIDDEN = True
    db.init_db()

    ex = db.execute
    ex("INSERT INTO teams(id,name,abbrev,city,division,league) "
       "VALUES (1,'Home Nine','HOM','Hometown','E','AL')")
    ex("INSERT INTO teams(id,name,abbrev,city,division,league) "
       "VALUES (2,'Away Crew','AWY','Awaycity','E','AL')")
    for pid, name, pos, tid in [(10, 'Slugger Sam', 'OF', 1),
                                (11, 'Steady Eddie', '1B', 1),
                                (20, 'Ace Alvarez', 'P', 2),
                                (21, 'Reliever Rex', 'P', 2)]:
        ex("INSERT INTO players(id,name,position,team_id) VALUES (?,?,?,?)",
           (pid, name, pos, tid))
    for i in range(1, 7):
        ex("INSERT INTO games(id,game_date,home_team_id,away_team_id,played,"
           "home_score,away_score) VALUES (?,?,1,2,1,5,3)",
           (i, f"2026-04-0{i}"))

    def bat(gid, pid, *, pa, ab, h, d2, d3, hr, rbi, r, bb, k, sb=0, hbp=0):
        ex("""INSERT INTO game_batter_stats(game_id,team_id,player_id,phase,pa,
              ab,hits,doubles,triples,hr,rbi,runs,bb,k,sb,hbp)
              VALUES (?,1,?,0,?,?,?,?,?,?,?,?,?,?,?,?)""",
           (gid, pid, pa, ab, h, d2, d3, hr, rbi, r, bb, k, sb, hbp))

    # Sam: HR in games 1-3 (streak 3), none g4, HR g5-6 (streak 2). A hit
    # every game (hitting streak 6). Game 6 is a 3-HR / 7-RBI monster.
    sam_hr = [1, 1, 1, 0, 1, 1]
    for i in range(6):
        gid = i + 1
        if gid == 6:
            bat(gid, 10, pa=4, ab=4, h=4, d2=0, d3=0, hr=3, rbi=7, r=3, bb=0, k=0)
        else:
            bat(gid, 10, pa=4, ab=4, h=2, d2=1, d3=0, hr=sam_hr[i],
                rbi=2 if sam_hr[i] else 1, r=1, bb=0, k=1)
    # Eddie: walks every game (on-base streak 6); hitless g3 (hitting streak 3).
    for i in range(6):
        gid = i + 1
        bat(gid, 11, pa=4, ab=3, h=0 if i == 2 else 1, d2=0, d3=0, hr=0,
            rbi=0, r=0, bb=1, k=0)

    def pit(gid, pid, *, outs, k, h, bb, er, runs, starter, wb_runs=0):
        ex("""INSERT INTO game_pitcher_stats(game_id,team_id,player_id,phase,
              outs_recorded,k,hits_allowed,bb,er,runs_allowed,is_starter,
              batters_faced,wb_runs) VALUES (?,2,?,0,?,?,?,?,?,?,?,?,?)""",
           (gid, pid, outs, k, h, bb, er, runs, starter, outs + h + bb, wb_runs))

    # Ace: starts g1-5, K = 11,12,10,8,13 -> double-digit streak g1-3 (3).
    # Runs: scoreless g1,g2 (14 IP), run g3, scoreless g4,g5. He allows 3
    # walk-back runs total (team 2 pitcher) -> team 1 SCORED those 3.
    ace_k = [11, 12, 10, 8, 13]
    ace_runs = [0, 0, 2, 0, 0]
    ace_wb = [0, 0, 2, 1, 0]
    for i in range(5):
        pit(i + 1, 20, outs=21, k=ace_k[i], h=4, bb=1,
            er=ace_runs[i], runs=ace_runs[i], starter=1, wb_runs=ace_wb[i])
    # Rex: relief, scoreless g1-3 (3 IP), run g4.
    for i, runs in enumerate([0, 0, 0, 1]):
        pit(i + 1, 21, outs=3, k=2, h=0, bb=0, er=runs, runs=runs, starter=0)

    # A prior archived season (season 1) so career totals span 2 seasons.
    ex("INSERT INTO seasons(id,season_number,year) VALUES (1,1,2025)")
    ex("""INSERT INTO player_career_lines(season_id,season_number,year,player_id,
          player_name,team_abbrev,is_pitcher,position,g,pa,ab,h,d2,d3,hr,r,rbi,
          bb,k,sb,avg,obp,slg,ops,wrc_plus)
          VALUES (1,1,2025,10,'Slugger Sam','HOM',0,'OF',150,600,540,180,30,2,
          40,100,120,50,90,5,.333,.39,.6,.99,150)""")
    ex("""INSERT INTO player_career_lines(season_id,season_number,year,player_id,
          player_name,team_abbrev,is_pitcher,position,p_g,w,l,outs,er,p_k,p_bb,
          p_h,wera,whip,wera_plus)
          VALUES (1,1,2025,20,'Ace Alvarez','HOM',1,'P',30,18,6,600,70,250,40,
          160,2.1,1.0,180)""")
    yield
    Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# In-season streaks
# ---------------------------------------------------------------------------

def test_home_run_streak_finds_longest_run(synth_db):
    from o27v2.analytics.streaks import home_run_streaks
    rows = home_run_streaks(team_ids=[1, 2])
    assert rows and rows[0]["player_name"] == "Slugger Sam"
    assert rows[0]["length"] == 3           # g1-g3, not the later 2-game run


def test_hitting_streak_breaks_on_hitless_game(synth_db):
    from o27v2.analytics.streaks import longest_hit_streaks
    by_name = {r["player_name"]: r for r in longest_hit_streaks(team_ids=[1, 2])}
    assert by_name["Slugger Sam"]["length"] == 6
    assert by_name["Steady Eddie"]["length"] == 3   # hitless g3 splits it


def test_on_base_streak_counts_walks(synth_db):
    from o27v2.analytics.streaks import on_base_streaks
    by_name = {r["player_name"]: r for r in on_base_streaks(team_ids=[1, 2])}
    assert by_name["Steady Eddie"]["length"] == 6   # walked every game


def test_double_digit_k_streak_counts_only_starts(synth_db):
    from o27v2.analytics.streaks import double_digit_k_streaks
    rows = double_digit_k_streaks(team_ids=[1, 2])
    assert rows and rows[0]["player_name"] == "Ace Alvarez"
    assert rows[0]["length"] == 3           # 11,12,10 K; the 8-K start breaks


def test_scoreless_streak_accumulates_innings(synth_db):
    from o27v2.analytics.streaks import scoreless_innings_streaks
    rows = scoreless_innings_streaks(team_ids=[1, 2])
    ace = next(r for r in rows if r["player_name"] == "Ace Alvarez")
    assert ace["outs"] == 42                # two clean 7-IP starts = 14 IP
    assert ace["ip_display"] == "14.0"


def test_streaks_respect_league_scope(synth_db):
    from o27v2.analytics.streaks import home_run_streaks
    # No teams in a phantom league -> no streaks.
    assert home_run_streaks(team_ids=[999]) == []


# ---------------------------------------------------------------------------
# Single-game records
# ---------------------------------------------------------------------------

def test_single_game_batter_records(synth_db):
    from o27v2.analytics.records import single_game_batter_records
    rec = single_game_batter_records(team_ids=[1, 2])
    assert rec["hr"][0]["player_name"] == "Slugger Sam"
    assert rec["hr"][0]["hr"] == 3 and rec["hr"][0]["rbi"] == 7
    # TB on the monster game: 4 hits incl 3 HR -> 1 single(1) + 3 HR(12) = 13.
    assert rec["tb"][0]["tb"] == 13


def test_single_game_pitcher_records(synth_db):
    from o27v2.analytics.records import single_game_pitcher_records
    rec = single_game_pitcher_records(team_ids=[1, 2])
    assert rec["k"][0]["player_name"] == "Ace Alvarez"
    assert rec["k"][0]["k"] == 13


# ---------------------------------------------------------------------------
# Cross-season records (archived season + live season folded in)
# ---------------------------------------------------------------------------

def test_career_batting_sums_across_seasons(synth_db):
    from o27v2.analytics.records import career_batting_records
    hr = career_batting_records()["hr"]
    sam = next(r for r in hr if r["player_name"] == "Slugger Sam")
    assert sam["seasons"] == 2
    assert sam["hr"] == 47                   # 40 archived + 7 live


def test_career_pitching_sums_strikeouts(synth_db):
    from o27v2.analytics.records import career_pitching_records
    k = career_pitching_records()["p_k"]
    ace = next(r for r in k if r["player_name"] == "Ace Alvarez")
    assert ace["p_k"] == 304                 # 250 archived + 54 live


def test_single_season_best_flags_live_season(synth_db):
    from o27v2.analytics.records import single_season_batting_records
    hr = single_season_batting_records()["hr"]
    # Archived 40-HR season outranks the live 7-HR one.
    assert hr[0]["hr"] == 40 and not hr[0].get("is_current")
    live = next(r for r in hr if r.get("is_current"))
    assert live["hr"] == 7


def test_team_walkback_runs_attributes_to_opponent(synth_db):
    from o27v2.analytics.records import team_walkback_runs
    rows = {r["team_abbrev"]: r for r in team_walkback_runs(team_ids=[1, 2])}
    # Team 2's pitcher (Ace) allowed 3 walk-back runs -> team 1 (HOM) scored
    # them; team 2 (AWY) is charged with allowing them.
    assert rows["HOM"]["scored"] == 3 and rows["HOM"]["allowed"] == 0
    assert rows["AWY"]["allowed"] == 3 and rows["AWY"]["scored"] == 0
