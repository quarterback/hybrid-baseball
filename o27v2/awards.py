"""
Phase 4e — Season Awards.

Regular-season awards (MVP, Cy Young, Rookie of the Year) are
selected the moment the regular season finishes (called from the
playoff-initiation hook). World Series MVP is selected when the
championship series ends.

Selection is direct on game-stat tables — we don't go through the
big web aggregator. Awards rows persist on `season_awards` so a
fresh league rerun can show the previous winners alongside the
current league.

Thresholds are scaled to the league's actual game count so a 30-game
test season qualifies players who'd be sub-threshold in a 162-game
league but are clearly category leaders for the sample size.
"""
from __future__ import annotations

import datetime as _dt
import hashlib as _hashlib
import random as _random

from o27v2 import db


# Min-qualifying thresholds as a fraction of total scheduled team-games.
# (Real MLB: 502 PA = 3.1 PA/G × 162 G; we approximate at 3.1 × G.)
_MIN_PA_FACTOR_HITTER  = 3.1   # PA per team-game → min PA = factor × G
_MIN_OUTS_FACTOR_PITCH = 3.0   # outs per team-game ≈ 1 IP/G; light bar so
                                # short-burst arms stay eligible at small samples
_ROOKIE_MAX_AGE        = 23    # proxy for "first-year" without service-time tracking

# BBWAA point weights for the official MVP/Cy Young ballots.
_BBWAA_POINTS = {1: 14, 2: 9, 3: 8, 4: 7, 5: 6, 6: 5, 7: 4, 8: 3, 9: 2, 10: 1}
_BBWAA_BALLOT_DEPTH = 10
_N_VOTERS = 30        # roughly BBWAA chapter size; gives clean point spreads
_VOTER_NOISE = 0.07   # gaussian σ as a fraction of |score|


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _season_team_games() -> int:
    """Average team-games scheduled in the regular season — drives the
    min-PA / min-outs qualifying thresholds."""
    row = db.fetchone("""
        SELECT 1.0 * SUM(team_games) / NULLIF(COUNT(*), 0) AS avg_g
        FROM (
            SELECT t.id,
                   (SELECT COUNT(*) FROM games g
                    WHERE COALESCE(g.is_playoff, 0) = 0
                      AND (g.home_team_id = t.id OR g.away_team_id = t.id)) AS team_games
            FROM teams t
        )
    """)
    return int((row or {}).get("avg_g") or 30)


def _aggregate_batting(season: int) -> list[dict]:
    """Per-player aggregated batting line for the regular season."""
    return db.fetchall("""
        SELECT p.id AS player_id, p.name, p.age, p.position,
               t.abbrev AS team_abbrev, t.id AS team_id,
               SUM(s.pa)      AS pa,
               SUM(s.ab)      AS ab,
               SUM(s.hits)    AS hits,
               SUM(s.doubles) AS doubles,
               SUM(s.triples) AS triples,
               SUM(s.hr)      AS hr,
               SUM(s.bb)      AS bb,
               SUM(s.hbp)     AS hbp,
               SUM(s.k)       AS k,
               SUM(s.runs)    AS runs,
               SUM(s.rbi)     AS rbi
        FROM game_batter_stats s
        JOIN games g   ON g.id = s.game_id
        JOIN players p ON p.id = s.player_id
        LEFT JOIN teams t ON t.id = p.team_id
        WHERE COALESCE(g.is_playoff, 0) = 0
        GROUP BY p.id
    """)


def _aggregate_pitching(season: int) -> list[dict]:
    """Per-player aggregated pitching line for the regular season."""
    return db.fetchall("""
        SELECT p.id AS player_id, p.name, p.age, p.position,
               t.abbrev AS team_abbrev, t.id AS team_id,
               SUM(s.batters_faced) AS bf,
               SUM(s.outs_recorded) AS outs,
               SUM(s.er)            AS er,
               SUM(s.hits_allowed)  AS h_allowed,
               SUM(s.bb)            AS bb,
               SUM(s.k)             AS k,
               SUM(s.hr_allowed)    AS hr_allowed,
               SUM(s.runs_allowed)  AS r_allowed
        FROM game_pitcher_stats s
        JOIN games g   ON g.id = s.game_id
        JOIN players p ON p.id = s.player_id
        LEFT JOIN teams t ON t.id = p.team_id
        WHERE COALESCE(g.is_playoff, 0) = 0
        GROUP BY p.id
    """)


