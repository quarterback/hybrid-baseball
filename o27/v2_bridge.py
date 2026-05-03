"""
o27/v2_bridge.py — Read-only bridge from o27v2 SQLite DB to the o27 stats site.

When the o27v2 league simulator is the primary game source, the /stats site
must read live league data (standings, schedule, box scores, leaders, rosters)
from o27v2/o27v2.db instead of from o27.data._RECENT / _GAMES.

Each helper here mirrors the dict shape produced by the matching helper in
o27/data.py so the existing Jinja templates render unchanged.

Activation rule (see is_active()): the bridge is "on" whenever the o27v2 DB
file exists and contains at least one team row. Callers in o27/data.py check
is_active() first and delegate when true.

Game-id encoding: o27v2 games have integer primary keys; we expose them to
the stats site as decimal strings (e.g. "42"). The legacy in-memory game-id
shape is "{seed}_{ABB}_{ABB}" so the two never collide.
"""
from __future__ import annotations

import os
from typing import Optional

from o27v2 import db as _v2db


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "o27v2", "o27v2.db")


def is_active() -> bool:
    """True iff the o27v2 DB exists and has at least one team row."""
    if not os.path.exists(_DB_PATH):
        return False
    try:
        row = _v2db.fetchone("SELECT COUNT(*) AS n FROM teams")
        return bool(row and row["n"] > 0)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    return name.lower().replace(" ", "_").replace(".", "")


def _player_id(team_abbrev: str, player_name: str) -> str:
    return f"{team_abbrev}_{player_name.replace(' ', '_').replace('.', '')}"


def _team_display(t: dict) -> str:
    city = (t.get("city") or "").strip()
    name = (t.get("name") or "").strip()
    return f"{city} {name}".strip()


def _player_to_dict(p: dict) -> dict:
    """Convert a players-table row to the shape o27.data.load_teams() returns."""
    archetype = (p.get("archetype") or "").strip()
    is_joker = bool(p.get("is_joker"))
    pos = p.get("position") or ""
    if is_joker and archetype:
        pos = f"JKR-{archetype[:3]}"
    return {
        "name": p["name"],
        "position": pos,
        "is_pitcher": bool(p.get("is_pitcher")),
        "is_joker": is_joker,
        "joker_archetype": archetype,
        "skill": float(p.get("skill") or 0.0),
        "speed": float(p.get("speed") or 0.0),
        "pitcher_skill": float(p.get("pitcher_skill") or 0.0),
        "stay_aggressiveness": float(p.get("stay_aggressiveness") or 0.0),
        "contact_quality_threshold": float(p.get("contact_quality_threshold") or 0.0),
    }


_TEAM_BY_ID_CACHE: dict[int, dict] | None = None
_TEAM_BY_ABB_CACHE: dict[str, dict] | None = None


def _team_index() -> tuple[dict[int, dict], dict[str, dict]]:
    """Cache lookup maps team-id → team row, abbrev → team row.

    Cached for the lifetime of the process; cheap to rebuild if needed via
    invalidate_caches() — currently only called by tests.
    """
    global _TEAM_BY_ID_CACHE, _TEAM_BY_ABB_CACHE
    if _TEAM_BY_ID_CACHE is None or _TEAM_BY_ABB_CACHE is None:
        rows = _v2db.fetchall("SELECT * FROM teams")
        _TEAM_BY_ID_CACHE = {r["id"]: r for r in rows}
        _TEAM_BY_ABB_CACHE = {r["abbrev"]: r for r in rows}
    return _TEAM_BY_ID_CACHE, _TEAM_BY_ABB_CACHE


def invalidate_caches() -> None:
    """Drop cached team maps. Call after seed_league or DB resets."""
    global _TEAM_BY_ID_CACHE, _TEAM_BY_ABB_CACHE
    _TEAM_BY_ID_CACHE = None
    _TEAM_BY_ABB_CACHE = None


