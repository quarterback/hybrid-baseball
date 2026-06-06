"""CapSpace (/fantasy) — real-data builder for the Daily Slate.

Turns the active save into the JSON shapes the CapSpace front-end expects
(`window.__CAPSPACE_DATA__`), mirroring the mock layer in
`static/capspace-data.jsx`. Read-only: no engine changes, no new tables.

Scope of this pass (per the build brief): the **player pool** and the
**slate** are real; contests / leaderboard / live scoring stay as the
designed placeholders baked into the JS.

Mapping summary
---------------
* salary   ← computed fresh as a small DOLLAR figure from a ratings overall,
             scaled to the ~$1,000 cap (NOT the game's economy valuations);
             stored as guilders so the currency switcher converts cleanly
* ratings  ← the ``players`` 20-80 block, clamped to [20, 80] for display
* pos      ← engine field positions collapsed to the CapSpace slots
             (P→PILOT, LF/CF/RF→OF, others 1:1)
* proj     ← average DFS fantasy points over the player's recent games,
             with a ratings-based fallback for players who have not played
* log/form ← ``game_batter_stats`` / ``game_pitcher_stats`` joined to
             ``games`` (the same per-game substrate the box score reads)

The DFS scoring rule echoes the ``_batter_game_score`` weights already used
in the main app, expressed here as an uncapped counting score and extended
with the O27-native stay bonuses the design surfaces.
"""

from __future__ import annotations

from o27v2 import db, currency

# CapSpace roster slot for each engine field position. O27 also has a 10th
# fielder — the Nickelfielder (NF) — which has no dedicated CapSpace slot, so
# it collapses into OF (keeps the player draftable + STAY-eligible). Jokers
# (J) are tactical plate appearances, not a lineup slot; they're filtered out
# of the DFS pool below (a future "Joker Draft" format scores them directly).
_POS_MAP = {
    "C": "C", "1B": "1B", "2B": "2B", "3B": "3B", "SS": "SS",
    "LF": "OF", "CF": "OF", "RF": "OF", "OF": "OF", "DH": "OF",
    "NF": "OF",
}

# Team accent palette — the CSS custom properties defined in capspace.css.
# Cycled across the teams on the slate so each gets a stable colour.
_TEAM_COLORS = [
    "var(--c-coral)", "var(--c-teal)", "var(--c-blue)", "var(--c-violet)",
    "var(--c-green)", "var(--c-pink)", "var(--c-lime)", "var(--c-amber)",
]

# DFS batter scoring weights. Standard fantasy-baseball counting stats lead;
# the O27-specific "stay" events are kept as small flavor bonuses, not the
# headline (a typical multi-stay game adds ~1 pt, not ~9). When main lands
# dedicated walk-back / advancement columns we can promote them here.
_W = {
    "single": 4.0, "double": 7.0, "triple": 10.0, "hr": 13.0,
    "bb": 2.0, "hbp": 2.0, "rbi": 2.0, "run": 1.5, "sb": 4.0,
    "k": -1.5,
    "stay": 0.5, "stay_rbi": 1.0,   # O27 flavor — demoted
}


def _clamp_rating(v) -> int:
    """Clamp a raw rating to the 20-80 display scale."""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return 50
    return max(20, min(80, n))


def _map_position(row: dict) -> str:
    if row.get("is_pitcher"):
        return "PILOT"
    return _POS_MAP.get((row.get("position") or "").upper(), "OF")


def _eligible_positions(row: dict) -> list[str]:
    """Every CapSpace slot a player qualifies for. Real players cover multiple
    spots; the engine tracks this in `role_field_pos` (a comma list of field
    positions). Primary position first, then the extra eligibilities, mapped to
    CapSpace slots (LF/CF/RF/DH/NF all collapse to OF) and de-duped."""
    if row.get("is_pitcher"):
        return ["PILOT"]
    out = [_map_position(row)]
    for tok in (row.get("role_field_pos") or "").split(","):
        slot = _POS_MAP.get(tok.strip().upper())
        if slot and slot not in out:
            out.append(slot)
    return out


