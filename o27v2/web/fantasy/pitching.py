"""CapSpace — "Pilots" (the pitching game).

O27 is hitter-dominant and has no innings or save rule, so this game is built
to teach the pitching side on its own terms. Standard pitching counting stats
lead — strikeouts, outs recorded, run prevention, the quality start — and the
O27 finisher stats season it: Terminal Outs and Quality Finishes reward the
arms that actually close games out, whatever their usage pattern.

Pick up to three pilots a slate; score from persisted `game_pitcher_stats`:

    K x3  +  Out x1  -  ER x2  +  QS +6  +  QF +6  +  TO x0.5

  QS (quality start) = a starter who recorded >=18 outs allowing <=3 ER.
  QF (quality finish) = entered and sealed 9+ of the final outs never trailing.
  TO (terminal out)   = an out recorded protecting a never-relinquished lead.

A running season total lets the bombers' counterweights pile up too.
"""

from __future__ import annotations

import datetime as _dt
import statistics

from o27v2 import db
from . import data as slate_data
from ._schema_once import once
from . import buyins

MAX_PICKS = 3
BUY_IN = 1000  # ƒ1,000 per slate; beat the field to cash
_W_K, _W_OUT, _W_ER, _W_QS, _W_QF, _W_TO = 3.0, 1.0, -2.0, 6.0, 6.0, 0.5


def _slate_final(slate_date: str) -> bool:
    r = db.fetchone("SELECT COUNT(*) total, COALESCE(SUM(played),0) played "
                    "FROM games WHERE game_date = ?", (slate_date,))
    return bool(r and r["total"] and r["played"] == r["total"])


def _slate_payout(fee: int, score: float, field_avg: float, ceiling: float) -> int:
    if ceiling and score >= 0.9 * ceiling:
        return fee * 5
    if field_avg and score >= field_avg:
        return fee * 2
    if field_avg and score >= 0.6 * field_avg:
        return fee
    return 0


def settle() -> None:
    """Cash out finished slates: payout vs the field, into the wallet."""
    for b in buyins.unsettled("pilots"):
        sd = b["ekey"]
        if not _slate_final(sd):
            continue
        ids = [r["player_id"] for r in db.fetchall(
            "SELECT player_id FROM pilot_picks WHERE slate_date = ?", (sd,))]
        ent = _slate_entry(sd, ids)
        fa, ceil = _benchmark(sd)
        buyins.settle_one("pilots", sd, _slate_payout(b["fee"], ent["score"], fa or 0, ceil or 0))


@once
def ensure_schema() -> None:
    conn = db.get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pilot_picks (
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


def _pitchers_for(slate_date: str) -> list[dict]:
    blob = slate_data.build_slate_data()
    if not blob or blob.get("SLATE_DATE") != slate_date:
        return []
    ps = [p for p in blob["PLAYERS"] if p.get("isPitcher")]
    ps.sort(key=lambda p: p.get("proj", 0), reverse=True)
    return ps


def _player_game(slate_date: str, dbid: int):
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


def _score_row(s: dict) -> float:
    outs = s.get("outs_recorded", 0) or 0
    er = s.get("er", 0) or 0
    pts = (
        _W_K * (s.get("k", 0) or 0)
        + _W_OUT * outs
        + _W_ER * er
        + _W_QF * (s.get("quality_finish", 0) or 0)
        + _W_TO * (s.get("terminal_outs", 0) or 0)
    )
    if (s.get("is_starter") or 0) and outs >= 18 and er <= 3:
        pts += _W_QS
    return pts


def _score(game_id: int, dbid: int) -> float:
    s = db.fetchone(
        "SELECT k, outs_recorded, er, is_starter, quality_finish, terminal_outs "
        "FROM game_pitcher_stats WHERE game_id = ? AND player_id = ? AND phase = 0",
        (game_id, dbid),
    )
    return _score_row(s) if s else 0.0


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
    games = [g["id"] for g in db.fetchall(
        "SELECT id FROM games WHERE game_date = ? AND played = 1", (slate_date,))]
    if not games:
        return None, None
    ph = ",".join("?" for _ in games)
    rows = db.fetchall(
        "SELECT k, outs_recorded, er, is_starter, quality_finish, terminal_outs "
        f"FROM game_pitcher_stats WHERE game_id IN ({ph}) AND phase = 0",
        tuple(games),
    )
    scores = sorted((_score_row(r) for r in rows), reverse=True)
    if not scores:
        return 0.0, 0.0
    field_avg = round(statistics.mean(scores) * MAX_PICKS, 1)
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
    settle()
    picks = db.fetchall("SELECT * FROM pilot_picks ORDER BY slate_date")
    by_slate: dict[str, list[int]] = {}
    for p in picks:
        by_slate.setdefault(p["slate_date"], []).append(p["player_id"])

    slate = slate_data._slate_date()
    season = 0.0
    your_slate = None
    history = []
    for sd in sorted(by_slate, reverse=True):
        entry = _slate_entry(sd, by_slate[sd])
        entry["payout"] = buyins.payout_for("pilots", sd)
        if entry["settled"]:
            season += entry["score"]
        if sd == slate:
            your_slate = entry
        else:
            history.append(entry)

    picked_ids = set(by_slate.get(slate, [])) if slate else set()
    pool = []
    if slate and len(picked_ids) < MAX_PICKS:
        for p in _pitchers_for(slate):
            if _db_id(p["id"]) in picked_ids:
                continue
            pool.append({
                "id": p["id"], "name": p["name"], "team": p["team"],
                "pos": p["pos"], "opp": p.get("opp", ""),
                "teamColor": p.get("teamColor", ""), "init": p.get("init", ""),
                "proj": p.get("proj", 0),
            })

    return {
        "slate_date": slate, "season": round(season, 1), "max": MAX_PICKS,
        "picked": len(picked_ids), "your_slate": your_slate,
        "pool": pool, "history": history[:12],
        "buyIn": BUY_IN, "entered": bool(slate and buyins.entry("pilots", slate)),
    }


def pick(player_id) -> dict:
    ensure_schema()
    slate = slate_data._slate_date()
    if not slate:
        return {"ok": False, "error": "No upcoming slate to pick."}
    dbid = _db_id(player_id)
    if dbid not in {_db_id(p["id"]) for p in _pitchers_for(slate)}:
        return {"ok": False, "error": "That pitcher isn't on the upcoming slate."}
    _, done = _player_game(slate, dbid)
    if done:
        return {"ok": False, "error": "That game has already started."}
    if db.fetchone("SELECT 1 FROM pilot_picks WHERE slate_date = ? AND player_id = ?",
                   (slate, dbid)):
        return {"ok": True, "slate_date": slate}
    n = db.fetchone("SELECT COUNT(*) c FROM pilot_picks WHERE slate_date = ?", (slate,))["c"]
    if n >= MAX_PICKS:
        return {"ok": False, "error": f"You already have {MAX_PICKS} pilots tonight."}
    bi = buyins.enter("pilots", slate, BUY_IN)  # charged once per slate
    if not bi.get("ok"):
        return bi
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO pilot_picks (slate_date, player_id, created_at) VALUES (?,?,?)",
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
    conn.execute("DELETE FROM pilot_picks WHERE slate_date = ? AND player_id = ?", (slate, dbid))
    conn.commit()
    return {"ok": True}
