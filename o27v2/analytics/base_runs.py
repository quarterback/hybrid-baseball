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
    B = c1·TB + c2·H + c3·HR + c4·(BB+HBP)
    C = AB − H                   batting outs
    D = HR

    BsR = A · B / (B + C)  +  D

MLB-fit canonical coefficients are c = (1.4, −0.6, −3.0, 0.1). O27's
~22 R/G run environment shifts the optimal *shape* of B, not just its
scale. We do two passes:

  1. Default coefficients + multiplicative league re-center (so the
     league means match). Residuals here include both shape mis-fit
     and genuine sequencing luck — useful as a baseline.
  2. Empirical refit of all four coefficients via coordinate-descent
     ternary search, jointly minimising team-level SSE across offense
     *and* defense (60 datapoints, 4 parameters → ~15:1 DoF). This is
     the analogue of pythag.py's k* refit — it gives residuals that
     reflect within-league sequencing variance only.

We report both so the reader can see how much of the apparent "luck"
under default coefficients is really run-environment fit error.
"""
from __future__ import annotations

from o27v2 import db


_DEFAULT_COEFFS = (1.4, -0.6, -3.0, 0.1)


def _b_value(c: tuple[float, float, float, float],
             h: int, d2: int, d3: int, hr: int,
             bb: int, hbp: int) -> float:
    singles = h - d2 - d3 - hr
    tb = singles + 2 * d2 + 3 * d3 + 4 * hr
    return c[0] * tb + c[1] * h + c[2] * hr + c[3] * (bb + hbp)


def _bsr(c: tuple[float, float, float, float],
         h: int, d2: int, d3: int, hr: int,
         bb: int, hbp: int, ab: int) -> float:
    a = h + bb + hbp - hr
    b = _b_value(c, h, d2, d3, hr, bb, hbp)
    cc = ab - h
    d = hr
    if b + cc <= 0:
        return float(d)
    return a * b / (b + cc) + d


def _team_offense_rows() -> list[dict]:
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
    """Per-team allowed counting stats — sum opponents' batting against them."""
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


def _joint_sse(c: tuple[float, float, float, float],
               off_rows: list[dict], def_rows: list[dict],
               ra_actual: dict[int, int]) -> float:
    s = 0.0
    for r in off_rows:
        pred = _bsr(c, r["h"], r["d2"], r["d3"], r["hr"], r["bb"], r["hbp"], r["ab"])
        s += (r["r"] - pred) ** 2
    for r in def_rows:
        pred = _bsr(c, r["h"], r["d2"], r["d3"], r["hr"], r["bb"], r["hbp"], r["ab"])
        actual = ra_actual.get(r["team_id"], 0)
        s += (actual - pred) ** 2
    return s


# Coordinate-descent bounds. Wider than canonical to permit shape drift,
# tight enough to keep B+C > 0 for any plausible team line. With 30
# teams × 2 (offense + defense) = 60 datapoints and 4 parameters, these
# are the most generous bounds that don't risk degenerate fits.
_BOUNDS = (
    (0.5,  6.0),    # c1: TB weight  (high R/G envs push this well above 1.4)
    (-3.0, 1.0),    # c2: H weight
    (-8.0, 2.0),    # c3: HR weight
    (-1.0, 1.5),    # c4: (BB + HBP) weight
)


