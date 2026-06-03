"""CapSpace — Best Ball (draft once, your best lineup auto-counts).

The no-management format. You draft a roster that covers the diamond — one at
each of C / 1B / 2B / 3B / SS, two outfielders, two flex hitters, and four
pitchers — then never touch it again. Every slate your **best in-position
lineup** (C, 1B, 2B, 3B, SS, OF, OF + best 2 pitchers) auto-fills from your
roster and scores on the standard DFS fantasy points; draft depth at a
position lets the best performer there auto-start each day. Those slate scores
accumulate all season, ranked against a computed field of synthetic best-ball
rosters.

Scores come from persisted `game_batter_stats` / `game_pitcher_stats`;
nothing is re-simmed.
"""

from __future__ import annotations

import random

from o27v2 import db
from . import data as slate_data

# Hitter lineup slots auto-filled each slate (position, count).
LINEUP_H = (("C", 1), ("1B", 1), ("2B", 1), ("3B", 1), ("SS", 1), ("OF", 2))
DRAFT_REQ = dict(LINEUP_H)                       # roster minimum at each position
DRAFT_H = sum(c for _, c in LINEUP_H) + 2        # 7 in-position + 2 flex = 9
DRAFT_P = 4
START_P = 2
FIELD_SIZE = 48

_POS = {"C": "C", "1B": "1B", "2B": "2B", "3B": "3B", "SS": "SS",
        "LF": "OF", "CF": "OF", "RF": "OF", "OF": "OF", "DH": "OF", "NF": "OF"}


def _bucket(raw: str) -> str:
    return _POS.get((raw or "").upper(), "OF")


def ensure_schema() -> None:
    conn = db.get_conn()
    conn.executescript("CREATE TABLE IF NOT EXISTS bb_roster (player_id INTEGER PRIMARY KEY);")
    conn.commit()


def _db_id(pid) -> int:
    s = str(pid)
    return int(s[1:]) if s and s[0] == "p" else int(s)


class _Ctx:
    """Per-player, per-slate DFS points across the season, positions, and skill."""

    def __init__(self):
        self.meta = {}
        self.pos = {}
        for r in db.fetchall(
            "SELECT p.id, p.name, p.is_pitcher, p.position, COALESCE(t.abbrev, 'FA') AS team "
            "FROM players p LEFT JOIN teams t ON p.team_id = t.id"
        ):
            self.meta[r["id"]] = {"name": r["name"], "team": r["team"],
                                  "is_pitcher": bool(r["is_pitcher"])}
            if not r["is_pitcher"]:
                self.pos[r["id"]] = _bucket(r["position"])

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
        self.by_pos: dict[str, list[int]] = {}
        for i in self.hitters:
            self.by_pos.setdefault(self.pos.get(i, "OF"), []).append(i)

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
            by_pos: dict[str, list[float]] = {}
            for pid in h_ids:
                if pid in bd:
                    by_pos.setdefault(self.pos.get(pid, "OF"), []).append(bd[pid])
            slate = 0.0
            for posname, count in LINEUP_H:
                slate += sum(sorted(by_pos.get(posname, []), reverse=True)[:count])
            slate += sum(sorted((pd[p] for p in p_ids if p in pd), reverse=True)[:START_P])
            total += slate
        return round(total, 1)

    def _weighted(self, rng, ids, skill, n, bias, used=None):
        if n <= 0 or not ids:
            return []
        pool = [i for i in ids if not used or i not in used]
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
        """A position-valid synthetic roster: fill each required slot, then flex."""
        used: set = set()
        h: list[int] = []
        for posname, count in LINEUP_H:
            picks = self._weighted(rng, self.by_pos.get(posname, []), self.hskill, count, bias, used)
            used.update(picks)
            h += picks
        h += self._weighted(rng, self.hitters, self.hskill, DRAFT_H - len(h), bias, used)
        p = self._weighted(rng, self.pitchers, self.pskill, DRAFT_P, bias)
        return h, p


def get_roster() -> list[int]:
    ensure_schema()
    return [r["player_id"] for r in db.fetchall("SELECT player_id FROM bb_roster")]


def _active_dir() -> dict:
    """Every draftable player from the active save's rosters — works even with
    zero game history (pre-season), with a rating-based projection."""
    out = {}
    for r in db.fetchall(
        "SELECT p.*, COALESCE(t.abbrev, 'FA') AS team FROM players p "
        "LEFT JOIN teams t ON p.team_id = t.id "
        "WHERE p.is_active = 1 AND p.is_joker = 0"
    ):
        is_p = bool(r["is_pitcher"])
        out[r["id"]] = {
            "name": r["name"], "team": r["team"], "is_pitcher": is_p,
            "pos": "P" if is_p else _bucket(r["position"]),
            "proj": slate_data._proj_from_ratings(r, is_p),
        }
    return out


def _season_bat() -> dict:
    return {r["player_id"]: r for r in db.fetchall(
        "SELECT player_id, SUM(ab) ab, SUM(hits) h, SUM(doubles) d2, SUM(triples) d3, "
        "SUM(hr) hr, SUM(rbi) rbi, SUM(runs) r, SUM(sb) sb, SUM(bb) bb, SUM(hbp) hbp "
        "FROM game_batter_stats WHERE phase = 0 GROUP BY player_id")}