def _ops(b: dict) -> float:
    pa  = b.get("pa") or 0
    ab  = b.get("ab") or 0
    h   = b.get("hits") or 0
    bb  = b.get("bb") or 0
    hbp = b.get("hbp") or 0
    if ab == 0 or pa == 0:
        return 0.0
    obp = (h + bb + hbp) / pa
    singles = h - (b.get("doubles") or 0) - (b.get("triples") or 0) - (b.get("hr") or 0)
    tb = singles + 2 * (b.get("doubles") or 0) + 3 * (b.get("triples") or 0) + 4 * (b.get("hr") or 0)
    slg = tb / ab
    return obp + slg


def _era(p: dict) -> float:
    """Runs allowed per 27 outs — O27 is a 27-out single-inning sport."""
    outs = p.get("outs") or 0
    er   = p.get("er") or 0
    if outs == 0:
        return 99.0
    return er * 27.0 / outs


def _record_award(season: int, category: str, league: str | None,
                  player_id: int | None, player_name: str | None,
                  team_abbrev: str | None, headline: str) -> int:
    return db.execute(
        "INSERT INTO season_awards "
        "(season, category, league, player_id, player_name, team_abbrev, "
        " headline_stat, awarded_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (season, category, league or "", player_id, player_name,
         team_abbrev, headline, _dt.datetime.utcnow().isoformat(timespec="seconds")),
    )