def _refit_coeffs(off_rows: list[dict], def_rows: list[dict],
                  ra_actual: dict[int, int],
                  init: tuple[float, float, float, float] = _DEFAULT_COEFFS,
                  outer_iters: int = 8, inner_iters: int = 50,
                  ) -> tuple[tuple[float, float, float, float], float]:
    """Joint coordinate-descent refit of the four B coefficients.

    Cycle each coordinate through a 1-D ternary search while holding
    the others fixed; repeat outer_iters times. SSE is convex in each
    coordinate when the others are fixed (B is linear in each c_i and
    the BsR map is monotone in B given B+C > 0), so 1-D ternary search
    is well-behaved per axis. The overall problem is non-convex jointly,
    but warm-starting from MLB defaults keeps us near a sensible basin.

    Returns (fitted_coeffs, final_sse).
    """
    c = list(init)
    for _ in range(outer_iters):
        for i in range(4):
            lo, hi = _BOUNDS[i]
            for _ in range(inner_iters):
                m1 = lo + (hi - lo) / 3
                m2 = hi - (hi - lo) / 3
                c1 = tuple(c[:i] + [m1] + c[i+1:])
                c2 = tuple(c[:i] + [m2] + c[i+1:])
                if _joint_sse(c1, off_rows, def_rows, ra_actual) \
                 < _joint_sse(c2, off_rows, def_rows, ra_actual):
                    hi = m2
                else:
                    lo = m1
            c[i] = (lo + hi) / 2
    fitted = (c[0], c[1], c[2], c[3])
    return fitted, _joint_sse(fitted, off_rows, def_rows, ra_actual)


