"""Expanded O27 metrics — the stuff beyond the box score.

Everything here is derived from data already persisted (game_pa_log state
stamps + EV/LA/spray, game_batter_stats / game_pitcher_stats aggregates), so
no engine or schema change is needed. Functions return plain dicts/lists for
the web layer.

Contents:
  * build_expected_stats     — xBA / xSLG / xwOBA per batter (EV/LA bins)
  * build_pitch_arsenal      — run value & contact allowed by pitch type
  * build_baserunning_value  — Extra-Bases-Taken% + a baserunning run value
  * build_tto_penalty        — times-through-order K% decline per pitcher
  * build_second_chance_value— run value of Second-Chance ABs (O27-native)
  * build_fielding_value     — Outs Above Average / Fielding Run Value
  * build_win_probability    — WPA + average Leverage Index per player

Approximations are documented inline and in the AAR — WP and fielding-zone
attribution are necessarily empirical/heuristic.
"""
from __future__ import annotations
from collections import defaultdict

from o27v2 import db
from o27v2.analytics.linear_weights import derive_linear_weights

# Out-coded hit types (the BIP outcomes that retire the batter).
_OUT_TYPES = {"ground_out", "fly_out", "line_out", "fielders_choice",
              "double_play", "triple_play", "itp_out"}
_HIT_TYPES = {"single", "infield_single", "double", "triple", "hr", "home_run"}
_RUN_PER_OUT = 0.78   # O27 RE-derived value of an out (≈ avg RE swing)


def _team_in(team_ids, col="team_id") -> str:
    if not team_ids:
        return ""
    return " AND %s IN (%s)" % (col, ",".join(str(int(t)) for t in team_ids))


