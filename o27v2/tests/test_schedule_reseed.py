"""Re-seeding the schedule on a populated save.

A schedule refresh (different seed) wipes played results. Every table that
FK-references games() must be cleared BEFORE games itself, or FK enforcement
(get_conn turns it ON) rejects the parent delete. Regression for a
'FOREIGN KEY constraint failed' crash that hit initdb/seed_schedule once a save
had played games — the old wipe list was hardcoded and missed half the child
tables.
"""
from __future__ import annotations
import os
import importlib
import tempfile
import shutil

import pytest


@pytest.fixture()
def league(monkeypatch):
    """Isolated tiny league ('tu': 8 teams, 4 g/team) with a real schema and
    schedule, using the saves registry for path resolution."""
    tmp = tempfile.mkdtemp()
    monkeypatch.delenv("O27V2_DB_PATH", raising=False)
    monkeypatch.setenv("O27V2_SAVES_DIR", os.path.join(tmp, "saves"))
    import o27v2.db as db
    import o27v2.saves as saves
    import o27v2.league as league_mod
    import o27v2.schedule as schedule
    importlib.reload(db)
    importlib.reload(saves)
    saves.new_save("Test", "tu", 0)
    db.init_db()
    league_mod.seed_league(config_id="tu")
    schedule.seed_schedule(config_id="tu", rng_seed=1)
    try:
        yield db, schedule
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        importlib.reload(db)
        importlib.reload(saves)


def test_child_tables_of_games_is_complete(league):
    db, _ = league
    kids = set(db.child_tables_of("games"))
    assert {
        "game_batter_stats", "game_pitcher_stats", "game_pbp",
        "game_bunt_log", "game_pa_log", "game_scoring_events",
        "game_power_play_stats", "team_phase_outs",
    } <= kids


def test_reseed_with_played_children_does_not_trip_fk(league):
    db, schedule = league
    gid = db.fetchone("SELECT id FROM games ORDER BY id LIMIT 1")["id"]
    # Simulate played data: child rows that FK-reference the game.
    db.execute("INSERT INTO game_batter_stats (game_id, team_id, player_id, pa, ab) "
               "VALUES (?, 1, 1, 4, 3)", (gid,))
    db.execute("INSERT INTO game_bunt_log (game_id, team_id, batter_id, outcome) "
               "VALUES (?, 1, 1, 'sacrifice')", (gid,))
    assert db.fetchone("SELECT COUNT(*) AS n FROM game_batter_stats")["n"] == 1

    # A different seed forces the wipe branch. Before the fix this raised
    # sqlite3.IntegrityError: FOREIGN KEY constraint failed.
    n = schedule.seed_schedule(config_id="tu", rng_seed=2)

    assert n > 0                                              # rescheduled
    assert db.fetchone("SELECT COUNT(*) AS n FROM games")["n"] > 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM game_batter_stats")["n"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM game_bunt_log")["n"] == 0
