"""Separate SQLite database for CapSpace's OWN tables.

Why: the sim holds the main DB's single WAL writer for a whole "sim a day" run.
CapSpace used to read *and write* (settle, wallet credits, picks, bets) that same
DB, so using the fantasy app during a sim threw ``database is locked``. CapSpace
state is independent of the sim, so it now lives in its own file
(``<sim-db>-capspace.db``). The fantasy code opens that file as ``main`` and
ATTACHes the sim DB as ``sim`` (read-only use), so:

* fantasy **writes** hit the fantasy file — never the sim's write lock;
* fantasy **reads** of games/players/stats still work with the same unqualified
  queries (the fantasy and sim table names are disjoint, so SQLite resolves each
  name to the right file), and reading an attached WAL DB never blocks the sim's
  writer.

Drop-in: the fantasy modules ``import`` this as ``db`` (``from . import fdb as
db``), so every existing ``db.get_conn()/fetchall/fetchone/execute`` call routes
here unchanged.
"""

from __future__ import annotations

import os
import sqlite3

from o27v2 import db as _maindb

# CapSpace-owned tables — everything that is play-money / fantasy state and has
# no business on the sim's write lock. Used by the one-time migration below.
_FANTASY_TABLES = (
    "cap_wallet", "cap_records", "cap_profile",
    "dfs_contests", "dfs_entries",          # dfs_entries FK → dfs_contests: keep this order
    "cs_buyins", "slugger_picks", "pilot_picks",
    "cat_rosters", "bb_roster", "streak_picks",
    "sb_bets", "sb_lines",
)

# Migration runs once per fantasy-DB path per process.
_migrated: set[str] = set()


def _fantasy_path() -> str:
    """The CapSpace DB file: sibling of the active sim DB, ``-capspace`` suffix.
    Tracks the active save (derived from the sim path) so each universe gets its
    own fantasy file."""
    main = _maindb._resolve_path()
    base, ext = os.path.splitext(main)
    return f"{base}-capspace{ext or '.db'}"


def _ensure_migrated(conn: sqlite3.Connection, fan_path: str) -> None:
    """One-time lift of pre-split CapSpace tables from the sim DB into the
    fantasy DB. Best-effort and idempotent: only copies a table that exists in
    the sim DB and is absent/empty in the fantasy DB. The sim-side copies are
    left in place (orphaned, read by nobody) rather than dropped, so the
    migration never writes to the sim DB."""
    if fan_path in _migrated:
        return
    _migrated.add(fan_path)  # set first so a re-entrant get_conn() doesn't recurse
    try:
        for t in _FANTASY_TABLES:
            sim_tbl = conn.execute(
                "SELECT sql FROM sim.sqlite_master WHERE type='table' AND name=?", (t,)
            ).fetchone()
            if not sim_tbl:
                continue  # not present in the sim DB — nothing to lift
            fan_tbl = conn.execute(
                "SELECT name FROM main.sqlite_master WHERE type='table' AND name=?", (t,)
            ).fetchone()
            if fan_tbl is None:
                conn.execute(sim_tbl[0])  # recreate in the fantasy DB from the sim DDL
            else:
                cnt = conn.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()
                if cnt and cnt[0] > 0:
                    continue  # already populated — don't double-copy
            sim_cols = [r[1] for r in conn.execute(f"PRAGMA sim.table_info({t})").fetchall()]
            fan_cols = [r[1] for r in conn.execute(f"PRAGMA main.table_info({t})").fetchall()]
            common = [c for c in sim_cols if c in fan_cols]
            if not common:
                continue
            cols = ",".join(common)
            conn.execute(f"INSERT INTO main.{t} ({cols}) SELECT {cols} FROM sim.{t}")
        conn.commit()
    except Exception:
        # Never let a migration hiccup break the app — ensure_schema still
        # creates the (possibly empty) tables and the user can carry on.
        pass


def get_conn() -> sqlite3.Connection:
    fan_path = _fantasy_path()
    sim_path = _maindb._resolve_path()
    # Same self-closing context-manager semantics as the main db layer: a
    # bare ``with get_conn() as conn:`` commits AND closes, so the fantasy
    # read/write paths (which ATTACH the sim DB, doubling the open handles)
    # don't leak file descriptors and exhaust the open-file limit.
    conn = sqlite3.connect(fan_path, factory=_maindb._ManagedConnection)
    conn.row_factory = sqlite3.Row
    # WAL + NORMAL + busy_timeout, same rationale as the main db layer.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    # Read the sim universe without ever taking its write lock.
    conn.execute("ATTACH DATABASE ? AS sim", (sim_path,))
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_migrated(conn, fan_path)
    return conn


def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def fetchone(sql: str, params: tuple = ()) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: tuple = ()) -> int:
    def _run() -> int:
        with get_conn() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid
    return _maindb._retry_on_locked(_run)


def executemany(sql: str, param_list: list[tuple]) -> None:
    def _run() -> None:
        with get_conn() as conn:
            conn.executemany(sql, param_list)
            conn.commit()
    _maindb._retry_on_locked(_run)
