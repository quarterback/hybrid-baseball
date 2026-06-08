"""Bunting rate analytics for O27.

A league "barometer" panel built off the persisted bunt counters in
``game_batter_stats`` (``bunt_att``, ``bunt_hits``, ``sh``, ``sqz``,
``sqz_rbi``) plus ``pa``. It surfaces the *rate* framing the raw box-score
totals don't — how often teams bunt, how often those bunts reach base, and
how the bunt mix splits between bunt-for-hit, sacrifice, and squeeze.

These are the same definitions used to calibrate the engine's bunt model:

  * bunt rate        = bunt_att / pa                (how bunt-happy)
  * bunt-hit rate    = bunt_hits / bunt_att         (reached base)
  * sacrifice share  = sh / bunt_att                (advanced / scored a runner;
                                                      ``sh`` also counts squeeze
                                                      runs that scored)
  * squeeze share    = sqz / bunt_att               (how much of the bunting is
                                                      runner-on-third squeezing)
  * productive rate  = (bunt_hits + sh) / bunt_att  (reached base OR moved a
                                                      runner — a positive outcome)

O27 has no DH, so the split between pitchers (the classic sacrifice bunter)
and position players is meaningful and reported separately.

All functions return plain dicts so the web layer can JSON-serialise them.
Regulation only (``phase = 0``), regular season only (``is_playoff = 0``).
"""
from __future__ import annotations

from o27v2 import db


def _team_in(team_ids, col="bs.team_id") -> str:
    """SQL fragment scoping a query to a league's teams (or '' for all)."""
    if not team_ids:
        return ""
    csv = ",".join(str(int(t)) for t in team_ids)
    return f" AND {col} IN ({csv})"


def _rates(row: dict) -> dict:
    """Derive bunt rates from a row of summed counters. Division-safe."""
    pa   = row.get("pa", 0) or 0
    ba   = row.get("bunt_att", 0) or 0
    bh   = row.get("bunt_hits", 0) or 0
    sh   = row.get("sh", 0) or 0
    sqz  = row.get("sqz", 0) or 0
    out = {
        "pa":         pa,
        "bunt_att":   ba,
        "bunt_hits":  bh,
        "sh":         sh,
        "sqz":        sqz,
        "sqz_rbi":    row.get("sqz_rbi", 0) or 0,
        "bunt_pct_pa":    (ba / pa) if pa else 0.0,
        "bunt_hit_rate":  (bh / ba) if ba else 0.0,
        "sac_share":      (sh / ba) if ba else 0.0,
        "sqz_share":      (sqz / ba) if ba else 0.0,
        "productive_pct": ((bh + sh) / ba) if ba else 0.0,
    }
    return out


def build_bunting_rates(team_ids=None) -> dict:
    """Build the league bunting-rate barometer.

    Returns::

        {
            "league":   {<rates>},                 # all hitters pooled
            "pitchers": {<rates>},                 # is_pitcher = 1
            "position": {<rates>},                 # is_pitcher = 0
            "teams": [ {team_id, team_name, team_abbrev, <rates>}, ... ],
        }

    Every ``<rates>`` block carries the raw counters (pa, bunt_att, bunt_hits,
    sh, sqz, sqz_rbi) and the derived rates (bunt_pct_pa, bunt_hit_rate,
    sac_share, sqz_share, productive_pct). The team list is sorted by bunt
    rate (most bunt-happy first). All zeros on an empty DB — never raises.
    """
    where = (" FROM game_batter_stats bs"
             " WHERE bs.phase = 0 AND COALESCE(bs.is_playoff, 0) = 0")

    _cols = ("COALESCE(SUM(bs.pa),0) AS pa,"
             "COALESCE(SUM(bs.bunt_att),0) AS bunt_att,"
             "COALESCE(SUM(bs.bunt_hits),0) AS bunt_hits,"
             "COALESCE(SUM(bs.sh),0) AS sh,"
             "COALESCE(SUM(bs.sqz),0) AS sqz,"
             "COALESCE(SUM(bs.sqz_rbi),0) AS sqz_rbi")

    # League total.
    league_row = db.fetchone(
        f"SELECT {_cols}{where}{_team_in(team_ids)}"
    ) or {}
    league = _rates(league_row)

    # Pitcher vs position-player split (no DH in O27).
    split = {"pitchers": _rates({}), "position": _rates({})}
    rows = db.fetchall(
        f"SELECT p.is_pitcher AS is_pitcher, {_cols}"
        " FROM game_batter_stats bs"
        " JOIN players p ON bs.player_id = p.id"
        " WHERE bs.phase = 0 AND COALESCE(bs.is_playoff, 0) = 0"
        f"{_team_in(team_ids)}"
        " GROUP BY p.is_pitcher"
    )
    for r in rows:
        key = "pitchers" if r.get("is_pitcher") else "position"
        split[key] = _rates(r)

    # Per-team breakdown, most bunt-happy first.
    team_rows = db.fetchall(
        f"SELECT t.id AS team_id, t.name AS team_name, t.abbrev AS team_abbrev, {_cols}"
        " FROM game_batter_stats bs"
        " JOIN teams t ON bs.team_id = t.id"
        " WHERE bs.phase = 0 AND COALESCE(bs.is_playoff, 0) = 0"
        f"{_team_in(team_ids)}"
        " GROUP BY t.id"
        " HAVING SUM(bs.pa) > 0"
    )
    teams = []
    for r in team_rows:
        row = _rates(r)
        row.update({
            "team_id":     r.get("team_id"),
            "team_name":   r.get("team_name"),
            "team_abbrev": r.get("team_abbrev"),
        })
        teams.append(row)
    teams.sort(key=lambda r: r["bunt_pct_pa"], reverse=True)

    return {
        "league":   league,
        "pitchers": split["pitchers"],
        "position": split["position"],
        "teams":    teams,
    }
