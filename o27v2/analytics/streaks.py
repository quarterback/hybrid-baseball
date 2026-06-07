"""Hit streaks, no-hitters, and perfect games.

All three pull from per-game tables:
  * `game_batter_stats` — hits per game per player
  * `game_pitcher_stats` — outs / hits / bb / hbp / uer per appearance
  * `games` — game_date for streak ordering, team abbrevs for narrative

No state outside the DB; recomputed each request.
"""
from __future__ import annotations
from collections import defaultdict

from o27v2 import db


def _team_in(team_ids, col="team_id"):
    """SQL fragment restricting `col` to team_ids, or '' when unfiltered."""
    if not team_ids:
        return ""
    ids = ",".join(str(int(t)) for t in team_ids)
    return f" AND {col} IN ({ids})"


def longest_hit_streaks(top_n: int = 10, team_ids=None) -> list[dict]:
    """Return the top-N hit streaks of the current season.

    A hit streak is consecutive games (in date order) where the batter
    has hits >= 1 AND took at least one AB (passing on the lineup
    doesn't break a streak — but a zero-PA day does not extend one).

    Active streaks (still in progress at the latest game date) are
    flagged with `active=True`. `team_ids` scopes to one league.
    """
    rows = db.fetchall(
        f"""
        SELECT bs.player_id, bs.hits, bs.ab, bs.pa, g.id AS game_id,
               g.game_date
        FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) bs
        JOIN games g ON bs.game_id = g.id
        WHERE g.played = 1 AND bs.phase = 0{_team_in(team_ids, "bs.team_id")}
        ORDER BY bs.player_id, g.game_date, g.id
        """
    )
    # Group by player.
    by_player: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_player[int(r["player_id"])].append(r)

    # Latest game date across the league — used to mark active streaks.
    latest_row = db.fetchone(
        "SELECT MAX(g.game_date) AS dt FROM games g WHERE g.played = 1"
    )
    latest_date = (latest_row or {}).get("dt") if latest_row else None

    # Player names + team abbrev (one batched lookup).
    pname = {
        int(r["id"]): r
        for r in db.fetchall(
            """SELECT p.id, p.name, t.id AS team_id, t.abbrev
               FROM players p JOIN teams t ON p.team_id = t.id"""
        )
    }

    streaks: list[dict] = []
    for pid, games in by_player.items():
        best_len = 0
        best_start = None
        best_end   = None
        cur_len = 0
        cur_start = None
        cur_end   = None
        for g in games:
            played_ab = (g["ab"] or 0) > 0
            had_hit   = (g["hits"] or 0) > 0
            if had_hit:
                if cur_len == 0:
                    cur_start = g
                cur_len += 1
                cur_end = g
                if cur_len > best_len:
                    best_len  = cur_len
                    best_start = cur_start
                    best_end   = cur_end
            elif played_ab:
                cur_len = 0
                cur_start = None
                cur_end = None
            # zero-AB game: leaves the streak intact but doesn't extend it
        # Active streak = the player's current streak ends on the latest league date.
        active = (
            cur_len > 0
            and latest_date is not None
            and cur_end is not None
            and cur_end["game_date"] == latest_date
        )
        if best_len >= 2:
            info = pname.get(pid, {})
            streaks.append({
                "player_id":   pid,
                "player_name": info.get("name", f"#{pid}"),
                "team_id":     info.get("team_id"),
                "team_abbrev": info.get("abbrev", ""),
                "length":      best_len,
                "start_date":  best_start["game_date"] if best_start else None,
                "end_date":    best_end["game_date"]   if best_end   else None,
                "active":      active and cur_len == best_len,
            })
    streaks.sort(key=lambda s: (-s["length"], s["start_date"] or ""))
    return streaks[:top_n]


