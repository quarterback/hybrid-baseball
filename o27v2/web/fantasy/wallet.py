"""CapSpace — the shared play-money wallet and career records.

One bankroll for the whole app. Every game debits its buy-in and credits its
winnings here, so the same fictional money is always at risk. Career records
persist per save so you're also playing against your own bests.

Guilders (ƒ) throughout — the canonical unit the rest of CapSpace uses.
"""

from __future__ import annotations

from o27v2 import db

START_BALANCE = 50_00_000  # ƒ50 lakh (~$50,000) seeded once per save

# Record keys tracked at the wallet layer (games add their own via bump()).
_REC_KEYS = ("peak_bankroll", "total_wagered", "total_won",
             "biggest_win", "entries", "cashes")


def ensure_schema() -> None:
    conn = db.get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cap_wallet (
            id      INTEGER PRIMARY KEY CHECK (id = 1),
            balance INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cap_records (
            key   TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        );
        """
    )
    conn.commit()


# --- records -----------------------------------------------------------------

def rec_get(key: str) -> int:
    r = db.fetchone("SELECT value FROM cap_records WHERE key = ?", (key,))
    return r["value"] if r else 0


def rec_set(key: str, value: int) -> None:
    conn = db.get_conn()
    conn.execute("INSERT INTO cap_records (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, int(value)))
    conn.commit()


def bump(key: str, delta: int = 1) -> None:
    rec_set(key, rec_get(key) + int(delta))


def rec_max(key: str, value: int) -> None:
    if int(value) > rec_get(key):
        rec_set(key, int(value))


# --- balance -----------------------------------------------------------------

def balance() -> int:
    ensure_schema()
    r = db.fetchone("SELECT balance FROM cap_wallet WHERE id = 1")
    if r is None:
        conn = db.get_conn()
        conn.execute("INSERT OR IGNORE INTO cap_wallet (id, balance) VALUES (1, ?)", (START_BALANCE,))
        conn.commit()
        rec_max("peak_bankroll", START_BALANCE)
        return START_BALANCE
    return r["balance"]


def _set(v: int) -> None:
    v = max(0, int(v))
    conn = db.get_conn()
    conn.execute("INSERT INTO cap_wallet (id, balance) VALUES (1, ?) "
                 "ON CONFLICT(id) DO UPDATE SET balance = excluded.balance", (v,))
    conn.commit()
    rec_max("peak_bankroll", v)


def debit(amount: int) -> bool:
    """Take a buy-in. Returns False (and no-op) if the wallet is short."""
    amount = int(amount)
    if amount <= 0:
        return True
    bal = balance()
    if bal < amount:
        return False
    _set(bal - amount)
    bump("total_wagered", amount)
    bump("entries", 1)
    return True


def credit(amount: int, *, cash: bool = True) -> None:
    """Pay winnings in. `cash=True` counts it as a cashed entry for records."""
    amount = int(amount)
    if amount <= 0:
        return
    _set(balance() + amount)
    bump("total_won", amount)
    rec_max("biggest_win", amount)
    if cash:
        bump("cashes", 1)


def records() -> dict:
    ensure_schema()
    balance()  # ensure seeded so peak_bankroll exists
    out = {k: rec_get(k) for k in _REC_KEYS}
    out["net"] = out["total_won"] - out["total_wagered"]
    return out