def _batter_fp(s: dict) -> float:
    """DFS fantasy points for one batter game-stat row."""
    h = s.get("hits", 0) or 0
    d2 = s.get("doubles", 0) or 0
    d3 = s.get("triples", 0) or 0
    hr = s.get("hr", 0) or 0
    singles = max(0, h - d2 - d3 - hr)
    return round(
        _W["single"] * singles
        + _W["double"] * d2
        + _W["triple"] * d3
        + _W["hr"] * hr
        + _W["bb"] * (s.get("bb", 0) or 0)
        + _W["hbp"] * (s.get("hbp", 0) or 0)
        + _W["rbi"] * (s.get("rbi", 0) or 0)
        + _W["run"] * (s.get("runs", 0) or 0)
        + _W["sb"] * (s.get("sb", 0) or 0)
        + _W["stay"] * (s.get("stays", 0) or 0)
        + _W["stay_rbi"] * (s.get("stay_rbi", 0) or 0)
        + _W["k"] * (s.get("k", 0) or 0),
        1,
    )


def _pitcher_fp(s: dict) -> float:
    """DFS fantasy points for one pilot (pitcher) game-stat row.

    No MLB-style win/save in O27; value is the continuous arc — outs
    recorded plus strikeouts, docked for damage allowed. A quality-start
    bonus rewards a starter who goes deep (>=18 outs) with <=3 earned runs,
    the standard-fantasy stand-in for the (nonexistent) pitcher win.
    """
    outs = s.get("outs_recorded", 0) or 0
    er = s.get("er", 0) or 0
    pts = (
        1.0 * outs
        + 3.0 * (s.get("k", 0) or 0)
        - 2.0 * er
        - 1.0 * (s.get("bb", 0) or 0)
        - 3.0 * (s.get("hr_allowed", 0) or 0)
    )
    if (s.get("is_starter") or 0) and outs >= 18 and er <= 3:
        pts += 6.0  # quality start
    return round(pts, 1)


def _proj_from_ratings(row: dict, is_pitcher: bool) -> float:
    """Fallback projection for players with no game log yet."""
    if is_pitcher:
        base = (
            (row.get("command", 50) or 50)
            + (row.get("movement", 50) or 50)
            + (row.get("stamina", 50) or 50)
        ) / 3.0
        # ~26 at a 50 overall, ~42 at 70.
        return round(26.0 + (base - 50.0) * 0.8, 1)
    base = (
        (row.get("contact", 50) or 50)
        + (row.get("power", 50) or 50)
        + (row.get("eye", 50) or 50)
    ) / 3.0
    # ~16 at a 50 overall, ~28 at 70.
    return round(16.0 + (base - 50.0) * 0.6, 1)


def _hitter_line(s: dict) -> str:
    """Compact real-baseball box line for the player drawer: H-AB, then the
    stuff that actually matters — HR, RBI, R, SB, BB."""
    h = s.get("hits", 0) or 0
    ab = s.get("ab", 0) or 0
    parts = [f"{h}-{ab}"]
    hr = s.get("hr", 0) or 0
    if hr:
        parts.append(f"{hr} HR")
    rbi = s.get("rbi", 0) or 0
    if rbi:
        parts.append(f"{rbi} RBI")
    runs = s.get("runs", 0) or 0
    if runs:
        parts.append(f"{runs} R")
    sb = s.get("sb", 0) or 0
    if sb:
        parts.append(f"{sb} SB")
    bb = s.get("bb", 0) or 0
    if bb:
        parts.append(f"{bb} BB")
    return " · ".join(parts)


def _pitcher_line(s: dict) -> str:
    outs = s.get("outs_recorded", 0) or 0
    k = s.get("k", 0) or 0
    er = s.get("er", 0) or 0
    return f"{outs // 3}.{outs % 3} arc · {k}K · {er}ER"