# ---------------------------------------------------------------------------
# Teams / players
# ---------------------------------------------------------------------------

def load_teams() -> list[dict]:
    teams_rows = _v2db.fetchall("SELECT * FROM teams ORDER BY abbrev")
    players_rows = _v2db.fetchall(
        "SELECT * FROM players ORDER BY team_id, is_joker, is_pitcher, id"
    )
    by_team: dict[int, list[dict]] = {}
    for p in players_rows:
        by_team.setdefault(p["team_id"], []).append(_player_to_dict(p))

    out: list[dict] = []
    for t in teams_rows:
        out.append({
            "abbrev": t["abbrev"],
            "name": t["name"],
            "city": t.get("city", ""),
            "level": t.get("league") or "MLB",
            "display": _team_display(t),
            "players": by_team.get(t["id"], []),
        })
    return out


def get_team(abbrev: str) -> Optional[dict]:
    _, by_abb = _team_index()
    t = by_abb.get(abbrev)
    if not t:
        return None
    players = _v2db.fetchall(
        "SELECT * FROM players WHERE team_id = ? ORDER BY is_joker, is_pitcher, id",
        (t["id"],),
    )
    return {
        "abbrev": t["abbrev"],
        "name": t["name"],
        "city": t.get("city", ""),
        "level": t.get("league") or "MLB",
        "display": _team_display(t),
        "players": [_player_to_dict(p) for p in players],
    }


def get_player(player_id: str) -> Optional[dict]:
    """Resolve canonical 'ABBREV_Name_With_Underscores' → {team, player, player_id}."""
    if "_" not in player_id:
        return None
    abbrev, _, _ = player_id.partition("_")
    team = get_team(abbrev)
    if not team:
        return None
    for p in team["players"]:
        if _player_id(abbrev, p["name"]) == player_id:
            return {"team": team, "player": p, "player_id": player_id}
    return None


def get_player_by_team_slug(team_abbrev: str, slug: str) -> Optional[dict]:
    team = get_team(team_abbrev)
    if not team:
        return None
    for p in team["players"]:
        if _slugify(p["name"]) == slug:
            return {
                "team": team,
                "player": p,
                "player_id": _player_id(team_abbrev, p["name"]),
            }
    return None


# ---------------------------------------------------------------------------
# Counts / activity
# ---------------------------------------------------------------------------

def has_data() -> bool:
    """True iff at least one v2 game has been played."""
    row = _v2db.fetchone("SELECT COUNT(*) AS n FROM games WHERE played = 1")
    return bool(row and row["n"] > 0)


def total_games() -> int:
    row = _v2db.fetchone("SELECT COUNT(*) AS n FROM games WHERE played = 1")
    return int(row["n"]) if row else 0


def recent_game_ids(limit: int = 200) -> list[str]:
    """Return played game IDs newest → oldest, for prev/next navigation."""
    rows = _v2db.fetchall(
        "SELECT id FROM games WHERE played = 1 "
        "ORDER BY game_date DESC, id DESC LIMIT ?",
        (limit,),
    )
    return [str(r["id"]) for r in rows]


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

