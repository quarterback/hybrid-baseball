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


def team_label(t: Any) -> str:
    """Full display label for a team, without duplicating the city.

    Real franchises store the nickname in `name` with `city` separate
    ("New York" + "Yankees" -> "New York Yankees"). Generated universe clubs
    embed the city in `name` ("Toronto" + "Toronto Forestry"); prepending the
    city again would double it. Only prepend when the name doesn't already
    lead with the city.
    """
    t = t or {}
    city = (t.get("city") or "").strip()
    name = (t.get("name") or "").strip()
    if city and name and not name.startswith(city):
        return f"{city} {name}".strip()
    return name or city



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

# Game Score coefficients (mirrors o27v2/web/app.py:_pitcher_game_score).
GSC_BASE   = 50.0
GSC_OUT    = 1.0
GSC_K_BONUS_OVER_3 = 2.0   # +2 per K beyond 3 (MLB convention preserved)
GSC_FO_BONUS       = 1.0   # O27 foul-out bonus
GSC_H_COST         = 2.0
GSC_HR_OVER_H_COST = 2.0   # additional cost for HR vs generic hit
GSC_BB_COST        = 1.0
GSC_ER_COST        = 4.0
GSC_UER_COST       = 2.0

# Replacement-level baselines (anchored to league).
REPL_WOBA_PCT = 0.85   # 85% of league wOBA
REPL_ERA_PCT  = 1.20   # 120% of league ERA

# Runs-per-win (Pythagorean-derived; updated at compute time once
# league_totals is known).
DEFAULT_RUNS_PER_WIN = 10.0

# Defensive position-group DRS ranges (rough, mirrors web app).
POS_DRS_RANGE = {
    "C": 18, "SS": 22, "2B": 18, "CF": 22, "3B": 16,
    "RF": 14, "LF": 12, "1B": 10, "DH": 0, "UT": 12, "P": 0,
}


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
    fielding_season:  list[dict]     = field(default_factory=list)

    batting_by_team:  dict[str, list[dict]] = field(default_factory=dict)
    pitching_by_team: dict[str, list[dict]] = field(default_factory=dict)

    batting_by_player:  dict[int, dict]       = field(default_factory=dict)
    pitching_by_player: dict[int, dict]       = field(default_factory=dict)
    fielding_by_player: dict[int, dict]       = field(default_factory=dict)
    game_logs_batter:   dict[int, list[dict]] = field(default_factory=dict)
    game_logs_pitcher:  dict[int, list[dict]] = field(default_factory=dict)

    team_totals_bat:  dict[str, dict] = field(default_factory=dict)
    team_totals_pit:  dict[str, dict] = field(default_factory=dict)
    team_pythag:      dict[str, dict] = field(default_factory=dict)
    pythag_summary:   dict[str, float] = field(default_factory=dict)

    league_totals:    dict[str, float] = field(default_factory=dict)
    runs_per_win:     float            = DEFAULT_RUNS_PER_WIN

    awards:           list[dict]      = field(default_factory=list)
    playoff_series:   list[dict]      = field(default_factory=list)
    scoring_events:   list[dict]      = field(default_factory=list)

    monthly_splits:   list[dict]      = field(default_factory=list)
    pa_log:           list[dict]      = field(default_factory=list)
    pa_log_by_game:   dict[int, list[dict]] = field(default_factory=dict)
    scoring_by_game:  dict[int, list[dict]] = field(default_factory=dict)

    # Career (multi-season) leaderboards aggregated from the per-player
    # season-line snapshots. Empty until at least one season is archived.
    career:           dict[str, Any]  = field(default_factory=dict)


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
    v.playoff_series = list(dataset.get("playoff_series") or [])
    v.scoring_events = list(dataset.get("scoring_events") or [])
    v.pa_log         = list(dataset.get("pa_log") or [])

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

    # League denominators + runs-per-win. In a multi-league universe each
    # league is its own run environment, so rate-plus stats (wOBA+, ERA+,
    # OPS+, FIP, VORP) must be measured against the player's OWN league —
    # pooling 84 teams would rank a high-offense league's hitters as average.
    # We keep a universe-wide league_totals for any global display, but
    # augment each player against per-league denominators when >1 league.
    v.league_totals = _league_totals(bat_agg, pit_agg)
    v.runs_per_win  = _runs_per_win(v.league_totals)

    leagues_present = sorted({r.get("league") or "" for r in bat_agg} - {""})
    if len(leagues_present) > 1:
        for lg in leagues_present:
            bsub = [r for r in bat_agg if (r.get("league") or "") == lg]
            psub = [r for r in pit_agg if (r.get("league") or "") == lg]
            lt  = _league_totals(bsub, psub)
            rpw = _runs_per_win(lt)
            _augment_batters(bsub, lt, rpw)
            _augment_pitchers(psub, lt, rpw)
    else:
        _augment_batters(bat_agg,  v.league_totals, v.runs_per_win)
        _augment_pitchers(pit_agg, v.league_totals, v.runs_per_win)

    v.batting_season  = sorted(bat_agg,  key=lambda r: -r["pa"])
    v.pitching_season = sorted(pit_agg, key=lambda r: -r["outs_recorded"])

    v.batting_by_player  = {r["player_id"]: r for r in bat_agg}
    v.pitching_by_player = {r["player_id"]: r for r in pit_agg}

    v.batting_by_team  = _group_by_team(bat_agg)
    v.pitching_by_team = _group_by_team(pit_agg, sort_key=lambda r: -r["outs_recorded"])

    # Fielding aggregation (PO/A/E per defensive appearance).
    v.fielding_season = _aggregate_fielders(
        dataset.get("batting") or [], players_by_id, teams_by_id,
    )
    v.fielding_by_player = {r["player_id"]: r for r in v.fielding_season}

    v.team_totals_bat = _team_totals(bat_agg, v.standings, teams_by_id, kind="batting")
    v.team_totals_pit = _team_totals(pit_agg, v.standings, teams_by_id, kind="pitching")

    # Pythagorean (fitted + MLB-default) per team.
    v.team_pythag, v.pythag_summary = _compute_pythag(v.standings)

    # Per-player game logs (newest-first).
    v.game_logs_batter  = _build_game_logs(
        dataset.get("batting")  or [], v.games, teams_by_id, kind="batting"
    )
    v.game_logs_pitcher = _build_game_logs(
        dataset.get("pitching") or [], v.games, teams_by_id, kind="pitching"
    )
    _augment_pitcher_game_logs(v.game_logs_pitcher)

    # Per-game indices for box-score augmentation.
    for r in v.scoring_events:
        v.scoring_by_game.setdefault(r["game_id"], []).append(r)
    for r in v.pa_log:
        v.pa_log_by_game.setdefault(r["game_id"], []).append(r)

    # Batted-ball profile (GB/LD/FB), times-through-order contact splits, and
    # the repertoire-driven Deception grade — attached onto the season pitcher
    # rows. Derived from the PA-log (balls-in-play) + repertoire.
    _attach_pitcher_battedball_tto(v.pitching_by_player, players_by_id, v.pa_log)

    # Monthly splits.
    v.monthly_splits = _monthly_splits(dataset.get("batting") or [],
                                       dataset.get("pitching") or [],
                                       v.games)

    # Career (multi-season) leaderboards from archived per-player lines.
    v.career = _career_leaderboards(
        dataset.get("season_player_batting") or [],
        dataset.get("season_player_pitching") or [],
        dataset.get("seasons") or [],
    )

    return v