def _slate_date() -> str | None:
    """The date whose games make up tonight's slate.

    Prefer the next date that still has an unplayed game (the live slate);
    fall back to the most recent date with games so the pool is never empty
    on a fully-simmed save.
    """
    row = db.fetchone(
        "SELECT game_date FROM games WHERE played = 0 "
        "ORDER BY game_date, id LIMIT 1"
    )
    if row and row.get("game_date"):
        return row["game_date"]
    row = db.fetchone("SELECT MAX(game_date) AS d FROM games")
    return row["d"] if row and row.get("d") else None


def _fmt_time(start_minute) -> str:
    if start_minute is None:
        return ""
    try:
        m = int(start_minute)
    except (TypeError, ValueError):
        return ""
    h, mm = m // 60, m % 60
    h12 = h % 12 or 12   # 12-hour clock, no meridian (slates are evening games)
    return f"{h12}:{mm:02d}"


# DFS salaries are small, friendly DOLLAR figures (USD is CapSpace's default
# display). We deliberately do NOT surface the game's economy valuations —
# those are season/auction scale. Salaries are computed fresh from a ratings
# overall and mapped into a per-group dollar band sized to the ~$1,000 cap, so
# the priciest pilot is only a few hundred dollars. Stored internally as
# guilders (ƒ100 = $1) so the currency switcher still converts cleanly.
_USD_BANDS = {True: (80, 260), False: (40, 190)}  # (lo, hi) dollars: pilot, hitter
_GUILDER_PER_USD = currency.GUILDER_PER_USD


# Per-position pool depth. A 15-game slate fields the whole league (every
# pitcher on every staff); a DFS pool surfaces the relevant arms and bats, not
# all 500+ pilots. Trim to the top of each position by projection — keeps the
# builder fast and the pool realistic while leaving plenty of choice.
_POOL_CAPS = {"PILOT": 40, "OF": 48, "C": 24, "1B": 24, "2B": 24, "3B": 24, "SS": 24}


def _trim_pool(players: list[dict]) -> list[dict]:
    from collections import defaultdict
    by_pos: dict[str, list[dict]] = defaultdict(list)
    for p in players:
        by_pos[p["pos"]].append(p)
    out: list[dict] = []
    for pos, grp in by_pos.items():
        grp.sort(key=lambda p: p["proj"], reverse=True)
        out.extend(grp[: _POOL_CAPS.get(pos, 24)])
    return out


def _overall(p: dict) -> float:
    """Talent overall = mean of the player's display ratings (20-80)."""
    r = p.get("r") or {}
    return sum(r.values()) / len(r) if r else 50.0


def _calibrate_salaries(players: list[dict]) -> None:
    """Assign DFS dollar salaries from a ratings overall, scaled to the cap.

    Rank players by overall *within* the pilot and hitter groups and map each
    into its dollar band, so salary tracks talent (keeping pts-per-dollar value
    meaningful against recent-form projection) and the whole pool fits a
    ~$1,000 lineup cap with real trade-offs.
    """
    for is_pitcher in (True, False):
        grp = [p for p in players if p["isPitcher"] is is_pitcher]
        if not grp:
            continue
        grp.sort(key=_overall)  # ascending talent
        lo, hi = _USD_BANDS[is_pitcher]
        n = len(grp)
        for i, p in enumerate(grp):
            pct = i / (n - 1) if n > 1 else 1.0
            usd = round((lo + pct * (hi - lo)) / 5) * 5   # nearest $5
            p["salary"] = int(usd * _GUILDER_PER_USD)     # store as guilders


# In-process cache for the slate blob. Building it scans the whole season's
# per-game stat tables for the ~175-player pool (see _build_logs / _season_lines)
# and it is rebuilt many times per request: _safe_slate calls it once, then
# _settle_all fans out into the per-game settle paths (contests._LiveContext,
# sluggers/pitching _benchmark/_slate_entry, streak) which each call it again.
# That repetition is what made /fantasy/api/{wallet,slate,activity} take
# 15-26 s — and the front-end shows a blank shell until /api/wallet returns.
# The blob only changes when a game is played, so cache it per slate_date and
# invalidate on the played-game count (cheap, indexed COUNT). Trades between
# sims can leave the pool marginally stale until the next game settles — an
# acceptable trade for turning a 15 s page into a sub-second one.
_SLATE_CACHE: dict[str, tuple[int, dict]] = {}


