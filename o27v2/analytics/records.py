"""Single-game records and cross-season (all-time) records.

Two families of leaderboards for the Streaks & Records page:

  * **Single-game records** — the best individual games of the *current*
    season, read from the per-game aggregate tables (`game_batter_stats` /
    `game_pitcher_stats`). League-scopable.

  * **Cross-season records** — career totals (summed across seasons) and the
    best single seasons ever, built from `player_career_lines` (the durable
    per-season snapshot, keyed by a stable `player_id` so rows link to player
    cards) merged with a freshly-computed line for the in-progress season.
    Universe-wide by design: a player can change leagues across seasons, so
    "all-time" records are not league-scoped.

Everything is recomputed per request from the DB — no cached state.

Note on the live season: `player_career_lines` is only written at season
rollover, so the in-progress season is folded in here from the live game
tables. Pitcher wins/losses require the web layer's decision map, so the
live season contributes K / IP / ERA / WHIP to career and single-season
pitching records but *not* W/L (those land once the season is archived).
"""
from __future__ import annotations

from collections import defaultdict

from o27v2 import db

# Modest qualification floors for rate-stat records, low enough to populate
# in a short opening season but high enough to exclude one-game flukes.
_CAREER_PA_FLOOR   = 50
_CAREER_OUTS_FLOOR = 45


def _team_in(team_ids, col="team_id") -> str:
    if not team_ids:
        return ""
    ids = ",".join(str(int(t)) for t in team_ids)
    return f" AND {col} IN ({ids})"


# ---------------------------------------------------------------------------
# Single-game records (current season)
# ---------------------------------------------------------------------------

def _single_game_batter_rows(team_ids) -> list[dict]:
    return db.fetchall(
        f"""
        SELECT bs.player_id, p.name AS player_name,
               bs.team_id, t.abbrev AS team_abbrev,
               bs.game_id, g.game_date,
               g.home_team_id, g.away_team_id,
               ht.abbrev AS home_abbrev, at.abbrev AS away_abbrev,
               COALESCE(bs.hits,0)    AS h,
               COALESCE(bs.doubles,0) AS d2,
               COALESCE(bs.triples,0) AS d3,
               COALESCE(bs.hr,0)      AS hr,
               COALESCE(bs.rbi,0)     AS rbi,
               COALESCE(bs.runs,0)    AS r,
               COALESCE(bs.bb,0)      AS bb,
               COALESCE(bs.sb,0)      AS sb
        FROM game_batter_stats bs
        JOIN games   g  ON bs.game_id = g.id
        JOIN players p  ON bs.player_id = p.id
        JOIN teams   t  ON bs.team_id = t.id
        JOIN teams   ht ON g.home_team_id = ht.id
        JOIN teams   at ON g.away_team_id = at.id
        WHERE g.played = 1 AND bs.phase = 0{_team_in(team_ids, "bs.team_id")}
        """
    )


def _opp_abbrev(row) -> str:
    """Opponent abbrev from the player's team perspective."""
    if int(row["team_id"]) == int(row["home_team_id"]):
        return row["away_abbrev"]
    return row["home_abbrev"]


def single_game_batter_records(top_n: int = 5, team_ids=None) -> dict[str, list[dict]]:
    """Best single batting games of the season, per category.

    Categories: hr, rbi, h (hits), tb (total bases), r (runs). Each value
    must be > 0 to list. `team_ids` scopes to one league.
    """
    rows = _single_game_batter_rows(team_ids)
    for r in rows:
        # TB = singles + 2*doubles + 3*triples + 4*HR
        #    = hits + doubles + 2*triples + 3*HR
        r["tb"] = (r["h"] or 0) + (r["d2"] or 0) + 2 * (r["d3"] or 0) + 3 * (r["hr"] or 0)
        r["opp_abbrev"] = _opp_abbrev(r)
    cats = ("hr", "rbi", "h", "tb", "r")
    out: dict[str, list[dict]] = {}
    for c in cats:
        ranked = sorted(
            (r for r in rows if (r.get(c) or 0) > 0),
            key=lambda r: (-(r.get(c) or 0), r["game_date"], r["game_id"]),
        )
        out[c] = ranked[:top_n]
    return out