# ---------------------------------------------------------------------------
# Career (multi-season) leaderboards
# ---------------------------------------------------------------------------

CAREER_MIN_PA   = 50   # career qualification for batting rate stats
CAREER_MIN_OUTS = 60   # career qualification for pitching rate stats


def _career_latest_meta(lines: list[dict]) -> dict[int, dict]:
    """Most-recent name / team / league per player (highest season_id)."""
    meta: dict[int, dict] = {}
    for r in lines:
        pid = r["player_id"]
        cur = meta.get(pid)
        if cur is None or (r.get("season_id") or 0) >= (cur.get("season_id") or 0):
            meta[pid] = r
    return meta


def _career_rank(rows, key, *, reverse=True, n=25, min_field=None, min_val=0):
    pool = [r for r in rows
            if min_field is None or (r.get(min_field) or 0) >= min_val]
    return sorted(pool, key=lambda r: (r.get(key) or 0), reverse=reverse)[:n]


def _career_batting(lines: list[dict]) -> list[dict]:
    meta = _career_latest_meta(lines)
    agg: dict[int, dict] = {}
    fields = ("g", "pa", "ab", "r", "h", "doubles", "triples",
              "hr", "rbi", "bb", "k", "sb", "hbp",
              "risp_pa", "risp_ab", "risp_h", "risp_2b", "risp_3b",
              "risp_hr", "risp_bb", "risp_hbp", "risp_rbi",
              "sh", "bunt_att", "bunt_hits", "sqz", "sqz_rbi")
    for r in lines:
        pid = r["player_id"]
        a = agg.get(pid)
        if a is None:
            a = agg[pid] = {f: 0 for f in fields}
            a["player_id"] = pid
            a["_seasons"] = set()
        for f in fields:
            a[f] += r.get(f) or 0
        a["_seasons"].add(r.get("season_id"))
    out: list[dict] = []
    for pid, a in agg.items():
        m = meta.get(pid, {})
        ab, h, bb, hbp = a["ab"], a["h"], a["bb"], a["hbp"]
        d2, d3, hr = a["doubles"], a["triples"], a["hr"]
        tb = (h - d2 - d3 - hr) + 2 * d2 + 3 * d3 + 4 * hr
        obp_den = ab + bb + hbp
        a.update(
            seasons=len(a.pop("_seasons")),
            player_name=m.get("player_name", "?"),
            team_abbrev=m.get("team_abbrev", ""),
            league=m.get("league", ""),
            tb=tb,
            avg=(h / ab) if ab else 0.0,
            obp=((h + bb + hbp) / obp_den) if obp_den else 0.0,
            slg=(tb / ab) if ab else 0.0,
        )
        a["ops"] = a["obp"] + a["slg"]
        # Career RISP slash — PA-denominated (matching the live app), summed
        # from per-season component totals; risp_conv = RBI per RISP PA.
        rpa = a["risp_pa"]
        r_tb = ((a["risp_h"] - a["risp_2b"] - a["risp_3b"] - a["risp_hr"])
                + 2 * a["risp_2b"] + 3 * a["risp_3b"] + 4 * a["risp_hr"])
        a["risp_pavg"] = (a["risp_h"] / rpa) if rpa else 0.0
        a["risp_obp"]  = ((a["risp_h"] + a["risp_bb"] + a["risp_hbp"]) / rpa) if rpa else 0.0
        a["risp_slg"]  = (r_tb / rpa) if rpa else 0.0
        a["risp_ops"]  = a["risp_obp"] + a["risp_slg"]
        a["risp_conv"] = (a["risp_rbi"] / rpa) if rpa else 0.0
        out.append(a)
    return out


def _career_pitching(lines: list[dict]) -> list[dict]:
    meta = _career_latest_meta(lines)
    agg: dict[int, dict] = {}
    fields = ("g", "gs", "w", "l", "outs", "h", "r", "er", "bb", "k", "hr",
              "ir_inherited", "ir_scored", "terminal_outs", "quality_finish",
              "lead_entries", "lead_held")
    for r in lines:
        pid = r["player_id"]
        a = agg.get(pid)
        if a is None:
            a = agg[pid] = {f: 0 for f in fields}
            a["player_id"] = pid
            a["_seasons"] = set()
        for f in fields:
            a[f] += r.get(f) or 0
        a["_seasons"].add(r.get("season_id"))
    out: list[dict] = []
    for pid, a in agg.items():
        m = meta.get(pid, {})
        outs = a["outs"]
        ip = outs / 3.0
        a.update(
            seasons=len(a.pop("_seasons")),
            player_name=m.get("player_name", "?"),
            team_abbrev=m.get("team_abbrev", ""),
            league=m.get("league", ""),
            ip=ip,
            ip_disp=f"{outs // 3}.{outs % 3}",
            era=((a["er"] * 27.0 / outs) if outs else 0.0),
            whip=(((a["bb"] + a["h"]) / ip) if ip else 0.0),
            k9=((a["k"] * 27.0 / outs) if outs else 0.0),
        )
        # Career relief/finisher rates (recomputed from component sums).
        inh, le = a["ir_inherited"], a["lead_entries"]
        a["ir_stop_pct"] = ((inh - a["ir_scored"]) / inh) if inh else None
        a["lra"] = (10.0 * a["lead_held"] / le) if le else None
        out.append(a)
    return out


