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
# Reliability-regression constant for Field Run Value. reliability =
# chances/(chances+K); higher K = heavier shrink toward league-average defense.
# K=400 lands a full-season qualified fielder at ~0.2–0.46 weight, damping the
# raw ±1.8-win OAA noise to a believable ±0.5-win fielding contribution. Tunable.
_FIELDING_REGRESSION_K = 400


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
                  exit_velocity, launch_angle, spray_angle, team_id, game_id,
                  ab_seq, swing_idx
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
        "SELECT player_id, COALESCE(SUM(ab),0) AS ab FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) "
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

# MLB linear-weight fallbacks for (run_per_extra_base, run_per_sb, run_per_cs).
# Used only when there's no league data to fit O27-native values from.
_BSR_MLB_FALLBACK = (0.25, 0.20, -0.42)


def _baserunning_run_values(team_ids=None) -> tuple[float, float, float]:
    """O27-native (run_per_extra_base, run_per_sb, run_per_cs), derived from the
    league's OWN run-expectancy surface instead of imported MLB linear weights.

    O27 already fits wOBA weights, the FIP constant and _RUN_PER_OUT from live
    data; baserunning was the lone metric still hardcoding MLB constants
    (0.25 / 0.20 / -0.42). Those mis-state O27 badly: in a 27-out, ~27 R/G half a
    runner usually scores regardless of whether he's on 2B or 3B, so an extra
    base barely moves run expectancy — run_per_extra_base lands near 0, which
    correctly *zeroes the noisy extra-base-taken term* rather than inflating it
    250x with MLB's +0.25. (Extra-base advancement isn't speed-modelled in the
    engine yet — a Phase-2 gap — so valuing it at ~0 also stops that noise from
    polluting the metric.) Each value is a frequency-weighted average of the
    actual RE delta for that event over every base/out state the league reached.

    Falls back to `_BSR_MLB_FALLBACK` if no games are in scope.
    """
    rows = db.fetchall(
        "SELECT game_id, team_id, ab_seq, swing_idx, outs_before, bases_before, runs_scored "
        "FROM game_pa_log WHERE phase = 0" + _team_in(team_ids) +
        " ORDER BY game_id, team_id, ab_seq, swing_idx")
    if not rows:
        return _BSR_MLB_FALLBACK
    # Fine (per-exact-out) RE surface via a backwards tail-sum per half. Unlike
    # _re_lookup this is NOT bucketed — SB/CS deltas need single-out resolution.
    halves: dict = defaultdict(list)
    for r in rows:
        halves[(r["game_id"], r["team_id"])].append(r)
    rsum: dict = defaultdict(float); rcnt: dict = defaultdict(int)
    for evs in halves.values():
        future = 0
        for r in reversed(evs):
            k = (r["bases_before"], r["outs_before"])
            rsum[k] += future; rcnt[k] += 1
            future += (r["runs_scored"] or 0)
    RE = {k: rsum[k] / rcnt[k] for k in rcnt}

    def _re(mask, outs):
        return 0.0 if outs >= 27 else RE.get((mask, outs), 0.0)

    # Bases are a 3-bit mask: bit0 = 1B, bit1 = 2B, bit2 = 3B.
    sb_n = sb_d = cs_n = cs_d = xb_n = xb_d = 0.0
    for (mask, outs), n in rcnt.items():
        if outs >= 27:
            continue
        # Stolen base 1B→2B (1B occupied, 2B open), same out count.
        if (mask & 1) and not (mask & 2):
            sb_n += n * (_re((mask & ~1) | 2, outs) - _re(mask, outs)); sb_d += n
        # Caught stealing from 1B — runner erased AND an out added.
        if mask & 1:
            cs_n += n * (_re(mask & ~1, outs + 1) - _re(mask, outs)); cs_d += n
        # One extra base of advancement (1B→2B or 2B→3B with the next bag open).
        for b in (0, 1):
            if (mask & (1 << b)) and not (mask & (1 << (b + 1))):
                xb_n += n * (_re((mask & ~(1 << b)) | (1 << (b + 1)), outs) - _re(mask, outs))
                xb_d += n
    xb = xb_n / xb_d if xb_d else _BSR_MLB_FALLBACK[0]
    sb = sb_n / sb_d if sb_d else _BSR_MLB_FALLBACK[1]
    cs = cs_n / cs_d if cs_d else _BSR_MLB_FALLBACK[2]
    return (xb, sb, cs)


