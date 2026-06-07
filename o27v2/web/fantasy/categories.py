"""CapSpace — the category-league engine (Roto).

One engine, many formats. You draft a season-long roster; your players'
**season counting stats** are aggregated into a set of categories, and you're
ranked against a computed field of synthetic rosters Roto-style (best in a
category = most points; sum across categories = your standing).

Counting stats are the backbone — R / HR / RBI / SB / OBP for hitters,
K / QS / ERA / WHIP for pitchers. O27 has no save rule, so the "stopper"
category is **Quality Finish** (QF), the finisher stat from main.

Every format is just a config on this engine:

  * std5x5  — the standard line with the popular upgrades (OBP, QS, QF).
  * razz    — the anti-league: every category direction inverts (worst real
              production wins) but AB/out floors force you to roster players
              who actually play.
  * hronly  — a one-category home-run derby.
  * arms    — pitchers only, the five pitching categories.

Season aggregates come straight from persisted `game_batter_stats` /
`game_pitcher_stats` (phase 0); nothing is re-simmed.
"""

from __future__ import annotations

import random

from . import fdb as db  # CapSpace's own DB (separate file)
from . import data as slate_data
from ._schema_once import once
from . import buyins

FIELD_SIZE = 48
SEASON_BUYIN = 5000  # ƒ5,000 per league; pays at season's end by final rank


def settle() -> None:
    """At season's end (no games left to play), cash out each drafted league
    by its final standing."""
    if slate_data._slate_date() is not None:
        return
    for b in buyins.unsettled("categories"):
        fk = b["ekey"]
        fmt = _FMT_BY_KEY.get(fk)
        roster = get_roster(fk)
        if not fmt or len(roster) != fmt["h"] + fmt["p"]:
            buyins.settle_one("categories", fk, 0)
            continue
        st = standings(fk, roster)
        buyins.settle_one("categories", fk, buyins.rank_payout(b["fee"], st["rank"], st["field"]))


# --- category library --------------------------------------------------------
# Each entry: (label, side 'h'/'p', better 'high'/'low', value(H, P) -> float).
# H / P are the roster's summed hitter / pitcher raw-stat dicts.

def _obp(H):
    denom = H["ab"] + H["bb"] + H["hbp"]
    return (H["h"] + H["bb"] + H["hbp"]) / denom if denom else 0.0

def _avg(H):
    return H["h"] / H["ab"] if H["ab"] else 0.0

def _era(P):
    return 27.0 * P["er"] / P["outs"] if P["outs"] else 0.0

def _whip(P):
    return 3.0 * (P["bb"] + P["ha"]) / P["outs"] if P["outs"] else 0.0

CATS = {
    "R":    ("R",    "h", "high", lambda H, P: H["r"]),
    "HR":   ("HR",   "h", "high", lambda H, P: H["hr"]),
    "RBI":  ("RBI",  "h", "high", lambda H, P: H["rbi"]),
    "SB":   ("SB",   "h", "high", lambda H, P: H["sb"]),
    "TB":   ("TB",   "h", "high", lambda H, P: H["h"] + H["d2"] + 2 * H["d3"] + 3 * H["hr"]),
    "OBP":  ("OBP",  "h", "high", lambda H, P: _obp(H)),
    "AVG":  ("AVG",  "h", "high", lambda H, P: _avg(H)),
    "K":    ("K",    "p", "high", lambda H, P: P["k"]),
    "QS":   ("QS",   "p", "high", lambda H, P: P["qs"]),
    "QF":   ("QF",   "p", "high", lambda H, P: P["qf"]),
    "ERA":  ("ERA",  "p", "low",  lambda H, P: _era(P)),
    "WHIP": ("WHIP", "p", "low",  lambda H, P: _whip(P)),
}

RATE_CATS = {"OBP", "AVG", "ERA", "WHIP"}  # shown to 3 decimals/2 decimals