def _career_leaderboards(bat_lines: list[dict], pit_lines: list[dict],
                         seasons: list[dict]) -> dict[str, Any]:
    bat = _career_batting(bat_lines)
    pit = _career_pitching(pit_lines)
    batting = {
        "h":   _career_rank(bat, "h"),
        "hr":  _career_rank(bat, "hr"),
        "rbi": _career_rank(bat, "rbi"),
        "r":   _career_rank(bat, "r"),
        "sb":  _career_rank(bat, "sb"),
        "bb":  _career_rank(bat, "bb"),
        "avg": _career_rank(bat, "avg", min_field="pa", min_val=CAREER_MIN_PA),
        "obp": _career_rank(bat, "obp", min_field="pa", min_val=CAREER_MIN_PA),
        "ops": _career_rank(bat, "ops", min_field="pa", min_val=CAREER_MIN_PA),
        "sh":        _career_rank(bat, "sh"),
        "bunt_hits": _career_rank(bat, "bunt_hits"),
        "sqz_rbi":   _career_rank(bat, "sqz_rbi"),
        "risp_rbi":  _career_rank(bat, "risp_rbi"),
        "risp_ops":  _career_rank(bat, "risp_ops", min_field="risp_pa", min_val=CAREER_MIN_PA),
    }
    pitching = {
        "w":    _career_rank(pit, "w"),
        "k":    _career_rank(pit, "k"),
        "g":    _career_rank(pit, "g"),
        "gs":   _career_rank(pit, "gs"),
        "era":  _career_rank(pit, "era",  reverse=False,
                             min_field="outs", min_val=CAREER_MIN_OUTS),
        "whip": _career_rank(pit, "whip", reverse=False,
                             min_field="outs", min_val=CAREER_MIN_OUTS),
        "k9":   _career_rank(pit, "k9", min_field="outs", min_val=CAREER_MIN_OUTS),
        "terminal_outs":  _career_rank(pit, "terminal_outs"),
        "quality_finish": _career_rank(pit, "quality_finish"),
        "lra":            _career_rank(pit, "lra", min_field="lead_entries", min_val=3),
    }
    return {
        "batting": batting,
        "pitching": pitching,
        "has_data": bool(bat or pit),
        "n_seasons": len(seasons),
        "min_pa": CAREER_MIN_PA,
        "min_outs": CAREER_MIN_OUTS,
    }


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
            "home_name":    team_label(h),
            "away_name":    team_label(a),
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
    "adv_op_1b", "adv_adv_1b", "adv_op_2b", "adv_adv_2b",
    "adv_op_3b", "adv_adv_3b",
    "risp_pa", "risp_ab", "risp_h", "risp_2b", "risp_3b", "risp_hr",
    "risp_bb", "risp_hbp", "risp_rbi",
    "sh", "bunt_att", "bunt_hits", "sqz", "sqz_rbi",
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
        slot = agg.get(pid)
        if slot is None:
            slot = agg[pid] = _empty_batter_slot(pid, players_by_id, teams_by_id, r)
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
        "team_name":  team_label(t),
        "league":     t.get("league", ""),
        "division":   t.get("division", ""),
    }


def _augment_batters(rows: list[dict], league: dict[str, float],
                     runs_per_win: float) -> None:
    lg_ops    = league.get("ops", 0.0) or 0.0
    lg_woba   = league.get("woba", 0.0) or 0.0
    repl_woba = lg_woba * REPL_WOBA_PCT

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
        roe       = r.get("roe") or 0
        gidp      = r.get("gidp") or 0
        gitp      = r.get("gitp") or 0
        stay_h    = r.get("stay_hits") or 0
        stay_rbi  = r.get("stay_rbi")  or 0
        mhab      = r.get("multi_hit_abs") or 0

        singles = max(0, h - d - t - hr)
        tb      = singles + 2 * d + 3 * t + 4 * hr
        xbh     = d + t + hr

        r["singles"] = singles
        r["tb"]      = tb
        r["xbh"]     = xbh

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

        # Per-base RAD breakdown + total + per-PA.
        rad_1b = r.get("rad_1b") or 0
        rad_2b = r.get("rad_2b") or 0
        rad_3b = r.get("rad_3b") or 0
        rad    = rad_1b + rad_2b + rad_3b
        r["rad"]     = rad
        r["rad_pa"]  = (rad / pa) if pa else 0.0

        # 2C per-base conversion (move-runner success rate per stay opp).
        for base in ("1b", "2b", "3b"):
            op  = r.get(f"c2_op_{base}") or 0
            adv = r.get(f"c2_adv_{base}") or 0
            r[f"c2_conv_{base}"] = (adv / op) if op else 0.0
        c2_op_total  = sum(r.get(f"c2_op_{b}")  or 0 for b in ("1b", "2b", "3b"))
        c2_adv_total = sum(r.get(f"c2_adv_{b}") or 0 for b in ("1b", "2b", "3b"))
        r["c2_op_total"]  = c2_op_total
        r["c2_adv_total"] = c2_adv_total
        r["c2_conv_total"] = (c2_adv_total / c2_op_total) if c2_op_total else 0.0

        # Per-PA runner-advancement (any cause).
        for base in ("1b", "2b", "3b"):
            op  = r.get(f"adv_op_{base}") or 0
            adv = r.get(f"adv_adv_{base}") or 0
            r[f"adv_conv_{base}"] = (adv / op) if op else 0.0
        adv_op_total  = sum(r.get(f"adv_op_{b}")  or 0 for b in ("1b", "2b", "3b"))
        adv_adv_total = sum(r.get(f"adv_adv_{b}") or 0 for b in ("1b", "2b", "3b"))
        r["adv_op_total"]  = adv_op_total
        r["adv_adv_total"] = adv_adv_total
        r["adv_conv_total"] = (adv_adv_total / adv_op_total) if adv_op_total else 0.0

        # RISP slash line — PA-denominated, matching PAVG (stays let risp_h
        # exceed risp_ab, so per-AB is unreliable). risp_conv = RBI per RISP PA.
        rpa  = r.get("risp_pa") or 0
        rh   = r.get("risp_h") or 0
        r2   = r.get("risp_2b") or 0
        r3   = r.get("risp_3b") or 0
        rhr  = r.get("risp_hr") or 0
        rbb  = r.get("risp_bb") or 0
        rhbp = r.get("risp_hbp") or 0
        rtb  = (rh - r2 - r3 - rhr) + 2 * r2 + 3 * r3 + 4 * rhr
        r["risp_pavg"] = (rh / rpa) if rpa else 0.0
        r["risp_obp"]  = ((rh + rbb + rhbp) / rpa) if rpa else 0.0
        r["risp_slg"]  = (rtb / rpa) if rpa else 0.0
        r["risp_ops"]  = r["risp_obp"] + r["risp_slg"]
        r["risp_conv"] = ((r.get("risp_rbi") or 0) / rpa) if rpa else 0.0

        # Bunting — bunt-hit rate per bunt attempt.
        ba = r.get("bunt_att") or 0
        r["bunt_avg"] = ((r.get("bunt_hits") or 0) / ba) if ba else 0.0

        # ROE / GIDP / GITP rates.
        r["roe_pct"]  = (roe  / pa) if pa else 0.0
        r["gidp_pct"] = (gidp / ab) if ab else 0.0
        r["gitp_pct"] = (gitp / ab) if ab else 0.0

        # bVORP / WAR (offense only; defensive piece folded in later).
        bvorp = ((r["woba"] - repl_woba) * pa / 1.20) if pa else 0.0
        r["bvorp"]   = bvorp
        r["war_off"] = (bvorp / runs_per_win) if runs_per_win else 0.0

        # Fielding components live in fielding_season; bWAR adds dDRS there.
        r["bwar"] = r["war_off"]

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
    "k_tto1",  "k_tto2",  "k_tto3",
    "fo_tto1", "fo_tto2", "fo_tto3",
    "bf_tto1", "bf_tto2", "bf_tto3",
    "singles_allowed", "doubles_allowed", "triples_allowed",
    "fastball_pct", "breaking_pct", "offspeed_pct",
    "ir_inherited", "ir_scored",
    "terminal_outs", "quality_finish", "lead_entries", "lead_held",
]


