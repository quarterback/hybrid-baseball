"""Luck Ledger — per-game contact-luck table + expected-runs deserve-to-win.

Inspired by the MLB "deserve-to-win" simulators (resample a game's batted
balls by their contact quality and ask who *should* have won), but built
O27-native: instead of training on real Statcast, we lean on the
batted-ball physics already stamped on `game_pa_log` (exit_velocity,
launch_angle).

Two complementary lenses, both on the same contact-quality basis:

1. **Contact-luck table.** For each ball in play we compare the bases it
   actually produced (HR=4, 3B=3, 2B=2, single/credited-stay=1, out=0)
   against the league-average bases for that ball's (EV, LA) bin — what a
   ball hit that hard at that angle *typically* yields. The gap is luck:
   hard contact that found a glove, or weak contact that fell in. This
   drives the per-player "biggest swings" table and the team bases
   summary.

2. **Deserve-to-win (expected runs).** Bases alone are the wrong currency
   for a *win* in O27, where a walk-and-advance offense scores without
   piling up batter total bases (see game #794: 11 runs on 6 singles +
   7 walks). So deserve-to-win is built on **expected runs**: each ball's
   (EV, LA) bin yields not just expected bases but an expected event mix
   (P[1B], P[2B], P[3B], P[HR]); summed over a team's contact and combined
   with its *actual* walks/HBP and at-bats, that line is run through the
   league-fitted **BaseRuns** estimator (o27v2.analytics.base_runs) to get
   expected runs, which a Pythagorean turns into a win share. BaseRuns is
   already calibrated to O27's run environment and values walks/singles
   with the right diminishing returns, so a contact-light, walk-heavy
   offense is no longer undersold.

The stay convention matches the wOBA linear weights (STAY ≈ 1B): a
credited stay is one base and counts as a single-type event. Because the
(EV, LA) baseline is built with the *same* event function over the whole
league, the comparison stays unbiased.
"""
from __future__ import annotations

import functools
from collections import defaultdict

from o27v2 import db

# Pythagorean exponent for the expected-runs deserve-to-win. O27's run
# environment is higher and more variant than MLB's; 2.0 is a stable,
# config-agnostic default for a single game (the season-fit exponent lives
# in analytics/pythag.py and is overkill here; the scale factor cancels in
# the win-share ratio regardless).
_DTW_EXPONENT = 2.0

# Fine EV/LA grid for the contact surface. Deliberately finer than the 5x5
# (EV, LA) grid the xwOBA analytics use: that grid lumped every 100-110 mph
# / 24-40 deg barrel into ONE bin whose league-average bases (~2.0, dragged
# down by caught flies) made genuine home runs read as "+2 lucky" and a
# 9-HR blowout look fortunate. A finer grid lets elite contact estimate
# near its true value (a 108/35 ball lands in a bin that is mostly home
# runs). Kept local to this module so the xwOBA calibration is untouched.
_LL_EV_EDGES = (70.0, 80.0, 88.0, 95.0, 100.0, 104.0, 108.0, 112.0)
_LL_LA_EDGES = (-10.0, 0.0, 8.0, 16.0, 24.0, 32.0, 40.0, 48.0)
# Minimum balls in a bin before we trust its average; below this we fall
# back to the EV-marginal average, then the global BIP average. Keeps
# sparse fine bins from injecting noise.
_MIN_BIN_N = 20


def _bin_of(value: float, edges: tuple) -> int:
    for i, e in enumerate(edges):
        if value < e:
            return i
    return len(edges)


def _edge_case(col: str, edges: tuple) -> str:
    """SQL CASE bucketing `col` by `edges` (mirrors _bin_of)."""
    whens = " ".join(f"WHEN {col} < {e} THEN {i}" for i, e in enumerate(edges))
    return f"CASE {whens} ELSE {len(edges)} END"


_HIT_BASES = {
    "hr": 4, "home_run": 4,
    "triple": 3,
    "double": 2,
    "single": 1, "infield_single": 1,
}


