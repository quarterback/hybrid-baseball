"""CapSpace — "Go Streaking" (hit-streak survivor).

The simplest solo game: each slate you pick one hitter you think gets a hit.
A hit extends your streak; a hitless day resets it to zero. You're chasing
your longest run — no field, no cap, no opponents, just you versus a hit.

One active streak, one pick per slate date. Picks settle from persisted
`game_batter_stats` once the player's game is final; never re-sims.
"""

from __future__ import annotations

import datetime as _dt

from o27v2 import db
from . import data as slate_data
from ._schema_once import once
from . import wallet

# Milestone pots (streak length -> guilders). Free game, pure upside: a hot
# streak pays out, with a jackpot at 50. Each gate pays once, on the pick that
# reaches it; rebuild the streak and a fresh run can earn it again.
GATES = {20: 1000 * 100, 30: 5000 * 100, 50: 50000 * 100}


@once
def ensure_schema() -> None:
    conn = db.get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS streak_picks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            slate_date TEXT NOT NULL UNIQUE,
            player_id  INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            gate_paid  INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.commit()
    try:
        conn.execute("ALTER TABLE streak_picks ADD COLUMN gate_paid INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except Exception:
        pass


def settle() -> None:
    """Walk the settled picks and pay any streak-length pot the moment a pick
    reaches it (idempotent: each pick pays its gate once)."""
    ensure_schema()
    picks = db.fetchall("SELECT * FROM streak_picks ORDER BY slate_date")
    streak = 0
    best = 0
    to_pay = []  # (pick_id, gate_len, amount)
    for p in picks:
        res = _settle(p["slate_date"], p["player_id"])
        if res == "hit":
            streak += 1
            best = max(best, streak)
            amt = GATES.get(streak)
            if amt and not p["gate_paid"]:
                to_pay.append((p["id"], streak, amt))
        elif res == "miss":
            streak = 0
        # 'pending' / 'void' don't change the running streak
    # Persist the best run so /api/wallet can show it without recomputing the
    # whole grade walk on the request path (rec_max never lowers it).
    try:
        wallet.rec_max("best_streak", best)
    except Exception:
        pass
    if not to_pay:
        return
    # Mark the gates paid and commit FIRST (release the write lock), then credit
    # the wallet — wallet.credit() opens its own connection.
    conn = db.get_conn()
    for pid, gate, _amt in to_pay:
        conn.execute("UPDATE streak_picks SET gate_paid = ? WHERE id = ?", (gate, pid))
    conn.commit()
    for _pid, _gate, amt in to_pay:
        wallet.credit(amt)


def _db_id(pid) -> int:
    s = str(pid)
    return int(s[1:]) if s and s[0] == "p" else int(s)


def _hitters_for(slate_date: str) -> list[dict]:
    """Hitters available on a slate, drawn from the same pool DFS uses."""
    blob = slate_data.build_slate_data()
    if not blob or blob.get("SLATE_DATE") != slate_date:
        return []
    return [p for p in blob["PLAYERS"] if not p.get("isPitcher")]


def _settle(slate_date: str, dbid: int) -> str:
    """Grade one pick: 'hit' | 'miss' | 'pending' | 'void'.

    Pending until the player's own game on that date is final.
    """
    prow = db.fetchone("SELECT team_id, name FROM players WHERE id = ?", (dbid,))
    if not prow:
        return "void"
    g = db.fetchone(
        "SELECT id, played FROM games WHERE game_date = ? "
        "AND (home_team_id = ? OR away_team_id = ?)",
        (slate_date, prow["team_id"], prow["team_id"]),
    )
    if not g:
        return "void"
    if not g["played"]:
        return "pending"
    h = db.fetchone(
        "SELECT COALESCE(SUM(hits), 0) AS hits FROM game_batter_stats "
        "WHERE game_id = ? AND player_id = ? AND phase = 0",
        (g["id"], dbid),
    )
    return "hit" if (h and h["hits"] > 0) else "miss"


def _player_label(dbid: int) -> dict:
    row = db.fetchone(
        "SELECT p.id, p.name, t.abbrev AS team FROM players p "
        "JOIN teams t ON p.team_id = t.id WHERE p.id = ?",
        (dbid,),
    )
    if not row:
        return {"id": f"p{dbid}", "name": "—", "team": ""}
    return {"id": f"p{row['id']}", "name": row["name"], "team": row["team"]}


def status() -> dict:
    """Full streak state: current / best run, today's pick or the eligible
    pool to pick from, and recent history."""
    ensure_schema()
    settle()  # pay any newly-reached milestone pots
    picks = db.fetchall("SELECT * FROM streak_picks ORDER BY slate_date")

    claimed = {p["gate_paid"] for p in picks if p["gate_paid"]}
    cur = best = 0
    history = []
    for p in picks:
        res = _settle(p["slate_date"], p["player_id"])
        if res == "hit":
            cur += 1
            best = max(best, cur)
        elif res == "miss":
            cur = 0
        # 'pending' / 'void' don't change the running streak
        lab = _player_label(p["player_id"])
        history.append({
            "slate_date": p["slate_date"], "player": lab["name"],
            "team": lab["team"], "result": res,
        })
    history.reverse()  # newest first

    slate = slate_data._slate_date()
    today_pick = None
    pool = []
    if slate:
        existing = db.fetchone(
            "SELECT player_id FROM streak_picks WHERE slate_date = ?", (slate,)
        )
        if existing:
            lab = _player_label(existing["player_id"])
            today_pick = {**lab, "result": _settle(slate, existing["player_id"])}
        else:
            for p in _hitters_for(slate):
                pool.append({
                    "id": p["id"], "name": p["name"], "team": p["team"],
                    "pos": p["pos"], "opp": p.get("opp", ""),
                    "teamColor": p.get("teamColor", ""), "init": p.get("init", ""),
                })

    gates = [{"len": n, "amount": GATES[n], "claimed": n in claimed,
              "reached": cur >= n} for n in sorted(GATES)]
    next_gate = next((g for g in gates if cur < g["len"]), None)
    return {
        "current": cur, "best": best,
        "slate_date": slate, "today_pick": today_pick,
        "pool": pool, "history": history[:20],
        "gates": gates, "nextGate": next_gate,
    }


def make_pick(player_id) -> dict:
    """Pick a hitter for the upcoming slate. Replaces an existing pick for
    that date as long as the player's game hasn't started."""
    ensure_schema()
    slate = slate_data._slate_date()
    if not slate:
        return {"ok": False, "error": "No upcoming slate to pick."}
    dbid = _db_id(player_id)
    if dbid not in {_db_id(p["id"]) for p in _hitters_for(slate)}:
        return {"ok": False, "error": "That hitter isn't on the upcoming slate."}
    if _settle(slate, dbid) not in ("pending", "void"):
        return {"ok": False, "error": "That game has already started."}
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO streak_picks (slate_date, player_id, created_at) VALUES (?,?,?) "
        "ON CONFLICT(slate_date) DO UPDATE SET player_id = excluded.player_id, "
        "created_at = excluded.created_at",
        (slate, dbid, _dt.datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit()
    return {"ok": True, "slate_date": slate}