def _aggregate_pitchers(
    rows: list[dict],
    players_by_id: dict[int, dict],
    teams_by_id: dict[int, dict],
) -> list[dict]:
    agg: dict[int, dict] = {}
    games_seen: dict[int, set[int]] = {}
    starts: dict[int, int] = {}
    primary_counts: dict[int, dict[str, int]] = {}

    for r in rows:
        pid = r["player_id"]
        slot = agg.get(pid)
        if slot is None:
            slot = agg[pid] = _empty_pitcher_slot(pid, players_by_id, teams_by_id, r)
        for f in _PITCHING_SUM_FIELDS:
            slot[f] = (slot.get(f) or 0) + (r.get(f) or 0)
        games_seen.setdefault(pid, set()).add(r["game_id"])
        if r.get("is_starter"):
            starts[pid] = starts.get(pid, 0) + 1
        pp = (r.get("primary_pitch") or "").strip()
        if pp:
            primary_counts.setdefault(pid, {})[pp] = \
                primary_counts.setdefault(pid, {}).get(pp, 0) + 1

    out = []
    for pid, slot in agg.items():
        slot["g"]  = len(games_seen.get(pid, set()))
        slot["gs"] = starts.get(pid, 0)
        counts = primary_counts.get(pid, {})
        slot["primary_pitch"] = max(counts.items(), key=lambda x: x[1])[0] if counts else ""
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
        "team_name": team_label(t),
        "league":    t.get("league", ""),
        "division":  t.get("division", ""),
    }