def no_hitters_and_perfect_games(team_ids=None) -> dict:
    """Find single-pitcher no-hitters and perfect games (regulation only).

    Definitions:
      * No-hitter: one pitcher recorded all of his team's defensive outs
        in regulation AND allowed 0 hits.
      * Perfect game: no-hitter AND 0 BB AND 0 HBP AND 0 unearned runs
        AND the opposing team had 0 reached-on-error events that game.

    Super-innings are excluded — a game that went past 27 outs without a
    hit through regulation still counts as a no-hitter at the end of
    regulation, but if the team batting in regulation didn't see a hit-
    breaking event during SI we still credit the regulation no-hitter.
    """
    # Per-game, per-pitcher line in regulation only.
    rows = db.fetchall(
        f"""
        SELECT ps.game_id, ps.player_id, ps.team_id,
               SUM(ps.outs_recorded) AS outs,
               SUM(ps.hits_allowed)  AS h,
               SUM(ps.bb)            AS bb,
               SUM(ps.hbp_allowed)   AS hbp,
               SUM(ps.unearned_runs) AS uer,
               SUM(ps.k)             AS k
        FROM (SELECT * FROM game_pitcher_stats WHERE COALESCE(is_playoff,0) = 0) ps
        WHERE ps.phase = 0{_team_in(team_ids, "ps.team_id")}
        GROUP BY ps.game_id, ps.player_id
        HAVING SUM(ps.outs_recorded) >= 27
           AND SUM(ps.hits_allowed)  = 0
        """
    )
    if not rows:
        return {"no_hitters": [], "perfect_games": []}

    game_ids = sorted({int(r["game_id"]) for r in rows})
    placeholders = ",".join("?" * len(game_ids))
    games_meta = {
        int(g["id"]): g for g in db.fetchall(
            f"""SELECT g.id, g.game_date, g.home_team_id, g.away_team_id,
                       ht.abbrev AS home_abbrev, at.abbrev AS away_abbrev,
                       g.home_score, g.away_score
                FROM games g
                JOIN teams ht ON g.home_team_id = ht.id
                JOIN teams at ON g.away_team_id = at.id
                WHERE g.id IN ({placeholders})""",
            tuple(game_ids),
        )
    }
    # Pitcher team_id → abbrev lookup for box-score label rendering.
    team_abbrev = {
        int(r["id"]): r["abbrev"]
        for r in db.fetchall("SELECT id, abbrev FROM teams")
    }
    # Reaches-on-error per (game, defending team) for perfect-game gate.
    roe_rows = db.fetchall(
        f"""SELECT game_id, team_id, COALESCE(SUM(roe),0) AS roe
            FROM game_batter_stats
            WHERE game_id IN ({placeholders}) AND phase = 0
            GROUP BY game_id, team_id""",
        tuple(game_ids),
    )
    roe_map: dict[tuple[int, int], int] = {
        (int(r["game_id"]), int(r["team_id"])): int(r["roe"] or 0)
        for r in roe_rows
    }
    # Player name lookup.
    pids = sorted({int(r["player_id"]) for r in rows})
    pn_placeholders = ",".join("?" * len(pids))
    pnames = {
        int(p["id"]): p["name"] for p in db.fetchall(
            f"SELECT id, name FROM players WHERE id IN ({pn_placeholders})",
            tuple(pids),
        )
    }

    no_hitters: list[dict] = []
    perfect: list[dict] = []
    for r in rows:
        gid    = int(r["game_id"])
        gmeta  = games_meta.get(gid)
        if not gmeta:
            continue
        # Opposing team_id from the pitcher's perspective.
        pitcher_team = int(r["team_id"])
        if pitcher_team == int(gmeta["home_team_id"]):
            opp_team = int(gmeta["away_team_id"])
            opp_abbr = gmeta["away_abbrev"]
        else:
            opp_team = int(gmeta["home_team_id"])
            opp_abbr = gmeta["home_abbrev"]
        record = {
            "game_id":     gid,
            "game_date":   gmeta["game_date"],
            "player_id":   int(r["player_id"]),
            "player_name": pnames.get(int(r["player_id"]), f"#{r['player_id']}"),
            "team_id":     pitcher_team,
            "team_abbrev": team_abbrev.get(pitcher_team, ""),
            "opp_abbrev":  opp_abbr,
            "k":           int(r["k"] or 0),
            "bb":          int(r["bb"] or 0),
            "hbp":         int(r["hbp"] or 0),
        }
        no_hitters.append(record)
        # Perfect game: no walks, no HBP, no UER, no ROE on opposing batting line.
        opp_roe = roe_map.get((gid, opp_team), 0)
        if (int(r["bb"] or 0) == 0
                and int(r["hbp"] or 0) == 0
                and int(r["uer"] or 0) == 0
                and opp_roe == 0):
            perfect.append(record)

    no_hitters.sort(key=lambda x: (x["game_date"], x["game_id"]))
    perfect.sort(key=lambda x: (x["game_date"], x["game_id"]))
    return {"no_hitters": no_hitters, "perfect_games": perfect}


