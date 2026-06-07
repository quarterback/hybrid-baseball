"""CapSpace — "Sluggers" (the Walk-Back home-run game).

A simple home-run counting game with an O27 twist. Each slate you pick up to
three sluggers; you bank their home runs plus the runs/RBI that bring those
walk-back runners home — in O27 a homer doesn't just score, it sets a runner
back on base who can be driven in again. Power is the headline; getting them
home is the bonus. A running season total lets you watch the bombers pile up.

Scoring per hitter on a slate (clean counting stats, settled from persisted
`game_batter_stats`):

    HR x4  +  Walk-Back run x4  +  RBI x1

`walkback_runs` is the engine's per-hitter count of runs a player scored as a
Walk-Back bonus runner — his homer set him back on base and he was driven in
again. It is the exact "homers that keep paying" stat, the per-hitter mirror
of the pitcher's wb_runs.
"""

from __future__ import annotations

import datetime as _dt
import statistics

from . import fdb as db  # CapSpace's own DB (separate file)
from . import data as slate_data
from ._schema_once import once
from . import buyins

MAX_PICKS = 3
BUY_IN = 1000  # ƒ1,000 per slate; beat the field to cash
_W_HR, _W_WBR, _W_RBI = 4.0, 4.0, 1.0


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
    for b in buyins.unsettled("sluggers"):
        sd = b["ekey"]
        if not _slate_final(sd):
            continue
        ids = [r["player_id"] for r in db.fetchall(
            "SELECT player_id FROM slugger_picks WHERE slate_date = ?", (sd,))]
        ent = _slate_entry(sd, ids)
        fa, ceil = _benchmark(sd)
        buyins.settle_one("sluggers", sd, _slate_payout(b["fee"], ent["score"], fa or 0, ceil or 0))


@once
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
    conn.close()


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
        "SELECT hr, rbi, walkback_runs FROM game_batter_stats "
        "WHERE game_id = ? AND player_id = ? AND phase = 0",
        (game_id, dbid),
    )
    if not s:
        return 0.0
    return _W_HR * (s["hr"] or 0) + _W_WBR * (s["walkback_runs"] or 0) + _W_RBI * (s["rbi"] or 0)


def _season_hr_map(dbids: list[int]) -> dict[int, int]:
    """Current-season HR total per player, from persisted batter stats.
    The DB holds one season's games, so a plain SUM over phase-0 rows is the
    season-to-date HR count (tonight's slate games aren't played yet, so they
    contribute nothing)."""
    if not dbids:
        return {}
    ph = ",".join("?" for _ in dbids)
    rows = db.fetchall(
        f"SELECT player_id, COALESCE(SUM(hr), 0) hr FROM game_batter_stats "
        f"WHERE player_id IN ({ph}) AND phase = 0 GROUP BY player_id",
        tuple(dbids),
    )
    return {r["player_id"]: r["hr"] for r in rows}


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
        f"SELECT hr, rbi, walkback_runs FROM game_batter_stats WHERE game_id IN ({ph}) AND phase = 0",
        tuple(games),
    )
    scores = sorted(
        (_W_HR * (r["hr"] or 0) + _W_WBR * (r["walkback_runs"] or 0) + _W_RBI * (r["rbi"] or 0)
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
    # No inline settle on the read path (writes collided with the sim →
    # "database is locked"); the background pass settles. Read + grade only.
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
        entry["payout"] = buyins.payout_for("sluggers", sd)
        if entry["settled"]:
            season += entry["score"]
        if sd == slate:
            your_slate = entry
        else:
            history.append(entry)

    picked_ids = set(by_slate.get(slate, [])) if slate else set()
    pool = []
    if slate and len(picked_ids) < MAX_PICKS:
        cands = [p for p in _hitters_for(slate) if _db_id(p["id"]) not in picked_ids]
        hr_map = _season_hr_map([_db_id(p["id"]) for p in cands])
        for p in cands:
            pool.append({
                "id": p["id"], "name": p["name"], "team": p["team"],
                "pos": p["pos"], "opp": p.get("opp", ""),
                "teamColor": p.get("teamColor", ""), "init": p.get("init", ""),
                "hr": hr_map.get(_db_id(p["id"]), 0),
                "power": (p.get("r", {}) or {}).get("power", 0),
            })
        # Lead with the season's HR leaders; power breaks ties so the order
        # still reads sensibly early in the year when totals are bunched at 0.
        pool.sort(key=lambda e: (e["hr"], e["power"]), reverse=True)

    return {
        "slate_date": slate, "season": round(season, 1), "max": MAX_PICKS,
        "picked": len(picked_ids), "your_slate": your_slate,
        "pool": pool, "history": history[:12],
        "buyIn": BUY_IN, "entered": bool(slate and buyins.entry("sluggers", slate)),
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
    bi = buyins.enter("sluggers", slate, BUY_IN)  # charged once per slate
    if not bi.get("ok"):
        return bi
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO slugger_picks (slate_date, player_id, created_at) VALUES (?,?,?)",
        (slate, dbid, _dt.datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
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
    conn.close()
    return {"ok": True}