def _run_vote(season: int, category: str,
              scored_candidates: list[tuple[float, dict, str]]) -> dict | None:
    """Simulate _N_VOTERS BBWAA-style voters ranking the candidates.
    Each voter applies a deterministic per-(season, category, voter, player)
    gaussian jitter so the ballots are reproducible but not identical.
    Persists each voter's top-10 to `award_ballots` and returns the winning
    candidate dict (or None if there were no candidates).

    `scored_candidates`: list of (raw_score, candidate_dict, headline) tuples.
    Higher raw_score = better.
    """
    if not scored_candidates:
        return None

    rows: list[tuple] = []
    point_tally: dict[int, int] = {}

    for voter_id in range(1, _N_VOTERS + 1):
        jittered: list[tuple[float, dict, str]] = []
        for score, cand, headline in scored_candidates:
            pid = cand.get("player_id") or 0
            key = f"{season}:{category}:{voter_id}:{pid}".encode()
            seed = int(_hashlib.md5(key).hexdigest()[:8], 16)
            rng = _random.Random(seed)
            jitter = rng.gauss(0.0, _VOTER_NOISE) * max(abs(score), 1.0)
            jittered.append((score + jitter, cand, headline))
        jittered.sort(key=lambda r: r[0], reverse=True)
        for rank, (_, cand, headline) in enumerate(
                jittered[:_BBWAA_BALLOT_DEPTH], start=1):
            rows.append((
                season, category, voter_id, rank,
                cand.get("player_id"), cand.get("name"),
                cand.get("team_abbrev"), headline,
            ))
            point_tally[cand.get("player_id")] = (
                point_tally.get(cand.get("player_id"), 0)
                + _BBWAA_POINTS.get(rank, 0)
            )

    db.executemany(
        "INSERT INTO award_ballots "
        "(season, category, voter_id, rank, player_id, player_name, "
        " team_abbrev, headline_stat) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )

    winner_pid = max(point_tally, key=point_tally.get)
    for _, cand, _h in scored_candidates:
        if cand.get("player_id") == winner_pid:
            return cand
    return scored_candidates[0][1]


# ---------------------------------------------------------------------------
# Headline builders (one-line stat blurb per category)
# ---------------------------------------------------------------------------

def _headline_mvp(b: dict) -> str:
    return (f"{b.get('hits') or 0}-{b.get('ab') or 0}, "
            f"{b.get('hr') or 0} HR, {b.get('rbi') or 0} RBI, "
            f"{_ops(b):.3f} OPS")


def _headline_cy(p: dict) -> str:
    outs = p.get("outs") or 0
    return (f"{outs // 3}.{outs % 3} IP, "
            f"{p.get('k') or 0} K, {p.get('bb') or 0} BB, "
            f"{_era(p):.2f} ERA")


def _headline_rookie_bat(b: dict) -> str:
    return (f"age {b.get('age')}, {_ops(b):.3f} OPS, "
            f"{b.get('hr') or 0} HR, {b.get('rbi') or 0} RBI")


def _headline_rookie_arm(p: dict) -> str:
    outs = p.get("outs") or 0
    return (f"age {p.get('age')}, {outs // 3}.{outs % 3} IP, "
            f"{p.get('k') or 0} K, {_era(p):.2f} ERA")


def _headline_ws_bat(b: dict) -> str:
    return (f"WS: {b.get('hits') or 0}-{b.get('ab') or 0}, "
            f"{b.get('hr') or 0} HR, {b.get('rbi') or 0} RBI, "
            f"{_ops(b):.3f} OPS")


def _headline_ws_arm(p: dict) -> str:
    outs = p.get("outs") or 0
    return (f"WS: {outs // 3}.{outs % 3} IP, "
            f"{p.get('k') or 0} K, {p.get('bb') or 0} BB, "
            f"{_era(p):.2f} ERA")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def select_regular_season_awards(season: int = 1) -> dict:
    """Pick MVP / Cy Young / Rookie of the Year for the regular season.
    Idempotent — returns the existing row count if awards have already
    been granted for this season."""
    existing = db.fetchone(
        "SELECT COUNT(*) AS n FROM season_awards WHERE season = ? "
        "AND category IN ('mvp', 'cy_young', 'roy')", (season,)
    )
    if existing and existing["n"] > 0:
        return {"ok": True, "already": True, "count": existing["n"]}

    g = _season_team_games()
    min_pa   = max(20, int(_MIN_PA_FACTOR_HITTER  * g))
    min_outs = max(15, int(_MIN_OUTS_FACTOR_PITCH * g))

    bats = [b for b in _aggregate_batting(season) if (b["pa"] or 0) >= min_pa]
    arms = [p for p in _aggregate_pitching(season) if (p["outs"] or 0) >= min_outs]

    awarded: list[dict] = []

    # MVP — every qualifying bat is a candidate, ranked by OPS.
    if bats:
        mvp_cands = [(_ops(b), b, _headline_mvp(b)) for b in bats]
        winner = _run_vote(season, "mvp", mvp_cands)
        if winner:
            _record_award(season, "mvp", None, winner["player_id"],
                          winner["name"], winner["team_abbrev"],
                          _headline_mvp(winner))
            awarded.append({"category": "mvp", "name": winner["name"],
                            "ops": round(_ops(winner), 3)})

    # Cy Young — every qualifying arm is a candidate, ranked by -ERA so
    # lower is better.
    if arms:
        cy_cands = [(-_era(p), p, _headline_cy(p)) for p in arms]
        winner = _run_vote(season, "cy_young", cy_cands)
        if winner:
            _record_award(season, "cy_young", None, winner["player_id"],
                          winner["name"], winner["team_abbrev"],
                          _headline_cy(winner))
            awarded.append({"category": "cy_young", "name": winner["name"],
                            "era": round(_era(winner), 2)})

    # Rookie of the Year — bats and arms compete on the same scale via
    # the larger of (OPS / .800) and (4.0 / ERA). League .800 / 4.0 are
    # rough O27 medians.
    rookies_b = [b for b in bats if (b.get("age") or 99) <= _ROOKIE_MAX_AGE]
    rookies_p = [p for p in arms if (p.get("age") or 99) <= _ROOKIE_MAX_AGE]

    rookie_cands: list[tuple[float, dict, str]] = []
    for b in rookies_b:
        rookie_cands.append((_ops(b) / 0.800, b, _headline_rookie_bat(b)))
    for p in rookies_p:
        rookie_cands.append((4.0 / max(0.5, _era(p)), p, _headline_rookie_arm(p)))

    if rookie_cands:
        winner = _run_vote(season, "roy", rookie_cands)
        if winner:
            # Headline depends on whether the winner is a bat or an arm.
            head = (_headline_rookie_bat(winner) if "ab" in winner
                    else _headline_rookie_arm(winner))
            _record_award(season, "roy", None, winner["player_id"],
                          winner["name"], winner["team_abbrev"], head)
            awarded.append({"category": "roy", "name": winner["name"]})

    return {"ok": True, "awarded": awarded, "min_pa": min_pa, "min_outs": min_outs}


def select_ws_mvp(season: int = 1) -> dict | None:
    """Pick the World Series MVP from the championship team's players,
    using their stats during the final (rounds_to_final=0) series.
    Called when the final series concludes."""
    final_series = db.fetchone("""
        SELECT * FROM playoff_series
        WHERE season = ? AND rounds_to_final = 0 AND winner_team_id IS NOT NULL
        ORDER BY id DESC LIMIT 1
    """, (season,))
    if not final_series:
        return None

    existing = db.fetchone(
        "SELECT COUNT(*) AS n FROM season_awards WHERE season = ? AND category = 'ws_mvp'",
        (season,),
    )
    if existing and existing["n"] > 0:
        return {"ok": True, "already": True}

    champ_id = final_series["winner_team_id"]
    sid      = final_series["id"]

    # Stats from the championship series only.
    bats = db.fetchall("""
        SELECT p.id AS player_id, p.name, t.abbrev AS team_abbrev,
               SUM(s.pa) AS pa, SUM(s.ab) AS ab, SUM(s.hits) AS hits,
               SUM(s.doubles) AS doubles, SUM(s.triples) AS triples,
               SUM(s.hr) AS hr, SUM(s.bb) AS bb, SUM(s.hbp) AS hbp,
               SUM(s.rbi) AS rbi, SUM(s.k) AS k
        FROM game_batter_stats s
        JOIN games g ON g.id = s.game_id
        JOIN players p ON p.id = s.player_id
        LEFT JOIN teams t ON t.id = p.team_id
        WHERE g.series_id = ? AND s.team_id = ?
        GROUP BY p.id
        HAVING SUM(s.pa) >= 4
    """, (sid, champ_id))

    arms = db.fetchall("""
        SELECT p.id AS player_id, p.name, t.abbrev AS team_abbrev,
               SUM(s.batters_faced) AS bf, SUM(s.outs_recorded) AS outs,
               SUM(s.er) AS er, SUM(s.k) AS k, SUM(s.bb) AS bb,
               SUM(s.hr_allowed) AS hr_allowed
        FROM game_pitcher_stats s
        JOIN games g ON g.id = s.game_id
        JOIN players p ON p.id = s.player_id
        LEFT JOIN teams t ON t.id = p.team_id
        WHERE g.series_id = ? AND s.team_id = ?
        GROUP BY p.id
        HAVING SUM(s.outs_recorded) >= 9
    """, (sid, champ_id))

    candidates: list[tuple[float, dict, str]] = []
    for b in bats:
        candidates.append((_ops(b), b, _headline_ws_bat(b)))
    for p in arms:
        # Convert pitcher's series ERA into an OPS-comparable score.
        era = _era(p)
        candidates.append((max(0.0, 1.6 - era / 8.0), p, _headline_ws_arm(p)))

    if not candidates:
        return None

    winner = _run_vote(season, "ws_mvp", candidates)
    if not winner:
        return None
    head = (_headline_ws_bat(winner) if "ab" in winner
            else _headline_ws_arm(winner))
    _record_award(season, "ws_mvp", None, winner["player_id"],
                  winner["name"], winner["team_abbrev"], head)
    return {"ok": True, "name": winner["name"]}


def get_award_results(season: int = 1, category: str | None = None,
                      limit: int = 5) -> list[dict]:
    """Return the top-N candidates per category by BBWAA-weighted points,
    with vote breakdowns (1st / 2nd / 3rd place counts, total ballots
    appeared on, headline stat). Used by /playoffs to render the
    awards top-5 tables.

    Caller can pass `category` to restrict to a single award; otherwise
    every category present in `award_ballots` is returned. The result
    is already pre-sliced to `limit` rows per category — pre-sliced via
    a CTE so callers don't need to do per-group pagination.
    """
    where = "WHERE season = ?"
    args: list = [season]
    if category:
        where += " AND category = ?"
        args.append(category)

    rows = db.fetchall(
        f"""
        SELECT category, player_id, player_name, team_abbrev,
               MAX(headline_stat) AS headline_stat,
               SUM(CASE rank
                     WHEN 1 THEN 14 WHEN 2 THEN 9 WHEN 3 THEN 8
                     WHEN 4 THEN 7  WHEN 5 THEN 6 WHEN 6 THEN 5
                     WHEN 7 THEN 4  WHEN 8 THEN 3 WHEN 9 THEN 2
                     WHEN 10 THEN 1 ELSE 0
                   END) AS points,
               SUM(CASE WHEN rank = 1 THEN 1 ELSE 0 END) AS first_place,
               SUM(CASE WHEN rank = 2 THEN 1 ELSE 0 END) AS second_place,
               SUM(CASE WHEN rank = 3 THEN 1 ELSE 0 END) AS third_place,
               COUNT(*)                                  AS appeared_on
        FROM award_ballots
        {where}
        GROUP BY category, player_id
        ORDER BY category, points DESC
        """,
        tuple(args),
    )

    # Slice to `limit` rows per category in Python — SQLite lacks a clean
    # per-group LIMIT and the candidate set is small enough that a single
    # post-query pass is cheaper than CTE gymnastics.
    out: list[dict] = []
    seen: dict[str, int] = {}
    for r in rows:
        cat = r["category"]
        if seen.get(cat, 0) >= limit:
            continue
        seen[cat] = seen.get(cat, 0) + 1
        out.append(r)
    return out


def get_awards(season: int = 1) -> list[dict]:
    return db.fetchall(
        "SELECT * FROM season_awards WHERE season = ? ORDER BY "
        "CASE category "
        "  WHEN 'mvp' THEN 1 WHEN 'cy_young' THEN 2 WHEN 'roy' THEN 3 "
        "  WHEN 'ws_mvp' THEN 4 ELSE 99 END, id ASC",
        (season,),
    )