def get_standings() -> list[dict]:
    """Compute standings + L10 + streak directly from played games."""
    by_id, _ = _team_index()
    record: dict[int, dict] = {}
    team_hist: dict[int, list[bool]] = {}

    games = _v2db.fetchall(
        "SELECT id, home_team_id, away_team_id, home_score, away_score, winner_id "
        "FROM games WHERE played = 1 ORDER BY game_date, id"
    )

    for g in games:
        ht_id = g["home_team_id"]
        at_id = g["away_team_id"]
        hs = g["home_score"] or 0
        as_ = g["away_score"] or 0
        winner = g["winner_id"]

        for tid, rf, ra, won in [
            (at_id, as_, hs, winner == at_id),
            (ht_id, hs, as_, winner == ht_id),
        ]:
            if tid not in record:
                t = by_id.get(tid, {})
                record[tid] = {
                    "abbrev": t.get("abbrev", "?"),
                    "name":   _team_display(t) if t else "?",
                    "level":  t.get("league") or "—",
                    "w": 0, "l": 0, "r_for": 0, "r_against": 0,
                }
                team_hist[tid] = []
            r = record[tid]
            r["r_for"] += rf
            r["r_against"] += ra
            if won:
                r["w"] += 1
            else:
                r["l"] += 1
            team_hist[tid].append(won)

    # Include team rows that have no games yet
    for tid, t in by_id.items():
        if tid not in record:
            record[tid] = {
                "abbrev": t["abbrev"],
                "name":   _team_display(t),
                "level":  t.get("league") or "—",
                "w": 0, "l": 0, "r_for": 0, "r_against": 0,
            }
            team_hist[tid] = []

    rows = list(record.values())
    for tid, r in zip(record.keys(), rows):
        gp = r["w"] + r["l"]
        r["gp"]  = gp
        r["pct"] = r["w"] / gp if gp else 0.0
        r["rd"]  = r["r_for"] - r["r_against"]
        r["rpg"]  = (r["r_for"] / gp) if gp else 0.0
        r["rapg"] = (r["r_against"] / gp) if gp else 0.0

        hist = team_hist.get(tid, [])
        last10 = hist[-10:]
        r["l10_w"] = sum(1 for x in last10 if x)
        r["l10_l"] = len(last10) - r["l10_w"]

        streak_n = 0
        if hist:
            cur = hist[-1]
            for result in reversed(hist):
                if result == cur:
                    streak_n += 1
                else:
                    break
        r["streak"] = (
            (f"W{streak_n}" if hist[-1] else f"L{streak_n}")
            if (hist and streak_n > 0) else "—"
        )

    rows.sort(key=lambda x: (-x["pct"], -x["rd"]))
    if rows:
        lw = rows[0]["w"]
        ll = rows[0]["l"]
        for r in rows:
            gb = ((lw - r["w"]) + (r["l"] - ll)) / 2
            r["gb"] = "—" if gb <= 0 else (
                str(int(gb)) if gb == int(gb) else f"{gb:.1f}"
            )
    return rows


# ---------------------------------------------------------------------------
# Game / schedule
# ---------------------------------------------------------------------------

def _game_summary_row(g: dict) -> dict:
    """Build the dict shape get_schedule() / get_game() expose to templates.

    Does NOT include batting/pitching detail — see get_game() for that.
    """
    by_id, _ = _team_index()
    home = by_id.get(g["home_team_id"], {})
    away = by_id.get(g["away_team_id"], {})
    winner = g.get("winner_id")
    if winner == g["home_team_id"]:
        winner_id = "home"
    elif winner == g["away_team_id"]:
        winner_id = "visitors"
    else:
        winner_id = ""
    return {
        "game_id": str(g["id"]),
        "seed": g.get("seed") or 0,
        "date": g.get("game_date", ""),
        "visitors_abbrev": away.get("abbrev", ""),
        "home_abbrev":     home.get("abbrev", ""),
        "visitors_name":   _team_display(away),
        "home_name":       _team_display(home),
        "v_score": g.get("away_score") or 0,
        "h_score": g.get("home_score") or 0,
        "winner_id": winner_id,
        "super_flag": bool(g.get("super_inning")),
    }


def get_schedule(limit: int = 40, team: str = "") -> list[dict]:
    sql = (
        "SELECT * FROM games WHERE played = 1 "
    )
    params: list = []
    if team:
        _, by_abb = _team_index()
        t = by_abb.get(team)
        if t is None:
            return []
        sql += "AND (home_team_id = ? OR away_team_id = ?) "
        params += [t["id"], t["id"]]
    sql += "ORDER BY game_date DESC, id DESC LIMIT ?"
    params.append(limit)
    games = _v2db.fetchall(sql, tuple(params))

    out = []
    for g in games:
        row = _game_summary_row(g)
        # Schedule view expects v_batting / winner_pitcher; populate lightly.
        row["v_batting"] = _batters_for(g["id"], g["away_team_id"])
        row["h_batting"] = _batters_for(g["id"], g["home_team_id"])
        row["winner_pitcher"] = _winning_pitcher_name(g)
        out.append(row)
    return out