def _event_components(hit_type: str | None, was_stay: int, stay_credited: int):
    """One BIP event -> (bases, hit, d2, d3, hr) as numbers. A credited stay
    is a one-base, single-type event (STAY ≈ 1B); an uncredited stay is an
    out. Used for both the contact-luck bases and the expected-runs event
    line, so the two lenses share one definition."""
    if was_stay:
        return (1, 1, 0, 0, 0) if stay_credited else (0, 0, 0, 0, 0)
    if hit_type in ("hr", "home_run"):
        return (4, 1, 0, 0, 1)
    if hit_type == "triple":
        return (3, 1, 0, 1, 0)
    if hit_type == "double":
        return (2, 1, 1, 0, 0)
    if hit_type in ("single", "infield_single"):
        return (1, 1, 0, 0, 0)
    return (0, 0, 0, 0, 0)


def event_bases(hit_type: str | None, was_stay: int, stay_credited: int) -> int:
    return _event_components(hit_type, was_stay, stay_credited)[0]


def _spray_label(spray: float | None) -> str:
    """Pull / Center / Oppo from spray angle (− = pull, + = oppo)."""
    if spray is None:
        return ""
    if spray <= -12.0:
        return "Pull"
    if spray >= 12.0:
        return "Oppo"
    return "Center"


_RESULT_LABEL = {
    "hr": "Home Run", "home_run": "Home Run",
    "triple": "Triple", "double": "Double",
    "single": "Single", "infield_single": "Single",
    "error": "Reached on Error",
    "ground_out": "Out", "fly_out": "Out", "line_out": "Out",
    "fielders_choice": "Out", "double_play": "Double Play",
    "triple_play": "Triple Play",
}


def _result_label(hit_type: str | None, was_stay: int, stay_credited: int) -> str:
    if was_stay:
        return "Stay" if stay_credited else "Stay (out)"
    return _RESULT_LABEL.get(hit_type or "", (hit_type or "Out").title())


# Component keys carried per bin: bases + the BaseRuns event vector.
_COMPS = ("bases", "hit", "d2", "d3", "hr")


def _build_estimator(team_ids=None):
    """Return est(ev, la) -> per-BIP expected vector
    {bases, hit, d2, d3, hr} (league averages on the fine grid, with
    EV-marginal then global fallbacks for sparse bins). Regulation BIP
    only, matching the xwOBA surface."""
    ev_sql = _edge_case("exit_velocity", _LL_EV_EDGES)
    la_sql = _edge_case("launch_angle", _LL_LA_EDGES)
    rows = db.fetchall(
        f"""
        SELECT {ev_sql} AS ev_bin, {la_sql} AS la_bin,
               hit_type, was_stay, stay_credited, COUNT(*) AS n
        FROM game_pa_log
        WHERE phase = 0 AND exit_velocity IS NOT NULL
        GROUP BY ev_bin, la_bin, hit_type, was_stay, stay_credited
        """
    )
    bin_sum: dict[tuple, dict] = defaultdict(lambda: {c: 0.0 for c in _COMPS})
    bin_cnt: dict[tuple, int] = defaultdict(int)
    ev_sum: dict[int, dict] = defaultdict(lambda: {c: 0.0 for c in _COMPS})
    ev_cnt: dict[int, int] = defaultdict(int)
    g_sum = {c: 0.0 for c in _COMPS}
    g_cnt = 0
    for r in rows:
        comps = _event_components(r["hit_type"], r["was_stay"], r["stay_credited"])
        n = r["n"]
        key = (r["ev_bin"], r["la_bin"])
        for c, v in zip(_COMPS, comps):
            bin_sum[key][c] += v * n
            ev_sum[r["ev_bin"]][c] += v * n
            g_sum[c] += v * n
        bin_cnt[key] += n
        ev_cnt[r["ev_bin"]] += n
        g_cnt += n

    g_avg = {c: (g_sum[c] / g_cnt if g_cnt else 0.0) for c in _COMPS}

    def est(ev: float, la: float) -> dict:
        eb = _bin_of(ev, _LL_EV_EDGES)
        key = (eb, _bin_of(la, _LL_LA_EDGES))
        if bin_cnt.get(key, 0) >= _MIN_BIN_N:
            n = bin_cnt[key]
            return {c: bin_sum[key][c] / n for c in _COMPS}
        if ev_cnt.get(eb, 0) >= _MIN_BIN_N:
            n = ev_cnt[eb]
            return {c: ev_sum[eb][c] / n for c in _COMPS}
        return dict(g_avg)

    return est