FORMATS = [
    {"key": "std5x5", "name": "Standard 5×5", "h": 6, "p": 4, "invert": False, "floor": False,
     "cats": ["R", "HR", "RBI", "SB", "OBP", "K", "QS", "ERA", "WHIP", "QF"],
     "blurb": "The standard category line with the popular upgrades: OBP over "
              "AVG, QS over wins, and Quality Finish standing in for saves."},
    {"key": "razz", "name": "Razz (anti-league)", "h": 6, "p": 4, "invert": True, "floor": True,
     "cats": ["R", "HR", "RBI", "OBP", "K", "ERA", "WHIP"],
     "blurb": "The anti-league. Every category flips — worst real production "
              "wins — but you must clear AB and out floors, so you need bad "
              "players who actually play, not bench dust."},
    {"key": "hronly", "name": "HR Derby", "h": 6, "p": 0, "invert": False, "floor": False,
     "cats": ["HR"],
     "blurb": "One category: home runs. Draft six bats, chase the bombs."},
    {"key": "arms", "name": "Pitchers Only", "h": 0, "p": 5, "invert": False, "floor": False,
     "cats": ["K", "QS", "ERA", "WHIP", "QF"],
     "blurb": "The five pitching categories only — a clean way to learn the "
              "arms in a hitter-dominant league."},
]

_FMT_BY_KEY = {f["key"]: f for f in FORMATS}