def _batters_for(game_id: int, team_id: int) -> list[dict]:
    rows = _v2db.fetchall(
        """SELECT bs.*, p.name AS player_name, p.position, p.is_joker, p.archetype
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           WHERE bs.game_id = ? AND bs.team_id = ?
           ORDER BY bs.id""",
        (game_id, team_id),
    )
    out: list[dict] = []
    for r in rows:
        ab = r["ab"] or 0
        hits = r["hits"] or 0
        archetype = (r.get("archetype") or "").strip()
        is_joker = bool(r.get("is_joker"))
        pos = r.get("position") or ""
        if is_joker and archetype:
            pos = f"JKR-{archetype[:3]}"
        out.append({
            "name":     r["player_name"],
            "pos":      pos,
            "is_joker": is_joker,
            "archetype": archetype,
            "pa":      r["pa"]      or 0,
            "ab":      ab,
            "runs":    r["runs"]    or 0,
            "hits":    hits,
            "doubles": r["doubles"] or 0,
            "triples": r["triples"] or 0,
            "hr":      r["hr"]      or 0,
            "rbi":     r["rbi"]     or 0,
            "bb":      r["bb"]      or 0,
            "k":       r["k"]       or 0,
            "stays":   r["stays"]   or 0,
            "hbp":     0,
            "or_":     0,
            "avg":     f"{hits/ab:.3f}" if ab > 0 else ".000",
            "h_ab":    f"{hits/ab:.3f}" if ab > 0 else ".000",
        })
    return out


def _pitchers_for(game_id: int, team_id: int) -> list[dict]:
    rows = _v2db.fetchall(
        """SELECT ps.*, p.name AS player_name
           FROM game_pitcher_stats ps
           JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ?
           ORDER BY ps.id""",
        (game_id, team_id),
    )
    out: list[dict] = []
    for r in rows:
        outs = r["outs_recorded"] or 0
        runs = r["runs_allowed"] or 0
        h    = r["hits_allowed"] or 0
        bb   = r["bb"] or 0
        k    = r["k"]  or 0
        bf   = r["batters_faced"] or 0
        out.append({
            "name": r["player_name"],
            "bf":   bf,
            "outs": outs,
            "r":    runs,
            "er":   runs,
            "h":    h,
            "bb":   bb,
            "k":    k,
            "hr":   0,
            "p":    0,
            "out":  outs,
            "os_pct": f"{round(outs / 27 * 100)}%" if outs > 0 else "0%",
            "era":  f"{runs / outs * 27:.2f}" if outs > 0 else "—",
            "whip": f"{(bb + h) / outs * 27:.2f}" if outs > 0 else "—",
        })
    return out


def _winning_pitcher_name(g: dict) -> str:
    """Pick pitcher with most outs on the winning team (matches o27 convention)."""
    winner = g.get("winner_id")
    if not winner:
        return "—"
    rows = _v2db.fetchall(
        """SELECT ps.outs_recorded AS outs, p.name
           FROM game_pitcher_stats ps
           JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ?
           ORDER BY ps.outs_recorded DESC LIMIT 1""",
        (g["id"], winner),
    )
    return rows[0]["name"] if rows else "—"


def _losing_pitcher_name(g: dict) -> str:
    winner = g.get("winner_id")
    if not winner:
        return "—"
    loser = (g["home_team_id"]
             if winner == g["away_team_id"] else g["away_team_id"])
    rows = _v2db.fetchall(
        """SELECT ps.runs_allowed AS r, p.name
           FROM game_pitcher_stats ps
           JOIN players p ON ps.player_id = p.id
           WHERE ps.game_id = ? AND ps.team_id = ?
           ORDER BY ps.runs_allowed DESC LIMIT 1""",
        (g["id"], loser),
    )
    return rows[0]["name"] if rows else "—"