@functools.lru_cache(maxsize=2)
def _fitted_run_model(version: int):
    """League-fitted BaseRuns coefficients + run-scale + the running-game
    run values (SB/CS), memoized on a DB version key so the (mildly
    expensive) league refit runs once per sim state."""
    from o27v2.analytics.base_runs import build_base_runs_table, _DEFAULT_COEFFS
    from o27v2.analytics.linear_weights import derive_linear_weights
    tbl = build_base_runs_table()
    coeffs = tbl.get("fitted_coeffs") or _DEFAULT_COEFFS
    scale = tbl.get("fitted_b_scale_off") or 1.0
    rv = derive_linear_weights().get("rv", {})
    return tuple(coeffs), float(scale), float(rv.get("SB", 0.0)), float(rv.get("CS", 0.0))


def _run_model():
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM games "
        "WHERE played = 1 AND COALESCE(is_playoff, 0) = 0"
    )
    return _fitted_run_model(int(row["n"]) if row else 0)


def _expected_runs(coeffs, scale, xh, x2b, x3b, xhr, bb, hbp, ab) -> float:
    """BaseRuns expected runs for one expected event line, on the league
    run scale. Mirrors o27v2.analytics.base_runs._bsr."""
    from o27v2.analytics.base_runs import _bsr
    return _bsr(coeffs, xh, x2b, x3b, xhr, bb, hbp, ab) * scale