def single_game_pitcher_records(top_n: int = 5, team_ids=None) -> dict[str, list[dict]]:
    """Best single pitching games of the season, per category.

    Categories: k (strikeouts) and outs (longest outing, shown as IP).
    `team_ids` scopes to one league.
    """
    rows = db.fetchall(
        f"""
        SELECT ps.player_id, p.name AS player_name,
               ps.team_id, t.abbrev AS team_abbrev,
               ps.game_id, g.game_date,
               g.home_team_id, g.away_team_id,
               ht.abbrev AS home_abbrev, at.abbrev AS away_abbrev,
               COALESCE(ps.outs_recorded,0) AS outs,
               COALESCE(ps.k,0)             AS k,
               COALESCE(ps.hits_allowed,0)  AS h,
               COALESCE(ps.bb,0)            AS bb,
               COALESCE(ps.er,0)            AS er
        FROM game_pitcher_stats ps
        JOIN games   g  ON ps.game_id = g.id
        JOIN players p  ON ps.player_id = p.id
        JOIN teams   t  ON ps.team_id = t.id
        JOIN teams   ht ON g.home_team_id = ht.id
        JOIN teams   at ON g.away_team_id = at.id
        WHERE g.played = 1 AND ps.phase = 0 AND ps.outs_recorded > 0
              {_team_in(team_ids, "ps.team_id")}
        """
    )
    for r in rows:
        r["opp_abbrev"] = _opp_abbrev(r)
        outs = int(r["outs"] or 0)
        r["ip_display"] = f"{outs // 3}.{outs % 3}"
    out: dict[str, list[dict]] = {}
    out["k"] = sorted(
        (r for r in rows if (r["k"] or 0) > 0),
        key=lambda r: (-(r["k"] or 0), r["game_date"], r["game_id"]),
    )[:top_n]
    out["outs"] = sorted(
        rows,
        key=lambda r: (-(r["outs"] or 0), r["game_date"], r["game_id"]),
    )[:top_n]
    return out


# ---------------------------------------------------------------------------
# Cross-season (all-time) records
# ---------------------------------------------------------------------------

def _tb(line) -> int:
    return (line.get("h") or 0) + (line.get("d2") or 0) \
        + 2 * (line.get("d3") or 0) + 3 * (line.get("hr") or 0)


def _bat_rates(line) -> None:
    """Attach avg/obp/slg/ops to a batting line from its component totals."""
    ab = line.get("ab") or 0
    bb = line.get("bb") or 0
    h  = line.get("h") or 0
    line["avg"] = (h / ab) if ab else 0.0
    obp_den = ab + bb
    line["obp"] = ((h + bb) / obp_den) if obp_den else 0.0
    line["slg"] = (_tb(line) / ab) if ab else 0.0
    line["ops"] = line["obp"] + line["slg"]


def _pit_rates(line) -> None:
    """Attach era/whip/k9 to a pitching line from its component totals."""
    outs = line.get("outs") or 0
    ip = outs / 3.0
    er = line.get("er") or 0
    bb = line.get("p_bb") or 0
    h  = line.get("p_h") or 0
    k  = line.get("p_k") or 0
    line["ip"] = ip
    line["ip_display"] = f"{int(outs) // 3}.{int(outs) % 3}"
    line["era"]  = (er * 9.0 / ip) if ip else 0.0
    line["whip"] = ((bb + h) / ip) if ip else 0.0
    line["k9"]   = (k * 9.0 / ip) if ip else 0.0


def _archived_lines() -> list[dict]:
    return db.fetchall("SELECT * FROM player_career_lines")


def _live_season_label() -> tuple[int, int | None]:
    """(season_number, year) to stamp on the in-progress season's lines."""
    row = db.fetchone(
        "SELECT MAX(season_number) AS n, MAX(year) AS y FROM player_career_lines"
    )
    prev = (row or {}).get("n") if row else None
    return (int(prev) + 1 if prev else 1), (row or {}).get("y") if row else None


