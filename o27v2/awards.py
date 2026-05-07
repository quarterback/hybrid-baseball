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

from o27v2 import db


# Min-qualifying thresholds as a fraction of total scheduled team-games.
# (Real MLB: 502 PA = 3.1 PA/G × 162 G; we approximate at 3.1 × G.)
_MIN_PA_FACTOR_HITTER  = 3.1   # PA per team-game → min PA = factor × G
_MIN_OUTS_FACTOR_PITCH = 3.0   # outs per team-game ≈ 1 IP/G; light bar so
                                # short-burst arms stay eligible at small samples
_ROOKIE_MAX_AGE        = 23    # proxy for "first-year" without service-time tracking


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

    # MVP — best hitter by OPS.
    if bats:
        mvp = max(bats, key=_ops)
        ops = _ops(mvp)
        _record_award(
            season, "mvp", None, mvp["player_id"], mvp["name"], mvp["team_abbrev"],
            f"{mvp['hits']}-{mvp['ab']}, {mvp['hr']} HR, {mvp['rbi']} RBI, "
            f"{_ops(mvp):.3f} OPS",
        )
        awarded.append({"category": "mvp", "name": mvp["name"], "ops": round(ops, 3)})

    # Cy Young — best pitcher by lowest ERA among qualifiers.
    if arms:
        cy = min(arms, key=_era)
        _record_award(
            season, "cy_young", None, cy["player_id"], cy["name"], cy["team_abbrev"],
            f"{(cy['outs'] or 0) // 3}.{(cy['outs'] or 0) % 3} IP, "
            f"{cy['k']} K, {cy['bb']} BB, {_era(cy):.2f} ERA",
        )
        awarded.append({"category": "cy_young", "name": cy["name"], "era": round(_era(cy), 2)})

    # Rookie of the Year — best <=23 season among qualifiers, weighted
    # by the larger of (OPS / .800) and (4.0 / ERA) so a young arm and
    # a young bat compete on the same scale. League .800 / 4.0 are
    # rough O27 medians; a real comparator would use league averages
    # for the season but this is plenty good for headline picks.
    rookies_b = [b for b in bats if (b.get("age") or 99) <= _ROOKIE_MAX_AGE]
    rookies_p = [p for p in arms if (p.get("age") or 99) <= _ROOKIE_MAX_AGE]

    rookie_score: list[tuple[float, dict, str]] = []
    for b in rookies_b:
        rookie_score.append((_ops(b) / 0.800, b, "bat"))
    for p in rookies_p:
        rookie_score.append((4.0 / max(0.5, _era(p)), p, "arm"))

    if rookie_score:
        rookie_score.sort(key=lambda r: r[0], reverse=True)
        score, who, kind = rookie_score[0]
        if kind == "bat":
            head = (f"age {who['age']}, {_ops(who):.3f} OPS, "
                    f"{who['hr']} HR, {who['rbi']} RBI")
        else:
            head = (f"age {who['age']}, {(who['outs'] or 0) // 3}.{(who['outs'] or 0) % 3} IP, "
                    f"{who['k']} K, {_era(who):.2f} ERA")
        _record_award(
            season, "roy", None, who["player_id"], who["name"], who["team_abbrev"], head,
        )
        awarded.append({"category": "roy", "name": who["name"], "score": round(score, 2)})

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
        candidates.append((_ops(b), b, "bat"))
    for p in arms:
        # Convert pitcher's series ERA into an OPS-comparable score.
        era = _era(p)
        candidates.append((max(0.0, 1.6 - era / 8.0), p, "arm"))

    if not candidates:
        return None
    candidates.sort(key=lambda r: r[0], reverse=True)
    _, who, kind = candidates[0]
    if kind == "bat":
        head = (f"WS: {who['hits']}-{who['ab']}, {who['hr']} HR, {who['rbi']} RBI, "
                f"{_ops(who):.3f} OPS")
    else:
        head = (f"WS: {(who['outs'] or 0) // 3}.{(who['outs'] or 0) % 3} IP, "
                f"{who['k']} K, {who['bb']} BB, {_era(who):.2f} ERA")
    _record_award(
        season, "ws_mvp", None, who["player_id"], who["name"], who["team_abbrev"], head,
    )
    return {"ok": True, "name": who["name"]}


def get_awards(season: int = 1) -> list[dict]:
    return db.fetchall(
        "SELECT * FROM season_awards WHERE season = ? ORDER BY "
        "CASE category "
        "  WHEN 'mvp' THEN 1 WHEN 'cy_young' THEN 2 WHEN 'roy' THEN 3 "
        "  WHEN 'ws_mvp' THEN 4 ELSE 99 END, id ASC",
        (season,),
    )