# ---------------------------------------------------------------------------
# Generic consecutive-game streak engine
#
# Every streak below is computed the same way the rest of the app computes
# stats: from the per-game aggregate tables (`game_batter_stats` /
# `game_pitcher_stats`), never from `game_pa_log` — its `hit_type` column is
# NULL on non-BIP events (strikeouts, walks), so it can't reliably classify
# K / out outcomes. Per-game aggregates are exact.
# ---------------------------------------------------------------------------

def _player_lookup() -> dict[int, dict]:
    """player_id -> {name, team_id, abbrev}, one batched query."""
    return {
        int(r["id"]): r
        for r in db.fetchall(
            """SELECT p.id, p.name, t.id AS team_id, t.abbrev
               FROM players p JOIN teams t ON p.team_id = t.id"""
        )
    }


def _latest_game_date() -> str | None:
    row = db.fetchone(
        "SELECT MAX(g.game_date) AS dt FROM games g WHERE g.played = 1"
    )
    return (row or {}).get("dt") if row else None


def _count_streaks(rows, *, extends, breaks, pname, latest_date,
                   top_n, min_len=2) -> list[dict]:
    """Longest run of consecutive games (date-ordered, per player) for which
    `extends(game)` is true. `breaks(game)` ends a run; any game that neither
    extends nor breaks is neutral (carries the streak without lengthening it,
    e.g. a no-PA day). Returns up to `top_n` streaks of length >= `min_len`.
    """
    by_player: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_player[int(r["player_id"])].append(r)

    out: list[dict] = []
    for pid, games in by_player.items():
        best_len = 0
        best_start = best_end = None
        cur_len = 0
        cur_start = cur_end = None
        for g in games:
            if extends(g):
                if cur_len == 0:
                    cur_start = g
                cur_len += 1
                cur_end = g
                if cur_len > best_len:
                    best_len, best_start, best_end = cur_len, cur_start, cur_end
            elif breaks(g):
                cur_len = 0
                cur_start = cur_end = None
            # else: neutral — leave the run intact but don't extend it
        active = (
            cur_len > 0
            and latest_date is not None
            and cur_end is not None
            and cur_end["game_date"] == latest_date
            and cur_len == best_len
        )
        if best_len >= min_len:
            info = pname.get(pid, {})
            out.append({
                "player_id":   pid,
                "player_name": info.get("name", f"#{pid}"),
                "team_id":     info.get("team_id"),
                "team_abbrev": info.get("abbrev", ""),
                "length":      best_len,
                "start_date":  best_start["game_date"] if best_start else None,
                "end_date":    best_end["game_date"] if best_end else None,
                "active":      active,
            })
    out.sort(key=lambda s: (-s["length"], s["start_date"] or ""))
    return out[:top_n]


def home_run_streaks(top_n: int = 10, team_ids=None) -> list[dict]:
    """Consecutive games (in date order) with at least one home run.

    A game where the batter took a PA but didn't homer breaks the streak;
    a no-PA day (pinch slot passed over) leaves it intact. `team_ids`
    scopes to one league.
    """
    rows = db.fetchall(
        f"""
        SELECT bs.player_id, bs.hr, bs.pa, g.id AS game_id, g.game_date
        FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) bs
        JOIN games g ON bs.game_id = g.id
        WHERE g.played = 1 AND bs.phase = 0{_team_in(team_ids, "bs.team_id")}
        ORDER BY bs.player_id, g.game_date, g.id
        """
    )
    return _count_streaks(
        rows,
        extends=lambda g: (g["hr"] or 0) > 0,
        breaks=lambda g: (g["pa"] or 0) > 0 and (g["hr"] or 0) == 0,
        pname=_player_lookup(),
        latest_date=_latest_game_date(),
        top_n=top_n,
    )