def _augment_pitchers(rows: list[dict], league: dict[str, float],
                      runs_per_win: float) -> None:
    lg_era    = league.get("era", 0.0) or 0.0
    lg_fip    = league.get("fip", 0.0) or 0.0
    lg_gsc    = league.get("gsc_avg", 50.0) or 50.0
    lg_outs_g = league.get("outs_per_game", 13.5) or 13.5
    lg_decay  = league.get("decay_raw_mean", 0.0) or 0.0
    repl_era  = lg_era * REPL_ERA_PCT

    # League non-HR hit shares (used for opponent SLG estimation when the
    # pitcher table doesn't break hits-allowed into 1B/2B/3B).
    share_1b = league.get("nonhr_1b_share", 0.85)
    share_2b = league.get("nonhr_2b_share", 0.12)
    share_3b = league.get("nonhr_3b_share", 0.03)

    for r in rows:
        outs = r.get("outs_recorded") or 0
        bf   = r.get("batters_faced") or 0
        h    = r.get("hits_allowed")  or 0
        er   = r.get("er") or r.get("runs_allowed") or 0
        uer  = r.get("unearned_runs") or 0
        ra   = r.get("runs_allowed") or 0
        bb   = r.get("bb") or 0
        k    = r.get("k")  or 0
        hr   = r.get("hr_allowed") or 0
        hbp  = r.get("hbp_allowed") or 0
        pitches = r.get("pitches") or 0
        fo_ind  = r.get("fo_induced") or 0
        sb_a    = r.get("sb_allowed") or 0
        cs_c    = r.get("cs_caught") or 0
        g    = r.get("g")  or 0
        gs   = r.get("gs") or 0

        ip = outs / 3.0
        r["ip"] = ip
        r["ip_disp"] = _format_ip(outs)
        r["era"]  = (er * 27.0 / outs) if outs else 0.0
        r["whip"] = ((bb + h) / ip)    if ip   else 0.0
        r["k9"]   = (k  * 9.0  / ip)   if ip   else 0.0
        r["bb9"]  = (bb * 9.0  / ip)   if ip   else 0.0
        r["hr9"]  = (hr * 9.0  / ip)   if ip   else 0.0
        # FIP constant calibrated per-league so league_FIP == league_ERA.
        # See _league_totals — fip_const stuffed there. Falls back to 3.10
        # (MLB default) if the league dict didn't carry one through.
        fip_const = league.get("fip_const", 3.10) or 3.10
        r["fip"]  = ((13 * hr + 3 * (bb + hbp) - 2 * k) / ip + fip_const) if ip else 0.0
        # xFIP — K% includes foul-outs as documented (uses K+FO as "K-equivalent")
        r["xfip"] = (
            (13 * hr + 3 * (bb + hbp) - 2 * (k + fo_ind)) * 27.0 / outs + fip_const
        ) if outs else 0.0

        # Rate stats (BF-denominated, per spec).
        r["k_pct"]   = ((k + fo_ind) / bf) if bf else 0.0      # K% includes FO per docs
        r["k_pct_pure"] = (k / bf) if bf else 0.0
        r["bb_pct"]  = (bb / bf) if bf else 0.0
        r["hr_pct"]  = (hr / bf) if bf else 0.0
        r["k_bb"]    = (k / bb)  if bb else float(k)
        r["k_minus_bb_pct"] = ((k - bb) / bf) if bf else 0.0
        r["fo_pct_pit"]     = (fo_ind / bf) if bf else 0.0
        r["op_per_pitch"]   = (outs / pitches) if pitches else 0.0
        r["p_per_bf"]       = (pitches / bf) if bf else 0.0
        r["cs_pct"]         = (cs_c / (sb_a + cs_c)) if (sb_a + cs_c) else 0.0

        # Opponent slash (oAVG/oBABIP/oOBP/oSLG/oOPS).
        ab_face = max(0, bf - bb - hbp)
        r["oavg"]   = (h / ab_face) if ab_face > 0 else 0.0
        bip_face   = max(0, bf - bb - hbp - k - hr)
        r["obabip"] = ((h - hr) / bip_face) if bip_face > 0 else 0.0
        r["oobp"]   = ((h + bb + hbp) / bf) if bf else 0.0
        non_hr_h   = max(0, h - hr)
        est_1b = non_hr_h * share_1b
        est_2b = non_hr_h * share_2b
        est_3b = non_hr_h * share_3b
        est_tb = est_1b + 2 * est_2b + 3 * est_3b + 4 * hr
        r["oslg"] = (est_tb / ab_face) if ab_face > 0 else 0.0
        r["oops"] = r["oobp"] + r["oslg"]

        r["os_pct"] = (outs / (g * 27.0)) if g else 0.0
        r["aor"]    = (outs / g) if g else 0.0
        r["os_plus"]= round(100.0 * r["aor"] / lg_outs_g) if (lg_outs_g and r["aor"]) else 100

        # Arc-bucket rates.
        bf1, bf2, bf3 = r.get("bf_arc1") or 0, r.get("bf_arc2") or 0, r.get("bf_arc3") or 0
        er1, er2, er3 = r.get("er_arc1") or 0, r.get("er_arc2") or 0, r.get("er_arc3") or 0
        k1, k2, k3    = r.get("k_arc1")  or 0, r.get("k_arc2")  or 0, r.get("k_arc3")  or 0
        fo1, fo2, fo3 = r.get("fo_arc1") or 0, r.get("fo_arc2") or 0, r.get("fo_arc3") or 0

        weighted_er  = ARC_W_1 * er1 + ARC_W_2 * er2 + ARC_W_3 * er3
        weighted_bf  = ARC_W_1 * bf1 + ARC_W_2 * bf2 + ARC_W_3 * bf3
        r["wera"] = (weighted_er * 27.0 / outs) * (
            (bf / weighted_bf) if weighted_bf else 1.0
        ) if outs and bf else 0.0

        k1_rate = (k1 / bf1) if bf1 else 0.0
        k3_rate = (k3 / bf3) if bf3 else 0.0
        r["decay_raw"]   = (k1_rate - k3_rate) * 100.0      # in % points
        r["decay"]       = r["decay_raw"] - lg_decay        # drift-corrected
        r["late_k_pct"]  = ((k3 + fo3) / bf3) if bf3 else 0.0
        r["early_k_pct"] = ((k1 + fo1) / bf1) if bf1 else 0.0
        r["arc3_reach_pct"] = (1.0 if bf3 > 0 else 0.0)

        # Relief / finisher value. IR-Stop% = inherited runners stranded;
        # LR% = lead-entries held; late_er_per_bf = final-arc run prevention.
        # terminal_outs / quality_finish pass through as season SUMs.
        ir_inh = r.get("ir_inherited") or 0
        ir_sc  = r.get("ir_scored") or 0
        r["ir_stop_pct"] = ((ir_inh - ir_sc) / ir_inh) if ir_inh else None
        le = r.get("lead_entries") or 0
        lh = r.get("lead_held") or 0
        r["lr_pct"] = (lh / le) if le else None
        r["late_er_per_bf"] = ((r.get("er_arc3") or 0) / bf3) if bf3 else None

        # Times-through-the-order (FAMILIARITY axis — the engine TTO buckets).
        # K% (incl. foul-outs, matching the arc K% convention) by how many
        # times the batter has faced this pitcher in the game.
        kt1, kt2, kt3 = r.get("k_tto1") or 0, r.get("k_tto2") or 0, r.get("k_tto3") or 0
        ft1, ft2, ft3 = r.get("fo_tto1") or 0, r.get("fo_tto2") or 0, r.get("fo_tto3") or 0
        bt1, bt2, bt3 = r.get("bf_tto1") or 0, r.get("bf_tto2") or 0, r.get("bf_tto3") or 0
        r["tto1_k_pct"] = ((kt1 + ft1) / bt1) if bt1 else 0.0
        r["tto2_k_pct"] = ((kt2 + ft2) / bt2) if bt2 else 0.0
        r["tto3_k_pct"] = ((kt3 + ft3) / bt3) if bt3 else 0.0
        r["tto1_bf"], r["tto2_bf"], r["tto3_bf"] = bt1, bt2, bt3
        # TTO K-Decay: K% points lost from the 1st look to the 3rd+ look.
        # Positive = the lineup cracks his code across repeat looks (low
        # deception); ~0 / negative = he holds his whiffs as familiarity grows
        # (high deception — the arbitrage arm). The familiarity-axis sibling
        # of Decay (which is the fatigue/arc axis).
        r["tto_k_decay"] = (r["tto1_k_pct"] - r["tto3_k_pct"]) * 100.0

        # Per-arc ER rates (R/9-style)
        r["arc1_era"] = (er1 * 27.0 / (bf1 * (outs / bf))) if (bf and bf1 and outs) else 0.0
        r["arc2_era"] = (er2 * 27.0 / (bf2 * (outs / bf))) if (bf and bf2 and outs) else 0.0
        r["arc3_era"] = (er3 * 27.0 / (bf3 * (outs / bf))) if (bf and bf3 and outs) else 0.0

        # Game Score (per appearance — averaged via game logs; here, season summary)
        r["gsc_total"] = _pitcher_game_score(
            outs=outs, k=k, h=h, er=er, uer=uer, bb=bb, hr=hr, fo=fo_ind,
        )
        # gsc_avg is set later via _augment_pitcher_game_logs once we have per-game lines.

        r["era_plus"] = round(100.0 * lg_era / r["era"]) if (r["era"] and lg_era) else 100
        r["fip_plus"] = round(100.0 * lg_fip / r["fip"]) if (r["fip"] and lg_fip) else 100
        r["xfip_plus"] = round(100.0 * lg_fip / r["xfip"]) if (r["xfip"] and lg_fip) else 100

        # pVORP / pWAR (replacement = REPL_ERA_PCT * league ERA).
        if outs:
            run_diff = (repl_era - r["wera"]) * (outs / 27.0)
            r["pvorp"] = run_diff
            r["pwar"]  = run_diff / runs_per_win if runs_per_win else 0.0
        else:
            r["pvorp"] = 0.0
            r["pwar"]  = 0.0

        # Pitch mix shares (already on the row per-game; here we surface them
        # as the season aggregate — assume rows already carry per-game means
        # because we summed them in _aggregate_pitchers; normalize by g).
        for fld in ("fastball_pct", "breaking_pct", "offspeed_pct"):
            tot = r.get(fld) or 0.0
            r[fld] = (tot / g) if g else 0.0
        prim = r.get("primary_pitch") or ""
        r["primary_pitch"] = prim

        r["qualified"] = outs >= MIN_OUTS_QUALIFIED


