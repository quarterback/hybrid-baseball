"""CapSpace — Best Ball (draft once, your best lineup auto-counts).

The no-management format. You draft a roster — 8 hitters and 4 pitchers — and
then never touch it again. Every slate, your **best 5 hitters and best 2
pitchers** who played that day are auto-started and scored on the standard DFS
fantasy points (the rebalanced counting-stat line in `data.py`). Those slate
scores accumulate all season, and you're ranked against a computed field of
synthetic best-ball rosters.

No lineup decisions, no waiver churn — your draft is the whole game. Scores
come from persisted `game_batter_stats` / `game_pitcher_stats`; nothing is
re-simmed.
"""

from __future__ import annotations

import random

from o27v2 import db
from . import data as slate_data

DRAFT_H, DRAFT_P = 8, 4      # roster you draft
START_H, START_P = 5, 2      # best ones that auto-count each slate
FIELD_SIZE = 48


def ensure_schema() -> None:
    conn = db.get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bb_roster (
            player_id INTEGER PRIMARY KEY
        );
        """
    )
    conn.commit()


def _db_id(pid) -> int:
    s = str(pid)
    return int(s[1:]) if s and s[0] == "p" else int(s)


class _Ctx:
    """Per-player, per-slate DFS points across the season, plus skill for the
    field sampler."""

    def __init__(self):
        self.meta = {}
        for r in db.fetchall(
            "SELECT p.id, p.name, p.is_pitcher, COALESCE(t.abbrev, 'FA') AS team "
            "FROM players p LEFT JOIN teams t ON p.team_id = t.id"
        ):
            self.meta[r["id"]] = {"name": r["name"], "team": r["team"],
                                  "is_pitcher": bool(r["is_pitcher"])}

        self.bat_by_date: dict[str, dict[int, float]] = {}
        for r in db.fetchall(
            "SELECT b.player_id pid, g.game_date d, b.* FROM game_batter_stats b "
            "JOIN games g ON b.game_id = g.id WHERE b.phase = 0 AND g.played = 1"
        ):
            self.bat_by_date.setdefault(r["d"], {})[r["pid"]] = slate_data._batter_fp(r)

        self.pit_by_date: dict[str, dict[int, float]] = {}
        for r in db.fetchall(
            "SELECT p.player_id pid, g.game_date d, p.* FROM game_pitcher_stats p "
            "JOIN games g ON p.game_id = g.id WHERE p.phase = 0 AND g.played = 1"
        ):
            self.pit_by_date.setdefault(r["d"], {})[r["pid"]] = slate_data._pitcher_fp(r)

        self.dates = sorted(set(self.bat_by_date) | set(self.pit_by_date))

        hsum, psum = {}, {}
        for d in self.dates:
            for pid, fp in self.bat_by_date.get(d, {}).items():
                hsum[pid] = hsum.get(pid, 0.0) + fp
            for pid, fp in self.pit_by_date.get(d, {}).items():
                psum[pid] = psum.get(pid, 0.0) + fp
        self.hitters = [i for i in hsum if i in self.meta and not self.meta[i]["is_pitcher"]]
        self.pitchers = [i for i in psum if i in self.meta and self.meta[i]["is_pitcher"]]
        self.htotal, self.ptotal = hsum, psum
        self.hskill = self._norm({i: hsum[i] for i in self.hitters})
        self.pskill = self._norm({i: psum[i] for i in self.pitchers})

    @staticmethod
    def _norm(d):
        if not d:
            return {}
        lo, hi = min(d.values()), max(d.values())
        rng = (hi - lo) or 1.0
        return {k: (v - lo) / rng for k, v in d.items()}

    def score(self, h_ids, p_ids) -> float:
        h_ids, p_ids = set(h_ids), set(p_ids)
        total = 0.0
        for d in self.dates:
            bd = self.bat_by_date.get(d, {})
            pd = self.pit_by_date.get(d, {})
            hp = sorted((bd[p] for p in h_ids if p in bd), reverse=True)[:START_H]
            pp = sorted((pd[p] for p in p_ids if p in pd), reverse=True)[:START_P]
            total += sum(hp) + sum(pp)
        return round(total, 1)

    def _weighted(self, rng, ids, skill, n, bias):
        if n <= 0 or not ids:
            return []
        pool = list(ids)
        chosen = []
        w = {i: 0.15 + bias * skill.get(i, 0.0) for i in pool}
        for _ in range(min(n, len(pool))):
            tot = sum(w[i] for i in pool)
            r = rng.random() * tot
            acc = 0.0
            for i in pool:
                acc += w[i]
                if acc >= r:
                    chosen.append(i)
                    pool.remove(i)
                    break
        return chosen

    def sample(self, rng, bias):
        return (self._weighted(rng, self.hitters, self.hskill, DRAFT_H, bias),
                self._weighted(rng, self.pitchers, self.pskill, DRAFT_P, bias))


def get_roster() -> list[int]:
    ensure_schema()
    return [r["player_id"] for r in db.fetchall("SELECT player_id FROM bb_roster")]


def standings(roster_ids: list[int]) -> dict:
    ctx = _Ctx()
    h_ids = [i for i in roster_ids if not ctx.meta.get(i, {}).get("is_pitcher")]
    p_ids = [i for i in roster_ids if ctx.meta.get(i, {}).get("is_pitcher")]
    you = ctx.score(h_ids, p_ids)

    field = []
    rng = random.Random(99 + len(ctx.hitters) + len(ctx.pitchers))
    for i in range(FIELD_SIZE):
        bias = 0.2 + 0.8 * ((i % 12) / 11.0)
        h, p = ctx.sample(rng, bias)
        field.append(ctx.score(h, p))
    beat = sum(1 for f in field if you >= f)
    rank = sum(1 for f in field if f > you) + 1

    return {
        "score": you,
        "rank": rank,
        "field": len(field) + 1,
        "field_avg": round(sum(field) / len(field), 1) if field else 0.0,
        "field_best": round(max(field), 1) if field else 0.0,
        "pct": round(100 * beat / len(field)) if field else 0,
        "lineup": f"best {START_H} hitters + {START_P} pitchers each slate · {len(ctx.dates)} slates",
    }


def pool() -> dict:
    ctx = _Ctx()
    n = max(1, len(ctx.dates))
    hitters = [{
        "id": f"p{i}", "name": ctx.meta[i]["name"], "team": ctx.meta[i]["team"], "pos": "H",
        "line": f"{round(ctx.htotal[i], 1)} fp · {round(ctx.htotal[i] / n, 1)}/slate",
    } for i in sorted(ctx.hitters, key=lambda x: ctx.htotal[x], reverse=True)[:250]]
    pitchers = [{
        "id": f"p{i}", "name": ctx.meta[i]["name"], "team": ctx.meta[i]["team"], "pos": "P",
        "line": f"{round(ctx.ptotal[i], 1)} fp · {round(ctx.ptotal[i] / n, 1)}/slate",
    } for i in sorted(ctx.pitchers, key=lambda x: ctx.ptotal[x], reverse=True)[:250]]
    return {"hitters": hitters, "pitchers": pitchers}


def draft(player_ids: list) -> dict:
    ensure_schema()
    ids = [_db_id(p) for p in player_ids]
    ctx = _Ctx()
    nh = sum(1 for i in ids if not ctx.meta.get(i, {}).get("is_pitcher"))
    np_ = sum(1 for i in ids if ctx.meta.get(i, {}).get("is_pitcher"))
    if nh != DRAFT_H or np_ != DRAFT_P:
        return {"ok": False, "error": f"Need exactly {DRAFT_H} hitters and {DRAFT_P} pitchers "
                                      f"(have {nh}H / {np_}P)."}
    conn = db.get_conn()
    conn.execute("DELETE FROM bb_roster")
    conn.executemany("INSERT OR IGNORE INTO bb_roster (player_id) VALUES (?)", [(i,) for i in ids])
    conn.commit()
    return {"ok": True}


def state() -> dict:
    ensure_schema()
    roster_ids = get_roster()
    meta = {}
    if roster_ids:
        for r in db.fetchall(
            "SELECT p.id, p.name, p.is_pitcher, t.abbrev team FROM players p "
            "JOIN teams t ON p.team_id = t.id WHERE p.id IN (%s)"
            % ",".join("?" for _ in roster_ids), tuple(roster_ids)):
            meta[r["id"]] = {"name": r["name"], "team": r["team"],
                             "pos": "P" if r["is_pitcher"] else "H"}
    roster = [{"id": f"p{i}", **meta.get(i, {"name": "?", "team": "", "pos": ""})}
              for i in roster_ids]
    out = {
        "slots": {"h": DRAFT_H, "p": DRAFT_P},
        "start": {"h": START_H, "p": START_P},
        "roster": roster,
    }
    if len(roster_ids) == DRAFT_H + DRAFT_P:
        out["standings"] = standings(roster_ids)
    return out
