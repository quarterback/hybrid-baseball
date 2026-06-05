"""Sidecar manifest — the audio service's own index of produced clips.

Deliberately a *separate* SQLite file under ``o27audio/out/`` (not the game DB),
so the audio app stays a decoupled instance. The main app can optionally read
this to surface a "Listen" link, but nothing about audio touches the core schema.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audio_clips (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,          -- 'game' | 'roundup'
    ref_id       TEXT NOT NULL,          -- game_id (as text) or show date
    league       TEXT,
    wav_path     TEXT,
    mp3_path     TEXT,
    duration_s   REAL,
    n_turns      INTEGER,
    char_count   INTEGER,
    est_cost_usd REAL,
    model        TEXT,
    status       TEXT NOT NULL,          -- 'generating' | 'ok' | 'error'
    error        TEXT,
    script_json  TEXT,
    created_at   INTEGER NOT NULL,
    UNIQUE(kind, ref_id)
);
"""


def _path() -> str:
    os.makedirs(config.OUT_DIR, exist_ok=True)
    return os.path.join(config.OUT_DIR, "manifest.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_path())
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def record(
    kind: str,
    ref_id: str,
    *,
    league: str | None,
    wav_path: str | None,
    mp3_path: str | None,
    duration_s: float | None,
    n_turns: int,
    char_count: int,
    est_cost_usd: float,
    model: str,
    status: str,
    script: list[dict[str, Any]] | None,
) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO audio_clips (kind, ref_id, league, wav_path, mp3_path, "
            "duration_s, n_turns, char_count, est_cost_usd, model, status, "
            "script_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(kind, ref_id) DO UPDATE SET "
            "league=excluded.league, wav_path=excluded.wav_path, "
            "mp3_path=excluded.mp3_path, duration_s=excluded.duration_s, "
            "n_turns=excluded.n_turns, char_count=excluded.char_count, "
            "est_cost_usd=excluded.est_cost_usd, model=excluded.model, "
            "status=excluded.status, script_json=excluded.script_json, "
            "created_at=excluded.created_at",
            (kind, ref_id, league, wav_path, mp3_path, duration_s, n_turns,
             char_count, est_cost_usd, model, status,
             json.dumps(script) if script is not None else None, int(time.time())),
        )


def begin(kind: str, ref_id: str, *, league: str | None, model: str) -> None:
    """Mark a clip as in-progress so the UI can poll for it. Clears any prior
    error/paths for this ref so a retry starts clean."""
    with _conn() as c:
        c.execute(
            "INSERT INTO audio_clips (kind, ref_id, league, model, status, "
            "n_turns, char_count, est_cost_usd, created_at) "
            "VALUES (?,?,?,?, 'generating', 0, 0, 0, ?) "
            "ON CONFLICT(kind, ref_id) DO UPDATE SET "
            "status='generating', error=NULL, wav_path=NULL, mp3_path=NULL, "
            "league=excluded.league, model=excluded.model, "
            "created_at=excluded.created_at",
            (kind, ref_id, league, model, int(time.time())),
        )


def fail(kind: str, ref_id: str, message: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE audio_clips SET status='error', error=? "
            "WHERE kind=? AND ref_id=?",
            (message[:500], kind, ref_id),
        )


def get(kind: str, ref_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM audio_clips WHERE kind = ? AND ref_id = ?",
            (kind, ref_id),
        ).fetchone()
    return dict(row) if row else None