def _live_batting_lines() -> list[dict]:
    rows = db.fetchall(
        """SELECT p.id AS player_id, p.name AS player_name, t.abbrev AS team_abbrev,
                  COUNT(bs.game_id) AS g,
                  SUM(bs.pa) AS pa, SUM(bs.ab) AS ab, SUM(bs.hits) AS h,
                  SUM(bs.doubles) AS d2, SUM(bs.triples) AS d3, SUM(bs.hr) AS hr,
                  SUM(bs.runs) AS r, SUM(bs.rbi) AS rbi, SUM(bs.bb) AS bb,
                  SUM(bs.k) AS k, COALESCE(SUM(bs.sb),0) AS sb
             FROM game_batter_stats bs
             JOIN players p ON bs.player_id = p.id
             JOIN teams   t ON bs.team_id = t.id
            WHERE bs.phase = 0
            GROUP BY p.id
           HAVING SUM(bs.pa) > 0"""
    )
    return rows


def _live_pitching_lines() -> list[dict]:
    rows = db.fetchall(
        """SELECT p.id AS player_id, p.name AS player_name, t.abbrev AS team_abbrev,
                  COUNT(ps.game_id) AS p_g,
                  SUM(ps.outs_recorded) AS outs, SUM(ps.er) AS er,
                  SUM(ps.k) AS p_k, SUM(ps.bb) AS p_bb, SUM(ps.hits_allowed) AS p_h
             FROM game_pitcher_stats ps
             JOIN players p ON ps.player_id = p.id
             JOIN teams   t ON ps.team_id = t.id
            WHERE ps.phase = 0
            GROUP BY p.id
           HAVING SUM(ps.outs_recorded) > 0"""
    )
    return rows


def _include_live() -> bool:
    """True when the in-progress season isn't yet archived into career lines
    (so it should be folded into all-time records from the live tables)."""
    played = db.fetchone(
        "SELECT COUNT(*) AS n FROM games WHERE played = 1"
    )["n"] or 0
    if not played:
        return False
    try:
        from o27v2.season_archive import get_current_archived_season_id
        return get_current_archived_season_id() is None
    except Exception:
        return True


def _all_batting_lines() -> list[dict]:
    """Archived batting lines + (if unarchived) the live season's lines,
    each normalized to the player_career_lines column shape."""
    lines = [dict(r) for r in _archived_lines() if not (r.get("is_pitcher") or 0)]
    if _include_live():
        sn, yr = _live_season_label()
        for r in _live_batting_lines():
            r = dict(r)
            r.update(is_pitcher=0, season_number=sn, year=yr, is_current=True)
            lines.append(r)
    return lines


def _all_pitching_lines() -> list[dict]:
    lines = [dict(r) for r in _archived_lines() if (r.get("is_pitcher") or 0)]
    if _include_live():
        sn, yr = _live_season_label()
        for r in _live_pitching_lines():
            r = dict(r)
            r.update(is_pitcher=1, season_number=sn, year=yr, is_current=True)
            lines.append(r)
    return lines


def _career_aggregate(lines: list[dict], sum_keys: list[str]) -> list[dict]:
    """Sum `sum_keys` across all of a player's season lines. Team / latest
    season come from the player's most recent line."""
    by_pid: dict[int, list[dict]] = defaultdict(list)
    for ln in lines:
        by_pid[int(ln["player_id"])].append(ln)
    out: list[dict] = []
    for pid, pl in by_pid.items():
        latest = max(pl, key=lambda x: (x.get("season_number") or 0))
        agg = {
            "player_id":   pid,
            "player_name": latest.get("player_name", f"#{pid}"),
            "team_abbrev": latest.get("team_abbrev", ""),
            "seasons":     len(pl),
        }
        for k in sum_keys:
            agg[k] = sum((ln.get(k) or 0) for ln in pl)
        out.append(agg)
    return out


# Career counting categories with display label + format.
CAREER_BAT_COUNTING = ["hr", "h", "rbi", "r", "bb", "sb", "d2", "d3", "k"]
CAREER_BAT_RATE     = ["avg", "obp", "slg", "ops"]
CAREER_PIT_COUNTING = ["p_k", "outs", "w"]
CAREER_PIT_RATE     = ["era", "whip", "k9"]