def build_game_ledger(game_id: int) -> dict | None:
    """Build the Luck Ledger for one game.

    Returns None when the game has no EV/LA-stamped BIP (legacy rows).

    Per-team fields: name/abbrev, actual_runs, est_runs (BaseRuns expected
    runs), run_luck (actual − expected), dtw_pct (Pythagorean win share on
    expected runs); plus the contact-luck summary (est_batted/actual_batted
    bases, walks, est_total/actual_total/luck) and `swings` (top-5 BIP by
    |bases luck|).
    """
    game = db.fetchone(
        """SELECT g.home_team_id, g.away_team_id, g.home_score, g.away_score,
                  ht.name AS home_name, ht.abbrev AS home_abbrev,
                  at.name AS away_name, at.abbrev AS away_abbrev
           FROM games g
           JOIN teams ht ON g.home_team_id = ht.id
           JOIN teams at ON g.away_team_id = at.id
           WHERE g.id = ?""",
        (game_id,),
    )
    if not game:
        return None

    estimate = _build_estimator()

    bips = db.fetchall(
        """SELECT pa.team_id, pa.batter_id, pa.exit_velocity AS ev,
                  pa.launch_angle AS la, pa.spray_angle AS spray,
                  pa.hit_type, pa.was_stay, pa.stay_credited,
                  p.name AS batter_name
           FROM game_pa_log pa
           JOIN players p ON pa.batter_id = p.id
           WHERE pa.game_id = ? AND pa.exit_velocity IS NOT NULL""",
        (game_id,),
    )
    if not bips:
        return None

    # Actual batting line per team (walks/HBP/AB for the expected-runs line).
    line_rows = db.fetchall(
        """SELECT team_id,
                  COALESCE(SUM(bb), 0) AS bb, COALESCE(SUM(hbp), 0) AS hbp,
                  COALESCE(SUM(ab), 0) AS ab,
                  COALESCE(SUM(sb), 0) AS sb, COALESCE(SUM(cs), 0) AS cs
           FROM game_batter_stats
           WHERE game_id = ? AND phase = 0
           GROUP BY team_id""",
        (game_id,),
    )
    lines = {r["team_id"]: r for r in line_rows}

    away_id, home_id = game["away_team_id"], game["home_team_id"]
    agg = {
        tid: {"est_batted": 0.0, "actual_batted": 0.0, "bip": 0, "swings": [],
              "xh": 0.0, "x2b": 0.0, "x3b": 0.0, "xhr": 0.0}
        for tid in (away_id, home_id)
    }

    for r in bips:
        tid = r["team_id"]
        if tid not in agg:
            continue
        ev, la = r["ev"], r["la"]
        e = estimate(ev, la)
        bases, hit, d2, d3, hr = _event_components(
            r["hit_type"], r["was_stay"], r["stay_credited"])
        a = agg[tid]
        a["est_batted"] += e["bases"]
        a["actual_batted"] += bases
        a["xh"] += e["hit"]
        a["x2b"] += e["d2"]
        a["x3b"] += e["d3"]
        a["xhr"] += e["hr"]
        a["bip"] += 1
        a["swings"].append({
            "player_id": r["batter_id"],
            "player_name": r["batter_name"],
            "ev": round(ev, 0),
            "la": round(la, 0),
            "spray": _spray_label(r["spray"]),
            "result": _result_label(r["hit_type"], r["was_stay"], r["stay_credited"]),
            "est": round(e["bases"], 1),
            "act": bases,
            "luck": round(bases - e["bases"], 2),
        })

    coeffs, scale, sb_rv, cs_rv = _run_model()

    def _walks(tid):
        ln = lines.get(tid)
        return ((ln["bb"] or 0) + (ln["hbp"] or 0)) if ln else 0

    # Expected runs per team from the contact-quality event line + actual
    # walks/HBP/AB via league-fitted BaseRuns, plus the running game (actual
    # SB/CS at O27-derived run values — invisible to BaseRuns, so additive).
    est_runs = {}
    run_steals = {}
    for tid, a in agg.items():
        ln = lines.get(tid)
        bb = (ln["bb"] or 0) if ln else 0
        hbp = (ln["hbp"] or 0) if ln else 0
        ab = (ln["ab"] or 0) if ln else 0
        sb = (ln["sb"] or 0) if ln else 0
        cs = (ln["cs"] or 0) if ln else 0
        # Guard: AB must cover the expected hits for C = AB − H ≥ 0.
        ab = max(ab, int(round(a["xh"])))
        base = _expected_runs(
            coeffs, scale, a["xh"], a["x2b"], a["x3b"], a["xhr"], bb, hbp, ab)
        run_steals[tid] = sb * sb_rv + cs * cs_rv
        est_runs[tid] = base + run_steals[tid]

    # Clamp to >= 0 for the Pythagorean (a CS-heavy line can in principle
    # net below zero; a negative base would corrupt the win share).
    ea, eh = max(0.0, est_runs[away_id]), max(0.0, est_runs[home_id])
    denom = (ea ** _DTW_EXPONENT) + (eh ** _DTW_EXPONENT)
    dtw_home = ((eh ** _DTW_EXPONENT) / denom * 100.0) if denom > 0 else 50.0
    dtw_away = 100.0 - dtw_home

    def _side(tid, name, abbrev, actual_runs, dtw_pct):
        a = agg[tid]
        ln = lines.get(tid)
        bb = _walks(tid)
        sb = (ln["sb"] or 0) if ln else 0
        cs = (ln["cs"] or 0) if ln else 0
        a["swings"].sort(key=lambda s: -abs(s["luck"]))
        est_total = a["est_batted"] + bb
        actual_total = a["actual_batted"] + bb
        er = est_runs[tid]
        return {
            "team_id": tid,
            "name": name,
            "abbrev": abbrev,
            "actual_runs": actual_runs,
            "est_runs": round(er, 1),
            "run_luck": round((actual_runs or 0) - er, 1),
            "dtw_pct": round(dtw_pct, 0),
            "sb": sb,
            "cs": cs,
            "run_steals": round(run_steals[tid], 1),
            "bip": a["bip"],
            "bb": bb,
            "walks": bb,
            "est_batted": round(a["est_batted"], 1),
            "actual_batted": round(a["actual_batted"], 1),
            "est_total": round(est_total, 1),
            "actual_total": round(actual_total, 1),
            "luck": round(actual_total - est_total, 1),
            "swings": a["swings"][:5],
        }

    return {
        "teams": {
            "away": _side(away_id, game["away_name"], game["away_abbrev"],
                          game["away_score"], dtw_away),
            "home": _side(home_id, game["home_name"], game["home_abbrev"],
                          game["home_score"], dtw_home),
        }
    }
