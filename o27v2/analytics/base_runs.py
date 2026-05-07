"""BaseRuns — sequencing / cluster-luck decomposition.

BaseRuns (David Smyth) predicts how many runs an offense *should* have
scored from its raw event counts (H, 2B, 3B, HR, BB, HBP, AB), with no
information about the order events arrived in. The residual

    actual_R − BaseRuns_R

is therefore pure sequencing/cluster luck — runners-on-base
distribution, hit-with-RISP timing, double-play avoidance, etc. The
same applied to the *opposing* offense (i.e. what the team allowed)
gives sequencing luck on defense.

Formula (Smyth standard form, with HBP via Tango):

    A = H + BB + HBP − HR        baserunners
    B = 1.4·TB − 0.6·H − 3·HR + 0.1·(BB+HBP)
    C = AB − H                   batting outs
    D = HR

    BsR = A · B / (B + C)  +  D

The B coefficients are MLB-fit. O27's run environment is ~22 R/G vs
MLB's ~9, so the *level* of BsR will be off, but the residuals are
what matter and they're invariant to a constant multiplicative
mis-scale once we re-center on the league. We re-scale B so that
SUM(BsR) = SUM(actual_R) league-wide, then report demeaned residuals.
A B-coefficient *shape* refit (analogous to pythag.py's k* refit) is
a worthwhile follow-up but is left for a separate module — this one
captures the within-league sequencing variance, which is what a
luck-decomposition needs.
"""
from __future__ import annotations

from o27v2 import db


def _base_runs(a: float, b: float, c: float, d: float) -> float:
    if b + c <= 0:
        return d
    return a * b / (b + c) + d


def _components(h: int, d2: int, d3: int, hr: int,
                bb: int, hbp: int, ab: int) -> tuple[float, float, float, float]:
    singles = h - d2 - d3 - hr
    tb = singles + 2 * d2 + 3 * d3 + 4 * hr
    a = h + bb + hbp - hr
    b = 1.4 * tb - 0.6 * h - 3 * hr + 0.1 * (bb + hbp)
    c = ab - h
    d = hr
    return a, b, c, d


def _team_offense_rows() -> list[dict]:
    """Per-team batting aggregates over regulation, played non-playoff games."""
    return db.fetchall(
        """
        SELECT t.id AS team_id, t.abbrev, t.name,
               COALESCE(SUM(b.ab), 0)      AS ab,
               COALESCE(SUM(b.hits), 0)    AS h,
               COALESCE(SUM(b.doubles), 0) AS d2,
               COALESCE(SUM(b.triples), 0) AS d3,
               COALESCE(SUM(b.hr), 0)      AS hr,
               COALESCE(SUM(b.bb), 0)      AS bb,
               COALESCE(SUM(b.hbp), 0)     AS hbp,
               COALESCE(SUM(b.runs), 0)    AS r
        FROM teams t
        LEFT JOIN game_batter_stats b ON b.team_id = t.id AND b.phase = 0
        LEFT JOIN games g ON g.id = b.game_id
        WHERE (g.id IS NULL) OR (g.played = 1 AND g.is_playoff = 0)
        GROUP BY t.id
        """
    )


def _team_defense_rows() -> list[dict]:
    """Per-team allowed counting stats — sum opponents' batting against them.

    For each team t: stats from game_batter_stats rows in t's games where
    the batter's team_id is the OPPONENT. Plus actual runs allowed (mirror
    of the home/away score in games).
    """
    return db.fetchall(
        """
        SELECT t.id AS team_id, t.abbrev, t.name,
               COALESCE(SUM(b.ab), 0)      AS ab,
               COALESCE(SUM(b.hits), 0)    AS h,
               COALESCE(SUM(b.doubles), 0) AS d2,
               COALESCE(SUM(b.triples), 0) AS d3,
               COALESCE(SUM(b.hr), 0)      AS hr,
               COALESCE(SUM(b.bb), 0)      AS bb,
               COALESCE(SUM(b.hbp), 0)     AS hbp
        FROM teams t
        JOIN games g ON (g.home_team_id = t.id OR g.away_team_id = t.id)
                    AND g.played = 1 AND g.is_playoff = 0
        JOIN game_batter_stats b ON b.game_id = g.id
                                AND b.team_id != t.id
                                AND b.phase = 0
        GROUP BY t.id
        """
    )


