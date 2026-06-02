"""CapSpace — DFS contests, entries, scoring, and the computed field.

The solo-player model: you enter a lineup; the leaderboard you climb is a
**computed field** of synthetic lineups scored by the same rule, plus **par**
(the best possible lineup on the slate) and your **percentile**. No other
humans, no bot-GM drafting — the field is generated deterministically per
contest and scored against the real per-game stats as the slate's games play.

Read-only against the engine; the only new state is two small tables
(`dfs_contests`, `dfs_entries`). Scoring always reads persisted
`game_*_stats` — never re-sims.
"""

from __future__ import annotations

import json
import random
import datetime as _dt

from o27v2 import db, currency
from . import data as slate_data

_USD = currency.GUILDER_PER_USD  # 100 — money is stored as guilders


# ---------------------------------------------------------------------------
# Schema (lazy CREATE; mirrors the ALTER/IF-NOT-EXISTS pattern used elsewhere)
# ---------------------------------------------------------------------------

def ensure_schema() -> None:
    conn = db.get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dfs_contests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            slate_date  TEXT NOT NULL,
            key         TEXT NOT NULL,
            name        TEXT NOT NULL,
            kind        TEXT NOT NULL,
            fee         INTEGER NOT NULL DEFAULT 0,
            prize_pool  INTEGER NOT NULL DEFAULT 0,
            top_prize   INTEGER NOT NULL DEFAULT 0,
            field_size  INTEGER NOT NULL DEFAULT 1000,
            color       TEXT DEFAULT '',
            badge       TEXT DEFAULT '',
            seed        INTEGER NOT NULL DEFAULT 0,
            UNIQUE(slate_date, key)
        );
        CREATE TABLE IF NOT EXISTS dfs_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            contest_id  INTEGER NOT NULL REFERENCES dfs_contests(id),
            slate_date  TEXT NOT NULL,
            player_ids  TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dfs_entries_contest ON dfs_entries(contest_id);
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Contest templates — small dollar figures (stored as guilders)
# ---------------------------------------------------------------------------

# (key, name, kind, fee$, pool$, top$, field, color, badge)
_CONTEST_TEMPLATE = [
    ("big_slate", "The Big Slate",   "GPP",         10, 5000, 1000, 2000, "var(--c-coral)",  "BS"),
    ("nightcap",  "Tidewater Nightcap", "GPP",        3, 1000,  250,  600, "var(--c-teal)",   "TN"),
    ("double_up", "Double-Up Dockside", "Double Up",  5,  900,   10,  200, "var(--c-blue)",   "2x"),
    ("showdown",  "Single Stay Showdown", "Head-to-Head", 2, 40,  4,   20, "var(--c-violet)", "SS"),
    ("rookie",    "Rookie Reef (Free)", "Freeroll",    0,   50,   10, 1000, "var(--c-green)",  "F"),
]