# Launch-angle batted-ball classification (degrees). Mirrors the bands the
# batted-ball model samples around: < 10 grounder, 10-25 line drive, > 25 fly.
_GB_MAX_LA = 10.0
_LD_MAX_LA = 25.0


def _deception_grade(repertoire_json) -> int:
    """Repertoire-weighted timing_resistance → 20-80 scouting grade.

    The "Deception" grade: how un-timeable a pitcher's arsenal is across a
    27-out arc (knuckle / eephus / softball junk grade high; pure velocity
    grades low). Reads per-pitch timing_resistance from the engine catalog;
    returns 50 (league-neutral) when the pitcher has no typed repertoire.
    """
    import json as _json
    from o27 import config as _cfg
    raw = repertoire_json
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except (ValueError, TypeError):
            return 50
    if not isinstance(raw, list) or not raw:
        return 50
    acc = tot = 0.0
    for e in raw:
        if not isinstance(e, dict):
            continue
        cat = _cfg.PITCH_CATALOG.get(e.get("pitch_type"))
        if cat is None:
            continue
        w = float(e.get("usage_weight", 1.0) or 0.0)
        if w <= 0:
            continue
        acc += float(cat.get("timing_resistance", 0.5)) * w
        tot += w
    if tot <= 0:
        return 50
    tr = max(0.0, min(1.0, acc / tot))
    return int(round(20 + tr * 60))


def _attach_pitcher_battedball_tto(
    pitching_by_player: dict, players_by_id: dict, pa_log: list[dict]
) -> None:
    """Attach batted-ball profile (#1), times-through-order contact splits (#2),
    and the Deception grade (#4) to each pitcher's season row.

    All derived from game_pa_log (balls-in-play only — no K/BB rows exist
    there) plus the player's repertoire. The times-through "look" number is
    the rank of an AB's ab_seq within each (game, pitcher, batter) group, so
    it survives multi-row stay ABs. K%-by-look is NOT computable here (strike-
    outs aren't logged); that dimension needs engine-side TTO buckets.
    """
    by_pit: dict = {}
    by_gpb: dict = {}
    for e in pa_log:
        pid = e.get("pitcher_id")
        if pid is None:
            continue
        by_pit.setdefault(pid, []).append(e)
        by_gpb.setdefault((e.get("game_id"), pid, e.get("batter_id")), []).append(e)

    # Map each event to a look bucket (0 = 1st time facing, 1 = 2nd, 2 = 3rd+).
    look_bucket: dict = {}
    for evs in by_gpb.values():
        seqs = sorted({ev.get("ab_seq") for ev in evs if ev.get("ab_seq") is not None})
        rank = {s: i for i, s in enumerate(seqs)}
        for ev in evs:
            look = rank.get(ev.get("ab_seq"), 0)
            look_bucket[id(ev)] = 0 if look == 0 else (1 if look == 1 else 2)

    # Deception grade for every pitcher with a row (repertoire-driven).
    for pid, row in pitching_by_player.items():
        row["deception"] = _deception_grade(
            (players_by_id.get(pid) or {}).get("repertoire")
        )

    for pid, evs in by_pit.items():
        row = pitching_by_player.get(pid)
        if row is None:
            continue
        gb = ld = fb = bip = 0
        tto = [[0, 0] for _ in range(3)]  # per look bucket: [hard, total_contact]
        for e in evs:
            la = e.get("launch_angle")
            if la is not None:
                bip += 1
                if la < _GB_MAX_LA:
                    gb += 1
                elif la <= _LD_MAX_LA:
                    ld += 1
                else:
                    fb += 1
            q = e.get("quality")
            if q in ("weak", "medium", "hard"):
                b = look_bucket.get(id(e), 0)
                tto[b][1] += 1
                if q == "hard":
                    tto[b][0] += 1
        if bip:
            row["gb_pct"] = gb / bip
            row["ld_pct"] = ld / bip
            row["fb_pct"] = fb / bip
            row["go_ao"]  = (gb / fb) if fb else float(gb)
            row["bip_tracked"] = bip

        def _hh(b: int) -> float:
            return (tto[b][0] / tto[b][1]) if tto[b][1] else 0.0
        row["tto1_hardhit"] = _hh(0)
        row["tto2_hardhit"] = _hh(1)
        row["tto3_hardhit"] = _hh(2)
        # Familiarity signal: how much harder hitters square the pitcher up by
        # the 3rd+ look vs the 1st. Positive = gets figured out; ~0 / negative
        # = resists familiarity (the deception arms should sit here).
        row["tto_hardhit_delta"] = _hh(2) - _hh(0)
        row["tto1_contacts"] = tto[0][1]
        row["tto3_contacts"] = tto[2][1]


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
    # FIP constant is *defined* such that league_FIP == league_ERA. The
    # MLB-default 3.10 is wrong for O27's high-RPG environment (league
    # FIP would land ~8 runs below league ERA, making cross-pitcher FIP
    # comparisons against ERA nonsensical). Compute it dynamically from
    # this season's actual league totals — then league FIP collapses to
    # league ERA by construction and individual pitcher FIPs are
    # calibrated against the live environment.
    if p_ip:
        fip_kernel_league = (13 * p_hr + 3 * (p_bb + p_hbp) - 2 * p_k) / p_ip
        out["fip_const"]  = out["era"] - fip_kernel_league
        out["fip"]        = out["era"]    # by construction
    else:
        out["fip_const"] = 0.0
        out["fip"]       = 0.0
    out["p_k_pct"]  = (p_k  / p_bf) if p_bf else 0.0
    out["p_bb_pct"] = (p_bb / p_bf) if p_bf else 0.0

    # Non-HR hit shares (1B/2B/3B share within non-HR hits). Used by
    # opponent SLG estimation in _augment_pitchers.
    non_hr_h = max(0, h - hr)
    out["nonhr_1b_share"] = (singles / non_hr_h) if non_hr_h else 0.85
    out["nonhr_2b_share"] = (d       / non_hr_h) if non_hr_h else 0.12
    out["nonhr_3b_share"] = (t       / non_hr_h) if non_hr_h else 0.03

    # League AOR / GSc baselines — computed in a second pass against the
    # already-augmented pitcher rows. Placeholders here; _augment_pitchers
    # reads what's present and falls back to defaults.
    g_total = sum(r.get("g") or 0 for r in pit)
    out["outs_per_game"] = (p_outs / g_total) if g_total else 13.5
    # decay_raw_mean — computed in second pass too; pre-set neutral.
    out["decay_raw_mean"] = 0.0
    # League GSc placeholder — defaults to 50 (clamp midpoint) until
    # _augment_pitcher_game_logs computes per-appearance lines and we can
    # average them.
    out["gsc_avg"] = 50.0

    return out


