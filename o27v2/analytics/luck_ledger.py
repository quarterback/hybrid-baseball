"""Luck Ledger — per-game "deserve-to-win" / estimated-vs-actual bases.

Inspired by the MLB "deserve-to-win" simulators (resample a game's batted
balls by their contact quality and ask who *should* have won), but built
O27-native: instead of training on real Statcast, we lean on the
batted-ball physics already stamped on `game_pa_log` (exit_velocity,
launch_angle) and the same (EV, LA)-bin surface the xwOBA analytics use.

For each ball in play we compute:

  * actual bases — the bases the batted ball actually produced
    (HR=4, 3B=3, 2B=2, single/credited-stay=1, out/error/fielders-choice=0),
  * estimated bases — the league-average bases for that ball's (EV, LA)
    bin, i.e. what a ball hit that hard at that angle *typically* yields.

Summed over a team's contact, plus one base per walk/HBP (mirroring the
reference's "+walks"), this gives each side an **estimated production** in
bases. The gap between estimated and actual production is *luck* — good
contact that found gloves, or weak contact that fell in.

The stay convention matches the wOBA linear weights (STAY ≈ 1B): a
credited stay is worth one base. Because the (EV, LA) baseline is built
with the *same* event-bases function over the whole league, the baseline
already absorbs the league stay rate, so the estimated/actual comparison
stays unbiased.

Deserve-to-Win here is a lightweight Pythagorean win expectancy on
estimated bases (a runs proxy) — a single headline number. The fully
resampled run *distribution* (the 10K-sim histogram) is a separate,
forward-looking feature and would supersede this estimate when built.
"""
from __future__ import annotations

from collections import defaultdict

from o27v2 import db

# Pythagorean exponent for the bases-proxy deserve-to-win. O27's run
# environment is higher and more variant than MLB's; 2.0 is a stable,
# config-agnostic default for a single-game proxy (the full season-fit
# exponent lives in analytics/pythag.py and is overkill for one game).
_DTW_EXPONENT = 2.0

# Fine EV/LA grid for the estimated-bases surface. Deliberately finer than
# the 5x5 (EV, LA) grid the xwOBA analytics use: that grid lumped every
# 100-110 mph / 24-40 deg barrel into ONE bin whose league-average bases
# (~2.0, dragged down by caught flies) made genuine home runs read as
# "+2 lucky" and a 9-HR blowout look fortunate. A finer grid lets elite
# contact estimate near its true value (a 108/35 ball lands in a bin that
# is mostly home runs). Kept local to this module so the xwOBA calibration
# is untouched.
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


def event_bases(hit_type: str | None, was_stay: int, stay_credited: int) -> int:
    """Bases produced by one BIP event. Used for BOTH the actual tally and
    the league (EV, LA)-bin baseline, so the two stay on one scale."""
    if was_stay:
        # A credited stay advances runners and keeps the AB alive — worth a
        # base in the same spirit STAY ≈ 1B in the wOBA weights. An
        # uncredited stay (auto-out) is worth nothing.
        return 1 if stay_credited else 0
    return _HIT_BASES.get(hit_type or "", 0)


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


def _build_estimator(team_ids=None):
    """Return est(ev, la) -> league-average estimated bases on the fine
    grid, with EV-marginal and global fallbacks for sparse bins.
    Regulation BIP only, matching the xwOBA surface."""
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
    bsum: dict[tuple, float] = defaultdict(float)
    bcnt: dict[tuple, int] = defaultdict(int)
    evsum: dict[int, float] = defaultdict(float)
    evcnt: dict[int, int] = defaultdict(int)
    gsum = gcnt = 0.0
    for r in rows:
        b = event_bases(r["hit_type"], r["was_stay"], r["stay_credited"])
        n = r["n"]
        key = (r["ev_bin"], r["la_bin"])
        bsum[key] += b * n
        bcnt[key] += n
        evsum[r["ev_bin"]] += b * n
        evcnt[r["ev_bin"]] += n
        gsum += b * n
        gcnt += n
    global_avg = (gsum / gcnt) if gcnt else 0.0

    def est(ev: float, la: float) -> float:
        eb = _bin_of(ev, _LL_EV_EDGES)
        key = (eb, _bin_of(la, _LL_LA_EDGES))
        if bcnt.get(key, 0) >= _MIN_BIN_N:
            return bsum[key] / bcnt[key]
        if evcnt.get(eb, 0) >= _MIN_BIN_N:
            return evsum[eb] / evcnt[eb]
        return global_avg

    return est


