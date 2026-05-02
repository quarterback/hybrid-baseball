"""
SQLite database layer for O27v2.

All persistence lives in o27v2/o27v2.db (relative to workspace root).
Functions return plain dicts / lists so callers never deal with cursors.
"""
from __future__ import annotations
import os
import sqlite3
from typing import Any

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "o27v2.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    abbrev   TEXT NOT NULL,
    city     TEXT NOT NULL,
    division TEXT NOT NULL,
    league   TEXT NOT NULL,
    wins     INTEGER DEFAULT 0,
    losses   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id       INTEGER NOT NULL REFERENCES teams(id),
    name          TEXT NOT NULL,
    position      TEXT NOT NULL,
    is_pitcher    INTEGER DEFAULT 0,
    is_joker      INTEGER DEFAULT 0,
    skill         REAL DEFAULT 0.5,
    speed         REAL DEFAULT 0.5,
    pitcher_skill REAL DEFAULT 0.5,
    stay_aggressiveness REAL DEFAULT 0.4,
    contact_quality_threshold REAL DEFAULT 0.45
);

CREATE TABLE IF NOT EXISTS games (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    season       INTEGER DEFAULT 1,
    game_date    TEXT NOT NULL,
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    home_score   INTEGER,
    away_score   INTEGER,
    winner_id    INTEGER REFERENCES teams(id),
    super_inning INTEGER DEFAULT 0,
    played       INTEGER DEFAULT 0,
    seed         INTEGER
);

CREATE TABLE IF NOT EXISTS game_batter_stats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    INTEGER NOT NULL REFERENCES games(id),
    team_id    INTEGER NOT NULL REFERENCES teams(id),
    player_id  INTEGER NOT NULL REFERENCES players(id),
    pa         INTEGER DEFAULT 0,
    ab         INTEGER DEFAULT 0,
    runs       INTEGER DEFAULT 0,
    hits       INTEGER DEFAULT 0,
    doubles    INTEGER DEFAULT 0,
    triples    INTEGER DEFAULT 0,
    hr         INTEGER DEFAULT 0,
    rbi        INTEGER DEFAULT 0,
    bb         INTEGER DEFAULT 0,
    k          INTEGER DEFAULT 0,
    stays      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS game_pitcher_stats (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id        INTEGER NOT NULL REFERENCES games(id),
    team_id        INTEGER NOT NULL REFERENCES teams(id),
    player_id      INTEGER NOT NULL REFERENCES players(id),
    batters_faced  INTEGER DEFAULT 0,
    outs_recorded  INTEGER DEFAULT 0,
    hits_allowed   INTEGER DEFAULT 0,
    runs_allowed   INTEGER DEFAULT 0,
    bb             INTEGER DEFAULT 0,
    k              INTEGER DEFAULT 0
);
"""


def init_db() -> None:
    """Create tables (idempotent)."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def drop_all() -> None:
    """Drop all tables (for re-seeding)."""
    with get_conn() as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS game_pitcher_stats;
            DROP TABLE IF EXISTS game_batter_stats;
            DROP TABLE IF EXISTS games;
            DROP TABLE IF EXISTS players;
            DROP TABLE IF EXISTS teams;
        """)


def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def fetchone(sql: str, params: tuple = ()) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: tuple = ()) -> int:
    """Execute a DML statement; returns lastrowid."""
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def executemany(sql: str, param_list: list[tuple]) -> None:
    with get_conn() as conn:
        conn.executemany(sql, param_list)
        conn.commit()