def _season_pit() -> dict:
    return {r["player_id"]: r for r in db.fetchall(
        "SELECT player_id, SUM(outs_recorded) outs, SUM(k) k, SUM(er) er, SUM(bb) bb, "
        "SUM(hits_allowed) ha, "
        "SUM(CASE WHEN is_starter = 1 AND outs_recorded >= 18 AND er <= 3 THEN 1 ELSE 0 END) qs "
        "FROM game_pitcher_stats WHERE phase = 0 GROUP BY player_id")}


def _pos_counts(ctx, ids):
    c = {}
    for i in ids:
        if not ctx.meta.get(i, {}).get("is_pitcher"):
            c[ctx.pos.get(i, "OF")] = c.get(ctx.pos.get(i, "OF"), 0) + 1
    return c


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
    lineup = "C · 1B · 2B · 3B · SS · OF · OF + best 2 pitchers"
    return {
        "score": you, "rank": rank, "field": len(field) + 1,
        "field_avg": round(sum(field) / len(field), 1) if field else 0.0,
        "field_best": round(max(field), 1) if field else 0.0,
        "pct": round(100 * beat / len(field)) if field else 0,
        "lineup": f"{lineup} each slate · {len(ctx.dates)} slates",
    }


def pool() -> dict:
    """Draftable hitters / pitchers from the active roster. Real season stat
    lines once games are played; a rating-based projection pre-season."""
    d = _active_dir()
    bat, pit = _season_bat(), _season_pit()
    hitters, pitchers = [], []
    for pid, m in d.items():
        if m["is_pitcher"]:
            t = pit.get(pid)
            if t and (t["outs"] or 0) > 0:
                era = 27.0 * t["er"] / t["outs"]
                line = f"{t['k']} K · {t['qs']} QS · {era:.2f} ERA"
                srt = (t["k"] or 0) + 8 * (t["qs"] or 0) + 0.3 * t["outs"]
            else:
                line = f"proj {m['proj']} · preseason"
                srt = m["proj"]
            pitchers.append({"id": f"p{pid}", "name": m["name"], "team": m["team"],
                             "pos": "P", "line": line, "_s": srt})
        else:
            t = bat.get(pid)
            if t and (t["ab"] or 0) > 0:
                obp = (t["h"] + t["bb"] + t["hbp"]) / max(1, t["ab"] + t["bb"] + t["hbp"])
                line = f"{t['hr']} HR · {t['rbi']} RBI · {obp:.3f} OBP"
                srt = (t["h"] + t["d2"] + 2 * t["d3"] + 3 * t["hr"] + t["bb"]
                       + t["r"] + t["rbi"] + t["sb"])
            else:
                line = f"proj {m['proj']} · preseason"
                srt = m["proj"]
            hitters.append({"id": f"p{pid}", "name": m["name"], "team": m["team"],
                            "pos": m["pos"], "line": line, "_s": srt})
    hitters.sort(key=lambda x: x["_s"], reverse=True)
    pitchers.sort(key=lambda x: x["_s"], reverse=True)
    for x in hitters + pitchers:
        x.pop("_s", None)
    return {"hitters": hitters[:300], "pitchers": pitchers[:250]}


def draft(player_ids: list) -> dict:
    ensure_schema()
    ids = [_db_id(p) for p in player_ids]
    d = _active_dir()
    hitters = [i for i in ids if i in d and not d[i]["is_pitcher"]]
    pitchers = [i for i in ids if i in d and d[i]["is_pitcher"]]
    if len(hitters) != DRAFT_H or len(pitchers) != DRAFT_P:
        return {"ok": False, "error": f"Need {DRAFT_H} hitters and {DRAFT_P} pitchers "
                                      f"(have {len(hitters)}H / {len(pitchers)}P)."}
    counts = {}
    for i in hitters:
        counts[d[i]["pos"]] = counts.get(d[i]["pos"], 0) + 1
    missing = [f"{need}× {pos}" if need > 1 else pos
               for pos, need in DRAFT_REQ.items() if counts.get(pos, 0) < need]
    if missing:
        return {"ok": False, "error": "Roster must cover every slot — short at: " + ", ".join(missing) + "."}
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
            "SELECT p.id, p.name, p.is_pitcher, p.position, t.abbrev team FROM players p "
            "JOIN teams t ON p.team_id = t.id WHERE p.id IN (%s)"
            % ",".join("?" for _ in roster_ids), tuple(roster_ids)):
            meta[r["id"]] = {"name": r["name"], "team": r["team"],
                             "pos": "P" if r["is_pitcher"] else _bucket(r["position"])}
    roster = [{"id": f"p{i}", **meta.get(i, {"name": "?", "team": "", "pos": ""})}
              for i in roster_ids]
    # sort roster by a stable position order for display
    order = {"C": 0, "1B": 1, "2B": 2, "3B": 3, "SS": 4, "OF": 5, "P": 6}
    roster.sort(key=lambda r: order.get(r["pos"], 9))
    out = {
        "slots": {"h": DRAFT_H, "p": DRAFT_P},
        "require": dict(DRAFT_REQ),
        "lineup": [list(s) for s in LINEUP_H] + [["P", START_P]],
        "roster": roster,
    }
    if len(roster_ids) == DRAFT_H + DRAFT_P:
        out["standings"] = standings(roster_ids)
    return out
