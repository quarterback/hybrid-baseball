"""CapSpace — shared buy-in ledger for the non-DFS games.

A tiny ledger so every game can charge a buy-in into the one wallet and pay
winnings back when it settles. One row per (game, ekey) — ekey is a slate date
for the daily games or a format key for the season leagues. Idempotent: a
slate/season is only ever charged once.
"""

from __future__ import annotations

import datetime as _dt

from . import fdb as db  # CapSpace's own DB (separate file)
from . import wallet
from ._schema_once import once


@once
def ensure_schema() -> None:
    conn = db.get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cs_buyins (
            game       TEXT NOT NULL,
            ekey       TEXT NOT NULL,
            fee        INTEGER NOT NULL,
            settled    INTEGER NOT NULL DEFAULT 0,
            payout     INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            PRIMARY KEY (game, ekey)
        );
        """
    )
    conn.commit()
    conn.close()


def entry(game: str, ekey: str):
    ensure_schema()
    return db.fetchone("SELECT * FROM cs_buyins WHERE game = ? AND ekey = ?", (game, str(ekey)))


def enter(game: str, ekey: str, fee: int) -> dict:
    """Charge the buy-in once for this (game, ekey). Idempotent; rejects when
    the wallet can't cover it."""
    ensure_schema()
    if entry(game, ekey):
        return {"ok": True}
    fee = int(fee)
    if fee > 0 and not wallet.debit(fee):
        return {"ok": False, "error": "Not enough in your wallet for the buy-in."}
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO cs_buyins (game, ekey, fee, created_at) VALUES (?,?,?,?)",
        (game, str(ekey), fee, _dt.datetime.utcnow().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()
    return {"ok": True}


def settle_one(game: str, ekey: str, payout: int) -> None:
    """Mark an entry settled and credit its payout (once)."""
    e = entry(game, ekey)
    if not e or e["settled"]:
        return
    payout = int(payout)
    conn = db.get_conn()
    conn.execute(
        "UPDATE cs_buyins SET settled = 1, payout = ? WHERE game = ? AND ekey = ?",
        (payout, game, str(ekey)))
    conn.commit()
    conn.close()
    if payout > 0:
        wallet.credit(payout, cash=True)


def unsettled(game: str) -> list:
    ensure_schema()
    return db.fetchall("SELECT * FROM cs_buyins WHERE game = ? AND settled = 0", (game,))


def payout_for(game: str, ekey: str) -> int:
    e = entry(game, ekey)
    return int(e["payout"]) if (e and e["settled"]) else 0


def rank_payout(fee: int, rank: int, field: int) -> int:
    """Placement payout for a season league: win it for 10x, top-10% for 3x,
    top half for your money back, else nothing."""
    if not field or not rank:
        return 0
    if rank <= 1:
        return fee * 10
    if rank <= max(2, round(field * 0.10)):
        return fee * 3
    if rank <= round(field * 0.5):
        return fee
    return 0