def career_batting_records(top_n: int = 10) -> dict[str, list[dict]]:
    lines = _all_batting_lines()
    agg = _career_aggregate(
        lines, ["g", "pa", "ab", "h", "d2", "d3", "hr", "r", "rbi", "bb", "k", "sb"]
    )
    for a in agg:
        _bat_rates(a)
    out: dict[str, list[dict]] = {}
    for c in CAREER_BAT_COUNTING:
        out[c] = sorted(
            (a for a in agg if (a.get(c) or 0) > 0),
            key=lambda a, c=c: (-(a.get(c) or 0), -a.get("pa", 0)),
        )[:top_n]
    for c in CAREER_BAT_RATE:
        out[c] = sorted(
            (a for a in agg if (a.get("pa") or 0) >= _CAREER_PA_FLOOR),
            key=lambda a, c=c: (-(a.get(c) or 0.0),),
        )[:top_n]
    return out


def career_pitching_records(top_n: int = 10) -> dict[str, list[dict]]:
    lines = _all_pitching_lines()
    agg = _career_aggregate(
        lines, ["p_g", "w", "l", "outs", "er", "p_k", "p_bb", "p_h"]
    )
    for a in agg:
        _pit_rates(a)
    out: dict[str, list[dict]] = {}
    # Counting: K, IP (via outs), W.
    out["p_k"] = sorted(
        (a for a in agg if (a.get("p_k") or 0) > 0),
        key=lambda a: (-(a.get("p_k") or 0), -a.get("outs", 0)),
    )[:top_n]
    out["outs"] = sorted(
        (a for a in agg if (a.get("outs") or 0) > 0),
        key=lambda a: (-(a.get("outs") or 0),),
    )[:top_n]
    out["w"] = sorted(
        (a for a in agg if (a.get("w") or 0) > 0),
        key=lambda a: (-(a.get("w") or 0), -a.get("outs", 0)),
    )[:top_n]
    # Rate: ERA / WHIP ascending (lower is better), K/9 descending.
    qual = [a for a in agg if (a.get("outs") or 0) >= _CAREER_OUTS_FLOOR]
    out["era"]  = sorted(qual, key=lambda a: (a.get("era") or 1e9,))[:top_n]
    out["whip"] = sorted(qual, key=lambda a: (a.get("whip") or 1e9,))[:top_n]
    out["k9"]   = sorted(qual, key=lambda a: (-(a.get("k9") or 0.0),))[:top_n]
    return out


def single_season_batting_records(top_n: int = 10) -> dict[str, list[dict]]:
    """Best individual seasons ever, per batting category."""
    lines = [dict(r) for r in _all_batting_lines()]
    for ln in lines:
        _bat_rates(ln)
    out: dict[str, list[dict]] = {}
    for c in CAREER_BAT_COUNTING:
        out[c] = sorted(
            (ln for ln in lines if (ln.get(c) or 0) > 0),
            key=lambda ln, c=c: (-(ln.get(c) or 0),),
        )[:top_n]
    for c in CAREER_BAT_RATE:
        out[c] = sorted(
            (ln for ln in lines if (ln.get("pa") or 0) >= _CAREER_PA_FLOOR),
            key=lambda ln, c=c: (-(ln.get(c) or 0.0),),
        )[:top_n]
    return out


def single_season_pitching_records(top_n: int = 10) -> dict[str, list[dict]]:
    lines = [dict(r) for r in _all_pitching_lines()]
    for ln in lines:
        _pit_rates(ln)
    out: dict[str, list[dict]] = {}
    out["p_k"] = sorted(
        (ln for ln in lines if (ln.get("p_k") or 0) > 0),
        key=lambda ln: (-(ln.get("p_k") or 0),),
    )[:top_n]
    out["w"] = sorted(
        (ln for ln in lines if (ln.get("w") or 0) > 0),
        key=lambda ln: (-(ln.get("w") or 0),),
    )[:top_n]
    qual = [ln for ln in lines if (ln.get("outs") or 0) >= _CAREER_OUTS_FLOOR]
    out["era"]  = sorted(qual, key=lambda ln: (ln.get("era") or 1e9,))[:top_n]
    out["whip"] = sorted(qual, key=lambda ln: (ln.get("whip") or 1e9,))[:top_n]
    out["k9"]   = sorted(qual, key=lambda ln: (-(ln.get("k9") or 0.0),))[:top_n]
    return out
