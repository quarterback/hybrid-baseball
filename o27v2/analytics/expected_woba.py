"""Expected wOBA — strip BABIP variance from offensive value.

For each ball-in-play event the engine records a contact `quality`
bucket ('weak' | 'medium' | 'hard'). xwOBA replaces the actual wOBA
points of each BIP with the league-average wOBA-per-BIP at that
quality bucket. Walks and HBPs (which aren't BIP) keep their actual
weights.

Pipeline:

  1. League scan of game_pa_log: tabulate (sum wOBA-points, count) per
     `quality` bucket. This yields E[wOBA | quality], the per-BIP
     "expected" weight.
  2. Per-batter aggregate: replay each batter's BIP events with the
     expected weight, plus BB/HBP from game_batter_stats. Divide by PA.

Linear weights are loaded dynamically from
`o27v2.analytics.linear_weights.derive_linear_weights` so this module
and `_aggregate_batter_rows` in o27v2/web/app.py stay in sync.
"""
from __future__ import annotations
from collections import defaultdict

from o27v2 import db
from o27v2.analytics.linear_weights import derive_linear_weights


def _woba_weights() -> dict:
    return derive_linear_weights()["woba_weights"]


def _bip_woba_points(weights: dict,
                     hit_type: str | None,
                     was_stay: int, stay_credited: int) -> float:
    """wOBA points credited for one BIP event under the given weights."""
    if was_stay and stay_credited:
        return weights["1B"]    # stay-credited hit ≈ single
    if was_stay and not stay_credited:
        return 0.0              # stay event without credit (auto-out)
    if hit_type in ("hr", "home_run"):
        return weights["HR"]
    if hit_type == "triple":
        return weights["3B"]
    if hit_type == "double":
        return weights["2B"]
    if hit_type in ("single", "infield_single"):
        return weights["1B"]
    # error / fielders_choice / ground_out / fly_out / line_out / stay_*
    # outs: 0 weight
    return 0.0


def _quality_table() -> dict[str, dict]:
    """Compute league xwOBA-per-BIP for each quality bucket.

    Returns:
        {
            "weak":   {"n": int, "xwoba_per_bip": float, "actual_woba_per_bip": float},
            "medium": {...},
            "hard":   {...},
            None:     {...},   # legacy / unknown quality bucket
        }
    """
    weights = _woba_weights()
    rows = db.fetchall(
        """
        SELECT quality, hit_type, was_stay, stay_credited, COUNT(*) AS n
        FROM game_pa_log
        WHERE phase = 0
        GROUP BY quality, hit_type, was_stay, stay_credited
        """
    )
    sums: dict[str | None, float] = defaultdict(float)
    counts: dict[str | None, int]  = defaultdict(int)
    for r in rows:
        n = r["n"]
        wpts = _bip_woba_points(weights, r["hit_type"], r["was_stay"], r["stay_credited"])
        sums[r["quality"]]   += wpts * n
        counts[r["quality"]] += n
    out = {}
    for q, n in counts.items():
        out[q] = {
            "n":             n,
            "xwoba_per_bip": (sums[q] / n) if n else 0.0,
        }
    return out


def build_xwoba_table(min_pa: int = 162) -> dict:
    """Compute per-batter xwOBA across the active league.

    Args:
        min_pa: minimum PA for inclusion in the leaderboard (default
                matches the Leaders page qualifier).

    Returns:
        {
            "quality_table": {quality: {n, xwoba_per_bip}},
            "leaders":       [{player_id, player_name, team_abbrev, pa,
                              woba, xwoba, woba_minus_xwoba}, …],
            "league_woba":   float,
            "league_xwoba":  float,
        }
    """
    weights   = _woba_weights()
    qtable    = _quality_table()
    bip_xwoba = {q: v["xwoba_per_bip"] for q, v in qtable.items()}

    bip_rows = db.fetchall(
        """
        SELECT batter_id AS player_id, quality,
               SUM(CASE WHEN was_stay=1 AND stay_credited=1 THEN 1 ELSE 0 END) AS stay_h,
               SUM(CASE WHEN was_stay=1 AND stay_credited=0 THEN 1 ELSE 0 END) AS stay_miss,
               SUM(CASE WHEN hit_type IN ('hr','home_run') AND was_stay=0 THEN 1 ELSE 0 END) AS hr,
               SUM(CASE WHEN hit_type='triple' AND was_stay=0 THEN 1 ELSE 0 END) AS d3,
               SUM(CASE WHEN hit_type='double' AND was_stay=0 THEN 1 ELSE 0 END) AS d2,
               SUM(CASE WHEN hit_type IN ('single','infield_single') AND was_stay=0 THEN 1 ELSE 0 END) AS d1,
               COUNT(*) AS n_bip
        FROM game_pa_log
        WHERE phase = 0
        GROUP BY batter_id, quality
        """
    )
    actual_pts:   dict[int, float] = defaultdict(float)
    expected_pts: dict[int, float] = defaultdict(float)
    bip_count:    dict[int, int]   = defaultdict(int)
    for r in bip_rows:
        pid = r["player_id"]
        q = r["quality"]
        # Actual: rebuild from event mix
        actual_pts[pid] += (
            weights["HR"] * r["hr"] + weights["3B"] * r["d3"] +
            weights["2B"] * r["d2"] + weights["1B"] * r["d1"] +
            weights["1B"] * r["stay_h"]
        )
        bip_count[pid] += r["n_bip"]
        expected_pts[pid] += bip_xwoba.get(q, 0.0) * r["n_bip"]

    bat_rows = db.fetchall(
        """
        SELECT b.player_id, p.name AS player_name, t.abbrev AS team_abbrev,
               SUM(b.pa) AS pa, SUM(b.bb) AS bb, SUM(b.hbp) AS hbp
        FROM game_batter_stats b
        JOIN players p ON p.id = b.player_id
        LEFT JOIN teams t ON t.id = b.team_id
        WHERE b.phase = 0
        GROUP BY b.player_id
        """
    )

    leaders = []
    total_actual_pts = 0.0
    total_expected_pts = 0.0
    total_pa = 0
    for r in bat_rows:
        pid = r["player_id"]
        pa  = r["pa"] or 0
        if pa <= 0:
            continue
        bb  = r["bb"]  or 0
        hbp = r["hbp"] or 0
        actual  = actual_pts.get(pid, 0.0)   + weights["BB"] * bb + weights["HBP"] * hbp
        expect  = expected_pts.get(pid, 0.0) + weights["BB"] * bb + weights["HBP"] * hbp
        woba    = actual  / pa
        xwoba   = expect  / pa

        total_actual_pts   += actual
        total_expected_pts += expect
        total_pa           += pa

        if pa >= min_pa:
            leaders.append({
                "player_id":      pid,
                "player_name":    r["player_name"],
                "team_abbrev":    r["team_abbrev"] or "",
                "pa":             pa,
                "woba":           round(woba, 3),
                "xwoba":          round(xwoba, 3),
                "woba_minus_xwoba": round(woba - xwoba, 3),
            })
    leaders.sort(key=lambda x: -x["xwoba"])

    return {
        "quality_table": qtable,
        "leaders":       leaders,
        "league_woba":   (total_actual_pts   / total_pa) if total_pa else 0.0,
        "league_xwoba":  (total_expected_pts / total_pa) if total_pa else 0.0,
    }
