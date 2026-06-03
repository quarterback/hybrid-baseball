"""CapSpace — "Sluggers" (the Walk-Back home-run game).

A simple home-run counting game with an O27 twist. Each slate you pick up to
three sluggers; you bank their home runs plus the runs/RBI that bring those
walk-back runners home — in O27 a homer doesn't just score, it sets a runner
back on base who can be driven in again. Power is the headline; getting them
home is the bonus. A running season total lets you watch the bombers pile up.

Scoring per hitter on a slate (clean counting stats, settled from persisted
`game_batter_stats`):

    HR x4  +  RBI x2  +  Run x1

When main lands a dedicated walk-back-run column we can split Runs into the
explicit walk-back component; today's `runs` already includes the walk-back
score a homer sets up, so the intent holds.
"""

from __future__ import annotations

import datetime as _dt
import statistics

from o27v2 import db
from . import data as slate_data

MAX_PICKS = 3
_W_HR, _W_RBI, _W_RUN = 4.0, 2.0, 1.0


def ensure_schema() -> None:
    conn = db.get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS slugger_picks (
            slate_date TEXT NOT NULL,
            player_id  INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (slate_date, player_id)
        );
        """
    )
    conn.commit()


def _db_id(pid) -> int:
    s = str(pid)
    return int(s[1:]) if s and s[0] == "p" else int(s)


def _hitters_for(slate_date: str) -> list[dict]:
    blob = slate_data.build_slate_data()
    if not blob or blob.get("SLATE_DATE") != slate_date:
        return []
    hs = [p for p in blob["PLAYERS"] if not p.get("isPitcher")]
    # surface the bombers first
    hs.sort(key=lambda p: (p.get("r", {}) or {}).get("power", 0), reverse=True)
    return hs


def _player_game(slate_date: str, dbid: int):
    """(game_id, is_final) for a player's game on a slate, or (None, False)."""
    prow = db.fetchone("SELECT team_id FROM players WHERE id = ?", (dbid,))
    if not prow:
        return None, False
    g = db.fetchone(
        "SELECT id, played FROM games WHERE game_date = ? "
        "AND (home_team_id = ? OR away_team_id = ?)",
        (slate_date, prow["team_id"], prow["team_id"]),
    )
    if not g:
        return None, False
    return g["id"], bool(g["played"])


def _score(game_id: int, dbid: int) -> float:
    s = db.fetchone(
        "SELECT hr, rbi, runs FROM game_batter_stats "
        "WHERE game_id = ? AND player_id = ? AND phase = 0",
        (game_id, dbid),
    )
    if not s:
        return 0.0
    return _W_HR * (s["hr"] or 0) + _W_RBI * (s["rbi"] or 0) + _W_RUN * (s["runs"] or 0)


def _player_label(dbid: int) -> dict:
    row = db.fetchone(
        "SELECT p.id, p.name, t.abbrev AS team FROM players p "
        "JOIN teams t ON p.team_id = t.id WHERE p.id = ?",
        (dbid,),
    )
    if not row:
        return {"id": f"p{dbid}", "name": "—", "team": ""}
    return {"id": f"p{row['id']}", "name": row["name"], "team": row["team"]}


def _benchmark(slate_date: str):
    """(field_avg, ceiling) for a settled slate: a random 3-pick's expected
    score, and the best-3 a perfect pick would have banked. (None, None)
    while no games on the slate are final yet."""
    games = [g["id"] for g in db.fetchall(
        "SELECT id FROM games WHERE game_date = ? AND played = 1", (slate_date,))]
    if not games:
        return None, None
    ph = ",".join("?" for _ in games)
    rows = db.fetchall(
        f"SELECT hr, rbi, runs FROM game_batter_stats WHERE game_id IN ({ph}) AND phase = 0",
        tuple(games),
    )
    scores = sorted(
        (_W_HR * (r["hr"] or 0) + _W_RBI * (r["rbi"] or 0) + _W_RUN * (r["runs"] or 0)
         for r in rows), reverse=True)
    if not scores:
        return 0.0, 0.0
    field_avg = round(statistics.mean(scores) * MAX_PICKS, 1)  # a random 3-pick
    ceiling = round(sum(scores[:MAX_PICKS]), 1)
    return field_avg, ceiling


