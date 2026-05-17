"""Declared Seconds — derived season aggregates.

Reads the per-game declaration columns on `games` (home_bats_first,
{away,home}_declared_at, {away,home}_seconds_used, etc., wired in Step 7
of the Declared Seconds plan) and emits per-team and per-manager rate
stats that expose decision quality.

No new tables. Everything below is derivable from the existing `games`
columns plus a join to `teams.manager_archetype`. Functions return plain
dicts so the web layer can render them without dataclass coupling.
"""
from __future__ import annotations

from o27v2 import db


def per_team_summary(season: int | None = None) -> list[dict]:
    """One row per team for the given season (default = all seasons).

    Columns:
      team_id            int
      games              total games played
      declarations       # of games where this team declared at least once
      declare_rate       declarations / games
      avg_declared_at    avg out at which the team declared (NULL if never)
      comeback_attempts  games where the team came back for a seconds round
      comeback_wins      comeback attempts where this team ended up the winner
      comeback_win_rate  comeback_wins / comeback_attempts
      bat_first_rate     for home games: fraction where team chose bat-first
    """
    season_filter = " AND season = ?" if season is not None else ""
    params: tuple = (season,) if season is not None else ()
    teams = db.fetchall("SELECT id, abbrev, name FROM teams")
    out: list[dict] = []
    for t in teams:
        tid = t["id"]
        # Games where this team is home OR away
        games = db.fetchall(
            f"""SELECT id, home_team_id, away_team_id, winner_id,
                       home_bats_first,
                       home_declared_at, away_declared_at,
                       home_seconds_used, away_seconds_used
                  FROM games WHERE played=1{season_filter}
                   AND (home_team_id=? OR away_team_id=?)""",
            params + (tid, tid),
        )
        g_total = len(games)
        if g_total == 0:
            continue
        decl_count = 0
        decl_outs: list[int] = []
        comeback_attempts = 0
        comeback_wins = 0
        home_games = 0
        bat_first_count = 0
        for g in games:
            is_home = g["home_team_id"] == tid
            decl_at = g["home_declared_at"] if is_home else g["away_declared_at"]
            sec_used = g["home_seconds_used"] if is_home else g["away_seconds_used"]
            if decl_at is not None:
                decl_count += 1
                decl_outs.append(int(decl_at))
            if (sec_used or 0) > 0:
                comeback_attempts += 1
                if g["winner_id"] == tid:
                    comeback_wins += 1
            if is_home:
                home_games += 1
                if g["home_bats_first"]:
                    bat_first_count += 1
        out.append({
            "team_id": tid,
            "abbrev": t["abbrev"],
            "name": t["name"],
            "games": g_total,
            "declarations": decl_count,
            "declare_rate": decl_count / g_total if g_total else 0.0,
            "avg_declared_at": (sum(decl_outs) / len(decl_outs)
                                if decl_outs else None),
            "comeback_attempts": comeback_attempts,
            "comeback_wins": comeback_wins,
            "comeback_win_rate": (comeback_wins / comeback_attempts
                                  if comeback_attempts else None),
            "bat_first_rate": (bat_first_count / home_games
                               if home_games else None),
        })
    return out


def league_overview(season: int | None = None) -> dict:
    """League-wide Declared Seconds aggregates: how often the mechanic
    fires, what the bat-first split looks like, prevalence of comebacks.
    """
    season_filter = " WHERE played=1" + (
        " AND season=?" if season is not None else ""
    )
    params: tuple = (season,) if season is not None else ()
    rows = db.fetchall(
        f"""SELECT home_bats_first, home_declared_at, away_declared_at,
                   home_seconds_used, away_seconds_used,
                   seconds_outcome
              FROM games{season_filter}""",
        params,
    )
    n = len(rows)
    if n == 0:
        return {"games": 0}
    any_decl = sum(1 for r in rows
                   if r["home_declared_at"] is not None
                   or r["away_declared_at"] is not None)
    both_decl = sum(1 for r in rows
                    if r["home_declared_at"] is not None
                    and r["away_declared_at"] is not None)
    seconds_fired = sum(1 for r in rows
                        if (r["home_seconds_used"] or 0) > 0
                        or (r["away_seconds_used"] or 0) > 0)
    bat_first = sum(1 for r in rows if r["home_bats_first"])
    return {
        "games": n,
        "any_declaration_rate": any_decl / n,
        "both_declared_rate":   both_decl / n,
        "one_declared_rate":    (any_decl - both_decl) / n,
        "no_declaration_rate":  (n - any_decl) / n,
        "seconds_fired_rate":   seconds_fired / n,
        "home_bats_first_rate": bat_first / n,
    }