def build_baserunning_value(min_op: int = 10, team_ids=None) -> dict:
    """Extra-Bases-Taken% (advances / opportunities, from the runner-advance
    tracking) and an O27-native baserunning run value combining XBT, steals and
    caught-stealings. Run values come from the league's own run-expectancy
    surface (`_baserunning_run_values`), not MLB linear weights — which makes the
    extra-base term ~0 (correctly, for O27) and the steal terms positive-signal."""
    run_xb, run_sb, run_cs = _baserunning_run_values(team_ids)
    rows = db.fetchall(
        """SELECT player_id,
                  COALESCE(SUM(adv_op_1b),0)+COALESCE(SUM(adv_op_2b),0)+COALESCE(SUM(adv_op_3b),0) AS op,
                  COALESCE(SUM(adv_adv_1b),0)+COALESCE(SUM(adv_adv_2b),0)+COALESCE(SUM(adv_adv_3b),0) AS adv,
                  COALESCE(SUM(sb),0) AS sb, COALESCE(SUM(cs),0) AS cs
           FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) WHERE phase = 0""" + _team_in(team_ids, "team_id") +
        " GROUP BY player_id")
    valid = [r for r in rows if (r["op"] or 0) >= min_op]
    lg_xbt = (sum(r["adv"] for r in valid) / sum(r["op"] for r in valid)) if valid and sum(r["op"] for r in valid) else 0.0
    names = _names([r["player_id"] for r in valid])
    leaders = []
    for r in valid:
        op = r["op"] or 0; adv = r["adv"] or 0
        xbt = adv / op if op else 0.0
        # Run value, O27-native: extra bases above league rate × run_xb (≈0 in
        # O27 — see _baserunning_run_values) + steals × run_sb + caught × run_cs.
        bsr = (adv - lg_xbt * op) * run_xb + (r["sb"] or 0) * run_sb + (r["cs"] or 0) * run_cs
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
           FROM (SELECT * FROM game_pitcher_stats WHERE COALESCE(is_playoff,0) = 0) WHERE phase = 0""" + _team_in(team_ids, "team_id") +
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
    """Net run value of Second-Chance ABs. Each stay's RE24 advancement value is
    aggregated per batter, AND — when a 2C at-bat ends in a batter-out (the
    hitter advanced runners via a stay, then struck out or made an out on a
    later segment) — that terminal out's (negative) RE24 value is charged back
    to the 2C decision, "the same as a runner being put out." So 2C Runs measures
    whether a hitter's stays net positive AFTER the out risk, not just the
    upside. The league mean is the break-even bar.

    (This is the RE24-native companion to the wOBA strand penalty in the batter
    aggregator — both now count the downside of a stay that ends in an out, so
    the Savant 2C surface and wOBA agree on what a good 2C hitter is.)"""
    HIT_TYPES = frozenset(("single", "infield_single", "double", "triple", "hr", "home_run"))
    rows = _event_rows(team_ids)
    by_ab = defaultdict(list)
    for r in rows:
        by_ab[(r["game_id"], r["team_id"], r["ab_seq"])].append(r)

    per = defaultdict(lambda: {"n": 0, "rv": 0.0, "credited": 0, "strand_outs": 0})
    tot_rv = 0.0; tot_n = 0
    for evs in by_ab.values():
        stay_evs = [e for e in evs if e["was_stay"]]
        if not stay_evs:
            continue
        bid = stay_evs[0]["batter_id"]
        p = per[bid]
        # Upside: each stay's advancement RE24 value.
        for e in stay_evs:
            p["n"] += 1; p["rv"] += e["rv"]; p["credited"] += (e["stay_credited"] or 0)
            tot_rv += e["rv"]; tot_n += 1
        # Downside: a 2C AB that ends in a batter-out charges that out's RE24
        # cost to the 2C decision. Terminal = last segment; batter-out = the
        # batter wasn't credited a hit and an out was recorded on it (covers
        # ground/fly/line outs AND strikeouts).
        terminal = max(evs, key=lambda x: x["swing_idx"])
        if (not terminal["was_stay"]
                and terminal["hit_type"] not in HIT_TYPES
                and (terminal["outs_after"] or 0) > (terminal["outs_before"] or 0)):
            p["rv"] += terminal["rv"]; p["strand_outs"] += 1
            tot_rv += terminal["rv"]
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
            "strand_outs": d["strand_outs"],
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
    """Outs Above Average + Fielding Run Value.

    Catch probability per ball is the league out-rate for its
    **(zone, EV, LA)** bin — the share of comparable-difficulty balls *in that
    fielder's zone* that became outs. Conditioning the expectation on the zone
    (not just EV/LA) is what keeps the metric position-neutral: an outfielder is
    measured against other outfielders' catch rate on that ball, not against an
    EV/LA pool whose out-rate is inflated by easy infield outs. Without it the
    metric was systematically punishing OF regulars (who absorb every extra-base
    hit in their zone) and rewarding IF — a calibration bug, not real defense.

    Attribution is exact on outs (the engine-credited fielder_id, PO-consistent)
    and falls back to trajectory-zone → positional regular for balls that fell in.
    Home runs are excluded entirely — a ball over the fence is not a fielding
    chance. OAA = Σ(out − expected_out); FRV = OAA × run/out. `exact_pct` reports
    the share of chances exactly attributed."""
    rows = db.fetchall(
        """SELECT team_id, game_id, hit_type, exit_velocity, launch_angle, spray_angle, fielder_id
           FROM game_pa_log WHERE phase = 0 AND exit_velocity IS NOT NULL""" + _team_in(team_ids))

    # Canonical position per player — used to give an out's credited fielder the
    # same zone vocabulary the trajectory heuristic uses for hits.
    ppos = {p["id"]: (p["position"] or "").strip()
            for p in db.fetchall("SELECT id, position FROM players")}

    # Game → fielding team for each batting team.
    games = {g["id"]: (g["home_team_id"], g["away_team_id"])
             for g in db.fetchall("SELECT id, home_team_id, away_team_id FROM games WHERE played = 1")}

    # Per (fielding_team, position) regular = most-used player at that position.
    appers = db.fetchall(
        """SELECT b.team_id, b.player_id, p.position, COUNT(*) AS g
           FROM (SELECT * FROM game_batter_stats WHERE COALESCE(is_playoff,0) = 0) b JOIN players p ON p.id = b.player_id
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

    def _attribute(r, is_out):
        """(zone, fielder_id) for one ball, or (None, None) if unattributable.
        Outs use the engine-credited fielder (and that fielder's canonical
        position as the zone); hits/unattributed-outs fall back to the
        trajectory zone → fielding team's regular at that position."""
        if is_out and r["fielder_id"] is not None:
            return ppos.get(r["fielder_id"]) or None, r["fielder_id"]
        zone = _zone_from_trajectory(r["launch_angle"], r["spray_angle"])
        teams = games.get(r["game_id"])
        if not zone or not teams:
            return None, None
        home, away = teams
        field_team = home if r["team_id"] == away else away
        reg = regular.get((field_team, zone))
        if not reg:
            return None, None
        return zone, reg[0]

    # Pass 1 — league out-rate per (zone, EV, LA) bin, plus an (EV, LA) fallback
    # for sparse zone bins. Each ball is parsed once and cached for pass 2.
    zbin_out = defaultdict(int); zbin_n = defaultdict(int)
    gbin_out = defaultdict(int); gbin_n = defaultdict(int)
    parsed = []          # (fielder_id, zone, ev_bin, la_bin, is_out, exact)
    n_exact = 0; n_heur = 0
    for r in rows:
        if r["hit_type"] in ("hr", "home_run"):
            continue     # not a fielding chance — left the park
        is_out = r["hit_type"] in _OUT_TYPES
        zone, fielder = _attribute(r, is_out)
        if fielder is None or zone is None:
            continue
        exact = is_out and r["fielder_id"] is not None
        if exact: n_exact += 1
        else:     n_heur += 1
        ev = _ev_bin(r["exit_velocity"]); la = _la_bin(r["launch_angle"])
        zbin_n[(zone, ev, la)] += 1; gbin_n[(ev, la)] += 1
        if is_out:
            zbin_out[(zone, ev, la)] += 1; gbin_out[(ev, la)] += 1
        parsed.append((fielder, zone, ev, la, is_out))
    zrate = {k: zbin_out[k] / zbin_n[k] for k in zbin_n}
    grate = {k: gbin_out[k] / gbin_n[k] for k in gbin_n}

    # Pass 2 — OAA against the zone-conditional expectation.
    oaa = defaultdict(float); chances = defaultdict(int)
    for fielder, zone, ev, la, is_out in parsed:
        exp = zrate.get((zone, ev, la))
        if exp is None:
            exp = grate.get((ev, la), 0.0)
        oaa[fielder] += (1.0 if is_out else 0.0) - exp
        chances[fielder] += 1

    names = _names(list(oaa.keys()))
    leaders = []
    for pid, v in oaa.items():
        n = chances[pid]
        if n < min_chances:
            continue
        # Reliability regression. O27 resolves contact mostly from EV/LA physics
        # with only a team-level range shift + sparse individual gems, so raw
        # single-season OAA is dominated by batted-ball variance, not fielder
        # skill (measured corr to defense rating ≈ 0.1). Field Run Value — the
        # run figure that flows into WAR — is therefore the OAA shrunk toward the
        # league-average (0) by reliability = chances/(chances+K). This is the
        # standard defensive-metric damping; without it WAR would inherit ±2 wins
        # of fielding noise. `oaa` is kept raw (the observed count); `frv` is the
        # regressed, WAR-bound run value, so both surfaces and WAR agree on it.
        reliability = n / (n + _FIELDING_REGRESSION_K)
        frv = v * _RUN_PER_OUT * reliability
        leaders.append({
            "player_id": pid,
            "player_name": (names.get(pid) or {}).get("name", f"#{pid}"),
            "team_abbrev": (names.get(pid) or {}).get("team_abbrev", ""),
            "chances": n,
            "oaa": round(v, 1),
            "reliability": round(reliability, 3),
            "frv": round(frv, 1),
        })
    leaders.sort(key=lambda x: -x["frv"])
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
