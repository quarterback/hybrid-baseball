"""
o27.almanac.compute — Derived stat math.

Single source of truth for every rate, sabermetric, and league-relative
stat the almanac renders. Mirrors the formulas documented in
docs/stats-reference.md so the almanac can never silently drift from the
canonical definitions.

Public entry: `compute_views(dataset)` takes the loader output dict and
returns a `Views` namespace with every dataset the renderer needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Constants — single place to bump if the league recalibrates.
# ---------------------------------------------------------------------------

# wOBA linear weights (O27-tuned, from docs/stats-reference.md).
WOBA_W_BB  = 0.72
WOBA_W_HBP = 0.74
WOBA_W_1B  = 0.95
WOBA_W_2B  = 1.30
WOBA_W_3B  = 1.70
WOBA_W_HR  = 2.05

# Workload qualifiers for rate leaderboards.
MIN_PA_QUALIFIED   = 50    # batter-side, PA-denominated leaderboards
MIN_OUTS_QUALIFIED = 60    # pitcher-side, ERA/WHIP leaderboards (20 IP)

ARC_W_1 = 0.85
ARC_W_2 = 1.00
ARC_W_3 = 1.20


# ---------------------------------------------------------------------------
# Output container
# ---------------------------------------------------------------------------

@dataclass
class Views:
    """Everything the renderer needs, computed once."""
    meta:             dict[str, Any] = field(default_factory=dict)
    teams:            list[dict]     = field(default_factory=list)
    players:          list[dict]     = field(default_factory=list)
    games:            list[dict]     = field(default_factory=list)

    standings:        list[dict]     = field(default_factory=list)
    schedule:         list[dict]     = field(default_factory=list)

    batting_season:   list[dict]     = field(default_factory=list)
    pitching_season:  list[dict]     = field(default_factory=list)

    batting_by_team:  dict[str, list[dict]] = field(default_factory=dict)
    pitching_by_team: dict[str, list[dict]] = field(default_factory=dict)

    batting_by_player:  dict[int, dict] = field(default_factory=dict)
    pitching_by_player: dict[int, dict] = field(default_factory=dict)
    game_logs_batter:   dict[int, list[dict]] = field(default_factory=dict)
    game_logs_pitcher:  dict[int, list[dict]] = field(default_factory=dict)

    team_totals_bat:  dict[str, dict] = field(default_factory=dict)
    team_totals_pit:  dict[str, dict] = field(default_factory=dict)

    league_totals:    dict[str, float] = field(default_factory=dict)
    awards:           list[dict]      = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def compute_views(dataset: dict[str, Any]) -> Views:
    v = Views()
    v.meta    = dict(dataset.get("meta") or {})
    v.teams   = list(dataset.get("teams") or [])
    v.players = list(dataset.get("players") or [])
    v.games   = list(dataset.get("games") or [])
    v.awards  = list(dataset.get("awards") or [])

    teams_by_id  = {t["id"]: t for t in v.teams}
    players_by_id = {p["id"]: p for p in v.players}

    # Standings + schedule rebuilt from played games.
    v.standings = _build_standings(v.games, teams_by_id)
    v.schedule  = _build_schedule(v.games, teams_by_id)

    # Aggregate per-player season totals.
    bat_agg = _aggregate_batters(dataset.get("batting") or [],
                                 players_by_id, teams_by_id)
    pit_agg = _aggregate_pitchers(dataset.get("pitching") or [],
                                  players_by_id, teams_by_id)

    # League denominators (for OPS+, wOBA+, ERA+, FIP+).
    v.league_totals = _league_totals(bat_agg, pit_agg)

    _augment_batters(bat_agg,  v.league_totals)
    _augment_pitchers(pit_agg, v.league_totals)

    v.batting_season  = sorted(bat_agg,  key=lambda r: -r["pa"])
    v.pitching_season = sorted(pit_agg, key=lambda r: -r["outs_recorded"])

    v.batting_by_player  = {r["player_id"]: r for r in bat_agg}
    v.pitching_by_player = {r["player_id"]: r for r in pit_agg}

    v.batting_by_team  = _group_by_team(bat_agg)
    v.pitching_by_team = _group_by_team(pit_agg, sort_key=lambda r: -r["outs_recorded"])

    v.team_totals_bat = _team_totals(bat_agg, v.standings, teams_by_id, kind="batting")
    v.team_totals_pit = _team_totals(pit_agg, v.standings, teams_by_id, kind="pitching")

    # Per-player game logs (newest-first).
    v.game_logs_batter  = _build_game_logs(
        dataset.get("batting")  or [], v.games, teams_by_id, kind="batting"
    )
    v.game_logs_pitcher = _build_game_logs(
        dataset.get("pitching") or [], v.games, teams_by_id, kind="pitching"
    )

    return v


# ---------------------------------------------------------------------------
# Standings + schedule
# ---------------------------------------------------------------------------

def _build_standings(games: list[dict], teams_by_id: dict[int, dict]) -> list[dict]:
    rec: dict[int, dict] = {}
    history: dict[int, list[bool]] = {}

    for g in games:
        h_id, a_id = g["home_team_id"], g["away_team_id"]
        h_score, a_score = g.get("home_score") or 0, g.get("away_score") or 0
        h_won = h_score > a_score

        for tid, won, rf, ra in (
            (h_id, h_won,     h_score, a_score),
            (a_id, not h_won, a_score, h_score),
        ):
            t = teams_by_id.get(tid)
            if not t:
                continue
            r = rec.setdefault(tid, {
                "id": tid,
                "abbrev":   t.get("abbrev", "???"),
                "name":     t.get("name", ""),
                "city":     t.get("city", ""),
                "league":   t.get("league", ""),
                "division": t.get("division", ""),
                "w": 0, "l": 0,
                "rs": 0, "ra": 0,
                "rd": 0, "pct": 0.0, "gp": 0,
                "rpg": 0.0, "rapg": 0.0, "gb": "—",
                "streak": "—", "l10_w": 0, "l10_l": 0,
            })
            r["rs"] += rf
            r["ra"] += ra
            if won:
                r["w"] += 1
            else:
                r["l"] += 1
            history.setdefault(tid, []).append(won)

    rows = list(rec.values())
    for r in rows:
        gp = r["w"] + r["l"]
        r["gp"]  = gp
        r["pct"] = r["w"] / gp if gp else 0.0
        r["rd"]  = r["rs"] - r["ra"]
        r["rpg"]  = (r["rs"] / gp) if gp else 0.0
        r["rapg"] = (r["ra"] / gp) if gp else 0.0
        hist = history.get(r["id"], [])
        last10 = hist[-10:]
        r["l10_w"] = sum(1 for x in last10 if x)
        r["l10_l"] = len(last10) - r["l10_w"]
        streak_n = 0
        if hist:
            cur = hist[-1]
            for x in reversed(hist):
                if x == cur:
                    streak_n += 1
                else:
                    break
            r["streak"] = (f"W{streak_n}" if hist[-1] else f"L{streak_n}")
        else:
            r["streak"] = "—"

    rows.sort(key=lambda x: (x.get("league", ""), x.get("division", ""), -x["pct"], -x["rd"]))

    # Games-back computed within (league, division) groups.
    bucket: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        bucket.setdefault((r["league"], r["division"]), []).append(r)
    for group in bucket.values():
        if not group:
            continue
        lw, ll = group[0]["w"], group[0]["l"]
        for r in group:
            gb = ((lw - r["w"]) + (r["l"] - ll)) / 2.0
            r["gb"] = "—" if gb <= 0 else (str(int(gb)) if gb == int(gb) else f"{gb:.1f}")

    return rows


def _build_schedule(games: list[dict], teams_by_id: dict[int, dict]) -> list[dict]:
    out = []
    for g in games:
        h, a = teams_by_id.get(g["home_team_id"]), teams_by_id.get(g["away_team_id"])
        if not h or not a:
            continue
        out.append({
            "id":           g["id"],
            "date":         g.get("game_date", ""),
            "home_id":      g["home_team_id"],
            "away_id":      g["away_team_id"],
            "home_abbrev":  h["abbrev"],
            "away_abbrev":  a["abbrev"],
            "home_name":    f"{h.get('city','')} {h.get('name','')}".strip(),
            "away_name":    f"{a.get('city','')} {a.get('name','')}".strip(),
            "home_score":   g.get("home_score") or 0,
            "away_score":   g.get("away_score") or 0,
            "winner_id":    g.get("winner_id"),
            "super_inning": bool(g.get("super_inning")),
            "is_playoff":   bool(g.get("is_playoff")),
        })
    return out


# ---------------------------------------------------------------------------
# Batting aggregation
# ---------------------------------------------------------------------------

_BATTING_SUM_FIELDS = [
    "pa", "ab", "runs", "hits", "doubles", "triples", "hr", "rbi",
    "bb", "k", "stays", "outs_recorded", "hbp", "sb", "cs", "fo",
    "multi_hit_abs", "stay_rbi", "stay_hits", "roe", "po", "a", "e",
    "gidp", "gitp",
    "rad_1b", "rad_2b", "rad_3b",
    "c2_op_1b", "c2_adv_1b", "c2_op_2b", "c2_adv_2b",
    "c2_op_3b", "c2_adv_3b",
]


def _aggregate_batters(
    rows: list[dict],
    players_by_id: dict[int, dict],
    teams_by_id: dict[int, dict],
) -> list[dict]:
    agg: dict[int, dict] = {}
    games_seen: dict[int, set[int]] = {}

    for r in rows:
        pid = r["player_id"]
        slot = agg.setdefault(pid, _empty_batter_slot(pid, players_by_id, teams_by_id, r))
        for f in _BATTING_SUM_FIELDS:
            slot[f] = (slot.get(f) or 0) + (r.get(f) or 0)
        if (r.get("pa") or 0) > 0:
            games_seen.setdefault(pid, set()).add(r["game_id"])

    out = []
    for pid, slot in agg.items():
        slot["g"] = len(games_seen.get(pid, set()))
        out.append(slot)
    return out


def _empty_batter_slot(pid, players_by_id, teams_by_id, sample_row) -> dict:
    p = players_by_id.get(pid) or {}
    t = teams_by_id.get(sample_row.get("team_id")) or {}
    return {
        "player_id": pid,
        "name":      p.get("name", "?"),
        "slug":      _slugify(p.get("name", str(pid))),
        "position":  p.get("position", ""),
        "is_pitcher": bool(p.get("is_pitcher")),
        "is_joker":   bool(p.get("is_joker")),
        "archetype":  p.get("archetype", ""),
        "bats":       p.get("bats", "R"),
        "throws":     p.get("throws", "R"),
        "country":    p.get("country", ""),
        "age":        p.get("age"),
        "team_id":    sample_row.get("team_id"),
        "team":       t.get("abbrev", "?"),
        "team_name":  f"{t.get('city','')} {t.get('name','')}".strip(),
    }


def _augment_batters(rows: list[dict], league: dict[str, float]) -> None:
    lg_ops  = league.get("ops", 0.0) or 0.0
    lg_woba = league.get("woba", 0.0) or 0.0

    for r in rows:
        pa  = r.get("pa")  or 0
        ab  = r.get("ab")  or 0
        h   = r.get("hits") or 0
        bb  = r.get("bb")  or 0
        hbp = r.get("hbp") or 0
        d   = r.get("doubles") or 0
        t   = r.get("triples") or 0
        hr  = r.get("hr") or 0
        k   = r.get("k")  or 0
        sty = r.get("stays") or 0
        sb  = r.get("sb") or 0
        cs  = r.get("cs") or 0
        fo  = r.get("fo") or 0
        stay_h    = r.get("stay_hits") or 0
        stay_rbi  = r.get("stay_rbi")  or 0
        mhab      = r.get("multi_hit_abs") or 0

        singles = max(0, h - d - t - hr)
        tb      = singles + 2 * d + 3 * t + 4 * hr

        r["singles"] = singles
        r["tb"]      = tb

        r["pavg"]  = (h / pa) if pa else 0.0
        r["avg"]   = r["pavg"]
        r["bavg"]  = (h / ab) if ab else 0.0
        r["stay_diff"] = r["bavg"] - r["pavg"]
        r["obp"]   = ((h + bb + hbp) / pa) if pa else 0.0
        r["slg"]   = (tb / pa) if pa else 0.0
        r["ops"]   = r["obp"] + r["slg"]
        r["iso"]   = r["slg"] - r["pavg"]
        bip_denom  = pa - k - bb - hbp - hr
        r["babip"] = ((h - hr) / bip_denom) if bip_denom > 0 else 0.0

        r["k_pct"]  = (k / pa) if pa else 0.0
        r["bb_pct"] = (bb / pa) if pa else 0.0
        r["hr_pct"] = (hr / pa) if pa else 0.0
        r["bb_k"]   = (bb / k) if k else float(bb)

        sb_att = sb + cs
        r["sb_pct"] = (sb / sb_att) if sb_att else 0.0

        r["stay_pct"]      = (sty / pa) if pa else 0.0
        r["stay_rbi_pct"]  = (stay_rbi / r["rbi"]) if r.get("rbi") else 0.0
        r["stay_conv"]     = (stay_h / sty) if sty else 0.0
        r["fo_pct"]        = (fo / pa) if pa else 0.0
        r["mhab_pct"]      = (mhab / ab) if ab else 0.0

        r["woba"] = ((
            WOBA_W_BB  * bb
            + WOBA_W_HBP * hbp
            + WOBA_W_1B  * singles
            + WOBA_W_2B  * d
            + WOBA_W_3B  * t
            + WOBA_W_HR  * hr
        ) / pa) if pa else 0.0

        r["ops_plus"]  = round(100.0 * r["ops"]  / lg_ops)  if lg_ops  else 100
        r["woba_plus"] = round(100.0 * r["woba"] / lg_woba) if lg_woba else 100

        rad = (r.get("rad_1b") or 0) + (r.get("rad_2b") or 0) + (r.get("rad_3b") or 0)
        r["rad"] = rad
        r["rad_pa"] = (rad / pa) if pa else 0.0

        r["qualified"] = pa >= MIN_PA_QUALIFIED


# ---------------------------------------------------------------------------
# Pitching aggregation
# ---------------------------------------------------------------------------

_PITCHING_SUM_FIELDS = [
    "batters_faced", "outs_recorded", "hits_allowed", "runs_allowed", "er",
    "bb", "k", "hr_allowed", "pitches", "hbp_allowed", "unearned_runs",
    "sb_allowed", "cs_caught", "fo_induced",
    "er_arc1", "er_arc2", "er_arc3",
    "k_arc1",  "k_arc2",  "k_arc3",
    "fo_arc1", "fo_arc2", "fo_arc3",
    "bf_arc1", "bf_arc2", "bf_arc3",
    "singles_allowed", "doubles_allowed", "triples_allowed",
]


def _aggregate_pitchers(
    rows: list[dict],
    players_by_id: dict[int, dict],
    teams_by_id: dict[int, dict],
) -> list[dict]:
    agg: dict[int, dict] = {}
    games_seen: dict[int, set[int]] = {}
    starts: dict[int, int] = {}
    wins: dict[int, int] = {}

    for r in rows:
        pid = r["player_id"]
        slot = agg.setdefault(pid, _empty_pitcher_slot(pid, players_by_id, teams_by_id, r))
        for f in _PITCHING_SUM_FIELDS:
            slot[f] = (slot.get(f) or 0) + (r.get(f) or 0)
        games_seen.setdefault(pid, set()).add(r["game_id"])
        if r.get("is_starter"):
            starts[pid] = starts.get(pid, 0) + 1

    out = []
    for pid, slot in agg.items():
        slot["g"]  = len(games_seen.get(pid, set()))
        slot["gs"] = starts.get(pid, 0)
        out.append(slot)
    return out


def _empty_pitcher_slot(pid, players_by_id, teams_by_id, sample_row) -> dict:
    p = players_by_id.get(pid) or {}
    t = teams_by_id.get(sample_row.get("team_id")) or {}
    return {
        "player_id": pid,
        "name":      p.get("name", "?"),
        "slug":      _slugify(p.get("name", str(pid))),
        "position":  p.get("position", "P"),
        "is_pitcher": True,
        "is_joker":   bool(p.get("is_joker")),
        "throws":    p.get("throws", "R"),
        "country":   p.get("country", ""),
        "age":       p.get("age"),
        "team_id":   sample_row.get("team_id"),
        "team":      t.get("abbrev", "?"),
        "team_name": f"{t.get('city','')} {t.get('name','')}".strip(),
    }


def _augment_pitchers(rows: list[dict], league: dict[str, float]) -> None:
    lg_era = league.get("era", 0.0)
    lg_fip = league.get("fip", 0.0)

    for r in rows:
        outs = r.get("outs_recorded") or 0
        bf   = r.get("batters_faced") or 0
        h    = r.get("hits_allowed")  or 0
        er   = r.get("er") or r.get("runs_allowed") or 0
        ra   = r.get("runs_allowed") or 0
        bb   = r.get("bb") or 0
        k    = r.get("k")  or 0
        hr   = r.get("hr_allowed") or 0
        hbp  = r.get("hbp_allowed") or 0
        g    = r.get("g")  or 0

        ip = outs / 3.0
        r["ip"] = ip
        r["ip_disp"] = _format_ip(outs)
        r["era"]  = (er * 27.0 / outs) if outs else 0.0
        r["whip"] = ((bb + h) / ip)    if ip   else 0.0
        r["k9"]   = (k  * 9.0  / ip)   if ip   else 0.0
        r["bb9"]  = (bb * 9.0  / ip)   if ip   else 0.0
        r["hr9"]  = (hr * 9.0  / ip)   if ip   else 0.0
        r["fip"]  = ((13 * hr + 3 * (bb + hbp) - 2 * k) / ip + 3.10) if ip else 0.0
        r["k_pct"]  = (k  / bf) if bf else 0.0
        r["bb_pct"] = (bb / bf) if bf else 0.0
        r["k_bb"]   = (k / bb)  if bb else float(k)
        r["oavg"]   = (h / (bf - bb - hbp)) if (bf - bb - hbp) > 0 else 0.0

        r["os_pct"] = (outs / (g * 27.0)) if g else 0.0

        # wERA — arc-weighted earned-run rate.
        bf1, bf2, bf3 = r.get("bf_arc1") or 0, r.get("bf_arc2") or 0, r.get("bf_arc3") or 0
        er1, er2, er3 = r.get("er_arc1") or 0, r.get("er_arc2") or 0, r.get("er_arc3") or 0
        weighted_er  = ARC_W_1 * er1 + ARC_W_2 * er2 + ARC_W_3 * er3
        weighted_bf  = ARC_W_1 * bf1 + ARC_W_2 * bf2 + ARC_W_3 * bf3
        # Convert weighted ER to a per-9-out rate, mirroring ERA's per-27.
        # Use total outs as the denominator's "outs share" to keep units sane.
        r["wera"] = (weighted_er * 27.0 / outs) * (
            (bf / weighted_bf) if weighted_bf else 1.0
        ) if outs and bf else 0.0

        # Decay = K-rate drop from arc 1 → arc 3 (positive = fades late).
        k1_rate = (er1 and 0) or ((r.get("k_arc1") or 0) / bf1 if bf1 else 0.0)
        k3_rate = ((r.get("k_arc3") or 0) / bf3) if bf3 else 0.0
        r["decay"] = k1_rate - k3_rate

        r["era_plus"] = round(100.0 * lg_era / r["era"]) if (r["era"] and lg_era) else 100
        r["fip_plus"] = round(100.0 * lg_fip / r["fip"]) if (r["fip"] and lg_fip) else 100

        r["qualified"] = outs >= MIN_OUTS_QUALIFIED


def _format_ip(outs: int) -> str:
    full, rem = divmod(outs, 3)
    return f"{full}.{rem}"


# ---------------------------------------------------------------------------
# League totals
# ---------------------------------------------------------------------------

def _league_totals(bat: list[dict], pit: list[dict]) -> dict[str, float]:
    pa = sum(r.get("pa") or 0 for r in bat)
    ab = sum(r.get("ab") or 0 for r in bat)
    h  = sum(r.get("hits") or 0 for r in bat)
    d  = sum(r.get("doubles") or 0 for r in bat)
    t  = sum(r.get("triples") or 0 for r in bat)
    hr = sum(r.get("hr") or 0 for r in bat)
    bb = sum(r.get("bb") or 0 for r in bat)
    hbp = sum(r.get("hbp") or 0 for r in bat)
    k  = sum(r.get("k") or 0 for r in bat)
    sty = sum(r.get("stays") or 0 for r in bat)
    runs = sum(r.get("runs") or 0 for r in bat)
    singles = max(0, h - d - t - hr)
    tb = singles + 2 * d + 3 * t + 4 * hr

    out = {
        "pa": pa, "ab": ab, "h": h, "hr": hr, "bb": bb, "k": k, "hbp": hbp,
        "doubles": d, "triples": t, "singles": singles, "tb": tb,
        "stays": sty, "runs": runs,
        "pavg": (h / pa) if pa else 0.0,
        "obp":  ((h + bb + hbp) / pa) if pa else 0.0,
        "slg":  (tb / pa) if pa else 0.0,
        "k_pct":  (k / pa)  if pa else 0.0,
        "bb_pct": (bb / pa) if pa else 0.0,
    }
    out["ops"] = out["obp"] + out["slg"]
    out["woba"] = ((
        WOBA_W_BB  * bb
        + WOBA_W_HBP * hbp
        + WOBA_W_1B  * singles
        + WOBA_W_2B  * d
        + WOBA_W_3B  * t
        + WOBA_W_HR  * hr
    ) / pa) if pa else 0.0

    p_outs = sum(r.get("outs_recorded") or 0 for r in pit)
    p_er   = sum(r.get("er") or 0           for r in pit)
    p_bb   = sum(r.get("bb") or 0           for r in pit)
    p_k    = sum(r.get("k") or 0            for r in pit)
    p_hr   = sum(r.get("hr_allowed") or 0   for r in pit)
    p_h    = sum(r.get("hits_allowed") or 0 for r in pit)
    p_hbp  = sum(r.get("hbp_allowed") or 0  for r in pit)
    p_bf   = sum(r.get("batters_faced") or 0 for r in pit)
    p_ip = p_outs / 3.0

    out["era"]  = (p_er * 27.0 / p_outs) if p_outs else 0.0
    out["whip"] = ((p_bb + p_h) / p_ip)  if p_ip   else 0.0
    out["fip"]  = ((13 * p_hr + 3 * (p_bb + p_hbp) - 2 * p_k) / p_ip + 3.10) if p_ip else 0.0
    out["p_k_pct"]  = (p_k  / p_bf) if p_bf else 0.0
    out["p_bb_pct"] = (p_bb / p_bf) if p_bf else 0.0

    return out


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------

def _group_by_team(rows: list[dict], sort_key=None) -> dict[str, list[dict]]:
    if sort_key is None:
        sort_key = lambda r: -r.get("pa", 0)
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["team"], []).append(r)
    for v in out.values():
        v.sort(key=sort_key)
    return out


def _team_totals(rows, standings, teams_by_id, *, kind) -> dict[str, dict]:
    out: dict[str, dict] = {}
    abb_to_record = {r["abbrev"]: r for r in standings}

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["team"], []).append(r)

    for abb, players in grouped.items():
        if kind == "batting":
            pa = sum(r["pa"]  for r in players)
            ab = sum(r["ab"]  for r in players)
            h  = sum(r["hits"] for r in players)
            d  = sum(r["doubles"] for r in players)
            t  = sum(r["triples"] for r in players)
            hr = sum(r["hr"] for r in players)
            bb = sum(r["bb"] for r in players)
            k  = sum(r["k"]  for r in players)
            hbp = sum(r["hbp"] for r in players)
            sty = sum(r["stays"] for r in players)
            runs = sum(r["runs"] for r in players)
            singles = max(0, h - d - t - hr)
            tb = singles + 2 * d + 3 * t + 4 * hr
            slot = {
                "pa": pa, "ab": ab, "h": h, "hr": hr, "bb": bb, "k": k,
                "hbp": hbp, "doubles": d, "triples": t, "singles": singles,
                "tb": tb, "stays": sty, "runs": runs,
                "pavg": (h / pa) if pa else 0.0,
                "obp":  ((h + bb + hbp) / pa) if pa else 0.0,
                "slg":  (tb / pa) if pa else 0.0,
            }
            slot["ops"] = slot["obp"] + slot["slg"]
        else:
            outs = sum(r["outs_recorded"] for r in players)
            er   = sum(r["er"] for r in players)
            bb   = sum(r["bb"] for r in players)
            k    = sum(r["k"]  for r in players)
            h    = sum(r["hits_allowed"] for r in players)
            hr   = sum(r["hr_allowed"]   for r in players)
            ip = outs / 3.0
            slot = {
                "outs": outs, "ip": ip, "ip_disp": _format_ip(outs),
                "er": er, "bb": bb, "k": k, "h": h, "hr": hr,
                "era": (er * 27.0 / outs) if outs else 0.0,
                "whip": ((bb + h) / ip)    if ip   else 0.0,
                "k9":  (k * 9.0 / ip)      if ip   else 0.0,
                "bb9": (bb * 9.0 / ip)     if ip   else 0.0,
            }
        rec = abb_to_record.get(abb, {})
        slot["abbrev"] = abb
        slot["w"]   = rec.get("w", 0)
        slot["l"]   = rec.get("l", 0)
        slot["pct"] = rec.get("pct", 0.0)
        slot["gp"]  = rec.get("gp", 0)
        out[abb] = slot
    return out


# ---------------------------------------------------------------------------
# Per-player game logs (newest-first by game date+id)
# ---------------------------------------------------------------------------

def _build_game_logs(
    rows: list[dict],
    games: list[dict],
    teams_by_id: dict[int, dict],
    *,
    kind: str,
) -> dict[int, list[dict]]:
    game_index = {g["id"]: g for g in games}
    out: dict[int, list[dict]] = {}
    for r in rows:
        g = game_index.get(r["game_id"])
        if not g:
            continue
        opp_id = g["away_team_id"] if r["team_id"] == g["home_team_id"] else g["home_team_id"]
        opp = teams_by_id.get(opp_id, {})
        ha = "vs" if r["team_id"] == g["home_team_id"] else "@"
        entry = {
            "game_id": g["id"],
            "date":    g.get("game_date", ""),
            "ha":      ha,
            "opp":     opp.get("abbrev", "?"),
            **{k: r.get(k) for k in r.keys() if k not in ("id",)},
        }
        out.setdefault(r["player_id"], []).append(entry)
    # newest first
    for log in out.values():
        log.sort(key=lambda x: (x.get("date") or "", x.get("game_id") or 0), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    out = []
    for ch in (name or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    s = "".join(out).strip("_")
    return s or "player"


# ---------------------------------------------------------------------------
# Percentile shading helper used by the renderer
# ---------------------------------------------------------------------------

def percentile_ranks(values: list[float]) -> list[float]:
    """Return 0..1 percentile rank for each value (higher = better).

    Ties get the average rank. Empty / single-value inputs return zeros.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    sorted_pairs = sorted(((v, i) for i, v in enumerate(values)), key=lambda x: x[0])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_pairs[j + 1][0] == sorted_pairs[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0
        pct = avg_rank / (n - 1)
        for k in range(i, j + 1):
            out[sorted_pairs[k][1]] = pct
        i = j + 1
    return out