def build_game_ledger(game_id: int) -> dict | None:
    """Build the Luck Ledger for one game.

    Returns None when the game has no EV/LA-stamped BIP (legacy rows), so
    callers can skip the section cleanly.

    Returns:
        {
          "teams": {
            "away"/"home": {
              "team_id", "name", "abbrev", "actual_runs",
              "est_batted", "actual_batted", "walks",
              "est_total", "actual_total", "luck", "dtw_pct",
              "bip", "bb",
              "swings": [{player_id, player_name, ev, la, spray, result,
                          est, act, luck}, ...]   # top 5 by |luck|
            }
          }
        }
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

    # Per-BIP events for this game with the contact physics.
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

    # Walks / HBP per team for this game (one base each), like the
    # reference ledger's "+ walks".
    walk_rows = db.fetchall(
        """SELECT team_id, COALESCE(SUM(bb), 0) AS bb, COALESCE(SUM(hbp), 0) AS hbp
           FROM game_batter_stats
           WHERE game_id = ?
           GROUP BY team_id""",
        (game_id,),
    )
    walks = {r["team_id"]: (r["bb"] or 0) + (r["hbp"] or 0) for r in walk_rows}

    agg = {
        tid: {"est_batted": 0.0, "actual_batted": 0.0, "bip": 0, "swings": []}
        for tid in (game["away_team_id"], game["home_team_id"])
    }

    for r in bips:
        tid = r["team_id"]
        if tid not in agg:
            continue
        ev, la = r["ev"], r["la"]
        est = estimate(ev, la)
        act = event_bases(r["hit_type"], r["was_stay"], r["stay_credited"])
        a = agg[tid]
        a["est_batted"] += est
        a["actual_batted"] += act
        a["bip"] += 1
        a["swings"].append({
            "player_id": r["batter_id"],
            "player_name": r["batter_name"],
            "ev": round(ev, 0),
            "la": round(la, 0),
            "spray": _spray_label(r["spray"]),
            "result": _result_label(r["hit_type"], r["was_stay"], r["stay_credited"]),
            "est": round(est, 1),
            "act": act,
            "luck": round(act - est, 2),
        })

    # Estimated total production (bases) per team = batted + walks.
    est_total = {}
    for tid, a in agg.items():
        est_total[tid] = a["est_batted"] + walks.get(tid, 0)

    away_id, home_id = game["away_team_id"], game["home_team_id"]
    ea, eh = est_total[away_id], est_total[home_id]
    denom = (ea ** _DTW_EXPONENT) + (eh ** _DTW_EXPONENT)
    if denom > 0:
        dtw_home = (eh ** _DTW_EXPONENT) / denom * 100.0
    else:
        dtw_home = 50.0
    dtw_away = 100.0 - dtw_home

    def _side(tid, name, abbrev, actual_runs, dtw_pct):
        a = agg[tid]
        bb = walks.get(tid, 0)
        a["swings"].sort(key=lambda s: -abs(s["luck"]))
        return {
            "team_id": tid,
            "name": name,
            "abbrev": abbrev,
            "actual_runs": actual_runs,
            "bip": a["bip"],
            "bb": bb,
            "est_batted": round(a["est_batted"], 1),
            "actual_batted": round(a["actual_batted"], 1),
            "walks": bb,
            "est_total": round(est_total[tid], 1),
            "actual_total": round(a["actual_batted"] + bb, 1),
            "luck": round((a["actual_batted"] + bb) - est_total[tid], 1),
            "dtw_pct": round(dtw_pct, 0),
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