def list_contests(slate_date: str) -> list[dict]:
    """Contests for a slate, generated once and persisted so entries can
    reference them. Returns rows with live entry counts."""
    ensure_schema()
    rows = db.fetchall(
        "SELECT * FROM dfs_contests WHERE slate_date = ? ORDER BY id", (slate_date,)
    )
    if not rows:
        conn = db.get_conn()
        base_seed = abs(hash(("capspace", slate_date))) % (2**31)
        for i, (key, name, kind, fee, pool, top, field, color, badge) in enumerate(_CONTEST_TEMPLATE):
            conn.execute(
                """INSERT OR IGNORE INTO dfs_contests
                   (slate_date, key, name, kind, fee, prize_pool, top_prize,
                    field_size, color, badge, seed)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (slate_date, key, name, kind, fee * _USD, pool * _USD, top * _USD,
                 field, color, badge, base_seed + i),
            )
        conn.commit()
        rows = db.fetchall(
            "SELECT * FROM dfs_contests WHERE slate_date = ? ORDER BY id", (slate_date,)
        )
    for r in rows:
        cnt = db.fetchone(
            "SELECT COUNT(*) AS n FROM dfs_entries WHERE contest_id = ?", (r["id"],)
        )
        # entries shown = the synthetic field plus any real user entries.
        r["entries"] = r["field_size"] + (cnt["n"] if cnt else 0)
    return rows


# ---------------------------------------------------------------------------
# Scoring — fantasy points per player on the slate, from persisted stats
# ---------------------------------------------------------------------------

def _db_id(pid) -> int:
    """Front-end ids are 'p<dbid>'. Accept either form."""
    s = str(pid)
    return int(s[1:]) if s and s[0] == "p" else int(s)


class _LiveContext:
    """One authoritative scoring view of a slate, shared by every consumer so
    a given lineup scores identically everywhere. A roster spot earns realized
    fantasy points once that player's team game is **final**, else the
    projection stands in as the in-progress estimate."""

    def __init__(self, slate_date: str):
        self.pool = _pool_for(slate_date)
        games = db.fetchall(
            "SELECT id, played, home_team_id, away_team_id FROM games WHERE game_date = ?",
            (slate_date,),
        )
        self.total = len(games)
        self.played = sum(1 for g in games if g["played"])
        team_done: dict[int, bool] = {}
        done_ids: list[int] = []
        for g in games:
            done = bool(g["played"])
            team_done[g["home_team_id"]] = done
            team_done[g["away_team_id"]] = done
            if done:
                done_ids.append(g["id"])

        # realized fantasy points per db player_id, from finished games only
        self.realized: dict[int, float] = {}
        if done_ids:
            ph = ",".join("?" for _ in done_ids)
            for s in db.fetchall(
                f"SELECT * FROM game_batter_stats WHERE game_id IN ({ph}) AND phase = 0",
                tuple(done_ids),
            ):
                self.realized[s["player_id"]] = self.realized.get(s["player_id"], 0.0) + slate_data._batter_fp(s)
            for s in db.fetchall(
                f"SELECT * FROM game_pitcher_stats WHERE game_id IN ({ph}) AND phase = 0",
                tuple(done_ids),
            ):
                self.realized[s["player_id"]] = self.realized.get(s["player_id"], 0.0) + slate_data._pitcher_fp(s)

        # dbid -> team_id, in one batch, to decide each player's game state
        self.team_of: dict[int, int] = {}
        if self.pool:
            ph = ",".join("?" for _ in self.pool)
            for row in db.fetchall(
                f"SELECT id, team_id FROM players WHERE id IN ({ph})", tuple(self.pool.keys())
            ):
                self.team_of[row["id"]] = row["team_id"]
        self.team_done = team_done

    def done_for(self, dbid: int) -> bool:
        return self.team_done.get(self.team_of.get(dbid), False)

    def live_for(self, dbid: int) -> float:
        p = self.pool.get(dbid)
        if not p:
            return 0.0
        if self.done_for(dbid):
            return round(self.realized.get(dbid, 0.0), 1)
        return round(p["proj"], 1)

    def score_lineup(self, ids: list) -> float:
        return round(sum(self.live_for(_db_id(i)) for i in ids), 1)


# ---------------------------------------------------------------------------
# Field generation + par
# ---------------------------------------------------------------------------

_SLOT_POS = ["PILOT", "C", "1B", "2B", "3B", "SS", "OF"]  # + STAY (any hitter)


def _legal_random_lineup(pool: dict, rng: random.Random, skill: float, cap: int):
    """One synthetic lineup: pick each slot weighted toward projection^skill,
    then downgrade the worst value spots until under the cap. Returns the list
    of db ids, or None if it couldn't fit."""
    by_pos: dict[str, list] = {}
    for p in pool.values():
        by_pos.setdefault(p["pos"], []).append(p)

    def pick(cands, taken):
        cands = [c for c in cands if c["dbid"] not in taken]
        if not cands:
            return None
        weights = [max(0.1, c["proj"]) ** skill for c in cands]
        return rng.choices(cands, weights=weights, k=1)[0]

    taken: set = set()
    chosen = []
    for pos in _SLOT_POS:
        c = pick(by_pos.get(pos, []), taken)
        if not c:
            return None
        taken.add(c["dbid"]); chosen.append(c)
    # STAY flex — any hitter not already used
    hitters = [p for p in pool.values() if not p["isPitcher"]]
    c = pick(hitters, taken)
    if not c:
        return None
    taken.add(c["dbid"]); chosen.append(c)

    # Fit under cap: repeatedly swap the costliest spot down to a cheaper
    # same-eligibility player, giving up the least projection per dollar saved.
    for _ in range(40):
        total = sum(c["salary"] for c in chosen)
        if total <= cap:
            break
        # try to improve the worst over-budget spot
        worst_i, worst_gain = None, None
        for i, c in enumerate(chosen):
            pos = _SLOT_POS[i] if i < len(_SLOT_POS) else None
            cands = by_pos.get(pos, []) if pos else [h for h in pool.values() if not h["isPitcher"]]
            cheaper = [x for x in cands if x["salary"] < c["salary"] and x["dbid"] not in taken]
            if not cheaper:
                continue
            best = max(cheaper, key=lambda x: x["proj"])
            dollars = c["salary"] - best["salary"]
            gain = (c["proj"] - best["proj"]) / max(1, dollars)  # proj lost per ƒ saved
            if dollars > 0 and (worst_gain is None or gain < worst_gain):
                worst_gain, worst_i, worst_repl = gain, i, best
        if worst_i is None:
            return None
        taken.discard(chosen[worst_i]["dbid"])
        chosen[worst_i] = worst_repl
        taken.add(worst_repl["dbid"])
    if sum(c["salary"] for c in chosen) > cap:
        return None
    return [c["dbid"] for c in chosen]


def _par_lineup(pool: dict, live_for, cap: int) -> float:
    """A strong 'par' benchmark: best player per slot by live points, then
    downgrade least-costly-to-give-up spots until under the cap."""
    by_pos: dict[str, list] = {}
    for p in pool.values():
        by_pos.setdefault(p["pos"], []).append(p)

    def best(cands, taken):
        cands = [c for c in cands if c["dbid"] not in taken]
        return max(cands, key=lambda c: live_for(c["dbid"])) if cands else None

    taken: set = set()
    chosen = []
    for pos in _SLOT_POS:
        c = best(by_pos.get(pos, []), taken)
        if not c:
            return 0.0
        taken.add(c["dbid"]); chosen.append((pos, c))
    flex = best([h for h in pool.values() if not h["isPitcher"]], taken)
    if not flex:
        return 0.0
    taken.add(flex["dbid"]); chosen.append((None, flex))

    for _ in range(60):
        total = sum(c["salary"] for _, c in chosen)
        if total <= cap:
            break
        best_swap = None
        for i, (pos, c) in enumerate(chosen):
            cands = by_pos.get(pos, []) if pos else [h for h in pool.values() if not h["isPitcher"]]
            cheaper = [x for x in cands if x["salary"] < c["salary"] and x["dbid"] not in taken]
            if not cheaper:
                continue
            repl = max(cheaper, key=lambda x: live_for(x["dbid"]))
            dollars = c["salary"] - repl["salary"]
            lost = live_for(c["dbid"]) - live_for(repl["dbid"])
            ratio = lost / max(1, dollars)
            if dollars > 0 and (best_swap is None or ratio < best_swap[0]):
                best_swap = (ratio, i, pos, repl)
        if not best_swap:
            break
        _, i, pos, repl = best_swap
        taken.discard(chosen[i][1]["dbid"])
        chosen[i] = (pos, repl)
        taken.add(repl["dbid"])
    return round(sum(live_for(c["dbid"]) for _, c in chosen), 1)


# ---------------------------------------------------------------------------
# Public: enter a lineup, read contest results
# ---------------------------------------------------------------------------

CAP = 1_00_000  # ƒ — $1,000 (mirrors capspace-data.jsx)


def _pool_for(slate_date: str) -> dict:
    """The slate's player pool keyed by db id, with a 'dbid' field added."""
    blob = slate_data.build_slate_data()
    pool: dict[int, dict] = {}
    if not blob or blob.get("SLATE_DATE") != slate_date:
        return pool
    for p in blob["PLAYERS"]:
        p = dict(p)
        p["dbid"] = _db_id(p["id"])
        pool[p["dbid"]] = p
    return pool


def enter(contest_id: int, player_ids: list) -> dict:
    """Validate a lineup against the contest's slate + cap and persist it."""
    ensure_schema()
    contest = db.fetchone("SELECT * FROM dfs_contests WHERE id = ?", (contest_id,))
    if not contest:
        return {"ok": False, "error": "Contest not found."}
    pool = _pool_for(contest["slate_date"])
    ids = [_db_id(x) for x in player_ids]
    if len(ids) != 8 or len(set(ids)) != 8:
        return {"ok": False, "error": "A lineup needs 8 distinct players."}
    chosen = [pool.get(i) for i in ids]
    if any(c is None for c in chosen):
        return {"ok": False, "error": "A picked player isn't on this slate."}
    if sum(c["salary"] for c in chosen) > CAP:
        return {"ok": False, "error": "Lineup is over the salary cap."}
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO dfs_entries (contest_id, slate_date, player_ids, created_at) "
        "VALUES (?,?,?,?)",
        (contest_id, contest["slate_date"], json.dumps(ids),
         _dt.datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit()
    return {"ok": True, "entry_id": cur.lastrowid, "contest_id": contest_id}


def contest_results(contest_id: int, board_size: int = 12) -> dict | None:
    """Assemble the live board for a contest: your lineup score, the computed
    field, par, your rank + percentile, and games-final progress."""
    ensure_schema()
    contest = db.fetchone("SELECT * FROM dfs_contests WHERE id = ?", (contest_id,))
    if not contest:
        return None
    ctx = _LiveContext(contest["slate_date"])
    pool = ctx.pool
    if not pool:
        return None
    played, total = ctx.played, ctx.total

    # --- the field (deterministic per contest) ---
    rng = random.Random(contest["seed"])
    field_scores = []
    n_field = min(int(contest["field_size"]), 250)  # cap the synthetic set we score
    for _ in range(n_field):
        skill = rng.uniform(0.6, 3.0)  # spread of manager quality
        lu = _legal_random_lineup(pool, rng, skill, CAP)
        if lu:
            field_scores.append(ctx.score_lineup(lu))
    field_scores.sort(reverse=True)

    # --- the user's entries ---
    user_entries = db.fetchall(
        "SELECT * FROM dfs_entries WHERE contest_id = ? ORDER BY id DESC", (contest_id,)
    )
    user_rows = []
    best_user = None
    for e in user_entries:
        ids = json.loads(e["player_ids"])
        sc = ctx.score_lineup(ids)
        user_rows.append({"entry_id": e["id"], "pts": sc, "ids": ids})
        if best_user is None or sc > best_user["pts"]:
            best_user = {"entry_id": e["id"], "pts": sc, "ids": ids}

    par = _par_lineup(pool, ctx.live_for, CAP)

    # The synthetic field is a representative *sample*; the contest advertises a
    # much bigger field (thousands), so we scale the user's standing from their
    # position within the sample up to the full field size. The board reads like
    # a packed lobby of characters without scoring thousands of rows.
    sample = sorted(field_scores, reverse=True)
    field_total = int(contest["field_size"]) + len(user_rows)

    your_rank = None
    percentile = None
    if best_user is not None:
        better = sum(1 for s in sample if s >= best_user["pts"])
        frac = better / max(1, len(sample))            # ~0 at the top
        your_rank = max(1, min(field_total, int(round(frac * field_total)) or 1))
        percentile = round(100 * (1 - frac), 1)

    # Top-of-board: the synthetic leaders, each a distinct character handle.
    board = []
    for i, sc in enumerate(sample[:board_size]):
        board.append({
            "rank": i + 1,
            "user": _field_handle(contest["seed"], i),
            "pts": sc, "me": False,
            "win": _payout(contest, i + 1, field_total),
        })
    if best_user is not None:
        board.append({
            "rank": your_rank, "user": "YOU", "pts": best_user["pts"], "me": True,
            "win": _payout(contest, your_rank, field_total),
        })
        board.sort(key=lambda r: r["rank"])

    # cash line ≈ the score at the top-20% boundary of the sample
    cash_idx = min(len(sample) - 1, max(0, len(sample) // 5)) if sample else 0
    cash_line = sample[cash_idx] if sample else 0.0

    # the user's scored lineup detail (best entry) for the lineup panel
    lineup_detail = []
    if best_user:
        for pid in best_user["ids"]:
            dbid = _db_id(pid)
            p = pool.get(dbid)
            if not p:
                continue
            lineup_detail.append({
                "id": p["id"], "name": p["name"], "pos": p["pos"], "team": p["team"],
                "teamColor": p.get("teamColor", ""), "init": p.get("init", ""),
                "opp": p.get("opp", ""), "pts": ctx.live_for(dbid), "done": ctx.done_for(dbid),
            })

    return {
        "contest": {
            "id": contest["id"], "name": contest["name"], "kind": contest["kind"],
            "field_size": contest["field_size"], "color": contest["color"],
            "top_prize": contest["top_prize"],
        },
        "your_rank": your_rank,
        "field_total": field_total,
        "percentile": percentile,
        "your_points": best_user["pts"] if best_user else None,
        "par": par,
        "cash_line": round(cash_line, 1),
        "games_done": played, "games_total": total,
        "board": board,
        "lineup": lineup_detail,
        "has_entry": best_user is not None,
    }


def list_user_entries() -> list[dict]:
    """All of the user's entries with their live rank/points, newest first."""
    ensure_schema()
    rows = db.fetchall(
        """SELECT e.*, c.name AS contest_name, c.color, c.badge, c.kind,
                  c.field_size, c.slate_date AS sdate
           FROM dfs_entries e JOIN dfs_contests c ON e.contest_id = c.id
           ORDER BY e.id DESC"""
    )
    out = []
    res_cache: dict = {}
    ctx_cache: dict = {}
    for e in rows:
        cid = e["contest_id"]
        if cid not in res_cache:
            res_cache[cid] = contest_results(cid)
        res = res_cache[cid]
        sdate = e["sdate"]
        if sdate not in ctx_cache:
            ctx_cache[sdate] = _LiveContext(sdate)
        ctx = ctx_cache[sdate]
        ids = json.loads(e["player_ids"])
        out.append({
            "entry_id": e["id"], "contest": e["contest_name"], "color": e["color"],
            "badge": e["badge"], "kind": e["kind"], "pts": ctx.score_lineup(ids),
            "rank": (res or {}).get("your_rank"), "of": (res or {}).get("field_total"),
            "games_done": ctx.played, "games_total": ctx.total,
            "live": ctx.played < ctx.total, "contest_id": cid,
        })
    return out


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _payout(contest: dict, rank: int, field_total: int) -> int:
    """Toy payout curve (guilders) — top ~20% cash, steeper for GPPs."""
    if field_total <= 0:
        return 0
    pct = rank / field_total
    pool = contest["prize_pool"]
    if contest["kind"] == "Double Up":
        return int(contest["fee"] * 1.8) if pct <= 0.5 else 0
    if pct > 0.2:
        return 0
    if rank == 1:
        return contest["top_prize"]
    # geometric-ish decay across the cashing fifth
    share = max(0.0, (0.2 - pct) / 0.2)
    return int(contest["top_prize"] * 0.04 + share * pool * 0.02)


# Procedural opponent handles. Roots × tails × an optional number give many
# thousands of distinct, in-world usernames, so a big field feels populated
# with characters rather than a handful of repeats. Deterministic per (seed,i).
_HANDLE_ROOTS = [
    "salt", "reef", "tide", "fathom", "keel", "anchor", "drydock", "mistral",
    "galleon", "brigand", "leviathan", "coral", "nickel", "declare", "walkback",
    "stay", "joker", "pilot", "bosun", "skidder", "helms", "arc3", "crore",
    "guilder", "zora", "submarine", "sidearm", "eephus", "knuckle", "spitter",
    "marlin", "harbor", "wreck", "barnacle", "rigging", "lantern", "compass",
    "sloop", "rudder", "kraken",
]
_HANDLE_TAILS = [
    "wind", "_dreams", "light", "wrecker", "_hive", "_andy", "max", "_gus",
    "_king", "_dora", "_bo", "_sue", "man", "_or_go", "_jane", "_phil", "head",
    "runner", "hawk", "_77", "ster", "_iii", "core", "_dan", "fan", "_gal",
]


def _field_handle(seed: int, i: int) -> str:
    n = (int(seed) * 2654435761 + i * 40503) & 0xFFFFFFFF
    root = _HANDLE_ROOTS[n % len(_HANDLE_ROOTS)]
    tail = _HANDLE_TAILS[(n // len(_HANDLE_ROOTS)) % len(_HANDLE_TAILS)]
    h = root + tail
    if (n >> 5) % 3 == 0:
        h += str(2 + (n % 97))
    return h