# ---------------------------------------------------------------------------
# Runs-per-win + Game Score + Pitcher game-log augmentation
# ---------------------------------------------------------------------------

def _runs_per_win(league: dict[str, float]) -> float:
    """Approximate runs-per-win from league run environment.

    For MLB ~ 4.5 R/G we get ~10; O27's higher run env nudges this up.
    Formula: sqrt((R/G) * (RA/G)) × 2 — Pete Palmer-style derivation,
    O27-tuned by replacing 10 with the league's actual scoring rate.
    """
    rpg  = ((league.get("runs") or 0) /
            max(1, (league.get("pa") or 1) / 38.0))  # ~38 PA/team-game
    base = max(DEFAULT_RUNS_PER_WIN, rpg * 2)
    return base


def _pitcher_game_score(*, outs, k, h, er, uer, bb, hr, fo) -> float:
    score = (
        GSC_BASE
        + GSC_OUT * outs
        + GSC_K_BONUS_OVER_3 * max(0, k - 3)
        + GSC_FO_BONUS * fo
        - GSC_H_COST * h
        - GSC_HR_OVER_H_COST * hr
        - GSC_BB_COST * bb
        - GSC_ER_COST * er
        - GSC_UER_COST * uer
    )
    if score < 0:
        return 0.0
    if score > 100:
        return 100.0
    return float(score)


def _augment_pitcher_game_logs(game_logs: dict[int, list[dict]]) -> None:
    """Stamp per-appearance Game Score + IP_disp onto each pitcher log
    entry, and roll up gsc_avg / decay_pg / arc3_reach_rate onto the
    season row by mutating it through `season_ref` (not done here — the
    log just gets enriched in place)."""
    for pid, log in game_logs.items():
        for entry in log:
            outs = entry.get("outs_recorded") or 0
            entry["ip_disp"] = _format_ip(outs)
            entry["gsc"] = _pitcher_game_score(
                outs=outs,
                k=entry.get("k") or 0,
                h=entry.get("hits_allowed") or 0,
                er=entry.get("er") or 0,
                uer=entry.get("unearned_runs") or 0,
                bb=entry.get("bb") or 0,
                hr=entry.get("hr_allowed") or 0,
                fo=entry.get("fo_induced") or 0,
            )


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
            **{k: val for k, val in r.items() if k != "id"},
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

# ---------------------------------------------------------------------------
# Fielding aggregation
# ---------------------------------------------------------------------------

def _aggregate_fielders(rows, players_by_id, teams_by_id) -> list[dict]:
    """One row per (player, position-played). Computes PO/A/E/CH/FLD%
    plus a coarse dDRS / dWAR estimate from POS_DRS_RANGE."""
    agg: dict[tuple[int, str], dict] = {}
    games_seen: dict[tuple[int, str], set[int]] = {}

    for r in rows:
        pid = r["player_id"]
        pos = (r.get("game_position") or "").upper().strip() or "—"
        # Strip secondary positions like "SS-2B" → "SS" for grouping.
        primary = pos.split("-")[0]
        key = (pid, primary)
        slot = agg.get(key)
        if slot is None:
            slot = agg[key] = _empty_fielder_slot(pid, primary,
                                                  players_by_id, teams_by_id, r)
        slot["po"] += (r.get("po") or 0)
        slot["a"]  += (r.get("a")  or 0)
        slot["e"]  += (r.get("e")  or 0)
        if (r.get("pa") or 0) > 0 or (r.get("po") or 0) > 0:
            games_seen.setdefault(key, set()).add(r["game_id"])

    out = []
    for key, slot in agg.items():
        g = len(games_seen.get(key, set()))
        slot["g"] = g
        po, a, e = slot["po"], slot["a"], slot["e"]
        ch = po + a + e
        slot["ch"]   = ch
        slot["fld_pct"] = ((po + a) / ch) if ch else 0.0
        # dDRS — coarse: (FLD% - 0.970) × position DRS range × games-share.
        range_pts = POS_DRS_RANGE.get(slot["position"], 8)
        league_baseline = 0.970
        dDRS = (slot["fld_pct"] - league_baseline) * 2 * range_pts * (g / 162.0)
        slot["ddrs"] = dDRS
        slot["dwar"] = dDRS / DEFAULT_RUNS_PER_WIN
        out.append(slot)

    out.sort(key=lambda r: -(r["po"] + r["a"]))
    return out