def _team_runs_allowed() -> dict[int, int]:
    """Actual runs allowed per team across regulation, played non-playoff games."""
    rows = db.fetchall(
        """
        SELECT t.id AS team_id,
               COALESCE(SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score
                                 WHEN g.away_team_id = t.id THEN g.home_score
                                 ELSE 0 END), 0) AS ra
        FROM teams t
        LEFT JOIN games g ON (g.home_team_id = t.id OR g.away_team_id = t.id)
                          AND g.played = 1 AND g.is_playoff = 0
        GROUP BY t.id
        """
    )
    return {r["team_id"]: r["ra"] for r in rows}


def build_base_runs_table() -> dict:
    """Per-team BaseRuns predictions and sequencing-luck residuals.

    Returns:
        {
            "n_teams":          int,
            "league_actual_rs": int,
            "league_pred_rs":   float,
            "league_actual_ra": int,
            "league_pred_ra":   float,
            "b_scale_offense":  float,   # multiplicative re-center
            "b_scale_defense":  float,
            "teams": [{
                team_id, abbrev, name,
                rs, rs_pred,            # offense actual / predicted
                ra, ra_pred,
                bsr_off,                # rs - rs_pred  (positive = clutch)
                bsr_def,                # ra - ra_pred  (positive = unlucky on D)
                bsr_total,              # bsr_off - bsr_def  (net sequencing luck, runs)
            }, …],   # sorted by |bsr_total| desc
        }
    """
    off_rows = _team_offense_rows()
    def_rows = _team_defense_rows()
    ra_actual = _team_runs_allowed()

    # ---- Offense pass: raw BaseRuns prediction.
    off_pred: dict[int, float] = {}
    league_pred_rs = 0.0
    league_actual_rs = 0
    for r in off_rows:
        a, b, c, d = _components(
            r["h"], r["d2"], r["d3"], r["hr"], r["bb"], r["hbp"], r["ab"]
        )
        pred = _base_runs(a, b, c, d)
        off_pred[r["team_id"]] = pred
        league_pred_rs += pred
        league_actual_rs += r["r"]
    # League re-center: scale so SUM(pred) == SUM(actual). Pure
    # multiplicative — preserves rank order, just maps mean residual to 0.
    b_scale_off = (league_actual_rs / league_pred_rs) if league_pred_rs > 0 else 1.0
    for tid in off_pred:
        off_pred[tid] *= b_scale_off

    # ---- Defense pass: same formula on opponents' counting stats.
    def_pred: dict[int, float] = {}
    league_pred_ra = 0.0
    league_actual_ra = 0
    for r in def_rows:
        tid = r["team_id"]
        a, b, c, d = _components(
            r["h"], r["d2"], r["d3"], r["hr"], r["bb"], r["hbp"], r["ab"]
        )
        pred = _base_runs(a, b, c, d)
        def_pred[tid] = pred
        league_pred_ra += pred
        league_actual_ra += ra_actual.get(tid, 0)
    b_scale_def = (league_actual_ra / league_pred_ra) if league_pred_ra > 0 else 1.0
    for tid in def_pred:
        def_pred[tid] *= b_scale_def

    teams = []
    for r in off_rows:
        tid = r["team_id"]
        rs       = r["r"]
        rs_pred  = off_pred.get(tid, 0.0)
        ra       = ra_actual.get(tid, 0)
        ra_pred  = def_pred.get(tid, 0.0)
        bsr_off  = rs - rs_pred
        bsr_def  = ra - ra_pred
        teams.append({
            "team_id":   tid,
            "abbrev":    r["abbrev"],
            "name":      r["name"],
            "rs":        rs,
            "rs_pred":   round(rs_pred, 1),
            "ra":        ra,
            "ra_pred":   round(ra_pred, 1),
            "bsr_off":   round(bsr_off, 1),
            "bsr_def":   round(bsr_def, 1),
            "bsr_total": round(bsr_off - bsr_def, 1),
        })
    teams.sort(key=lambda t: -abs(t["bsr_total"]))

    return {
        "n_teams":            len(teams),
        "league_actual_rs":   league_actual_rs,
        "league_raw_pred_rs": round(league_pred_rs, 1),
        "league_actual_ra":   league_actual_ra,
        "league_raw_pred_ra": round(league_pred_ra, 1),
        "b_scale_offense":    round(b_scale_off, 4),
        "b_scale_defense":    round(b_scale_def, 4),
        "teams":              teams,
    }