def on_base_streaks(top_n: int = 10, team_ids=None) -> list[dict]:
    """Consecutive games reaching base safely (hit, walk, or HBP).

    Mirrors the real-world "on-base streak" record. A game with a PA but no
    time on base breaks it; a no-PA day is neutral. `team_ids` scopes to one
    league.
    """
    rows = db.fetchall(
        f"""
        SELECT bs.player_id, bs.pa,
               (COALESCE(bs.hits,0) + COALESCE(bs.bb,0)
                + COALESCE(bs.hbp,0)) AS reached,
               g.id AS game_id, g.game_date
        FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) bs
        JOIN games g ON bs.game_id = g.id
        WHERE g.played = 1 AND bs.phase = 0{_team_in(team_ids, "bs.team_id")}
        ORDER BY bs.player_id, g.game_date, g.id
        """
    )
    return _count_streaks(
        rows,
        extends=lambda g: (g["reached"] or 0) > 0,
        breaks=lambda g: (g["pa"] or 0) > 0 and (g["reached"] or 0) == 0,
        pname=_player_lookup(),
        latest_date=_latest_game_date(),
        top_n=top_n,
    )


def double_digit_k_streaks(top_n: int = 10, team_ids=None,
                           threshold: int = 10) -> list[dict]:
    """Consecutive starts with `threshold`+ strikeouts (default 10).

    Only starts count toward the streak; relief appearances are neutral so a
    starter who pitches out of the pen between starts doesn't break the run.
    A start under the threshold breaks it. `team_ids` scopes to one league.
    """
    rows = db.fetchall(
        f"""
        SELECT ps.player_id, ps.k, ps.is_starter,
               g.id AS game_id, g.game_date
        FROM (SELECT * FROM game_pitcher_stats WHERE COALESCE(is_playoff,0) = 0) ps
        JOIN games g ON ps.game_id = g.id
        WHERE g.played = 1 AND ps.phase = 0{_team_in(team_ids, "ps.team_id")}
        ORDER BY ps.player_id, g.game_date, g.id
        """
    )
    return _count_streaks(
        rows,
        extends=lambda g: (g["is_starter"] or 0) == 1 and (g["k"] or 0) >= threshold,
        breaks=lambda g: (g["is_starter"] or 0) == 1 and (g["k"] or 0) < threshold,
        pname=_player_lookup(),
        latest_date=_latest_game_date(),
        top_n=top_n,
    )


def scoreless_outs_streaks(top_n: int = 10, team_ids=None,
                           min_outs: int = 9) -> list[dict]:
    """Longest run of OUTS across consecutive scoreless appearances.

    O27 has no innings — one continuous 27-out half — so this is the native
    "longest stretch without allowing a run," measured in outs (the
    Hershiser-style scoreless streak, de-innings'd). Appearances are taken in
    date order per pitcher; a run extends through every appearance that allows
    no runs (earned or unearned), accumulating outs, and the first appearance
    to surrender a run ends it. Because runs are charged at the appearance
    level, an outing that allows a run contributes 0 rather than its clean
    fraction — a deliberate, slightly conservative simplification. Only runs of
    >= `min_outs` outs (default 9 = a third of a half) are returned.
    `team_ids` scopes to one league.
    """
    rows = db.fetchall(
        f"""
        SELECT ps.player_id, ps.outs_recorded AS outs, ps.runs_allowed AS r,
               g.id AS game_id, g.game_date
        FROM (SELECT * FROM game_pitcher_stats WHERE COALESCE(is_playoff,0) = 0) ps
        JOIN games g ON ps.game_id = g.id
        WHERE g.played = 1 AND ps.phase = 0 AND ps.outs_recorded > 0
              {_team_in(team_ids, "ps.team_id")}
        ORDER BY ps.player_id, g.game_date, g.id
        """
    )
    pname = _player_lookup()
    by_player: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_player[int(r["player_id"])].append(r)

    out: list[dict] = []
    for pid, apps in by_player.items():
        best = 0
        best_start = best_end = None
        cur = 0
        cur_start = cur_end = None
        for a in apps:
            if (a["r"] or 0) == 0:
                if cur == 0:
                    cur_start = a
                cur += int(a["outs"] or 0)
                cur_end = a
                if cur > best:
                    best, best_start, best_end = cur, cur_start, cur_end
            else:
                cur = 0
                cur_start = cur_end = None
        if best >= min_outs:
            info = pname.get(pid, {})
            out.append({
                "player_id":   pid,
                "player_name": info.get("name", f"#{pid}"),
                "team_id":     info.get("team_id"),
                "team_abbrev": info.get("abbrev", ""),
                "outs":        best,
                "start_date":  best_start["game_date"] if best_start else None,
                "end_date":    best_end["game_date"] if best_end else None,
            })
    out.sort(key=lambda s: (-s["outs"], s["start_date"] or ""))
    return out[:top_n]