def _empty_fielder_slot(pid, position, players_by_id, teams_by_id, sample_row):
    p = players_by_id.get(pid) or {}
    t = teams_by_id.get(sample_row.get("team_id")) or {}
    return {
        "player_id": pid,
        "name":      p.get("name", "?"),
        "slug":      _slugify(p.get("name", str(pid))),
        "position":  position,
        "team":      t.get("abbrev", "?"),
        "team_id":   sample_row.get("team_id"),
        "league":    t.get("league", ""),
        "division":  t.get("division", ""),
        "po": 0, "a": 0, "e": 0,
    }


# ---------------------------------------------------------------------------
# Pythagorean
# ---------------------------------------------------------------------------

def _compute_pythag(standings: list[dict]) -> tuple[dict[str, dict], dict[str, float]]:
    """Fit the Pythagorean exponent across teams and return per-team
    luck (fitted + MLB-default) plus a small summary dict."""
    empty_summary = {
        "fitted_exponent": 1.83, "mlb_default": 1.83, "n_teams": 0,
        "rmse_fit": 0.0, "rmse_default": 0.0, "improvement_pct": 0.0,
    }
    if len(standings) < 2:
        return {}, {**empty_summary, "n_teams": len(standings)}

    teams_in = [r for r in standings if (r["gp"] or 0) > 0
                and ((r["rs"] or 0) > 0 or (r["ra"] or 0) > 0)]
    if not teams_in:
        return {}, empty_summary

    def pct(r, ra, k):
        if r <= 0 and ra <= 0:
            return 0.5
        return (r ** k) / (r ** k + ra ** k)

    def sse(k):
        s = 0.0
        for r in teams_in:
            actual = r["w"] / r["gp"]
            s += (actual - pct(r["rs"], r["ra"], k)) ** 2
        return s

    # Ternary search for k* in [1.0, 4.0].
    a, b = 1.0, 4.0
    for _ in range(60):
        m1 = a + (b - a) / 3
        m2 = b - (b - a) / 3
        if sse(m1) < sse(m2):
            b = m2
        else:
            a = m1
    k_star = (a + b) / 2

    out: dict[str, dict] = {}
    for r in teams_in:
        gp = r["gp"]
        p_fit  = pct(r["rs"], r["ra"], k_star)
        p_def  = pct(r["rs"], r["ra"], 1.83)
        w_fit  = p_fit * gp
        w_def  = p_def * gp
        out[r["abbrev"]] = {
            "pythag_pct_fit":     p_fit,
            "pythag_pct_default": p_def,
            "pythag_w_fit":       w_fit,
            "pythag_w_default":   w_def,
            "luck_fit":           r["w"] - w_fit,
            "luck_default":       r["w"] - w_def,
        }

    n = len(teams_in)
    rmse_fit = (sse(k_star) / n) ** 0.5
    rmse_def = (sse(1.83)   / n) ** 0.5
    summary = {
        "fitted_exponent": k_star,
        "mlb_default":     1.83,
        "n_teams":         n,
        "rmse_fit":        rmse_fit,
        "rmse_default":    rmse_def,
        "improvement_pct": ((rmse_def - rmse_fit) / rmse_def * 100.0) if rmse_def else 0.0,
    }
    return out, summary


# ---------------------------------------------------------------------------
# Monthly splits (league-wide partition)
# ---------------------------------------------------------------------------

def _monthly_splits(batting_rows: list[dict],
                    pitching_rows: list[dict],
                    games: list[dict]) -> list[dict]:
    """For each calendar month present in the schedule, sum league-wide
    counters and compute the bedrock rates (AVG/OBP/SLG/OPS/ERA/WHIP)."""
    game_month = {}
    for g in games:
        d = g.get("game_date") or ""
        if len(d) >= 7:
            game_month[g["id"]] = d[:7]   # YYYY-MM

    months: dict[str, dict] = {}
    for r in batting_rows:
        m = game_month.get(r["game_id"])
        if not m:
            continue
        slot = months.setdefault(m, _new_month_bucket(m))
        for f in ("pa", "ab", "hits", "doubles", "triples", "hr",
                  "bb", "hbp", "k", "stays", "runs", "rbi"):
            slot[f] += r.get(f) or 0
        slot["games"].add(r["game_id"])
    for r in pitching_rows:
        m = game_month.get(r["game_id"])
        if not m:
            continue
        slot = months.setdefault(m, _new_month_bucket(m))
        slot["p_outs"] += r.get("outs_recorded") or 0
        slot["p_er"]   += r.get("er") or 0
        slot["p_bb"]   += r.get("bb") or 0
        slot["p_h"]    += r.get("hits_allowed") or 0

    out = []
    for m, slot in sorted(months.items()):
        pa, ab, h, d, t, hr, bb, hbp, k = (
            slot["pa"], slot["ab"], slot["hits"],
            slot["doubles"], slot["triples"], slot["hr"],
            slot["bb"], slot["hbp"], slot["k"],
        )
        singles = max(0, h - d - t - hr)
        tb = singles + 2 * d + 3 * t + 4 * hr
        ip = slot["p_outs"] / 3.0
        out.append({
            "month": m,
            "g":     len(slot["games"]),
            "pa": pa, "ab": ab, "h": h, "hr": hr, "bb": bb, "k": k, "stays": slot["stays"],
            "runs": slot["runs"], "rbi": slot["rbi"],
            "avg":  (h / ab) if ab else 0.0,
            "obp":  ((h + bb + hbp) / pa) if pa else 0.0,
            "slg":  (tb / pa) if pa else 0.0,
            "ops":  (((h + bb + hbp) / pa) if pa else 0.0)
                    + ((tb / pa) if pa else 0.0),
            "era":  (slot["p_er"] * 27.0 / slot["p_outs"]) if slot["p_outs"] else 0.0,
            "whip": ((slot["p_bb"] + slot["p_h"]) / ip) if ip else 0.0,
        })
    return out


def _new_month_bucket(month: str) -> dict:
    return {
        "month": month, "games": set(),
        "pa": 0, "ab": 0, "hits": 0, "doubles": 0, "triples": 0,
        "hr": 0, "bb": 0, "hbp": 0, "k": 0, "stays": 0,
        "runs": 0, "rbi": 0,
        "p_outs": 0, "p_er": 0, "p_bb": 0, "p_h": 0,
    }


# ---------------------------------------------------------------------------
# Percentile helper (unchanged)
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