def build_base_runs_table() -> dict:
    """Per-team BaseRuns predictions and sequencing-luck residuals.

    Returns:
        {
            "n_teams":  int,
            "league_actual_rs": int,
            "league_actual_ra": int,

            # Default-coefficient pass (MLB-fit B + multiplicative re-center).
            "default_coeffs":     (1.4, -0.6, -3.0, 0.1),
            "default_b_scale_off": float,   # league-mean re-center
            "default_b_scale_def": float,
            "default_sse":        float,    # joint SSE under defaults+rescale

            # Fitted-coefficient pass (shape refit, no re-center).
            "fitted_coeffs":      (c1, c2, c3, c4),
            "fitted_sse":         float,
            "improvement_pct":    float,    # (default_sse - fitted_sse) / default_sse * 100

            "teams": [{
                team_id, abbrev, name,
                rs, ra,
                # Default-coefficient predictions (with re-center)
                rs_pred_def, ra_pred_def,
                bsr_off_def, bsr_def_def, bsr_total_def,
                # Fitted-coefficient predictions (no re-center)
                rs_pred_fit, ra_pred_fit,
                bsr_off_fit, bsr_def_fit, bsr_total_fit,
            }, …],   # sorted by |bsr_total_fit| desc
        }
    """
    off_rows  = _team_offense_rows()
    def_rows  = _team_defense_rows()
    ra_actual = _team_runs_allowed()

    if not off_rows:
        return {"n_teams": 0, "teams": []}

    # ---- Pass 1: default coefficients + multiplicative league re-center.
    raw_off_pred: dict[int, float] = {}
    raw_def_pred: dict[int, float] = {}
    league_actual_rs = 0
    league_actual_ra = 0
    league_raw_pred_rs = 0.0
    league_raw_pred_ra = 0.0
    for r in off_rows:
        pred = _bsr(_DEFAULT_COEFFS, r["h"], r["d2"], r["d3"], r["hr"],
                    r["bb"], r["hbp"], r["ab"])
        raw_off_pred[r["team_id"]] = pred
        league_raw_pred_rs += pred
        league_actual_rs   += r["r"]
    for r in def_rows:
        pred = _bsr(_DEFAULT_COEFFS, r["h"], r["d2"], r["d3"], r["hr"],
                    r["bb"], r["hbp"], r["ab"])
        raw_def_pred[r["team_id"]] = pred
        league_raw_pred_ra += pred
        league_actual_ra   += ra_actual.get(r["team_id"], 0)
    scale_off = (league_actual_rs / league_raw_pred_rs) if league_raw_pred_rs > 0 else 1.0
    scale_def = (league_actual_ra / league_raw_pred_ra) if league_raw_pred_ra > 0 else 1.0
    off_pred_def = {tid: p * scale_off for tid, p in raw_off_pred.items()}
    def_pred_def = {tid: p * scale_def for tid, p in raw_def_pred.items()}
    sse_default = sum(
        (r["r"] - off_pred_def[r["team_id"]]) ** 2 for r in off_rows
    ) + sum(
        (ra_actual.get(r["team_id"], 0) - def_pred_def[r["team_id"]]) ** 2
        for r in def_rows
    )

    # ---- Pass 2: empirical shape refit, then league-mean re-center.
    # The fit minimises raw SSE (no scale parameter), so its predictions
    # may not sum exactly to league actual. Apply the same multiplicative
    # rescale as Pass 1 so residuals are mean-zero and comparable across
    # the two passes (we report rescaled SSE for both).
    fitted_coeffs, _ = _refit_coeffs(off_rows, def_rows, ra_actual)
    raw_off_pred_fit: dict[int, float] = {}
    raw_def_pred_fit: dict[int, float] = {}
    raw_off_sum_fit = 0.0
    raw_def_sum_fit = 0.0
    for r in off_rows:
        p = _bsr(fitted_coeffs, r["h"], r["d2"], r["d3"], r["hr"],
                 r["bb"], r["hbp"], r["ab"])
        raw_off_pred_fit[r["team_id"]] = p
        raw_off_sum_fit += p
    for r in def_rows:
        p = _bsr(fitted_coeffs, r["h"], r["d2"], r["d3"], r["hr"],
                 r["bb"], r["hbp"], r["ab"])
        raw_def_pred_fit[r["team_id"]] = p
        raw_def_sum_fit += p
    fit_scale_off = (league_actual_rs / raw_off_sum_fit) if raw_off_sum_fit > 0 else 1.0
    fit_scale_def = (league_actual_ra / raw_def_sum_fit) if raw_def_sum_fit > 0 else 1.0
    off_pred_fit = {tid: p * fit_scale_off for tid, p in raw_off_pred_fit.items()}
    def_pred_fit = {tid: p * fit_scale_def for tid, p in raw_def_pred_fit.items()}
    fitted_sse = sum(
        (r["r"] - off_pred_fit[r["team_id"]]) ** 2 for r in off_rows
    ) + sum(
        (ra_actual.get(r["team_id"], 0) - def_pred_fit[r["team_id"]]) ** 2
        for r in def_rows
    )

    teams = []
    for r in off_rows:
        tid = r["team_id"]
        rs  = r["r"]
        ra  = ra_actual.get(tid, 0)
        rsd = off_pred_def.get(tid, 0.0)
        rad = def_pred_def.get(tid, 0.0)
        rsf = off_pred_fit.get(tid, 0.0)
        raf = def_pred_fit.get(tid, 0.0)
        teams.append({
            "team_id":   tid,
            "abbrev":    r["abbrev"],
            "name":      r["name"],
            "rs":        rs,
            "ra":        ra,
            # Default-coeff pass
            "rs_pred_def":   round(rsd, 1),
            "ra_pred_def":   round(rad, 1),
            "bsr_off_def":   round(rs - rsd, 1),
            "bsr_def_def":   round(ra - rad, 1),
            "bsr_total_def": round((rs - rsd) - (ra - rad), 1),
            # Fitted-coeff pass
            "rs_pred_fit":   round(rsf, 1),
            "ra_pred_fit":   round(raf, 1),
            "bsr_off_fit":   round(rs - rsf, 1),
            "bsr_def_fit":   round(ra - raf, 1),
            "bsr_total_fit": round((rs - rsf) - (ra - raf), 1),
        })
    teams.sort(key=lambda t: -abs(t["bsr_total_fit"]))

    improvement = (
        (sse_default - fitted_sse) / sse_default * 100.0
        if sse_default > 0 else 0.0
    )

    return {
        "n_teams":              len(teams),
        "league_actual_rs":     league_actual_rs,
        "league_actual_ra":     league_actual_ra,

        "default_coeffs":       _DEFAULT_COEFFS,
        "default_b_scale_off":  round(scale_off, 4),
        "default_b_scale_def":  round(scale_def, 4),
        "default_sse":          round(sse_default, 1),

        "fitted_coeffs":        tuple(round(x, 3) for x in fitted_coeffs),
        "fitted_sse":           round(fitted_sse, 1),
        "improvement_pct":      round(improvement, 1),

        "teams":                teams,
    }