@once
def ensure_schema() -> None:
    conn = db.get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cat_rosters (
            format_key TEXT NOT NULL,
            player_id  INTEGER NOT NULL,
            PRIMARY KEY (format_key, player_id)
        );
        """
    )
    conn.commit()


def _db_id(pid) -> int:
    s = str(pid)
    return int(s[1:]) if s and s[0] == "p" else int(s)


# --- season aggregates -------------------------------------------------------

class _Ctx:
    """Season totals for every player, plus skill weights for sampling a field."""

    def __init__(self):
        self.meta = {}
        for r in db.fetchall(
            "SELECT p.id, p.name, p.is_pitcher, t.abbrev AS team, p.position "
            "FROM players p JOIN teams t ON p.team_id = t.id"
        ):
            self.meta[r["id"]] = {
                "name": r["name"], "team": r["team"],
                "is_pitcher": bool(r["is_pitcher"]),
                "pos": "P" if r["is_pitcher"] else (r["position"] or "")[:2].upper() or "H",
            }

        self.bat = {}
        for r in db.fetchall(
            "SELECT player_id, SUM(ab) ab, SUM(hits) h, SUM(doubles) d2, SUM(triples) d3, "
            "SUM(hr) hr, SUM(runs) r, SUM(rbi) rbi, SUM(sb) sb, SUM(bb) bb, SUM(hbp) hbp, SUM(k) k "
            "FROM game_batter_stats WHERE phase = 0 GROUP BY player_id"
        ):
            self.bat[r["player_id"]] = {k: (r[k] or 0) for k in
                ("ab", "h", "d2", "d3", "hr", "r", "rbi", "sb", "bb", "hbp", "k")}

        self.pit = {}
        for r in db.fetchall(
            "SELECT player_id, SUM(outs_recorded) outs, SUM(hits_allowed) ha, SUM(er) er, "
            "SUM(bb) bb, SUM(k) k, SUM(hr_allowed) hra, SUM(batters_faced) bf, "
            "SUM(CASE WHEN is_starter = 1 AND outs_recorded >= 18 AND er <= 3 THEN 1 ELSE 0 END) qs, "
            "SUM(quality_finish) qf, SUM(terminal_outs) toi "
            "FROM game_pitcher_stats WHERE phase = 0 GROUP BY player_id"
        ):
            self.pit[r["player_id"]] = {k: (r[k] or 0) for k in
                ("outs", "ha", "er", "bb", "k", "hra", "bf", "qs", "qf", "toi")}

        # eligible draftable players (must have actually played)
        self.hitters = [i for i, v in self.bat.items()
                        if v["ab"] > 0 and not self.meta.get(i, {}).get("is_pitcher")]
        self.pitchers = [i for i, v in self.pit.items() if v["outs"] > 0]

        self.hskill = self._norm({i: self.bat[i]["h"] + self.bat[i]["d2"] + 2 * self.bat[i]["d3"]
                                  + 3 * self.bat[i]["hr"] + self.bat[i]["bb"] + self.bat[i]["r"]
                                  + self.bat[i]["rbi"] + self.bat[i]["sb"] for i in self.hitters})
        self.pskill = self._norm({i: self.pit[i]["k"] + 8 * self.pit[i]["qs"]
                                  + 0.3 * self.pit[i]["outs"] - 1.5 * self.pit[i]["er"]
                                  for i in self.pitchers})

    @staticmethod
    def _norm(d):
        if not d:
            return {}
        lo, hi = min(d.values()), max(d.values())
        rng = (hi - lo) or 1.0
        return {k: (v - lo) / rng for k, v in d.items()}

    def agg(self, ids):
        H = {k: 0 for k in ("ab", "h", "d2", "d3", "hr", "r", "rbi", "sb", "bb", "hbp", "k")}
        P = {k: 0 for k in ("outs", "ha", "er", "bb", "k", "hra", "bf", "qs", "qf", "toi")}
        for i in ids:
            if i in self.bat:
                for k in H:
                    H[k] += self.bat[i][k]
            if i in self.pit:
                for k in P:
                    P[k] += self.pit[i][k]
        return H, P

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

    def sample(self, rng, fmt, bias):
        return (self._weighted(rng, self.hitters, self.hskill, fmt["h"], bias)
                + self._weighted(rng, self.pitchers, self.pskill, fmt["p"], bias))

    def floors(self, fmt):
        """Min ABs / outs a roster must clear in a floor format (razz)."""
        if not fmt.get("floor"):
            return 0, 0
        habs = [self.bat[i]["ab"] for i in self.hitters]
        pouts = [self.pit[i]["outs"] for i in self.pitchers]
        mean_ab = sum(habs) / len(habs) if habs else 0
        mean_out = sum(pouts) / len(pouts) if pouts else 0
        return 0.5 * fmt["h"] * mean_ab, 0.5 * fmt["p"] * mean_out


# --- roto scoring ------------------------------------------------------------

def _roto(entries, fmt):
    """entries: list of dicts with 'H','P','dq'. Returns per-cat points matrix
    and totals. Best in a category scores len(entries), worst scores 1."""
    cats = [CATS[k] for k in fmt["cats"]]
    invert = fmt["invert"]
    n = len(entries)
    points = [[0.0] * len(cats) for _ in entries]
    values = [[0.0] * len(cats) for _ in entries]

    for j, (label, side, better, fn) in enumerate(cats):
        if invert:
            better = "low" if better == "high" else "high"
        vals = [fn(e["H"], e["P"]) for e in entries]
        for i, v in enumerate(vals):
            values[i][j] = v
        # rank: higher points = better. DQ'd entries always rank worst.
        def keyfn(i):
            dq = entries[i]["dq"]
            v = vals[i]
            # sort ascending into points; we want best last to get high points
            base = v if better == "high" else -v
            return (0 if dq else 1, base)
        order = sorted(range(n), key=keyfn)  # worst first
        # assign points with tie averaging on the comparable value
        k = 0
        while k < n:
            j2 = k
            # group ties on (dq, comparable value)
            while (j2 + 1 < n and entries[order[j2 + 1]]["dq"] == entries[order[k]]["dq"]
                   and values[order[j2 + 1]][j] == values[order[k]][j]):
                j2 += 1
            avg_pts = sum(range(k + 1, j2 + 2)) / (j2 - k + 1)
            for t in range(k, j2 + 1):
                points[order[t]][j] = avg_pts
            k = j2 + 1
    totals = [round(sum(points[i]), 1) for i in range(n)]
    return values, points, totals


def standings(format_key: str, your_ids: list[int]) -> dict:
    ensure_schema()
    fmt = _FMT_BY_KEY.get(format_key, FORMATS[0])
    ctx = _Ctx()
    floor_ab, floor_outs = ctx.floors(fmt)

    def make_entry(ids, handle, you=False):
        H, P = ctx.agg(ids)
        dq = bool(fmt.get("floor")) and (
            (fmt["h"] > 0 and H["ab"] < floor_ab) or (fmt["p"] > 0 and P["outs"] < floor_outs))
        return {"handle": handle, "H": H, "P": P, "dq": dq, "you": you}

    entries = [make_entry(your_ids, "YOU", you=True)]
    rng = random.Random(1234 + len(ctx.bat) + len(ctx.pit))
    for i in range(FIELD_SIZE):
        bias = 0.2 + 0.8 * ((i % 12) / 11.0)
        entries.append(make_entry(ctx.sample(rng, fmt, bias), _handle(rng, i)))

    values, points, totals = _roto(entries, fmt)
    order = sorted(range(len(entries)), key=lambda i: totals[i], reverse=True)
    rank_of = {i: r + 1 for r, i in enumerate(order)}

    cat_meta = [CATS[k] for k in fmt["cats"]]
    you_i = 0
    cat_rows = []
    for j, k in enumerate(fmt["cats"]):
        label = cat_meta[j][0]
        # your rank within this single category (1 = best)
        col = [(values[i][j], entries[i]["dq"], i) for i in range(len(entries))]
        better = cat_meta[j][2]
        if fmt["invert"]:
            better = "low" if better == "high" else "high"
        col.sort(key=lambda t: (0 if t[1] else 1, t[0] if better == "high" else -t[0]), reverse=True)
        crank = next(r for r, t in enumerate(col, 1) if t[2] == you_i)
        cat_rows.append({
            "key": k, "label": label,
            "value": _fmt_val(k, values[you_i][j]),
            "points": points[you_i][j],
            "rank": crank,
        })

    return {
        "format": format_key,
        "roto": totals[you_i],
        "rank": rank_of[you_i],
        "field": len(entries),
        "dq": entries[you_i]["dq"],
        "max_points": len(entries) * len(fmt["cats"]),
        "categories": cat_rows,
        "floor": {"ab": round(floor_ab), "outs": round(floor_outs)} if fmt.get("floor") else None,
    }


def _fmt_val(key, v):
    if key in ("OBP", "AVG"):
        return f"{v:.3f}"
    if key in ("ERA", "WHIP"):
        return f"{v:.2f}"
    return round(v, 1)


_ADJ = ["salty", "tidal", "deep", "anchor", "reef", "storm", "gale", "harbor",
        "fathom", "crest", "drift", "mast", "keel", "shoal", "brine", "wake"]
_NOUN = ["dogs", "crew", "kings", "rats", "barons", "sharks", "gulls", "tars",
         "pilots", "swabs", "lords", "hands", "mates", "wreckers"]

def _handle(rng, i):
    return f"{rng.choice(_ADJ)}_{rng.choice(_NOUN)}"


# --- roster management -------------------------------------------------------

def get_roster(format_key: str) -> list[int]:
    ensure_schema()
    return [r["player_id"] for r in db.fetchall(
        "SELECT player_id FROM cat_rosters WHERE format_key = ?", (format_key,))]


_POS = {"C": "C", "1B": "1B", "2B": "2B", "3B": "3B", "SS": "SS",
        "LF": "OF", "CF": "OF", "RF": "OF", "OF": "OF", "DH": "OF", "NF": "OF"}


def _bucket(raw: str) -> str:
    return _POS.get((raw or "").upper(), "OF")


def _active_dir() -> dict:
    """Every draftable player from the active save — works with zero game
    history (a rating-based projection stands in pre-season)."""
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
        "SUM(hits_allowed) ha, SUM(quality_finish) qf, "
        "SUM(CASE WHEN is_starter = 1 AND outs_recorded >= 18 AND er <= 3 THEN 1 ELSE 0 END) qs "
        "FROM game_pitcher_stats WHERE phase = 0 GROUP BY player_id")}


def pool(format_key: str) -> dict:
    """Draftable hitters / pitchers for a format, from the active roster. Real
    season lines once games are played; a rating projection pre-season. Sorted
    worst-first for an inverted format (Razz), so the bad players you want lead."""
    fmt = _FMT_BY_KEY.get(format_key, FORMATS[0])
    d = _active_dir()
    bat, pit = _season_bat(), _season_pit()
    rev = not fmt["invert"]  # normal: best first; razz: worst first
    hitters, pitchers = [], []
    for pid, m in d.items():
        if not m["is_pitcher"] and fmt["h"] > 0:
            t = bat.get(pid)
            if t and (t["ab"] or 0) > 0:
                obp = (t["h"] + t["bb"] + t["hbp"]) / max(1, t["ab"] + t["bb"] + t["hbp"])
                line = f"{t['hr']} HR · {t['r']} R · {t['rbi']} RBI · {t['sb']} SB · {obp:.3f} OBP"
                srt = (t["h"] + t["d2"] + 2 * t["d3"] + 3 * t["hr"] + t["bb"] + t["r"] + t["rbi"] + t["sb"])
            else:
                line = f"proj {m['proj']} · preseason"
                srt = m["proj"]
            hitters.append({"id": f"p{pid}", "name": m["name"], "team": m["team"],
                            "pos": m["pos"], "line": line, "_s": srt})
        elif m["is_pitcher"] and fmt["p"] > 0:
            t = pit.get(pid)
            if t and (t["outs"] or 0) > 0:
                era = 27.0 * t["er"] / t["outs"]
                whip = 3.0 * (t["bb"] + t["ha"]) / t["outs"]
                line = f"{t['k']} K · {t['qs']} QS · {era:.2f} ERA · {whip:.2f} WHIP · {t['qf']} QF"
                srt = (t["k"] or 0) + 8 * (t["qs"] or 0) + 0.3 * t["outs"]
            else:
                line = f"proj {m['proj']} · preseason"
                srt = m["proj"]
            pitchers.append({"id": f"p{pid}", "name": m["name"], "team": m["team"],
                             "pos": "P", "line": line, "_s": srt})
    hitters.sort(key=lambda x: x["_s"], reverse=rev)
    pitchers.sort(key=lambda x: x["_s"], reverse=rev)
    for x in hitters + pitchers:
        x.pop("_s", None)
    return {"hitters": hitters[:250], "pitchers": pitchers[:250]}


def draft(format_key: str, player_ids: list) -> dict:
    ensure_schema()
    fmt = _FMT_BY_KEY.get(format_key)
    if not fmt:
        return {"ok": False, "error": "Unknown format."}
    ids = [_db_id(p) for p in player_ids]
    d = _active_dir()
    nh = sum(1 for i in ids if i in d and not d[i]["is_pitcher"])
    np_ = sum(1 for i in ids if i in d and d[i]["is_pitcher"])
    if nh != fmt["h"] or np_ != fmt["p"]:
        return {"ok": False, "error": f"Need exactly {fmt['h']} hitters and {fmt['p']} pitchers "
                                      f"(have {nh}H / {np_}P)."}
    bi = buyins.enter("categories", format_key, SEASON_BUYIN)  # once per league
    if not bi.get("ok"):
        return bi
    conn = db.get_conn()
    conn.execute("DELETE FROM cat_rosters WHERE format_key = ?", (format_key,))
    conn.executemany("INSERT OR IGNORE INTO cat_rosters (format_key, player_id) VALUES (?,?)",
                     [(format_key, i) for i in ids])
    conn.commit()
    return {"ok": True}


def state(format_key: str) -> dict:
    """Everything the UI needs for one format: meta, your roster, and — if a
    roster is drafted — the live standings."""
    ensure_schema()
    fmt = _FMT_BY_KEY.get(format_key, FORMATS[0])
    roster_ids = get_roster(fmt["key"])
    ctx_meta = {}
    if roster_ids:
        for r in db.fetchall(
            "SELECT p.id, p.name, p.is_pitcher, t.abbrev team FROM players p "
            "JOIN teams t ON p.team_id = t.id WHERE p.id IN (%s)"
            % ",".join("?" for _ in roster_ids), tuple(roster_ids)):
            ctx_meta[r["id"]] = {"name": r["name"], "team": r["team"],
                                 "pos": "P" if r["is_pitcher"] else "H"}
    roster = [{"id": f"p{i}", **ctx_meta.get(i, {"name": "?", "team": "", "pos": ""})}
              for i in roster_ids]
    out = {
        "formats": [{"key": f["key"], "name": f["name"], "blurb": f["blurb"],
                     "h": f["h"], "p": f["p"], "cats": f["cats"], "invert": f["invert"]}
                    for f in FORMATS],
        "format": fmt["key"], "slots": {"h": fmt["h"], "p": fmt["p"]},
        "roster": roster,
        "buyIn": SEASON_BUYIN,
        "entered": bool(buyins.entry("categories", fmt["key"])),
        "payout": buyins.payout_for("categories", fmt["key"]),
    }
    if len(roster_ids) == fmt["h"] + fmt["p"] and roster_ids:
        out["standings"] = standings(fmt["key"], roster_ids)
    return out