def get_game(game_id: str) -> Optional[dict]:
    try:
        gid = int(game_id)
    except (TypeError, ValueError):
        return None
    g = _v2db.fetchone("SELECT * FROM games WHERE id = ?", (gid,))
    if not g or not g["played"]:
        return None
    out = _game_summary_row(g)
    out["v_batting"] = _batters_for(gid, g["away_team_id"])
    out["h_batting"] = _batters_for(gid, g["home_team_id"])
    out["v_pitching"] = _pitchers_for(gid, g["away_team_id"])
    out["h_pitching"] = _pitchers_for(gid, g["home_team_id"])
    out["winner_pitcher"] = _winning_pitcher_name(g)
    out["loser_pitcher"]  = _losing_pitcher_name(g)
    return out


# ---------------------------------------------------------------------------
# Leaders
# ---------------------------------------------------------------------------

def get_leaders(stat: str = "hits", limit: int = 10) -> list[dict]:
    rows = _v2db.fetchall(
        """SELECT p.name AS name, t.abbrev AS team,
                  SUM(bs.pa) AS pa, SUM(bs.ab) AS ab,
                  SUM(bs.hits) AS hits, SUM(bs.hr) AS hr,
                  SUM(bs.rbi) AS rbi, SUM(bs.bb) AS bb,
                  SUM(bs.k) AS k, SUM(bs.runs) AS runs
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           JOIN teams   t ON bs.team_id   = t.id
           GROUP BY p.id"""
    )
    for r in rows:
        ab = r["ab"] or 0
        hits = r["hits"] or 0
        r["pa"]   = r["pa"] or 0
        r["ab"]   = ab
        r["hits"] = hits
        r["hr"]   = r["hr"] or 0
        r["rbi"]  = r["rbi"] or 0
        r["bb"]   = r["bb"] or 0
        r["k"]    = r["k"] or 0
        r["runs"] = r["runs"] or 0
        r["or_"]  = 0
        r["avg"]  = (hits / ab) if ab > 0 else 0.0
        r["h_ab"] = r["avg"]

    if stat == "avg":
        rows = [r for r in rows if r["ab"] >= 3]
        rows.sort(key=lambda x: -x["avg"])
    elif stat == "h_ab":
        rows = [r for r in rows if r["ab"] >= 3]
        rows.sort(key=lambda x: -x["h_ab"])
    else:
        rows.sort(key=lambda x: -x.get(stat, 0))
    return rows[:limit]


def get_pitching_leaders(stat: str = "k", limit: int = 10) -> list[dict]:
    rows = _v2db.fetchall(
        """SELECT p.name AS name, t.abbrev AS team,
                  COUNT(ps.game_id) AS g,
                  SUM(ps.batters_faced)  AS bf,
                  SUM(ps.outs_recorded)  AS outs,
                  SUM(ps.hits_allowed)   AS h,
                  SUM(ps.runs_allowed)   AS r,
                  SUM(ps.bb) AS bb,
                  SUM(ps.k)  AS k
           FROM game_pitcher_stats ps
           JOIN players p ON ps.player_id = p.id
           JOIN teams   t ON ps.team_id   = t.id
           GROUP BY p.id"""
    )
    for r in rows:
        outs = r["outs"] or 0
        g    = max(r["g"] or 0, 1)
        runs = r["r"]    or 0
        h    = r["h"]    or 0
        bb   = r["bb"]   or 0
        k    = r["k"]    or 0
        r["outs"] = outs
        r["r"]    = runs
        r["h"]    = h
        r["bb"]   = bb
        r["k"]    = k
        r["hr"]   = 0
        r["p"]    = 0
        r["out_sum"] = 0
        r["os_pct"] = f"{round(outs / (g * 27) * 100)}%" if outs > 0 else "0%"
        r["era"]    = round(runs / outs * 27, 2) if outs > 0 else 99.99
        r["whip"]   = round((bb + h) / outs * 27, 2) if outs > 0 else 99.99
        r["aor"]    = 0.0
        r["k9"]     = round(k  / outs * 27, 2) if outs > 0 else 0.0
        r["bb9"]    = round(bb / outs * 27, 2) if outs > 0 else 0.0
        ip = outs / 3.0
        r["fip"] = round((3 * bb - 2 * k) / ip + 11.50, 2) if ip > 0 else 0.0

    if stat == "era":
        rows = [r for r in rows if r["outs"] >= 9]
        rows.sort(key=lambda x: x["era"])
    elif stat == "whip":
        rows = [r for r in rows if r["outs"] >= 9]
        rows.sort(key=lambda x: x["whip"])
    elif stat == "outs":
        rows.sort(key=lambda x: -x["outs"])
    else:
        rows.sort(key=lambda x: -x.get(stat, 0))
    return rows[:limit]


