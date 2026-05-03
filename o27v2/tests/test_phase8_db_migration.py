"""
Regression test: init_db() + seed_league() must produce Phase-8-correct
rosters even when the database was created by a pre-Phase-8 schema
(no archetype / pitcher_role / hard_contact_delta / hr_weight_bonus columns).
"""
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture()
def stale_db(tmp_path):
    """Return path to a pre-Phase-8 SQLite database."""
    db_path = str(tmp_path / "stale.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE teams (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT NOT NULL, abbrev TEXT NOT NULL, city TEXT NOT NULL,
            division TEXT NOT NULL, league TEXT NOT NULL,
            wins     INTEGER DEFAULT 0, losses INTEGER DEFAULT 0
        );
        CREATE TABLE players (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id       INTEGER NOT NULL,
            name          TEXT NOT NULL, position TEXT NOT NULL,
            is_pitcher    INTEGER DEFAULT 0, is_joker INTEGER DEFAULT 0,
            skill         REAL DEFAULT 0.5, speed REAL DEFAULT 0.5,
            pitcher_skill REAL DEFAULT 0.5,
            stay_aggressiveness        REAL DEFAULT 0.4,
            contact_quality_threshold  REAL DEFAULT 0.45
        );
        INSERT INTO teams (name,abbrev,city,division,league)
               VALUES ('OldTeam','OLD','City','Div','NL');
        INSERT INTO players (team_id,name,position,is_pitcher,is_joker)
               VALUES (1,'Cf','CF',0,0),(1,'Pitcher','P',1,0),(1,'Joker','JKR',0,1);
    """)
    conn.commit()
    conn.close()
    return db_path


def test_init_db_wipes_stale_and_reseeds(stale_db):
    """init_db() detects stale data, wipes it, and seed_league() repopulates."""
    import o27v2.db as db
    import o27v2.league as league

    original_path = db._DB_PATH
    try:
        db._DB_PATH = stale_db

        db.init_db()

        # Tables should exist but be empty after wipe
        players_after_init = db.fetchone("SELECT COUNT(*) AS n FROM players")
        assert players_after_init["n"] == 0, "init_db() should wipe stale players"

        league.seed_league(rng_seed=7)

        # Pitchers must have workhorse/committee role
        pitchers = db.fetchall("SELECT pitcher_role FROM players WHERE is_pitcher = 1")
        assert len(pitchers) > 0, "No pitchers seeded"
        assert all(p["pitcher_role"] == "workhorse" for p in pitchers), (
            "All P-position players must be 'workhorse'; got "
            + str([p["pitcher_role"] for p in pitchers])
        )

        # Jokers must have archetype and modifiers
        jokers = db.fetchall(
            "SELECT archetype, hard_contact_delta, hr_weight_bonus FROM players WHERE is_joker = 1"
        )
        assert len(jokers) > 0, "No jokers seeded"
        archetypes_seen = {j["archetype"] for j in jokers}
        assert archetypes_seen == {"power", "speed", "contact"}, (
            f"Expected all three archetypes; got {archetypes_seen}"
        )
        power_jokers = [j for j in jokers if j["archetype"] == "power"]
        assert all(j["hard_contact_delta"] > 0 for j in power_jokers), (
            "Power jokers should have positive hard_contact_delta"
        )

    finally:
        db._DB_PATH = original_path


def test_init_db_idempotent_on_phase8_db():
    """init_db() on an already-migrated Phase-8 DB must not wipe data."""
    import o27v2.db as db
    import o27v2.league as league

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "phase8.db")
        original_path = db._DB_PATH
        try:
            db._DB_PATH = db_path

            db.init_db()
            league.seed_league(rng_seed=13)

            n_before = db.fetchone("SELECT COUNT(*) AS n FROM players")["n"]
            assert n_before > 0

            # Second call must not wipe
            db.init_db()

            n_after = db.fetchone("SELECT COUNT(*) AS n FROM players")["n"]
            assert n_after == n_before, (
                f"init_db() must be idempotent; had {n_before} players, now {n_after}"
            )
        finally:
            db._DB_PATH = original_path
