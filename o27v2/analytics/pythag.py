"""Pythagorean exponent re-fit for O27.

Bill James's Pythagorean win expectancy uses an exponent of 1.83 fit to
MLB's ~9 R/G run environment. O27 sits at ~22 R/G, so the optimal
exponent is empirically different. This module fits the exponent that
minimises league-wide squared error in win-percentage prediction.

Method:
  W%_team ≈ R^k / (R^k + RA^k)
  Solve for k* ∈ [1.0, 2.5] via 1-D grid search + ternary refinement
  (no scipy dependency in the project).

Output also reports residuals: (actual_W − pythag_W) per team using the
fitted exponent. These are the cleanest "luck" candidates available
without simulating BsR.
"""
from __future__ import annotations

from o27v2 import db


def _pythag_pct(r: int, ra: int, k: float) -> float:
    if r <= 0 and ra <= 0:
        return 0.5
    return (r ** k) / (r ** k + ra ** k)


def _team_records() -> list[dict]:
    """All teams with R / RA / GP / W."""
    rows = db.fetchall(
        """
        SELECT t.id AS team_id, t.abbrev, t.name,
               COALESCE(SUM(CASE WHEN g.played=1 AND g.is_playoff=0
                            AND g.home_team_id=t.id THEN g.home_score END), 0)
               + COALESCE(SUM(CASE WHEN g.played=1 AND g.is_playoff=0
                            AND g.away_team_id=t.id THEN g.away_score END), 0) AS r,
               COALESCE(SUM(CASE WHEN g.played=1 AND g.is_playoff=0
                            AND g.home_team_id=t.id THEN g.away_score END), 0)
               + COALESCE(SUM(CASE WHEN g.played=1 AND g.is_playoff=0
                            AND g.away_team_id=t.id THEN g.home_score END), 0) AS ra,
               t.wins AS w,
               (t.wins + t.losses) AS gp
        FROM teams t
        LEFT JOIN games g
          ON (g.home_team_id = t.id OR g.away_team_id = t.id)
        GROUP BY t.id
        """
    )
    return [r for r in rows if (r["gp"] or 0) > 0]


def _sse(exp: float, teams: list[dict]) -> float:
    s = 0.0
    for t in teams:
        gp = t["gp"]
        actual = t["w"] / gp
        pred   = _pythag_pct(t["r"], t["ra"], exp)
        s += (actual - pred) ** 2
    return s


def refit_pythag_exponent(
    *, lo: float = 1.0, hi: float = 4.0, iters: int = 60,
) -> dict:
    """Empirically fit the Pythagorean exponent over the league.

    Returns:
        {
            "fitted_exponent":        float,
            "fitted_sse":             float,
            "fitted_rmse":            float,
            "mlb_default_exponent":   1.83,
            "mlb_default_sse":        float,
            "improvement_pct":        float,
            "n_teams":                int,
            "residuals": [{team_id, abbrev, w, gp, r, ra,
                           pythag_w_default, pythag_w_fitted,
                           luck_default, luck_fitted}, …],
        }
    """
    teams = _team_records()
    if not teams:
        return {
            "fitted_exponent": 1.83, "fitted_sse": 0.0, "fitted_rmse": 0.0,
            "mlb_default_exponent": 1.83, "mlb_default_sse": 0.0,
            "improvement_pct": 0.0, "n_teams": 0, "residuals": [],
        }

    # Ternary search — _sse is unimodal in exp on [1.0, 2.5] for typical
    # league shapes. ~50 iters reduces the bracket to 1.5e-9, which is
    # plenty given league-level noise.
    a, b = lo, hi
    for _ in range(iters):
        m1 = a + (b - a) / 3
        m2 = b - (b - a) / 3
        if _sse(m1, teams) < _sse(m2, teams):
            b = m2
        else:
            a = m1
    k_star = (a + b) / 2

    sse_fit = _sse(k_star, teams)
    sse_def = _sse(1.83,   teams)
    n = len(teams)
    rmse_fit = (sse_fit / n) ** 0.5

    residuals = []
    for t in teams:
        gp = t["gp"]
        pyth_d = _pythag_pct(t["r"], t["ra"], 1.83)
        pyth_f = _pythag_pct(t["r"], t["ra"], k_star)
        residuals.append({
            "team_id":          t["team_id"],
            "abbrev":           t["abbrev"],
            "name":             t["name"],
            "w":                t["w"],
            "gp":               gp,
            "r":                t["r"],
            "ra":               t["ra"],
            "win_pct":          t["w"] / gp,
            "pythag_pct_default": round(pyth_d, 3),
            "pythag_pct_fitted":  round(pyth_f, 3),
            "pythag_w_default":   round(pyth_d * gp, 1),
            "pythag_w_fitted":    round(pyth_f * gp, 1),
            "luck_default":       round((t["w"] / gp - pyth_d) * gp, 1),
            "luck_fitted":        round((t["w"] / gp - pyth_f) * gp, 1),
        })
    residuals.sort(key=lambda x: -abs(x["luck_fitted"]))

    return {
        "fitted_exponent":      round(k_star, 3),
        "fitted_sse":           round(sse_fit, 5),
        "fitted_rmse":          round(rmse_fit, 4),
        "mlb_default_exponent": 1.83,
        "mlb_default_sse":      round(sse_def, 5),
        "improvement_pct":      round((1 - sse_fit / sse_def) * 100, 1) if sse_def else 0.0,
        "n_teams":              n,
        "residuals":            residuals,
    }