def _slate_cache_version() -> int:
    row = db.fetchone("SELECT COUNT(*) AS n FROM games WHERE played = 1")
    return int(row["n"]) if row and row.get("n") is not None else 0


def invalidate_slate_cache() -> None:
    """Drop the memoized slate blobs (e.g. after a reseed in the same process)."""
    _SLATE_CACHE.clear()


def build_slate_data(slate_date: str | None = None) -> dict | None:
    """Build the real CapSpace data blob for a slate (defaults to the current
    next-unplayed slate), or None if the save has no games (the front-end then
    falls back to its bundled mock data). Passing an explicit date lets a
    contest re-derive its own slate's pool even after the slate has advanced.

    Memoized per slate_date and invalidated by the played-game count, so the
    many calls within a single request (settle fan-out + the slate itself)
    rebuild the expensive stat scan at most once per simmed game. A shallow
    copy is returned so callers can stamp CONTESTS / WALLET without polluting
    the cache."""
    if slate_date is None:
        slate_date = _slate_date()
    if not slate_date:
        return None

    version = _slate_cache_version()
    cached = _SLATE_CACHE.get(slate_date)
    if cached and cached[0] == version:
        return dict(cached[1])

    blob = _build_slate_data_uncached(slate_date)
    if blob is not None:
        _SLATE_CACHE[slate_date] = (version, blob)
        return dict(blob)
    return None


def _build_slate_data_uncached(slate_date: str) -> dict | None:
    games = db.fetchall(
        """SELECT g.id, g.game_date, g.start_minute,
                  ht.abbrev AS home, ht.name AS home_name, g.home_team_id,
                  at.abbrev AS away, at.name AS away_name, g.away_team_id
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE g.game_date = ?
           ORDER BY g.id""",
        (slate_date,),
    )
    if not games:
        return None

    # Teams on the slate, with a stable accent colour each.
    team_ids: list[int] = []
    teams: dict[str, dict] = {}
    slate_games: list[dict] = []
    for g in games:
        for abbrev, name, tid in (
            (g["home"], g["home_name"], g["home_team_id"]),
            (g["away"], g["away_name"], g["away_team_id"]),
        ):
            if abbrev not in teams:
                teams[abbrev] = {
                    "name": name,
                    "color": _TEAM_COLORS[len(teams) % len(_TEAM_COLORS)],
                }
                team_ids.append(tid)
        slate_games.append(
            {"away": g["away"], "home": g["home"], "time": _fmt_time(g["start_minute"])}
        )

    if not team_ids:
        return None

    placeholders = ",".join("?" for _ in team_ids)
    rows = db.fetchall(
        f"""SELECT p.*, t.abbrev AS team_abbrev
            FROM players p
            JOIN teams t ON p.team_id = t.id
            WHERE p.team_id IN ({placeholders})
              AND p.is_active = 1
              AND p.is_joker = 0
            ORDER BY p.is_pitcher, p.position""",
        tuple(team_ids),
    )
    if not rows:
        return None

    pids = [r["id"] for r in rows]
    logs = _build_logs(pids)
    bat_lines, pit_lines = _season_lines(pids)

    players: list[dict] = []
    for r in rows:
        is_pitcher = bool(r.get("is_pitcher"))
        pl = logs.get(r["id"], {"log": [], "form": []})

        if pl["form"]:
            proj = round(sum(pl["form"]) / len(pl["form"]), 1)
        else:
            proj = _proj_from_ratings(r, is_pitcher)

        ratings = _ratings_for(r, is_pitcher)

        players.append({
            "id": f"p{r['id']}",
            "name": r["name"],
            "team": r["team_abbrev"],
            "pos": _map_position(r),
            "posEligible": _eligible_positions(r),
            "isPitcher": is_pitcher,
            "salary": 0,  # assigned by _calibrate_salaries (dollar tier)
            "proj": proj,
            "own": 0,  # placeholder until contest entries exist
            "r": ratings,
            "log": pl["log"],
            "form": pl["form"],
            "statline": (pit_lines.get(r["id"]) if is_pitcher else bat_lines.get(r["id"])) or "",
        })

    # Trim the league-wide field to a realistic per-position DFS pool, then
    # price that pool — calibrating after the trim spreads each position across
    # the full dollar band (cheap punts through studs), not just the top tier.
    players = _trim_pool(players)
    _calibrate_salaries(players)

    # Ownership is a placeholder this pass: rank by projection so the field
    # reads plausibly (chalk at the top) without real entry data.
    if players:
        ordered = sorted(players, key=lambda p: p["proj"], reverse=True)
        n = len(ordered)
        for i, p in enumerate(ordered):
            p["own"] = round(6 + (1 - i / max(1, n - 1)) * 38)

    return {
        "TEAMS": teams,
        "SLATE_GAMES": slate_games,
        "PLAYERS": players,
        "SLATE_DATE": slate_date,
    }


