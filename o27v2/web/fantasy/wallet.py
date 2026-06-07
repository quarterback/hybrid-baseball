"""CapSpace — the shared play-money wallet, personas, and lifetime status.

One bankroll for the whole app. Every game debits its buy-in and credits its
winnings here. Two career layers sit on top:

  * **Persona** — picked once per save (your starting bankroll + flavour). The
    wallet isn't seeded until you choose one.
  * **Lifetime earnings** — the monotonic sum of everything you've ever won.
    It never goes down (even when the wallet busts), and it drives your
    **status tier** through a ladder of money gates — the frequent-flier model
    (status from lifetime miles, not your current balance).

Guilders (ƒ) are the canonical unit; $1 = ƒ100. Persona/tier values below are
written in dollars for readability and converted on the way in/out.
"""

from __future__ import annotations

import datetime as _dt

from . import fdb as db  # CapSpace's own DB (separate file)
from ._schema_once import once

_USD = 100  # guilders per dollar

# --- personas: pick your degenerate (starting bankroll in dollars) -----------
PERSONAS = [
    {"key": "college", "name": "Broke college student", "start": 500,
     "blurb": "Ramen money. One bad beat from skipping the dining hall."},
    {"key": "father", "name": "Responsible father of two", "start": 2500,
     "blurb": "This is fun money. It stays fun money. The 529 is safe."},
    {"key": "degen", "name": "Swears he doesn't have a problem", "start": 5000,
     "blurb": "I can stop whenever I want. I just don't want to right now."},
    {"key": "pe", "name": "PE guy chasing the rush", "start": 25000,
     "blurb": "It's not gambling if you have an edge. Now EBITDA this."},
]
_PERSONA = {p["key"]: p for p in PERSONAS}

# --- status tiers by LIFETIME earnings (dollar gates). Names are placeholders
# refined from the loyalty-tier research; the gate ladder is the spine. -------
# Sports-career status ladder — the climb every fan knows, rookie to immortal.
TIERS = [
    {"min": 0,       "name": "Rookie"},
    {"min": 5000,    "name": "Role Player"},
    {"min": 10000,   "name": "Starter"},
    {"min": 25000,   "name": "Veteran"},
    {"min": 50000,   "name": "All-Star"},
    {"min": 100000,  "name": "All-Pro"},
    {"min": 250000,  "name": "MVP"},
    {"min": 500000,  "name": "Champion"},
    {"min": 1000000, "name": "Hall of Famer"},
]

# The level you've EARNED (via lifetime winnings) sets the bankroll you start /
# restart with — climbing tiers unlocks a bigger stake. Indexed to TIERS.
START_BY_TIER = [500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000]  # dollars

_REC_KEYS = ("peak_bankroll", "total_wagered", "total_won",
             "biggest_win", "entries", "cashes", "restarts")


@once
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
        CREATE TABLE IF NOT EXISTS cap_profile (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            persona    TEXT,
            created_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()


# --- records -----------------------------------------------------------------

def rec_get(key: str) -> int:
    r = db.fetchone("SELECT value FROM cap_records WHERE key = ?", (key,))
    return r["value"] if r else 0


def rec_set(key: str, value: int) -> None:
    conn = db.get_conn()
    conn.execute("INSERT INTO cap_records (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, int(value)))
    conn.commit()
    conn.close()


def bump(key: str, delta: int = 1) -> None:
    rec_set(key, rec_get(key) + int(delta))


def rec_max(key: str, value: int) -> None:
    if int(value) > rec_get(key):
        rec_set(key, int(value))


# --- onboarding / persona ----------------------------------------------------

def started() -> bool:
    ensure_schema()
    return db.fetchone("SELECT 1 FROM cap_wallet WHERE id = 1") is not None


def persona() -> str | None:
    ensure_schema()
    r = db.fetchone("SELECT persona FROM cap_profile WHERE id = 1")
    return r["persona"] if r else None


def start(persona_key: str) -> dict:
    """Seed the wallet from a chosen persona (once)."""
    ensure_schema()
    if started():
        return {"ok": False, "error": "Already started."}
    p = _PERSONA.get(persona_key) or PERSONAS[0]
    _set(p["start"] * _USD)
    conn = db.get_conn()
    conn.execute("INSERT OR REPLACE INTO cap_profile (id, persona, created_at) VALUES (1, ?, ?)",
                 (p["key"], _dt.datetime.utcnow().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()
    return {"ok": True, "balance": balance()}


# --- balance -----------------------------------------------------------------

def balance() -> int:
    ensure_schema()
    r = db.fetchone("SELECT balance FROM cap_wallet WHERE id = 1")
    return r["balance"] if r else 0


def _set(v: int) -> None:
    v = max(0, int(v))
    conn = db.get_conn()
    conn.execute("INSERT INTO cap_wallet (id, balance) VALUES (1, ?) "
                 "ON CONFLICT(id) DO UPDATE SET balance = excluded.balance", (v,))
    conn.commit()
    conn.close()
    rec_max("peak_bankroll", v)


def debit(amount: int) -> bool:
    """Take a buy-in. Returns False (no-op) if the wallet can't cover it."""
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
    """Pay winnings in. Bumps LIFETIME earnings (total_won), which never drops."""
    amount = int(amount)
    if amount <= 0:
        return
    _set(balance() + amount)
    bump("total_won", amount)
    rec_max("biggest_win", amount)
    if cash:
        bump("cashes", 1)


# --- tiers --------------------------------------------------------------------

def tier_for(lifetime_guilders: int) -> dict:
    """Current tier + the gates around it, all in guilders for the UI."""
    life_usd = lifetime_guilders / _USD
    idx = 0
    for i, t in enumerate(TIERS):
        if life_usd >= t["min"]:
            idx = i
    cur = TIERS[idx]
    nxt = TIERS[idx + 1] if idx + 1 < len(TIERS) else None
    return {
        "name": cur["name"], "idx": idx, "count": len(TIERS),
        "floor": cur["min"] * _USD,
        "nextName": nxt["name"] if nxt else None,
        "nextGate": (nxt["min"] * _USD) if nxt else None,
        "isMax": nxt is None,
        "startBankroll": START_BY_TIER[idx] * _USD,
        "nextStart": (START_BY_TIER[idx + 1] * _USD) if nxt else None,
    }


def restart() -> dict:
    """Soft landing: when you've busted, take a tier-scaled top-up back to the
    felt. It is NOT counted as winnings, so it never touches your lifetime
    status — your tier is permanent. Higher tiers get a bigger floor."""
    ensure_schema()
    if not started():
        return {"ok": False, "error": "Pick a player first."}
    bal = balance()
    if bal >= 5000:  # ƒ5,000 = $50 — you've still got chips
        return {"ok": False, "error": "You've still got chips — no restart needed."}
    t = tier_for(rec_get("total_won"))
    floor = START_BY_TIER[t["idx"]] * _USD  # your earned level's starting stake
    _set(floor)            # _set never lowers peak; lifetime/status untouched
    bump("restarts", 1)
    return {"ok": True, "balance": floor}


def records() -> dict:
    ensure_schema()
    out = {k: rec_get(k) for k in _REC_KEYS}
    out["net"] = out["total_won"] - out["total_wagered"]
    out["lifetime"] = out["total_won"]          # guilders; the status driver
    out["persona"] = persona()
    out["tier"] = tier_for(out["lifetime"])
    return out