def get_stays_leaders(limit: int = 10) -> list[dict]:
    rows = _v2db.fetchall(
        """SELECT p.name AS name, t.abbrev AS team,
                  SUM(bs.stays) AS stays
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           JOIN teams   t ON bs.team_id   = t.id
           GROUP BY p.id
           HAVING SUM(bs.stays) > 0
           ORDER BY stays DESC LIMIT ?""",
        (limit,),
    )
    for r in rows:
        r["stays"] = r["stays"] or 0
    return rows


def get_wins_leaders(limit: int = 10) -> list[dict]:
    """Wins = pitcher with most outs on the winning team of each played game."""
    games = _v2db.fetchall(
        "SELECT id, home_team_id, away_team_id, winner_id "
        "FROM games WHERE played = 1 AND winner_id IS NOT NULL"
    )
    wins: dict[str, dict] = {}
    by_id, _ = _team_index()
    for g in games:
        winner = g["winner_id"]
        rows = _v2db.fetchall(
            """SELECT p.name AS name, ps.outs_recorded AS outs
               FROM game_pitcher_stats ps
               JOIN players p ON ps.player_id = p.id
               WHERE ps.game_id = ? AND ps.team_id = ?
               ORDER BY ps.outs_recorded DESC LIMIT 1""",
            (g["id"], winner),
        )
        if not rows:
            continue
        wp = rows[0]["name"]
        team_abbrev = by_id.get(winner, {}).get("abbrev", "")
        key = f"{wp}|{team_abbrev}"
        if key not in wins:
            wins[key] = {"name": wp, "team": team_abbrev, "wins": 0, "g": 0}
        wins[key]["wins"] += 1
        wins[key]["g"]    += 1
    out = sorted(wins.values(), key=lambda x: -x["wins"])
    return out[:limit]


# ---------------------------------------------------------------------------
# Team / player aggregates
# ---------------------------------------------------------------------------

def _team_id_for(abbrev: str) -> Optional[int]:
    _, by_abb = _team_index()
    t = by_abb.get(abbrev)
    return t["id"] if t else None