def _season_lines(player_ids: list[int]) -> tuple[dict, dict]:
    """Compact season stat lines for the DFS pool, batched. Returns
    (batter_lines, pitcher_lines) keyed by player_id — the at-a-glance stats a
    DFS player actually reads (no ratings)."""
    if not player_ids:
        return {}, {}
    ph = ",".join("?" for _ in player_ids)
    bat, pit = {}, {}
    for r in db.fetchall(
        f"SELECT player_id, SUM(ab) ab, SUM(hits) h, SUM(hr) hr, SUM(rbi) rbi, "
        f"SUM(runs) ru, SUM(sb) sb, SUM(bb) bb, SUM(hbp) hbp, SUM(doubles) d2, SUM(triples) d3 "
        f"FROM game_batter_stats WHERE phase = 0 AND player_id IN ({ph}) GROUP BY player_id",
        tuple(player_ids)):
        ab = r["ab"] or 0
        if ab <= 0:
            continue
        obp = (r["h"] + r["bb"] + r["hbp"]) / max(1, ab + r["bb"] + r["hbp"])
        bat[r["player_id"]] = (f"{r['h'] / ab:.3f} AVG · {obp:.3f} OBP · "
                               f"{r['hr']} HR · {r['rbi']} RBI · {r['sb']} SB")
    for r in db.fetchall(
        f"SELECT player_id, SUM(outs_recorded) outs, SUM(k) k, SUM(er) er, SUM(bb) bb, "
        f"SUM(hits_allowed) ha, "
        f"SUM(CASE WHEN is_starter=1 AND outs_recorded>=18 AND er<=3 THEN 1 ELSE 0 END) qs "
        f"FROM game_pitcher_stats WHERE phase = 0 AND player_id IN ({ph}) GROUP BY player_id",
        tuple(player_ids)):
        outs = r["outs"] or 0
        if outs <= 0:
            continue
        era = 27.0 * r["er"] / outs
        whip = 3.0 * (r["bb"] + r["ha"]) / outs
        pit[r["player_id"]] = (f"{era:.2f} ERA · {whip:.2f} WHIP · {r['k']} K · {r['qs']} QS")
    return bat, pit