def _outs_bucket(outs) -> int:
    return min(8, max(0, (outs or 0) // 3))


def _names(player_ids) -> dict:
    if not player_ids:
        return {}
    ids = ",".join(str(int(p)) for p in player_ids)
    return {n["id"]: n for n in db.fetchall(
        f"""SELECT p.id, p.name, t.abbrev AS team_abbrev
            FROM players p LEFT JOIN teams t ON t.id = p.team_id
            WHERE p.id IN ({ids})""")}


# ---------------------------------------------------------------------------
# Run-expectancy backbone (powers run-value metrics)
# ---------------------------------------------------------------------------

def _re_lookup(team_ids=None) -> dict:
    """RE[(bases_mask, outs_bucket)] = mean runs from that state to half end.
    Built by a tail-sum scan of regulation PA-log rows."""
    rows = db.fetchall(
        "SELECT game_id, team_id, ab_seq, swing_idx, outs_before, bases_before, runs_scored "
        "FROM game_pa_log WHERE phase = 0" + _team_in(team_ids) +
        " ORDER BY game_id, team_id, ab_seq, swing_idx")
    # Group by half, walk backwards accumulating future runs.
    halves: dict = defaultdict(list)
    for r in rows:
        halves[(r["game_id"], r["team_id"])].append(r)
    sums: dict = defaultdict(float)
    cnts: dict = defaultdict(int)
    for evs in halves.values():
        future = 0
        for r in reversed(evs):
            key = (r["bases_before"], _outs_bucket(r["outs_before"]))
            sums[key] += future
            cnts[key] += 1
            future += (r["runs_scored"] or 0)
    return {k: (sums[k] / cnts[k]) if cnts[k] else 0.0 for k in cnts}


def _event_rows(team_ids=None) -> list[dict]:
    """Per-event rows with a run value attached (RE_after + runs − RE_before)."""
    re = _re_lookup(team_ids)
    rows = db.fetchall(
        """SELECT batter_id, pitcher_id, pitch_type, hit_type, was_stay, stay_credited,
                  outs_before, bases_before, outs_after, bases_after, runs_scored,
                  exit_velocity, launch_angle, spray_angle, team_id, game_id
           FROM game_pa_log WHERE phase = 0""" + _team_in(team_ids))
    out = []
    for r in rows:
        re_before = re.get((r["bases_before"], _outs_bucket(r["outs_before"])), 0.0)
        if r["outs_after"] is None or r["outs_after"] >= 27:
            re_after = 0.0
        else:
            re_after = re.get((r["bases_after"], _outs_bucket(r["outs_after"])), 0.0)
        r = dict(r)
        r["rv"] = re_after + (r["runs_scored"] or 0) - re_before
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# 1. Expected stats — xBA / xSLG / xwOBA from (EV, LA) bins
# ---------------------------------------------------------------------------

_EV_EDGES = (80.0, 90.0, 100.0, 110.0)
_LA_EDGES = (0.0, 12.0, 24.0, 40.0)


def _ev_bin(ev):
    if ev is None:
        return None
    for i, e in enumerate(_EV_EDGES):
        if ev < e:
            return i
    return len(_EV_EDGES)


def _la_bin(la):
    if la is None:
        return None
    for i, e in enumerate(_LA_EDGES):
        if la < e:
            return i
    return len(_LA_EDGES)


def build_expected_stats(min_bip: int = 20, team_ids=None) -> dict:
    """Per-batter xBA / xSLG / xwOBA: the league-average BA, total-base, and
    wOBA value of each (EV, LA) bin, replayed over the batter's BIP."""
    w = derive_linear_weights(team_ids=team_ids)["woba_weights"]

    def tb(ht):
        return {"single": 1, "infield_single": 1, "double": 2, "triple": 3,
                "hr": 4, "home_run": 4}.get(ht, 0)

    def wv(ht):
        return {"single": w["1B"], "infield_single": w["1B"], "double": w["2B"],
                "triple": w["3B"], "hr": w["HR"], "home_run": w["HR"]}.get(ht, 0.0)

    rows = db.fetchall(
        "SELECT batter_id, hit_type, exit_velocity, launch_angle "
        "FROM game_pa_log WHERE phase = 0 AND exit_velocity IS NOT NULL" + _team_in(team_ids))
    bin_h = defaultdict(int); bin_tb = defaultdict(float)
    bin_w = defaultdict(float); bin_n = defaultdict(int)
    per = defaultdict(lambda: {"bip": 0, "bins": []})
    for r in rows:
        key = (_ev_bin(r["exit_velocity"]), _la_bin(r["launch_angle"]))
        is_hit = 1 if r["hit_type"] in _HIT_TYPES else 0
        bin_h[key] += is_hit; bin_tb[key] += tb(r["hit_type"])
        bin_w[key] += wv(r["hit_type"]); bin_n[key] += 1
        per[r["batter_id"]]["bip"] += 1
        per[r["batter_id"]]["bins"].append(key)
    xba_bin = {k: bin_h[k] / bin_n[k] for k in bin_n}
    xslg_bin = {k: bin_tb[k] / bin_n[k] for k in bin_n}
    xw_bin = {k: bin_w[k] / bin_n[k] for k in bin_n}

    # AB / K for true xBA / xSLG (denominator = AB, so strikeouts drag down).
    ab = {r["player_id"]: r for r in db.fetchall(
        "SELECT player_id, COALESCE(SUM(ab),0) AS ab FROM game_batter_stats "
        "WHERE phase = 0" + _team_in(team_ids, "team_id") + " GROUP BY player_id")}
    names = _names(list(per.keys()))
    leaders = []
    for pid, d in per.items():
        if d["bip"] < min_bip:
            continue
        n = d["bip"]
        ab_n = (ab.get(pid) or {}).get("ab", 0) or n
        exp_h = sum(xba_bin[k] for k in d["bins"])
        exp_tb = sum(xslg_bin[k] for k in d["bins"])
        leaders.append({
            "player_id": pid,
            "player_name": (names.get(pid) or {}).get("name", f"#{pid}"),
            "team_abbrev": (names.get(pid) or {}).get("team_abbrev", ""),
            "bip": n,
            "xba":  round(exp_h / ab_n, 3),
            "xslg": round(exp_tb / ab_n, 3),
            "xwobacon": round(sum(xw_bin[k] for k in d["bins"]) / n, 3),
        })
    leaders.sort(key=lambda x: -x["xwobacon"])
    return {"leaders": leaders}


# ---------------------------------------------------------------------------
# 2. Pitch arsenal — run value & contact allowed by pitch type
# ---------------------------------------------------------------------------

def build_pitch_arsenal(min_bip: int = 15, team_ids=None) -> dict:
    """Per (pitcher, pitch_type): balls in play, run value allowed per 100 BIP
    (negative = run prevention, good), avg EV allowed, hit rate. NOTE: pa_log
    logs contact events only, so this is contact-quality-by-pitch, not whiffs."""
    ev = _event_rows(team_ids)
    agg = defaultdict(lambda: {"n": 0, "rv": 0.0, "ev_sum": 0.0, "ev_n": 0, "hits": 0})
    for r in ev:
        pid = r["pitcher_id"]; pt = r["pitch_type"]
        if pid is None or not pt:
            continue
        a = agg[(pid, pt)]
        a["n"] += 1
        a["rv"] += r["rv"]
        if r["exit_velocity"] is not None:
            a["ev_sum"] += r["exit_velocity"]; a["ev_n"] += 1
        if r["hit_type"] in _HIT_TYPES:
            a["hits"] += 1
    names = _names(list({pid for pid, _pt in agg}))
    rows = []
    for (pid, pt), a in agg.items():
        if a["n"] < min_bip:
            continue
        rows.append({
            "player_id": pid,
            "player_name": (names.get(pid) or {}).get("name", f"#{pid}"),
            "team_abbrev": (names.get(pid) or {}).get("team_abbrev", ""),
            "pitch_type": pt,
            "bip": a["n"],
            "rv100": round(-100.0 * a["rv"] / a["n"], 2),   # +ve = run prevention
            "avg_ev": round(a["ev_sum"] / a["ev_n"], 1) if a["ev_n"] else None,
            "hit_pct": round(100.0 * a["hits"] / a["n"], 1),
        })
    rows.sort(key=lambda x: -x["rv100"])
    return {"rows": rows}


# ---------------------------------------------------------------------------
# 3. Baserunning value — XBT% + a simple run value
# ---------------------------------------------------------------------------

def build_baserunning_value(min_op: int = 10, team_ids=None) -> dict:
    """Extra-Bases-Taken% (advances / opportunities, from the runner-advance
    tracking) and a baserunning run value combining XBT, steals and outs."""
    rows = db.fetchall(
        """SELECT player_id,
                  COALESCE(SUM(adv_op_1b),0)+COALESCE(SUM(adv_op_2b),0)+COALESCE(SUM(adv_op_3b),0) AS op,
                  COALESCE(SUM(adv_adv_1b),0)+COALESCE(SUM(adv_adv_2b),0)+COALESCE(SUM(adv_adv_3b),0) AS adv,
                  COALESCE(SUM(sb),0) AS sb, COALESCE(SUM(cs),0) AS cs
           FROM game_batter_stats WHERE phase = 0""" + _team_in(team_ids, "team_id") +
        " GROUP BY player_id")
    valid = [r for r in rows if (r["op"] or 0) >= min_op]
    lg_xbt = (sum(r["adv"] for r in valid) / sum(r["op"] for r in valid)) if valid and sum(r["op"] for r in valid) else 0.0
    names = _names([r["player_id"] for r in valid])
    leaders = []
    for r in valid:
        op = r["op"] or 0; adv = r["adv"] or 0
        xbt = adv / op if op else 0.0
        # Run value: extra bases above league rate (~0.25 run/base) + steals.
        bsr = (adv - lg_xbt * op) * 0.25 + (r["sb"] or 0) * 0.20 - (r["cs"] or 0) * 0.42
        leaders.append({
            "player_id": r["player_id"],
            "player_name": (names.get(r["player_id"]) or {}).get("name", f"#{r['player_id']}"),
            "team_abbrev": (names.get(r["player_id"]) or {}).get("team_abbrev", ""),
            "opp": op, "xbt_pct": round(100 * xbt, 1),
            "sb": r["sb"], "cs": r["cs"], "bsr": round(bsr, 1),
        })
    leaders.sort(key=lambda x: -x["bsr"])
    return {"leaders": leaders, "league_xbt_pct": round(100 * lg_xbt, 1)}


# ---------------------------------------------------------------------------
# 4. Times-through-order penalty
# ---------------------------------------------------------------------------

def build_tto_penalty(min_bf: int = 60, team_ids=None) -> dict:
    """Per pitcher: K% the 1st / 2nd / 3rd+ time through the order, and the
    1st→3rd penalty (decline). Plus the league aggregate."""
    rows = db.fetchall(
        """SELECT player_id,
                  COALESCE(SUM(bf_tto1),0) AS bf1, COALESCE(SUM(bf_tto2),0) AS bf2, COALESCE(SUM(bf_tto3),0) AS bf3,
                  COALESCE(SUM(k_tto1),0)  AS k1,  COALESCE(SUM(k_tto2),0)  AS k2,  COALESCE(SUM(k_tto3),0)  AS k3
           FROM game_pitcher_stats WHERE phase = 0""" + _team_in(team_ids, "team_id") +
        " GROUP BY player_id")
    lg = {f"bf{i}": 0 for i in (1, 2, 3)} | {f"k{i}": 0 for i in (1, 2, 3)}
    valid = []
    for r in rows:
        if (r["bf1"] + r["bf2"] + r["bf3"]) < min_bf:
            continue
        valid.append(r)
        for i in (1, 2, 3):
            lg[f"bf{i}"] += r[f"bf{i}"]; lg[f"k{i}"] += r[f"k{i}"]
    names = _names([r["player_id"] for r in valid])

    def pct(k, bf):
        return round(100 * k / bf, 1) if bf else None
    leaders = []
    for r in valid:
        k1 = pct(r["k1"], r["bf1"]); k3 = pct(r["k3"], r["bf3"])
        # A real 1st→3rd penalty needs enough 3rd-time-through batters faced.
        penalty = (round(k1 - k3, 1) if (k1 is not None and k3 is not None
                                         and r["bf1"] >= 15 and r["bf3"] >= 15) else None)
        leaders.append({
            "player_id": r["player_id"],
            "player_name": (names.get(r["player_id"]) or {}).get("name", f"#{r['player_id']}"),
            "team_abbrev": (names.get(r["player_id"]) or {}).get("team_abbrev", ""),
            "k_tto1": k1, "k_tto2": pct(r["k2"], r["bf2"]), "k_tto3": k3,
            "penalty": penalty,
        })
    leaders.sort(key=lambda x: (x["penalty"] is None, -(x["penalty"] or 0)))
    league = {f"k_tto{i}": pct(lg[f"k{i}"], lg[f"bf{i}"]) for i in (1, 2, 3)}
    return {"leaders": leaders, "league": league}


# ---------------------------------------------------------------------------
# 5. Second-Chance run value (O27-native)
# ---------------------------------------------------------------------------

def build_second_chance_value(min_2c: int = 10, team_ids=None) -> dict:
    """Run value of Second-Chance ABs. Each 2C event's RE24 run value is
    aggregated per batter; the league mean is the break-even bar (a batter
    above it is making +EV second-chance decisions)."""
    ev = [r for r in _event_rows(team_ids) if r["was_stay"]]
    per = defaultdict(lambda: {"n": 0, "rv": 0.0, "credited": 0})
    tot_rv = 0.0; tot_n = 0
    for r in ev:
        p = per[r["batter_id"]]
        p["n"] += 1; p["rv"] += r["rv"]; p["credited"] += (r["stay_credited"] or 0)
        tot_rv += r["rv"]; tot_n += 1
    lg_per = (tot_rv / tot_n) if tot_n else 0.0
    names = _names(list(per.keys()))
    leaders = []
    for pid, d in per.items():
        if d["n"] < min_2c:
            continue
        leaders.append({
            "player_id": pid,
            "player_name": (names.get(pid) or {}).get("name", f"#{pid}"),
            "team_abbrev": (names.get(pid) or {}).get("team_abbrev", ""),
            "n_2c": d["n"],
            "conv_pct": round(100 * d["credited"] / d["n"], 1),
            "rv_per_2c": round(d["rv"] / d["n"], 3),
            "rv_total": round(d["rv"], 1),
            "rv_vs_lg": round(d["rv"] / d["n"] - lg_per, 3),
        })
    leaders.sort(key=lambda x: -x["rv_total"])
    return {"leaders": leaders, "league_rv_per_2c": round(lg_per, 3)}


# ---------------------------------------------------------------------------
# 6. Fielding — Outs Above Average / Fielding Run Value
# ---------------------------------------------------------------------------

def _zone_from_trajectory(la, spray):
    """Heuristic responsible position from (launch angle, spray). Approximate —
    no batted-ball coordinates, just LA band + spray side. RHB pull = negative
    spray (LF side)."""
    if la is None or spray is None:
        return None
    if la < 10:   # ground ball → infield
        if spray <= -18: return "3B"
        if spray <= -4:  return "SS"
        if spray < 12:   return "2B"
        return "1B"
    # air ball → outfield
    if spray <= -12: return "LF"
    if spray < 12:   return "CF"
    return "RF"


def build_fielding_value(min_chances: int = 25, team_ids=None) -> dict:
    """Outs Above Average + Fielding Run Value. Catch probability per ball is
    the league out-rate for its (EV, LA) bin. Attribution is exact on outs (the
    engine-credited fielder_id, PO-consistent) and falls back to trajectory-zone
    → positional regular for balls that fell in. OAA = Σ(out − expected_out);
    FRV = OAA × run/out. `exact_pct` reports the share of chances exactly
    attributed."""
    # League out-rate per (EV, LA) bin.
    binr_out = defaultdict(int); binr_n = defaultdict(int)
    rows = db.fetchall(
        """SELECT team_id, game_id, hit_type, exit_velocity, launch_angle, spray_angle, fielder_id
           FROM game_pa_log WHERE phase = 0 AND exit_velocity IS NOT NULL""" + _team_in(team_ids))
    for r in rows:
        key = (_ev_bin(r["exit_velocity"]), _la_bin(r["launch_angle"]))
        binr_n[key] += 1
        if r["hit_type"] in _OUT_TYPES:
            binr_out[key] += 1
    out_rate = {k: binr_out[k] / binr_n[k] for k in binr_n}

    # Game → fielding team for each batting team.
    games = {g["id"]: (g["home_team_id"], g["away_team_id"])
             for g in db.fetchall("SELECT id, home_team_id, away_team_id FROM games WHERE played = 1")}

    # Per (fielding_team, position) regular = most-used player at that position.
    appers = db.fetchall(
        """SELECT b.team_id, b.player_id, p.position, COUNT(*) AS g
           FROM game_batter_stats b JOIN players p ON p.id = b.player_id
           WHERE b.phase = 0""" + _team_in(team_ids, "b.team_id") +
        " GROUP BY b.team_id, b.player_id")
    regular = {}   # (team_id, pos) -> (player_id, games)
    for a in appers:
        pos = (a["position"] or "").strip()
        if not pos:
            continue
        key = (a["team_id"], pos)
        if key not in regular or a["g"] > regular[key][1]:
            regular[key] = (a["player_id"], a["g"])

    oaa = defaultdict(float); chances = defaultdict(int)
    n_exact = 0; n_heur = 0
    for r in rows:
        is_out = r["hit_type"] in _OUT_TYPES
        # Exact attribution: on an out the engine credits a real fielder
        # (PO-consistent). Fall back to trajectory-zone → positional regular for
        # hits and for outs with no persisted fielder (legacy rows / bunts).
        fielder = None
        if is_out and r["fielder_id"] is not None:
            fielder = r["fielder_id"]; n_exact += 1
        else:
            zone = _zone_from_trajectory(r["launch_angle"], r["spray_angle"])
            teams = games.get(r["game_id"])
            if not zone or not teams:
                continue
            home, away = teams
            field_team = home if r["team_id"] == away else away
            reg = regular.get((field_team, zone))
            if not reg:
                continue
            fielder = reg[0]; n_heur += 1
        exp = out_rate.get((_ev_bin(r["exit_velocity"]), _la_bin(r["launch_angle"])), 0.0)
        actual = 1.0 if is_out else 0.0
        oaa[fielder] += (actual - exp)
        chances[fielder] += 1

    names = _names(list(oaa.keys()))
    leaders = []
    for pid, v in oaa.items():
        if chances[pid] < min_chances:
            continue
        leaders.append({
            "player_id": pid,
            "player_name": (names.get(pid) or {}).get("name", f"#{pid}"),
            "team_abbrev": (names.get(pid) or {}).get("team_abbrev", ""),
            "chances": chances[pid],
            "oaa": round(v, 1),
            "frv": round(v * _RUN_PER_OUT, 1),
        })
    leaders.sort(key=lambda x: -x["oaa"])
    tot = n_exact + n_heur
    return {"leaders": leaders,
            "exact_pct": round(100 * n_exact / tot, 1) if tot else 0.0}


# ---------------------------------------------------------------------------
# 7. Win Probability Added + Leverage Index
# ---------------------------------------------------------------------------

def _second_half(row) -> int:
    """Is the event's batting team the SECOND to bat? Uses the persisted
    home_bats_first flag (the first-batting team = home iff home_bats_first)."""
    hbf = row["home_bats_first"] if row["home_bats_first"] is not None else 0
    first_team = row["home_team_id"] if hbf else row["away_team_id"]
    return 0 if row["team_id"] == first_team else 1


def _wp_table(team_ids=None) -> dict:
    """Empirical P(batting team wins | half-order, score_diff, outs, bases).
    Split by which team bats first/second (persisted home_bats_first), so the
    huge structural asymmetry of O27's two sequential halves is captured."""
    rows = db.fetchall(
        """SELECT l.team_id, l.outs_before, l.bases_before, l.score_diff_before,
                  g.winner_id, g.home_bats_first, g.home_team_id, g.away_team_id
           FROM game_pa_log l JOIN games g ON g.id = l.game_id
           WHERE l.phase = 0 AND g.played = 1 AND g.winner_id IS NOT NULL"""
        + _team_in(team_ids, "l.team_id"))
    win = defaultdict(int); n = defaultdict(int)
    for r in rows:
        sd = max(-10, min(10, r["score_diff_before"] if r["score_diff_before"] is not None else 0))
        key = (_second_half(r), sd, _outs_bucket(r["outs_before"]), r["bases_before"])
        n[key] += 1
        if r["winner_id"] == r["team_id"]:
            win[key] += 1
    return {k: win[k] / n[k] for k in n}


def build_win_probability(min_pa: int = 50, team_ids=None) -> dict:
    """WPA (sum of win-probability swings) and average Leverage Index per
    batter and pitcher. The WP table is empirical from this league's games and
    split by batting half (persisted home_bats_first), so it respects O27's two
    sequential 27-out halves rather than pooling them."""
    wp = _wp_table(team_ids)

    def lookup(half, sd, outs, bases):
        if sd is None or outs is None or bases is None:
            return None
        return wp.get((half, max(-10, min(10, sd)), _outs_bucket(outs), bases))

    rows = db.fetchall(
        """SELECT l.batter_id, l.pitcher_id, l.team_id,
                  l.outs_before, l.bases_before, l.score_diff_before,
                  l.outs_after, l.bases_after, l.score_diff_after,
                  g.home_bats_first, g.home_team_id, g.away_team_id
           FROM game_pa_log l JOIN games g ON g.id = l.game_id
           WHERE l.phase = 0""" + _team_in(team_ids, "l.team_id"))
    swings = []
    bat = defaultdict(lambda: {"wpa": 0.0, "li": 0.0, "n": 0})
    pit = defaultdict(lambda: {"wpa": 0.0, "li": 0.0, "n": 0})
    for r in rows:
        half = _second_half(r)   # constant within a batting half
        wp0 = lookup(half, r["score_diff_before"], r["outs_before"], r["bases_before"])
        if r["outs_after"] is not None and r["outs_after"] >= 27:
            wp1 = wp0   # half boundary — no in-half swing measured
        else:
            wp1 = lookup(half, r["score_diff_after"], r["outs_after"], r["bases_after"])
        if wp0 is None or wp1 is None:
            continue
        d = wp1 - wp0
        swings.append(abs(d))
        bat[r["batter_id"]]["wpa"] += d; bat[r["batter_id"]]["n"] += 1
        bat[r["batter_id"]]["li"] += abs(d)
        if r["pitcher_id"] is not None:
            pit[r["pitcher_id"]]["wpa"] -= d; pit[r["pitcher_id"]]["n"] += 1
            pit[r["pitcher_id"]]["li"] += abs(d)
    mean_swing = (sum(swings) / len(swings)) if swings else 1e-9

    def finish(agg, key_min):
        names = _names(list(agg.keys()))
        out = []
        for pid, d in agg.items():
            if d["n"] < key_min:
                continue
            out.append({
                "player_id": pid,
                "player_name": (names.get(pid) or {}).get("name", f"#{pid}"),
                "team_abbrev": (names.get(pid) or {}).get("team_abbrev", ""),
                "pa": d["n"],
                "wpa": round(d["wpa"], 2),
                "li": round((d["li"] / d["n"]) / mean_swing, 2) if d["n"] else None,
            })
        out.sort(key=lambda x: -x["wpa"])
        return out

    return {"batters": finish(bat, min_pa), "pitchers": finish(pit, min_pa)}