def get_team_batting(abbrev: str) -> list[dict]:
    tid = _team_id_for(abbrev)
    if tid is None:
        return []
    rows = _v2db.fetchall(
        """SELECT p.name AS name, p.position, p.is_joker, p.archetype,
                  COUNT(CASE WHEN bs.pa > 0 THEN 1 END) AS gp,
                  SUM(bs.pa) AS pa, SUM(bs.ab) AS ab,
                  SUM(bs.runs) AS runs, SUM(bs.hits) AS hits,
                  SUM(bs.doubles) AS doubles, SUM(bs.triples) AS triples,
                  SUM(bs.hr) AS hr, SUM(bs.rbi) AS rbi,
                  SUM(bs.bb) AS bb, SUM(bs.k) AS k,
                  SUM(bs.stays) AS stays
           FROM game_batter_stats bs
           JOIN players p ON bs.player_id = p.id
           WHERE bs.team_id = ?
           GROUP BY p.id
           ORDER BY pa DESC, p.name""",
        (tid,),
    )
    out: list[dict] = []
    for r in rows:
        ab = r["ab"] or 0
        hits = r["hits"] or 0
        archetype = (r.get("archetype") or "").strip()
        is_joker = bool(r.get("is_joker"))
        pos = r.get("position") or ""
        if is_joker and archetype:
            pos = f"JKR-{archetype[:3]}"
        out.append({
            "name": r["name"], "pos": pos,
            "is_joker": is_joker, "archetype": archetype,
            "gp": r["gp"] or 0,
            "pa": r["pa"] or 0, "ab": ab,
            "runs":    r["runs"]    or 0,
            "hits":    hits,
            "doubles": r["doubles"] or 0,
            "triples": r["triples"] or 0,
            "hr": r["hr"] or 0, "rbi": r["rbi"] or 0,
            "bb": r["bb"] or 0, "k": r["k"] or 0,
            "stays": r["stays"] or 0,
            "hbp": 0, "or_": 0,
            "avg": f"{hits/ab:.3f}" if ab > 0 else ".000",
        })
    return out


def get_team_pitching(abbrev: str) -> list[dict]:
    tid = _team_id_for(abbrev)
    if tid is None:
        return []
    rows = _v2db.fetchall(
        """SELECT p.name AS name,
                  COUNT(ps.game_id) AS g,
                  SUM(ps.batters_faced) AS bf,
                  SUM(ps.outs_recorded) AS outs,
                  SUM(ps.hits_allowed)  AS h,
                  SUM(ps.runs_allowed)  AS r,
                  SUM(ps.bb) AS bb, SUM(ps.k) AS k
           FROM game_pitcher_stats ps
           JOIN players p ON ps.player_id = p.id
           WHERE ps.team_id = ?
           GROUP BY p.id
           ORDER BY outs DESC, p.name""",
        (tid,),
    )
    out: list[dict] = []
    for r in rows:
        outs = r["outs"] or 0
        g    = max(r["g"] or 0, 1)
        h    = r["h"]  or 0
        bb   = r["bb"] or 0
        k    = r["k"]  or 0
        runs = r["r"]  or 0
        ip   = outs / 3.0
        out.append({
            "name": r["name"],
            "g":    r["g"] or 0,
            "bf":   r["bf"] or 0,
            "outs": outs,
            "p":    0,
            "h":    h, "r": runs, "er": runs,
            "bb":   bb, "k": k, "hr": 0,
            "out_sum": 0,
            "os_pct": f"{round(outs / (g * 27) * 100)}%" if outs > 0 else "0%",
            "era":  f"{runs / outs * 27:.2f}"           if outs > 0 else "—",
            "whip": f"{(bb + h) / outs * 27:.2f}"       if outs > 0 else "—",
            "aor":  "—",
            "k9":   f"{k  / outs * 27:.2f}" if outs > 0 else "—",
            "bb9":  f"{bb / outs * 27:.2f}" if outs > 0 else "—",
            "fip":  f"{(3 * bb - 2 * k) / ip + 11.50:.2f}" if ip > 0 else "—",
        })
    return out


