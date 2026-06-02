"""CapSpace (/fantasy) — real-data builder for the Daily Slate.

Turns the active save into the JSON shapes the CapSpace front-end expects
(`window.__CAPSPACE_DATA__`), mirroring the mock layer in
`static/capspace-data.jsx`. Read-only: no engine changes, no new tables.

Scope of this pass (per the build brief): the **player pool** and the
**slate** are real; contests / leaderboard / live scoring stay as the
designed placeholders baked into the JS.

Mapping summary
---------------
* salary   ← ``valuation.estimate_player_value`` (persisted ƒ salary, else
             derived from trade value)
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

from o27v2 import db, valuation

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

# DFS batter scoring weights (mirror of the bGSc coefficient vector, plus the
# stay bonuses CapSpace shows in its scoring panel). Kept here so the proj /
# game-log fantasy points match the rule rendered in the UI.
_W = {
    "single": 4.0, "double": 7.0, "triple": 10.0, "hr": 13.0,
    "bb": 2.0, "rbi": 2.0, "run": 1.5, "stay": 3.0, "stay_rbi": 4.0,
    "k": -1.5,
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
        + _W["rbi"] * (s.get("rbi", 0) or 0)
        + _W["run"] * (s.get("runs", 0) or 0)
        + _W["stay"] * (s.get("stays", 0) or 0)
        + _W["stay_rbi"] * (s.get("stay_rbi", 0) or 0)
        + _W["k"] * (s.get("k", 0) or 0),
        1,
    )


def _pitcher_fp(s: dict) -> float:
    """DFS fantasy points for one pilot (pitcher) game-stat row.

    No MLB-style win/save in O27; value is the continuous arc — outs
    recorded plus strikeouts, docked for damage allowed.
    """
    return round(
        1.0 * (s.get("outs_recorded", 0) or 0)
        + 3.0 * (s.get("k", 0) or 0)
        - 2.0 * (s.get("er", 0) or 0)
        - 1.0 * (s.get("bb", 0) or 0)
        - 3.0 * (s.get("hr_allowed", 0) or 0),
        1,
    )


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
    """Compact box line for the player drawer — O27-flavoured."""
    h = s.get("hits", 0) or 0
    ab = s.get("ab", 0) or 0
    hr = s.get("hr", 0) or 0
    stays = s.get("stays", 0) or 0
    parts = [f"{h}-{ab}"]
    if hr:
        parts.append(f"{hr}HR")
    if stays:
        parts.append(f"{stays} stay")
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


# DFS salary bands (in guilders), sized to the ƒ1 crore cap so an 8-slot
# lineup is buildable with real trade-offs: stacking studs (~18L pilot +
# 15L hitters) blows the cap; a balanced build comes in comfortably under.
_LAKH = 1_00_000
_SAL_BANDS = {True: (8 * _LAKH, 18 * _LAKH), False: (5 * _LAKH, 15 * _LAKH)}


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


def _calibrate_salaries(players: list[dict]) -> None:
    """Rescale league-economy valuations (crores) into a DFS salary tier.

    ``estimate_player_value`` returns season/auction-scale guilder figures —
    far larger than CapSpace's ƒ1 crore daily cap. We rank players by that
    talent signal *within* the pilot and hitter groups and map each into a
    band, so salary tracks talent (keeping pts/ƒ value meaningful against the
    recent-form projection) while the whole pool fits the cap.
    """
    for is_pitcher in (True, False):
        grp = [p for p in players if p["isPitcher"] is is_pitcher]
        if not grp:
            continue
        grp.sort(key=lambda p: p["salary"])  # ascending raw valuation
        lo, hi = _SAL_BANDS[is_pitcher]
        n = len(grp)
        for i, p in enumerate(grp):
            pct = i / (n - 1) if n > 1 else 1.0
            sal = lo + pct * (hi - lo)
            p["salary"] = int(round(sal / 10_000) * 10_000)  # nearest 0.1 lakh


def build_slate_data() -> dict | None:
    """Build the real CapSpace data blob, or None if the save has no games
    (the front-end then falls back to its bundled mock data)."""
    slate_date = _slate_date()
    if not slate_date:
        return None

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

    players: list[dict] = []
    for r in rows:
        is_pitcher = bool(r.get("is_pitcher"))
        pl = logs.get(r["id"], {"log": [], "form": []})

        if pl["form"]:
            proj = round(sum(pl["form"]) / len(pl["form"]), 1)
        else:
            proj = _proj_from_ratings(r, is_pitcher)

        if is_pitcher:
            ratings = {
                "command": _clamp_rating(r.get("command")),
                "stuff": _clamp_rating(r.get("movement")),
                "decay": _clamp_rating(r.get("stamina")),
                "control": _clamp_rating(r.get("pitcher_skill")),
                "late": _clamp_rating(
                    0.6 * (r.get("stamina", 50) or 50)
                    + 0.4 * (r.get("command", 50) or 50)
                ),
            }
        else:
            ratings = {
                "contact": _clamp_rating(r.get("contact")),
                "power": _clamp_rating(r.get("power")),
                "eye": _clamp_rating(r.get("eye")),
                "stay": _clamp_rating(20 + (r.get("stay_aggressiveness", 0.4) or 0.4) * 75),
                "speed": _clamp_rating(r.get("speed")),
                "field": _clamp_rating(r.get("defense")),
            }

        players.append({
            "id": f"p{r['id']}",
            "name": r["name"],
            "team": r["team_abbrev"],
            "pos": _map_position(r),
            "isPitcher": is_pitcher,
            "salary": int(valuation.estimate_player_value(dict(r))),
            "proj": proj,
            "own": 0,  # placeholder until contest entries exist
            "r": ratings,
            "log": pl["log"],
            "form": pl["form"],
        })

    # Rescale salaries from league economy into the DFS cap tier, then trim
    # the league-wide field down to a realistic per-position DFS pool.
    _calibrate_salaries(players)
    players = _trim_pool(players)

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


def _build_logs(player_ids: list[int]) -> dict:
    """Last-5 game logs + form sparkline per player, from the persisted
    per-game stat tables. Two batch queries, assembled in Python."""
    if not player_ids:
        return {}
    ph = ",".join("?" for _ in player_ids)
    out: dict = {pid: {"log": [], "form": []} for pid in player_ids}

    bat = db.fetchall(
        f"""SELECT bs.*, g.game_date,
                   CASE WHEN g.home_team_id = bs.team_id THEN at.abbrev
                        ELSE ht.abbrev END AS opp
            FROM game_batter_stats bs
            JOIN games g ON bs.game_id = g.id
            JOIN teams ht ON g.home_team_id = ht.id
            JOIN teams at ON g.away_team_id = at.id
            WHERE bs.player_id IN ({ph}) AND g.played = 1 AND bs.phase = 0
            ORDER BY g.game_date DESC, g.id DESC""",
        tuple(player_ids),
    )
    pit = db.fetchall(
        f"""SELECT ps.*, g.game_date,
                   CASE WHEN g.home_team_id = ps.team_id THEN at.abbrev
                        ELSE ht.abbrev END AS opp
            FROM game_pitcher_stats ps
            JOIN games g ON ps.game_id = g.id
            JOIN teams ht ON g.home_team_id = ht.id
            JOIN teams at ON g.away_team_id = at.id
            WHERE ps.player_id IN ({ph}) AND g.played = 1 AND ps.phase = 0
            ORDER BY g.game_date DESC, g.id DESC""",
        tuple(player_ids),
    )

    def _short_date(d: str) -> str:
        # "2026-06-16" -> "J16"-ish compact tag; fall back to the raw tail.
        try:
            mm, dd = d.split("-")[1:]
            month = "JFMAMJJASOND"[int(mm) - 1]
            return f"{month}{int(dd)}"
        except Exception:
            return d[-3:]

    for s in bat:
        pid = s["player_id"]
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
