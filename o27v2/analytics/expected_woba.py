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


def _team_in(team_ids, col="team_id"):
    """SQL fragment restricting `col` to team_ids, or '' when unfiltered."""
    if not team_ids:
        return ""
    ids = ",".join(str(int(t)) for t in team_ids)
    return f" AND {col} IN ({ids})"


def _woba_weights(team_ids=None) -> dict:
    return derive_linear_weights(team_ids=team_ids)["woba_weights"]


def _bip_woba_points(weights: dict,
                     hit_type: str | None,
                     was_stay: int, stay_credited: int) -> float:
    """wOBA points credited for one BIP event under the given weights."""
    if was_stay and stay_credited:
        # Stay-credited events have their own weight in the refit table;
        # fall back to the 1B weight only if a legacy weight dict is
        # missing the STAY key.
        return weights.get("STAY", weights["1B"])
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


def _quality_table(team_ids=None) -> dict[str, dict]:
    """Compute league xwOBA-per-BIP for each quality bucket.

    Returns:
        {
            "weak":   {"n": int, "xwoba_per_bip": float, "actual_woba_per_bip": float},
            "medium": {...},
            "hard":   {...},
            None:     {...},   # legacy / unknown quality bucket
        }
    """
    weights = _woba_weights(team_ids)
    rows = db.fetchall(
        """
        SELECT quality, hit_type, was_stay, stay_credited, COUNT(*) AS n
        FROM game_pa_log
        WHERE phase = 0"""
        + _team_in(team_ids, "team_id")
        + """
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


# EV / LA bin edges for the physics-native xwOBA. With physics-first
# resolution the trajectory DRIVES the outcome, so binning by (exit velocity,
# launch angle) — Statcast's actual method — is now meaningful, where the
# quality-bucket version (above) only saw weak/medium/hard.
_EV_EDGES = (80.0, 90.0, 100.0, 110.0)   # → 5 buckets
_LA_EDGES = (0.0, 12.0, 24.0, 40.0)      # → 5 buckets


def _ev_la_bin_sql() -> tuple[str, str]:
    ev = ("CASE WHEN exit_velocity < %g THEN 0 WHEN exit_velocity < %g THEN 1 "
          "WHEN exit_velocity < %g THEN 2 WHEN exit_velocity < %g THEN 3 ELSE 4 END"
          % _EV_EDGES)
    la = ("CASE WHEN launch_angle < %g THEN 0 WHEN launch_angle < %g THEN 1 "
          "WHEN launch_angle < %g THEN 2 WHEN launch_angle < %g THEN 3 ELSE 4 END"
          % _LA_EDGES)
    return ev, la


def _ev_la_bin_xwoba(weights: dict, team_ids=None) -> dict:
    """League-average wOBA-per-BIP for each (ev_bin, la_bin). Shared by the
    batter and pitcher EV/LA xwOBA tables so both use the identical surface."""
    ev_sql, la_sql = _ev_la_bin_sql()
    rows = db.fetchall(
        f"""
        SELECT {ev_sql} AS ev_bin, {la_sql} AS la_bin,
               hit_type, was_stay, stay_credited, COUNT(*) AS n
        FROM game_pa_log
        WHERE phase = 0 AND exit_velocity IS NOT NULL"""
        + _team_in(team_ids, "team_id")
        + """
        GROUP BY ev_bin, la_bin, hit_type, was_stay, stay_credited
        """
    )
    bsum: dict[tuple, float] = defaultdict(float)
    bcnt: dict[tuple, int] = defaultdict(int)
    for r in rows:
        key = (r["ev_bin"], r["la_bin"])
        bsum[key] += _bip_woba_points(weights, r["hit_type"], r["was_stay"], r["stay_credited"]) * r["n"]
        bcnt[key] += r["n"]
    return {k: (bsum[k] / bcnt[k]) if bcnt[k] else 0.0 for k in bcnt}


def build_xwoba_against_table(min_bf: int = 1, team_ids=None) -> dict:
    """Per-pitcher xwOBA-against: the league-average (EV, LA)-bin value of every
    ball a pitcher allowed, plus walks/HBP allowed, over batters faced. The
    pitcher-side mirror of build_xwoba_ev_table. Returns rows keyed by
    `player_id` with `woba_against`, `xwoba_against`, `xwoba_diff`
    (woba_against − xwoba_against; negative = suppressed more than expected)."""
    weights = _woba_weights(team_ids)
    bin_xwoba = _ev_la_bin_xwoba(weights, team_ids)
    ev_sql, la_sql = _ev_la_bin_sql()

    pid_in = ""
    if team_ids:
        pid_in = (" AND pitcher_id IN (SELECT id FROM players WHERE team_id IN (%s))"
                  % ",".join(str(int(t)) for t in team_ids))
    rows = db.fetchall(
        f"""
        SELECT {ev_sql} AS ev_bin, {la_sql} AS la_bin,
               pitcher_id AS player_id, hit_type, was_stay, stay_credited, COUNT(*) AS n
        FROM game_pa_log
        WHERE phase = 0 AND exit_velocity IS NOT NULL{pid_in}
        GROUP BY ev_bin, la_bin, pitcher_id, hit_type, was_stay, stay_credited
        """
    )
    actual_pts: dict[int, float] = defaultdict(float)
    expected_pts: dict[int, float] = defaultdict(float)
    for r in rows:
        pid = r["player_id"]
        key = (r["ev_bin"], r["la_bin"])
        actual_pts[pid] += _bip_woba_points(weights, r["hit_type"], r["was_stay"], r["stay_credited"]) * r["n"]
        expected_pts[pid] += bin_xwoba.get(key, 0.0) * r["n"]

    team_in = ""
    if team_ids:
        team_in = " AND team_id IN (%s)" % ",".join(str(int(t)) for t in team_ids)
    pit = db.fetchall(
        f"""SELECT player_id, SUM(batters_faced) AS bf, SUM(bb) AS bb,
                   COALESCE(SUM(hbp_allowed), 0) AS hbp
            FROM (SELECT * FROM game_pitcher_stats WHERE COALESCE(is_playoff,0) = 0)
            WHERE phase = 0{team_in}
            GROUP BY player_id""")

    out = {}
    for r in pit:
        pid = r["player_id"]
        bf = r["bf"] or 0
        if bf <= 0:
            continue
        bb = r["bb"] or 0
        hbp = r["hbp"] or 0
        actual = actual_pts.get(pid, 0.0) + weights["BB"] * bb + weights["HBP"] * hbp
        expect = expected_pts.get(pid, 0.0) + weights["BB"] * bb + weights["HBP"] * hbp
        if bf >= min_bf:
            out[pid] = {
                "woba_against":  round(actual / bf, 3),
                "xwoba_against": round(expect / bf, 3),
                "xwoba_diff":    round((actual - expect) / bf, 3),
            }
    return out


def build_xwoba_ev_table(min_pa: int = 162, team_ids=None) -> dict:
    """Per-batter xwOBA where the expected value of each ball in play is the
    league-average wOBA for its (EV, LA) bin — the physics-native version of
    build_xwoba_table. Same return shape, plus per-bin diagnostics."""
    weights = _woba_weights(team_ids)
    ev_sql, la_sql = _ev_la_bin_sql()

    rows = db.fetchall(
        f"""
        SELECT {ev_sql} AS ev_bin, {la_sql} AS la_bin,
               batter_id AS player_id, hit_type, was_stay, stay_credited,
               COUNT(*) AS n
        FROM game_pa_log
        WHERE phase = 0 AND exit_velocity IS NOT NULL"""
        + _team_in(team_ids, "team_id")
        + """
        GROUP BY ev_bin, la_bin, batter_id, hit_type, was_stay, stay_credited
        """
    )

    # Pass 1: league xwOBA-per-BIP for each (ev_bin, la_bin).
    bin_sum: dict[tuple, float] = defaultdict(float)
    bin_cnt: dict[tuple, int] = defaultdict(int)
    for r in rows:
        key = (r["ev_bin"], r["la_bin"])
        wpts = _bip_woba_points(weights, r["hit_type"], r["was_stay"], r["stay_credited"])
        bin_sum[key] += wpts * r["n"]
        bin_cnt[key] += r["n"]
    bin_xwoba = {k: (bin_sum[k] / bin_cnt[k]) if bin_cnt[k] else 0.0 for k in bin_cnt}

    # Pass 2: per-batter expected + actual points from the same rows.
    actual_pts: dict[int, float] = defaultdict(float)
    expected_pts: dict[int, float] = defaultdict(float)
    for r in rows:
        pid = r["player_id"]
        key = (r["ev_bin"], r["la_bin"])
        actual_pts[pid] += _bip_woba_points(weights, r["hit_type"], r["was_stay"], r["stay_credited"]) * r["n"]
        expected_pts[pid] += bin_xwoba.get(key, 0.0) * r["n"]

    bat_rows = db.fetchall(
        """
        SELECT b.player_id, p.name AS player_name, t.abbrev AS team_abbrev,
               SUM(b.pa) AS pa, SUM(b.bb) AS bb, SUM(b.hbp) AS hbp
        FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) b
        JOIN players p ON p.id = b.player_id
        LEFT JOIN teams t ON t.id = b.team_id
        WHERE b.phase = 0"""
        + _team_in(team_ids, "b.team_id")
        + """
        GROUP BY b.player_id
        """
    )

    leaders = []
    total_actual = total_expected = 0.0
    total_pa = 0
    for r in bat_rows:
        pid = r["player_id"]
        pa = r["pa"] or 0
        if pa <= 0:
            continue
        bb = r["bb"] or 0
        hbp = r["hbp"] or 0
        actual = actual_pts.get(pid, 0.0) + weights["BB"] * bb + weights["HBP"] * hbp
        expect = expected_pts.get(pid, 0.0) + weights["BB"] * bb + weights["HBP"] * hbp
        total_actual += actual
        total_expected += expect
        total_pa += pa
        if pa >= min_pa:
            leaders.append({
                "player_id":        pid,
                "player_name":      r["player_name"],
                "team_abbrev":      r["team_abbrev"] or "",
                "pa":               pa,
                "woba":             round(actual / pa, 3),
                "xwoba":            round(expect / pa, 3),
                "woba_minus_xwoba": round((actual - expect) / pa, 3),
            })
    leaders.sort(key=lambda x: -x["xwoba"])
    return {
        "leaders":      leaders,
        "league_woba":  (total_actual / total_pa) if total_pa else 0.0,
        "league_xwoba": (total_expected / total_pa) if total_pa else 0.0,
        "n_bins":       len(bin_cnt),
    }


def build_xwoba_table(min_pa: int = 162, team_ids=None) -> dict:
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
    weights   = _woba_weights(team_ids)
    qtable    = _quality_table(team_ids)
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
        WHERE phase = 0"""
        + _team_in(team_ids, "team_id")
        + """
        GROUP BY batter_id, quality
        """
    )
    actual_pts:   dict[int, float] = defaultdict(float)
    expected_pts: dict[int, float] = defaultdict(float)
    bip_count:    dict[int, int]   = defaultdict(int)
    for r in bip_rows:
        pid = r["player_id"]
        q = r["quality"]
        # Actual: rebuild from event mix. Stays use their own STAY
        # weight rather than being lumped into 1B (legacy fallback only
        # if a stale weight dict is missing the key).
        stay_w = weights.get("STAY", weights["1B"])
        actual_pts[pid] += (
            weights["HR"] * r["hr"] + weights["3B"] * r["d3"] +
            weights["2B"] * r["d2"] + weights["1B"] * r["d1"] +
            stay_w        * r["stay_h"]
        )
        bip_count[pid] += r["n_bip"]
        expected_pts[pid] += bip_xwoba.get(q, 0.0) * r["n_bip"]

    bat_rows = db.fetchall(
        """
        SELECT b.player_id, p.name AS player_name, t.abbrev AS team_abbrev,
               SUM(b.pa) AS pa, SUM(b.bb) AS bb, SUM(b.hbp) AS hbp
        FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) b
        JOIN players p ON p.id = b.player_id
        LEFT JOIN teams t ON t.id = b.team_id
        WHERE b.phase = 0"""
        + _team_in(team_ids, "b.team_id")
        + """
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


_DEAD_OUT_HIT_TYPES = {"ground_out", "fly_out", "line_out", "double_play",
                       "triple_play", "itp_out"}


def _has_runner_advancement(bases_before, bases_after, runs_scored) -> bool:
    """Best-effort runner-advancement detector for PA-log base masks.

    A dead out allows no run and no existing runner to reach a higher base. The
    PA log stores occupancy masks rather than runner identities, so detect only
    clear advancement patterns: 1B -> 2B/3B, 2B -> 3B, or any run scoring.
    Runner erasures without advancement (for example a DP) can still be dead.
    """
    if runs_scored:
        return True
    if bases_before is None or bases_after is None:
        return False
    before = int(bases_before or 0)
    after = int(bases_after or 0)
    if (before & 1) and (after & (2 | 4)):
        return True
    if (before & 2) and (after & 4):
        return True
    return False


def build_expected_outs_table(min_bf: int = 1, team_ids=None) -> dict:
    """Per-pitcher Expected Outs (xO).

    xO is the out-probability sibling of xwOBA: strikeouts and foul-outs are
    credited as automatic expected outs, while balls in play are replayed at
    the league-average out count for their (EV, LA) bucket. The actual-minus-
    expected gap (`outs_minus_xouts`) is positive when the pitcher/defense
    converted more outs than the contact profile predicted and negative when
    out-shaped contact was not converted.
    """
    ev_sql, la_sql = _ev_la_bin_sql()
    team_filter = _team_in(team_ids, "team_id")
    pid_filter = ""
    if team_ids:
        pid_filter = (" AND pitcher_id IN (SELECT id FROM players WHERE team_id IN (%s))"
                      % ",".join(str(int(t)) for t in team_ids))

    league_bins = db.fetchall(
        f"""
        SELECT {ev_sql} AS ev_bin, {la_sql} AS la_bin,
               SUM(CASE WHEN outs_before IS NOT NULL AND outs_after IS NOT NULL
                        THEN CASE WHEN outs_after > outs_before
                                  THEN outs_after - outs_before ELSE 0 END
                        ELSE 0 END) AS outs,
               COUNT(*) AS n
        FROM game_pa_log
        WHERE phase = 0 AND exit_velocity IS NOT NULL{team_filter}
        GROUP BY ev_bin, la_bin
        """
    )
    bin_xouts = {
        (r["ev_bin"], r["la_bin"]): ((r["outs"] or 0) / r["n"] if (r["n"] or 0) else 0.0)
        for r in league_bins
    }

    bip_rows = db.fetchall(
        f"""
        SELECT {ev_sql} AS ev_bin, {la_sql} AS la_bin, pitcher_id AS player_id,
               COUNT(*) AS n
        FROM game_pa_log
        WHERE phase = 0 AND exit_velocity IS NOT NULL{pid_filter}
        GROUP BY ev_bin, la_bin, pitcher_id
        """
    )
    bip_xouts: dict[int, float] = defaultdict(float)
    for r in bip_rows:
        if r["player_id"] is None:
            continue
        bip_xouts[r["player_id"]] += bin_xouts.get((r["ev_bin"], r["la_bin"]), 0.0) * r["n"]

    team_in = ""
    if team_ids:
        team_in = " AND team_id IN (%s)" % ",".join(str(int(t)) for t in team_ids)
    pit = db.fetchall(
        f"""SELECT ps.player_id, p.name AS player_name, t.abbrev AS team_abbrev,
                  SUM(ps.batters_faced) AS bf, SUM(ps.outs_recorded) AS outs,
                  SUM(ps.k) AS k, COALESCE(SUM(ps.fo_induced), 0) AS fo
           FROM (SELECT * FROM game_pitcher_stats WHERE COALESCE(is_playoff,0) = 0) ps
           JOIN players p ON p.id = ps.player_id
           LEFT JOIN teams t ON t.id = ps.team_id
           WHERE ps.phase = 0{team_in}
           GROUP BY ps.player_id"""
    )
    pp = db.fetchall(
        f"""SELECT player_id, COALESCE(SUM(ppp_hits_saved), 0) AS hits_saved
            FROM game_power_play_stats
            WHERE 1=1{team_in}
            GROUP BY player_id"""
    )
    pp_saved_outs = {r["player_id"]: (r["hits_saved"] or 0) for r in pp}

    leaders = []
    by_player = {}
    for r in pit:
        pid = r["player_id"]
        bf = r["bf"] or 0
        if bf <= 0:
            continue
        automatic_outs = (r["k"] or 0) + (r["fo"] or 0)
        defense_support_outs = pp_saved_outs.get(pid, 0)
        pure_xouts = automatic_outs + bip_xouts.get(pid, 0.0)
        xouts = pure_xouts + defense_support_outs
        actual_outs = r["outs"] or 0
        row = {
            "player_id": pid,
            "player_name": r["player_name"],
            "team_abbrev": r["team_abbrev"] or "",
            "bf": bf,
            "outs": actual_outs,
            "pure_xouts": round(pure_xouts, 1),
            "pure_xouts_per_27": round(27 * pure_xouts / bf, 2),
            "xouts": round(xouts, 1),
            "xouts_per_27": round(27 * xouts / bf, 2),
            "outs_minus_xouts": round(actual_outs - xouts, 1),
            "defense_support_xouts": defense_support_outs,
        }
        by_player[pid] = row
        if bf >= min_bf:
            leaders.append(row)
    leaders.sort(key=lambda x: -x["xouts_per_27"])
    return {"leaders": leaders, "by_player": by_player, "n_bins": len(bin_xouts)}


def build_dead_outs_table(min_bf: int = 1, team_ids=None) -> dict:
    """Per-pitcher Dead Out Percentage (DO%).

    A dead out is an out that burns the batting side's out envelope without a
    run, runner advancement, or batter reach. Strikeouts and foul-outs are dead
    by definition. Contact outs qualify when the PA-log state shows no scoring
    and no runner advancement. Multi-out erasers count by outs, so a two-out
    double play contributes two dead outs and one dead-out PA.
    """
    pid_filter = ""
    if team_ids:
        pid_filter = (" AND pitcher_id IN (SELECT id FROM players WHERE team_id IN (%s))"
                      % ",".join(str(int(t)) for t in team_ids))
    events = db.fetchall(
        f"""SELECT pitcher_id AS player_id, hit_type, runs_scored,
                  outs_before, outs_after, bases_before, bases_after
            FROM game_pa_log
            WHERE phase = 0 AND pitcher_id IS NOT NULL{pid_filter}"""
    )
    bip_dead_outs: dict[int, int] = defaultdict(int)
    bip_dead_pas: dict[int, int] = defaultdict(int)
    for e in events:
        ht = e["hit_type"]
        if ht not in _DEAD_OUT_HIT_TYPES:
            continue
        if e["outs_before"] is None or e["outs_after"] is None:
            continue
        out_delta = max(0, int(e["outs_after"] or 0) - int(e["outs_before"] or 0))
        if out_delta <= 0 or (e["runs_scored"] or 0) > 0:
            continue
        # Double/triple plays are dead-out erasers if they did not score a run;
        # count the full out delta even though pre-existing runners disappeared.
        if ht not in ("double_play", "triple_play") and _has_runner_advancement(
            e["bases_before"], e["bases_after"], e["runs_scored"]
        ):
            continue
        pid = e["player_id"]
        bip_dead_outs[pid] += out_delta
        bip_dead_pas[pid] += 1

    team_in = ""
    if team_ids:
        team_in = " AND ps.team_id IN (%s)" % ",".join(str(int(t)) for t in team_ids)
    pit = db.fetchall(
        f"""SELECT ps.player_id, p.name AS player_name, t.abbrev AS team_abbrev,
                  SUM(ps.batters_faced) AS bf, SUM(ps.outs_recorded) AS outs,
                  SUM(ps.k) AS k, COALESCE(SUM(ps.fo_induced), 0) AS fo
           FROM (SELECT * FROM game_pitcher_stats WHERE COALESCE(is_playoff,0) = 0) ps
           JOIN players p ON p.id = ps.player_id
           LEFT JOIN teams t ON t.id = ps.team_id
           WHERE ps.phase = 0{team_in}
           GROUP BY ps.player_id"""
    )
    leaders = []
    by_player = {}
    for r in pit:
        pid = r["player_id"]
        bf = r["bf"] or 0
        outs = r["outs"] or 0
        if bf <= 0:
            continue
        automatic_dead = (r["k"] or 0) + (r["fo"] or 0)
        dead_outs = automatic_dead + bip_dead_outs.get(pid, 0)
        dead_pas = automatic_dead + bip_dead_pas.get(pid, 0)
        row = {
            "player_id": pid,
            "player_name": r["player_name"],
            "team_abbrev": r["team_abbrev"] or "",
            "bf": bf,
            "outs": outs,
            "dead_outs": dead_outs,
            "dead_out_pas": dead_pas,
            "dead_out_pct": round(100 * dead_outs / outs, 1) if outs else None,
            "dead_out_pa_pct": round(100 * dead_pas / bf, 1) if bf else None,
        }
        by_player[pid] = row
        if bf >= min_bf:
            leaders.append(row)
    leaders.sort(key=lambda x: -(x["dead_out_pct"] or 0))
    return {"leaders": leaders, "by_player": by_player}


def build_hitter_dead_outs_table(min_pa: int = 1, team_ids=None) -> dict:
    """Per-hitter dead-out avoidance.

    Mirrors pitcher DO% from the batting perspective. A two-out DP is two dead
    outs in `dead_outs`, while `dead_out_pas` counts the PA once.
    """
    team_filter = _team_in(team_ids, "team_id")
    events = db.fetchall(
        f"""SELECT batter_id AS player_id, hit_type, runs_scored,
                  outs_before, outs_after, bases_before, bases_after
            FROM game_pa_log
            WHERE phase = 0 AND batter_id IS NOT NULL{team_filter}"""
    )
    bip_dead_outs: dict[int, int] = defaultdict(int)
    bip_dead_pas: dict[int, int] = defaultdict(int)
    for e in events:
        ht = e["hit_type"]
        if ht not in _DEAD_OUT_HIT_TYPES:
            continue
        if e["outs_before"] is None or e["outs_after"] is None:
            continue
        out_delta = max(0, int(e["outs_after"] or 0) - int(e["outs_before"] or 0))
        if out_delta <= 0 or (e["runs_scored"] or 0) > 0:
            continue
        if ht not in ("double_play", "triple_play") and _has_runner_advancement(
            e["bases_before"], e["bases_after"], e["runs_scored"]
        ):
            continue
        pid = e["player_id"]
        bip_dead_outs[pid] += out_delta
        bip_dead_pas[pid] += 1

    bat = db.fetchall(
        f"""SELECT b.player_id, p.name AS player_name, t.abbrev AS team_abbrev,
                  SUM(b.pa) AS pa, SUM(b.k) AS k, COALESCE(SUM(b.fo), 0) AS fo
           FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) b
           JOIN players p ON p.id = b.player_id
           LEFT JOIN teams t ON t.id = b.team_id
           WHERE b.phase = 0{_team_in(team_ids, "b.team_id")}
           GROUP BY b.player_id"""
    )
    leaders = []
    by_player = {}
    for r in bat:
        pid = r["player_id"]
        pa = r["pa"] or 0
        if pa <= 0:
            continue
        automatic_dead = (r["k"] or 0) + (r["fo"] or 0)
        dead_outs = automatic_dead + bip_dead_outs.get(pid, 0)
        dead_pas = automatic_dead + bip_dead_pas.get(pid, 0)
        dead_pa_pct = round(100 * dead_pas / pa, 1) if pa else None
        row = {
            "player_id": pid,
            "player_name": r["player_name"],
            "team_abbrev": r["team_abbrev"] or "",
            "pa": pa,
            "dead_outs_bat": dead_outs,
            "dead_out_pas_bat": dead_pas,
            "dead_out_pa_pct_bat": dead_pa_pct,
            "dead_out_avoid_pct": round(100 - dead_pa_pct, 1) if dead_pa_pct is not None else None,
        }
        by_player[pid] = row
        if pa >= min_pa:
            leaders.append(row)
    leaders.sort(key=lambda x: -(x["dead_out_avoid_pct"] or 0))
    return {"leaders": leaders, "by_player": by_player}