def get_player_stats(team_abbrev: str, player_name: str) -> Optional[dict]:
    tid = _team_id_for(team_abbrev)
    if tid is None:
        return None
    pl = _v2db.fetchone(
        "SELECT id, position, is_joker, archetype FROM players "
        "WHERE team_id = ? AND name = ?",
        (tid, player_name),
    )
    if not pl:
        return None
    r = _v2db.fetchone(
        """SELECT COUNT(CASE WHEN pa > 0 THEN 1 END) AS gp,
                  SUM(pa) AS pa, SUM(ab) AS ab, SUM(runs) AS runs,
                  SUM(hits) AS hits, SUM(doubles) AS doubles,
                  SUM(triples) AS triples, SUM(hr) AS hr,
                  SUM(rbi) AS rbi, SUM(bb) AS bb, SUM(k) AS k,
                  SUM(stays) AS stays
           FROM game_batter_stats WHERE player_id = ?""",
        (pl["id"],),
    )
    if not r or not r["pa"]:
        return None
    ab = r["ab"] or 0
    hits = r["hits"] or 0
    archetype = (pl.get("archetype") or "").strip()
    is_joker = bool(pl.get("is_joker"))
    pos = pl.get("position") or ""
    if is_joker and archetype:
        pos = f"JKR-{archetype[:3]}"
    return {
        "name": player_name, "pos": pos,
        "is_joker": is_joker, "archetype": archetype,
        "gp": r["gp"] or 0,
        "pa": r["pa"] or 0, "ab": ab,
        "runs":    r["runs"]    or 0,
        "hits":    hits,
        "doubles": r["doubles"] or 0,
        "triples": r["triples"] or 0,
        "hr": r["hr"] or 0, "rbi": r["rbi"] or 0,
        "bb": r["bb"] or 0, "k": r["k"] or 0,
        "stays": r["stays"] or 0,
        "hbp": 0, "or_": 0,
        "avg": f"{hits/ab:.3f}" if ab > 0 else ".000",
    }


def get_pitcher_game_log(team_abbrev: str, player_name: str,
                         limit: int = 15) -> list[dict]:
    tid = _team_id_for(team_abbrev)
    if tid is None:
        return []
    pl = _v2db.fetchone(
        "SELECT id FROM players WHERE team_id = ? AND name = ?",
        (tid, player_name),
    )
    if not pl:
        return []
    rows = _v2db.fetchall(
        """SELECT ps.*, g.id AS gid, g.game_date,
                  g.home_team_id, g.away_team_id,
                  ht.abbrev AS home_abbrev, at.abbrev AS away_abbrev
           FROM game_pitcher_stats ps
           JOIN games g  ON ps.game_id = g.id
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE ps.player_id = ?
           ORDER BY g.game_date DESC, g.id DESC LIMIT ?""",
        (pl["id"], limit),
    )
    out: list[dict] = []
    for r in rows:
        outs = r["outs_recorded"] or 0
        runs = r["runs_allowed"]  or 0
        is_home = (r["team_id"] == r["home_team_id"])
        opp = r["away_abbrev"] if is_home else r["home_abbrev"]
        out.append({
            "game_id": str(r["gid"]),
            "opp":     opp,
            "ha":      "vs" if is_home else "@",
            "outs":    outs,
            "os_pct":  round(outs / 27 * 100),
            "bf":      r["batters_faced"] or 0,
            "p":       0,
            "out":     outs,
            "r":       runs,
            "er":      runs,
            "h":       r["hits_allowed"] or 0,
            "bb":      r["bb"] or 0,
            "k":       r["k"]  or 0,
            "hr":      0,
            "era":     f"{runs / outs * 27:.2f}" if outs > 0 else "—",
        })
    return out


def get_upcoming(n: int = 3) -> list[dict]:
    """Next N unplayed games in schedule order."""
    games = _v2db.fetchall(
        """SELECT g.id, ht.abbrev AS h_abb, ht.name AS h_name, ht.city AS h_city,
                  at.abbrev AS a_abb, at.name AS a_name, at.city AS a_city
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE g.played = 0
           ORDER BY g.game_date, g.id
           LIMIT ?""",
        (n,),
    )
    out = []
    for g in games:
        out.append({
            "visitors_abbrev": g["a_abb"],
            "visitors_name":   f"{g['a_city']} {g['a_name']}".strip(),
            "home_abbrev":     g["h_abb"],
            "home_name":       f"{g['h_city']} {g['h_name']}".strip(),
        })
    return out