def _build_logs(player_ids: list[int]) -> dict:
    """Last-5 game logs + form sparkline per player, from the persisted
    per-game stat tables. Two batch queries, assembled in Python."""
    if not player_ids:
        return {}
    ph = ",".join("?" for _ in player_ids)
    out: dict = {pid: {"log": [], "form": []} for pid in player_ids}
    # Pitchers who also bat must show their PITCHING log, not their batting
    # line — so skip their batter rows below.
    pitcher_ids = {r["id"] for r in db.fetchall(
        f"SELECT id FROM players WHERE id IN ({ph}) AND is_pitcher = 1", tuple(player_ids))}

    # Only the latest 5 played games per player are kept, but the pool spans
    # the whole league — selecting every season row just to slice 5 in Python
    # was the dominant cost of a slate build. A windowed ROW_NUMBER caps each
    # player to 5 rows in SQL (cutting a ~14k-row scan to ~875), leaving the
    # Python assembly below unchanged.
    bat = db.fetchall(
        f"""SELECT * FROM (
                SELECT bs.*, g.game_date,
                       CASE WHEN g.home_team_id = bs.team_id THEN at.abbrev
                            ELSE ht.abbrev END AS opp,
                       ROW_NUMBER() OVER (PARTITION BY bs.player_id
                                          ORDER BY g.game_date DESC, g.id DESC) AS rn
                FROM game_batter_stats bs
                JOIN games g ON bs.game_id = g.id
                JOIN teams ht ON g.home_team_id = ht.id
                JOIN teams at ON g.away_team_id = at.id
                WHERE bs.player_id IN ({ph}) AND g.played = 1 AND bs.phase = 0
            ) WHERE rn <= 5
            ORDER BY game_date DESC, rn""",
        tuple(player_ids),
    )
    pit = db.fetchall(
        f"""SELECT * FROM (
                SELECT ps.*, g.game_date,
                       CASE WHEN g.home_team_id = ps.team_id THEN at.abbrev
                            ELSE ht.abbrev END AS opp,
                       ROW_NUMBER() OVER (PARTITION BY ps.player_id
                                          ORDER BY g.game_date DESC, g.id DESC) AS rn
                FROM game_pitcher_stats ps
                JOIN games g ON ps.game_id = g.id
                JOIN teams ht ON g.home_team_id = ht.id
                JOIN teams at ON g.away_team_id = at.id
                WHERE ps.player_id IN ({ph}) AND g.played = 1 AND ps.phase = 0
            ) WHERE rn <= 5
            ORDER BY game_date DESC, rn""",
        tuple(player_ids),
    )

    def _short_date(d: str) -> str:
        # "2026-06-16" -> "Jun 16" (readable; single-letter months are ambiguous).
        try:
            mm, dd = d.split("-")[1:]
            mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(mm) - 1]
            return f"{mon} {int(dd)}"
        except Exception:
            return d

    for s in bat:
        pid = s["player_id"]
        if pid in pitcher_ids:
            continue  # pitchers show their pitching log, not their bat line
        bucket = out.get(pid)
        if bucket is None or len(bucket["log"]) >= 5:
            continue
        fp = _batter_fp(s)
        bucket["log"].append({
            "date": _short_date(s["game_date"]),
            "opp": s.get("opp", ""),
            "line": _hitter_line(s),
            "fp": fp,
        })
    for s in pit:
        pid = s["player_id"]
        if pid not in pitcher_ids:
            continue  # position players who mopped up keep their batting log
        bucket = out.get(pid)
        if bucket is None or len(bucket["log"]) >= 5:
            continue
        fp = _pitcher_fp(s)
        bucket["log"].append({
            "date": _short_date(s["game_date"]),
            "opp": s.get("opp", ""),
            "line": _pitcher_line(s),
            "fp": fp,
        })

    # Form sparkline = oldest→newest fantasy points (drawer renders L→R).
    for bucket in out.values():
        bucket["form"] = [e["fp"] for e in reversed(bucket["log"])]
    return out


def _ratings_for(r: dict, is_pitcher: bool) -> dict:
    """20-80 scouting ratings mapped from a player's raw attributes."""
    if is_pitcher:
        return {
            "command": _clamp_rating(r.get("command")),
            "stuff": _clamp_rating(r.get("movement")),
            "decay": _clamp_rating(r.get("stamina")),
            "control": _clamp_rating(r.get("pitcher_skill")),
            "late": _clamp_rating(0.6 * (r.get("stamina", 50) or 50)
                                  + 0.4 * (r.get("command", 50) or 50)),
        }
    return {
        "contact": _clamp_rating(r.get("contact")),
        "power": _clamp_rating(r.get("power")),
        "eye": _clamp_rating(r.get("eye")),
        "stay": _clamp_rating(20 + (r.get("stay_aggressiveness", 0.4) or 0.4) * 75),
        "speed": _clamp_rating(r.get("speed")),
        "field": _clamp_rating(r.get("defense")),
    }


