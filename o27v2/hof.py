"""
Hall of Fame — career honors for O27 players.

Two distinct halls, by design:

  * The LEAGUE Hall of Fame is gated and automatic. It uses an LPGA-style
    points model: a player accumulates HOF points from concrete, era-relative
    achievements (major awards, championship rings, leading the league =
    "black ink", top-10 finishes = "gray ink", sustained-excellence seasons by
    the park/league-normalized + stats, and longevity). Once a player clears
    the points threshold AND meets the longevity/age eligibility window, they
    are enshrined — no voting, no subjectivity.

  * TEAM Halls of Fame use a lower, franchise-scoped bar. A player is inducted
    into a team's hall either automatically (the same points, but counting only
    what they did while wearing that uniform, against a lower threshold) or
    manually from the team HOF page.

Why points instead of milestone thresholds (500 HR, 3000 H, …): O27's run
environment varies by league config and seasons are of variable length, so
fixed counting-stat milestones don't transfer. Awards, league-leadership, and
the already-normalized "+" stats are era-relative and travel cleanly.

Data sources (all survive the offseason rollover that wipes per-game stats):
  * player_career_lines  — full per-season line for every qualified player,
                           snapshotted by season_archive at archive time.
  * season_batting_leaders / season_pitching_leaders — top-10 per category,
                           the black-ink / gray-ink source.
  * season_awards        — MVP / Cy Young / ROY / WS MVP.
  * seasons              — champion_abbrev per season (ring attribution).
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Any

from o27v2 import db

# --------------------------------------------------------------------------
# Tunables. All points weights and gates live here so the Hall can be
# recalibrated without touching logic.
# --------------------------------------------------------------------------
PTS_MVP        = 5.0
PTS_CY_YOUNG   = 5.0
PTS_WS_MVP     = 3.0
PTS_ROY        = 2.0
PTS_RING       = 2.0
PTS_BLACK_INK  = 3.0    # led the league in a major category that season
PTS_GRAY_INK   = 0.5    # top-10 (rank 2..10) in a major category
PTS_ELITE_SEASON = 1.0  # + stat >= ELITE_PLUS
PTS_GREAT_SEASON = 0.5  # + stat >= GREAT_PLUS (does not stack with elite)
PTS_PER_SEASON   = 0.25 # longevity / accumulation

ELITE_PLUS = 140.0
GREAT_PLUS = 120.0

AWARD_POINTS = {
    "mvp":      PTS_MVP,
    "cy_young": PTS_CY_YOUNG,
    "ws_mvp":   PTS_WS_MVP,
    "roy":      PTS_ROY,
}

# League gate: enshrine when ALL of these hold.
LEAGUE_MIN_SEASONS = 8     # qualified seasons logged
LEAGUE_MIN_AGE     = 34    # past peak — proxy for a near-complete career
LEAGUE_THRESHOLD   = 20.0  # HOF points

# Team criteria path (manual induction ignores these).
TEAM_MIN_SEASONS = 4
TEAM_THRESHOLD   = 8.0

# Categories that count toward black/gray ink. Match the categories
# season_archive._snapshot_leaders actually persists.
MAJOR_BATTING_CATS  = {"avg", "hr", "rbi", "ops", "wrc_plus"}
MAJOR_PITCHING_CATS = {"w", "werra", "k", "wera_plus"}


# --------------------------------------------------------------------------
# Lookups built once per evaluation pass.
# --------------------------------------------------------------------------
def _build_ink_lookup() -> dict[tuple, dict[str, int]]:
    """(season_id, player_name, team_abbrev) -> {'black': n, 'gray': n}.

    Leaders are denormalized to (player_name, team_abbrev) with no player_id,
    so career lines are matched back on that triple. Rank 1 = black ink;
    ranks 2..10 = gray ink. Only the major categories count.
    """
    out: dict[tuple, dict[str, int]] = defaultdict(lambda: {"black": 0, "gray": 0})

    def _accumulate(rows: list[dict], majors: set[str]) -> None:
        for r in rows:
            if r["category"] not in majors:
                continue
            key = (r["season_id"], r["player_name"], r["team_abbrev"])
            if r["rank"] == 1:
                out[key]["black"] += 1
            else:
                out[key]["gray"] += 1

    _accumulate(
        db.fetchall(
            "SELECT season_id, category, rank, player_name, team_abbrev "
            "FROM season_batting_leaders"
        ),
        MAJOR_BATTING_CATS,
    )
    _accumulate(
        db.fetchall(
            "SELECT season_id, category, rank, player_name, team_abbrev "
            "FROM season_pitching_leaders"
        ),
        MAJOR_PITCHING_CATS,
    )
    return out


def _build_award_lookup() -> dict[int, list[dict]]:
    """player_id -> list of {'category', 'season', 'team_abbrev'}."""
    out: dict[int, list[dict]] = defaultdict(list)
    for r in db.fetchall(
        "SELECT season, category, player_id, team_abbrev FROM season_awards"
    ):
        pid = r.get("player_id")
        if pid is None or r["category"] not in AWARD_POINTS:
            continue
        out[pid].append(
            {"category": r["category"], "season": r["season"],
             "team_abbrev": r.get("team_abbrev") or ""}
        )
    return out


def _build_champion_lookup() -> dict[int, str]:
    """season_number -> champion team abbrev."""
    out: dict[int, str] = {}
    for r in db.fetchall(
        "SELECT season_number, champion_abbrev FROM seasons"
    ):
        if r.get("champion_abbrev"):
            out[r["season_number"]] = r["champion_abbrev"]
    return out


def _build_team_abbrev_map() -> dict[str, int]:
    return {
        r["abbrev"]: r["id"]
        for r in db.fetchall("SELECT id, abbrev FROM teams")
        if r.get("abbrev")
    }


def _current_age_map() -> dict[int, int]:
    return {
        r["id"]: r["age"]
        for r in db.fetchall("SELECT id, age FROM players")
        if r.get("age") is not None
    }


# --------------------------------------------------------------------------
# Per-season point attribution.
# --------------------------------------------------------------------------
def _season_components(
    line: dict,
    ink_lookup: dict[tuple, dict[str, int]],
    champ_by_season: dict[int, str],
) -> dict[str, float]:
    """Points a single season line contributes (excluding awards, which are
    handled separately so they can be attributed by team cleanly)."""
    is_pitcher = bool(line.get("is_pitcher"))
    plus = float((line.get("wera_plus") if is_pitcher else line.get("wrc_plus")) or 100)

    black = gray = 0
    ink = ink_lookup.get(
        (line["season_id"], line["player_name"], line.get("team_abbrev"))
    )
    if ink:
        black, gray = ink["black"], ink["gray"]

    ring = 1 if champ_by_season.get(line.get("season_number")) == line.get("team_abbrev") \
        and line.get("team_abbrev") else 0

    elite = 1 if plus >= ELITE_PLUS else 0
    great = 1 if (not elite and plus >= GREAT_PLUS) else 0

    return {
        "black_ink": black,
        "gray_ink": gray,
        "rings": ring,
        "elite_seasons": elite,
        "great_seasons": great,
        "points": (
            black * PTS_BLACK_INK
            + gray * PTS_GRAY_INK
            + ring * PTS_RING
            + elite * PTS_ELITE_SEASON
            + great * PTS_GREAT_SEASON
            + PTS_PER_SEASON
        ),
    }


def _career_summary(lines: list[dict], is_pitcher: bool) -> str:
    n = len(lines)
    if is_pitcher:
        w = sum(int(l.get("w") or 0) for l in lines)
        k = sum(int(l.get("p_k") or 0) for l in lines)
        outs = sum(int(l.get("outs") or 0) for l in lines)
        er = sum(int(l.get("er") or 0) for l in lines)
        wera = (27.0 * er / outs) if outs else 0.0
        return f"{n} seasons · {w} W · {k} K · {wera:.2f} wERA~"
    h = sum(int(l.get("h") or 0) for l in lines)
    hr = sum(int(l.get("hr") or 0) for l in lines)
    rbi = sum(int(l.get("rbi") or 0) for l in lines)
    return f"{n} seasons · {h} H · {hr} HR · {rbi} RBI"


def _primary_team(lines: list[dict]) -> str:
    """The team a player is most associated with: most seasons, latest wins ties."""
    by_team: dict[str, list[int]] = defaultdict(list)
    for l in lines:
        ab = l.get("team_abbrev") or ""
        if ab:
            by_team[ab].append(int(l.get("season_number") or 0))
    if not by_team:
        return ""
    return max(by_team.items(), key=lambda kv: (len(kv[1]), max(kv[1])))[0]


# --------------------------------------------------------------------------
# Whole-league evaluation.
# --------------------------------------------------------------------------
def compute_all() -> list[dict]:
    """Return a HOF metrics dict for every player who has any career line.

    Each dict carries league-level totals plus a per-team breakdown
    (`teams`: abbrev -> {seasons, points, ...}) for the team-hall logic.
    """
    lines = db.fetchall(
        "SELECT * FROM player_career_lines ORDER BY player_id, season_number"
    )
    if not lines:
        return []

    ink_lookup = _build_ink_lookup()
    award_lookup = _build_award_lookup()
    champ_by_season = _build_champion_lookup()
    age_map = _current_age_map()

    by_player: dict[int, list[dict]] = defaultdict(list)
    for l in lines:
        by_player[l["player_id"]].append(l)

    out: list[dict] = []
    for pid, plines in by_player.items():
        is_pitcher = bool(plines[-1].get("is_pitcher"))
        position = plines[-1].get("position") or ""
        player_name = plines[-1].get("player_name") or ""

        # League aggregate + per-team accumulation.
        agg = {"black_ink": 0, "gray_ink": 0, "rings": 0,
               "elite_seasons": 0, "great_seasons": 0, "points": 0.0}
        team_acc: dict[str, dict] = defaultdict(
            lambda: {"seasons": 0, "points": 0.0, "black_ink": 0,
                     "gray_ink": 0, "rings": 0,
                     "elite_seasons": 0, "great_seasons": 0,
                     "awards": defaultdict(int)}
        )
        for l in plines:
            comp = _season_components(l, ink_lookup, champ_by_season)
            ab = l.get("team_abbrev") or ""
            for key in ("black_ink", "gray_ink", "rings",
                        "elite_seasons", "great_seasons"):
                agg[key] += comp[key]
            agg["points"] += comp["points"]
            if ab:
                t = team_acc[ab]
                t["seasons"] += 1
                t["points"] += comp["points"]
                for key in ("black_ink", "gray_ink", "rings",
                            "elite_seasons", "great_seasons"):
                    t[key] += comp[key]

        # Awards (league + team attribution).
        awards: dict[str, int] = defaultdict(int)
        for a in award_lookup.get(pid, []):
            awards[a["category"]] += 1
            agg["points"] += AWARD_POINTS[a["category"]]
            ab = a.get("team_abbrev") or ""
            if ab and ab in team_acc:
                team_acc[ab]["points"] += AWARD_POINTS[a["category"]]
                team_acc[ab]["awards"][a["category"]] += 1

        seasons_played = len(plines)
        age = age_map.get(pid, plines[-1].get("age") or 0)
        league_eligible = (
            seasons_played >= LEAGUE_MIN_SEASONS and (age or 0) >= LEAGUE_MIN_AGE
        )

        out.append({
            "player_id": pid,
            "player_name": player_name,
            "is_pitcher": is_pitcher,
            "position": position,
            "age": age,
            "primary_team_abbrev": _primary_team(plines),
            "seasons_played": seasons_played,
            "hof_points": round(agg["points"], 2),
            "black_ink": agg["black_ink"],
            "gray_ink": agg["gray_ink"],
            "rings": agg["rings"],
            "elite_seasons": agg["elite_seasons"],
            "great_seasons": agg["great_seasons"],
            "awards": dict(awards),
            "league_eligible": league_eligible,
            "career_summary": _career_summary(plines, is_pitcher),
            "teams": {
                ab: {**{k: v for k, v in t.items() if k != "awards"},
                     "points": round(t["points"], 2),
                     "awards": dict(t["awards"])}
                for ab, t in team_acc.items()
            },
        })

    out.sort(key=lambda d: d["hof_points"], reverse=True)
    return out


# --------------------------------------------------------------------------
# Induction (called at season archive, or backfilled from the CLI).
# --------------------------------------------------------------------------
def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds")


def run_league_inductions(season_number: int, year: int | None) -> list[dict]:
    """Enshrine every eligible player over the league points threshold who
    isn't already in the Hall. Returns the newly inducted players."""
    already = {
        r["player_id"]
        for r in db.fetchall("SELECT player_id FROM hof_inductees")
    }
    inducted: list[dict] = []
    for c in compute_all():
        if c["player_id"] in already:
            continue
        if not c["league_eligible"]:
            continue
        if c["hof_points"] < LEAGUE_THRESHOLD:
            continue
        db.execute(
            """INSERT OR IGNORE INTO hof_inductees
               (player_id, player_name, primary_team_abbrev, is_pitcher,
                position, inducted_season_number, inducted_year, hof_points,
                seasons_played, career_summary, inducted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (c["player_id"], c["player_name"], c["primary_team_abbrev"],
             1 if c["is_pitcher"] else 0, c["position"], season_number, year,
             c["hof_points"], c["seasons_played"], c["career_summary"], _now()),
        )
        inducted.append(c)
    return inducted


def run_team_inductions(season_number: int, year: int | None) -> list[dict]:
    """Auto-induct players into a team hall when they clear the team criteria
    for that franchise. Skips teams that no longer exist and players already
    in the team's hall. Manual inductions are never overwritten."""
    abbrev_to_id = _build_team_abbrev_map()
    existing = {
        (r["team_id"], r["player_id"])
        for r in db.fetchall(
            "SELECT team_id, player_id FROM team_hof_inductees"
        )
    }
    inducted: list[dict] = []
    for c in compute_all():
        for ab, t in c["teams"].items():
            team_id = abbrev_to_id.get(ab)
            if team_id is None:
                continue
            if (team_id, c["player_id"]) in existing:
                continue
            if t["seasons"] < TEAM_MIN_SEASONS or t["points"] < TEAM_THRESHOLD:
                continue
            db.execute(
                """INSERT OR IGNORE INTO team_hof_inductees
                   (team_id, team_abbrev, player_id, player_name, is_pitcher,
                    position, inducted_season_number, inducted_year,
                    team_points, seasons_with_team, method, career_summary,
                    inducted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'criteria', ?, ?)""",
                (team_id, ab, c["player_id"], c["player_name"],
                 1 if c["is_pitcher"] else 0, c["position"], season_number,
                 year, round(t["points"], 2), t["seasons"],
                 c["career_summary"], _now()),
            )
            inducted.append({**c, "team_abbrev": ab,
                             "team_points": round(t["points"], 2),
                             "seasons_with_team": t["seasons"]})
    return inducted


def run_inductions(season_number: int, year: int | None) -> dict[str, list[dict]]:
    """End-of-season hook: run both halls. Never raises out."""
    league = run_league_inductions(season_number, year)
    team = run_team_inductions(season_number, year)
    return {"league": league, "team": team}


# --------------------------------------------------------------------------
# Manual team induction / removal (web write path).
# --------------------------------------------------------------------------
def induct_into_team_manual(
    team_id: int, player_id: int,
    season_number: int | None = None, year: int | None = None,
) -> bool:
    """Manually enshrine a player in a team's hall. Returns False if the team
    or player is unknown, or the player is already in that team's hall."""
    team = db.fetchone("SELECT id, abbrev FROM teams WHERE id = ?", (team_id,))
    if not team:
        return False
    if db.fetchone(
        "SELECT 1 FROM team_hof_inductees WHERE team_id = ? AND player_id = ?",
        (team_id, player_id),
    ):
        return False

    metrics = next(
        (c for c in compute_all() if c["player_id"] == player_id), None
    )
    player = db.fetchone(
        "SELECT name, is_pitcher, position FROM players WHERE id = ?",
        (player_id,),
    )
    if metrics is None and player is None:
        return False

    tinfo = (metrics or {}).get("teams", {}).get(team["abbrev"], {})
    db.execute(
        """INSERT OR IGNORE INTO team_hof_inductees
           (team_id, team_abbrev, player_id, player_name, is_pitcher,
            position, inducted_season_number, inducted_year, team_points,
            seasons_with_team, method, career_summary, inducted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)""",
        (team_id, team["abbrev"], player_id,
         (metrics or {}).get("player_name") or (player or {}).get("name") or "",
         1 if (metrics or {}).get("is_pitcher", (player or {}).get("is_pitcher")) else 0,
         (metrics or {}).get("position") or (player or {}).get("position") or "",
         season_number, year,
         round(float(tinfo.get("points", 0.0)), 2),
         int(tinfo.get("seasons", 0)),
         (metrics or {}).get("career_summary", ""), _now()),
    )
    return True


def remove_from_team(team_id: int, player_id: int) -> None:
    db.execute(
        "DELETE FROM team_hof_inductees WHERE team_id = ? AND player_id = ?",
        (team_id, player_id),
    )


# --------------------------------------------------------------------------
# Read helpers for the web layer.
# --------------------------------------------------------------------------
def league_hof() -> list[dict]:
    return db.fetchall(
        "SELECT * FROM hof_inductees "
        "ORDER BY hof_points DESC, inducted_season_number ASC"
    )


def team_hof(team_id: int) -> list[dict]:
    return db.fetchall(
        "SELECT * FROM team_hof_inductees WHERE team_id = ? "
        "ORDER BY team_points DESC, inducted_season_number ASC",
        (team_id,),
    )


def candidates(limit: int | None = None) -> list[dict]:
    """Every player's HOF metrics, ranked, annotated with hall membership.
    This is the 'monitor board' — who's tracking toward Cooperstown."""
    in_league = {r["player_id"] for r in db.fetchall(
        "SELECT player_id FROM hof_inductees")}
    rows = compute_all()
    for r in rows:
        r["in_league_hof"] = r["player_id"] in in_league
        r["pct_to_threshold"] = round(
            min(100.0, 100.0 * r["hof_points"] / LEAGUE_THRESHOLD), 1
        )
    return rows[:limit] if limit else rows