def _slate_entry(slate_date: str, dbids: list[int]) -> dict:
    rows = []
    score = 0.0
    settled = True
    for dbid in dbids:
        gid, done = _player_game(slate_date, dbid)
        pts = _score(gid, dbid) if (gid and done) else None
        if pts is None:
            settled = False
        else:
            score += pts
        lab = _player_label(dbid)
        rows.append({**lab, "pts": (round(pts, 1) if pts is not None else None)})
    field_avg, ceiling = _benchmark(slate_date)
    return {
        "slate_date": slate_date, "picks": rows, "score": round(score, 1),
        "settled": settled, "fieldAvg": field_avg, "ceiling": ceiling,
    }


def status() -> dict:
    ensure_schema()
    picks = db.fetchall("SELECT * FROM slugger_picks ORDER BY slate_date")
    by_slate: dict[str, list[int]] = {}
    for p in picks:
        by_slate.setdefault(p["slate_date"], []).append(p["player_id"])

    slate = slate_data._slate_date()
    season = 0.0
    your_slate = None
    history = []
    for sd in sorted(by_slate, reverse=True):
        entry = _slate_entry(sd, by_slate[sd])
        if entry["settled"]:
            season += entry["score"]
        if sd == slate:
            your_slate = entry
        else:
            history.append(entry)

    picked_ids = set(by_slate.get(slate, [])) if slate else set()
    pool = []
    if slate and len(picked_ids) < MAX_PICKS:
        for p in _hitters_for(slate):
            if _db_id(p["id"]) in picked_ids:
                continue
            pool.append({
                "id": p["id"], "name": p["name"], "team": p["team"],
                "pos": p["pos"], "opp": p.get("opp", ""),
                "teamColor": p.get("teamColor", ""), "init": p.get("init", ""),
                "power": (p.get("r", {}) or {}).get("power", 0),
            })

    return {
        "slate_date": slate, "season": round(season, 1), "max": MAX_PICKS,
        "picked": len(picked_ids), "your_slate": your_slate,
        "pool": pool, "history": history[:12],
    }


def pick(player_id) -> dict:
    ensure_schema()
    slate = slate_data._slate_date()
    if not slate:
        return {"ok": False, "error": "No upcoming slate to pick."}
    dbid = _db_id(player_id)
    if dbid not in {_db_id(p["id"]) for p in _hitters_for(slate)}:
        return {"ok": False, "error": "That hitter isn't on the upcoming slate."}
    _, done = _player_game(slate, dbid)
    if done:
        return {"ok": False, "error": "That game has already started."}
    if db.fetchone("SELECT 1 FROM slugger_picks WHERE slate_date = ? AND player_id = ?",
                   (slate, dbid)):
        return {"ok": True, "slate_date": slate}  # idempotent
    n = db.fetchone("SELECT COUNT(*) c FROM slugger_picks WHERE slate_date = ?", (slate,))["c"]
    if n >= MAX_PICKS:
        return {"ok": False, "error": f"You already have {MAX_PICKS} sluggers tonight."}
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO slugger_picks (slate_date, player_id, created_at) VALUES (?,?,?)",
        (slate, dbid, _dt.datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit()
    return {"ok": True, "slate_date": slate}


def remove(player_id) -> dict:
    ensure_schema()
    slate = slate_data._slate_date()
    if not slate:
        return {"ok": False, "error": "No upcoming slate."}
    dbid = _db_id(player_id)
    _, done = _player_game(slate, dbid)
    if done:
        return {"ok": False, "error": "That game has started — your pick is locked."}
    conn = db.get_conn()
    conn.execute("DELETE FROM slugger_picks WHERE slate_date = ? AND player_id = ?", (slate, dbid))
    conn.commit()
    return {"ok": True}