def _season_statline(dbid: int, is_pitcher: bool) -> list[dict]:
    """Real season stat line ([{k, v}, ...]) from persisted stats, or [] when
    the player has no game history yet (pre-season)."""
    if is_pitcher:
        r = db.fetchone(
            "SELECT COUNT(*) g, COALESCE(SUM(outs_recorded),0) outs, COALESCE(SUM(k),0) k, "
            "COALESCE(SUM(er),0) er, COALESCE(SUM(bb),0) bb, COALESCE(SUM(hits_allowed),0) ha, "
            "COALESCE(SUM(CASE WHEN is_starter=1 AND outs_recorded>=18 AND er<=3 THEN 1 ELSE 0 END),0) qs "
            "FROM game_pitcher_stats WHERE player_id=? AND phase=0", (dbid,))
        if not r or not r["g"] or not r["outs"]:
            return []
        outs = r["outs"]
        return [
            {"k": "G", "v": r["g"]}, {"k": "IP", "v": round(outs / 3, 1)},
            {"k": "ERA", "v": f"{27.0 * r['er'] / outs:.2f}"},
            {"k": "WHIP", "v": f"{3.0 * (r['bb'] + r['ha']) / outs:.2f}"},
            {"k": "K", "v": r["k"]}, {"k": "QS", "v": r["qs"]},
        ]
    r = db.fetchone(
        "SELECT COUNT(*) g, COALESCE(SUM(ab),0) ab, COALESCE(SUM(hits),0) h, "
        "COALESCE(SUM(doubles),0) d2, COALESCE(SUM(triples),0) d3, COALESCE(SUM(hr),0) hr, "
        "COALESCE(SUM(rbi),0) rbi, COALESCE(SUM(runs),0) ru, COALESCE(SUM(sb),0) sb, "
        "COALESCE(SUM(bb),0) bb, COALESCE(SUM(hbp),0) hbp FROM game_batter_stats "
        "WHERE player_id=? AND phase=0", (dbid,))
    if not r or not r["g"] or not r["ab"]:
        return []
    ab = r["ab"]
    obp = (r["h"] + r["bb"] + r["hbp"]) / max(1, ab + r["bb"] + r["hbp"])
    return [
        {"k": "G", "v": r["g"]}, {"k": "AVG", "v": f"{r['h'] / ab:.3f}"},
        {"k": "OBP", "v": f"{obp:.3f}"},
        {"k": "SLG", "v": f"{(r['h'] + r['d2'] + 2 * r['d3'] + 3 * r['hr']) / ab:.3f}"},
        {"k": "HR", "v": r["hr"]}, {"k": "RBI", "v": r["rbi"]},
        {"k": "R", "v": r["ru"]}, {"k": "SB", "v": r["sb"]},
    ]


def player_card(dbid: int) -> dict | None:
    """Full player-card payload for the fantasy drawer: real season stats,
    recent logs, 20-80 ratings (talent context), and a link to the almanac."""
    row = db.fetchone(
        "SELECT p.*, t.abbrev AS team, t.name AS team_name FROM players p "
        "JOIN teams t ON p.team_id = t.id WHERE p.id = ?", (dbid,))
    if not row:
        return None
    is_pitcher = bool(row.get("is_pitcher"))
    logs = _build_logs([dbid]).get(dbid, {"log": [], "form": []})
    proj = (round(sum(logs["form"]) / len(logs["form"]), 1)
            if logs["form"] else _proj_from_ratings(row, is_pitcher))
    return {
        "id": f"p{dbid}", "name": row["name"], "team": row["team"],
        "teamName": row["team_name"], "pos": _map_position(row),
        "isPitcher": is_pitcher, "proj": proj,
        "r": _ratings_for(row, is_pitcher),
        "stats": _season_statline(dbid, is_pitcher),
        "log": logs["log"], "form": logs["form"],
        "almanac": f"/player/{dbid}",
    }
