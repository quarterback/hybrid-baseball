"""Win Probability Added (WPA) and Leverage Index for O27.

WP is estimated empirically from the league's own game outcomes — no
MLB-borrowed table required. Every PA in `game_pa_log` stamps the pre-
and post-event state (outs, bases, score_diff); every game in `games`
stamps the winner. Joining those two and taking the conditional win
frequency at each state gives WP(state | batting_side, score_diff,
outs, bases). Sparse cells fall back through a coarsening hierarchy.

WPA per PA = WP(state_after) − WP(state_before), expressed from the
batting team's perspective. The pitcher's WPA is the negation. Player
season aggregates simply sum WPA across appearances.

Leverage Index (LI) measures how much the win probability could swing
from a given state. We use the league-wide stdev of per-PA WPA at each
state, normalized so league-average LI = 1.0. A 2.0 LI means the PA
sits in a state with twice the swing potential of an average PA.

All queries go through `o27v2.db`; results are plain dicts so the web
layer can render without extra plumbing.
"""
from __future__ import annotations
from collections import defaultdict
from typing import Iterable

from o27v2 import db


# ---------------------------------------------------------------------------
# State coarsening — the empirical table is sparse at extreme score
# differentials and rare base states, so we collapse axes when needed.

def _clip_score_diff(d: int | None) -> int:
    """Cap |score_diff| at 10 — anything beyond rounds to ±10 since the
    win prob at +10 vs +15 is indistinguishable from data."""
    if d is None:
        return 0
    if d >  10: return  10
    if d < -10: return -10
    return int(d)


def _outs_coarse(outs: int | None) -> int:
    """Coarsen outs to 3-out buckets (0..8). Matches the RE24 bucketing
    in `run_expectancy.py` so sample size per cell is comparable."""
    if outs is None:
        return 0
    return min(8, max(0, int(outs) // 3))


# ---------------------------------------------------------------------------
# Build the empirical WP table.

def _winner_map() -> dict[int, int | None]:
    """game_id → winner_team_id (None for unresolved / tied games)."""
    return {
        int(r["id"]): (r["winner_id"] if r["winner_id"] is not None else None)
        for r in db.fetchall("SELECT id, winner_id FROM games WHERE played = 1")
    }


def build_wp_table() -> dict:
    """Build the empirical win-prob lookup.

    Returns:
        {
          "n_pas":   int,
          "n_games": int,
          "wp": {
              (batting_is_home, outs_bucket, bases, score_diff_clipped):
                  {"n": int, "wp": float}
          },
          # Fallback marginals — keyed only by (batting_is_home, score_diff)
          # for cells with no per-state data.
          "wp_margin": { (batting_is_home, score_diff_clipped): {"n", "wp"} },
        }
    """
    winners = _winner_map()
    rows = db.fetchall(
        """
        SELECT pa.game_id, pa.team_id, g.home_team_id,
               pa.outs_before, pa.bases_before, pa.score_diff_before
        FROM game_pa_log pa
        JOIN games g ON pa.game_id = g.id
        WHERE pa.phase = 0
          AND pa.outs_before IS NOT NULL
          AND pa.bases_before IS NOT NULL
          AND pa.score_diff_before IS NOT NULL
        """
    )

    # Per-cell wins and counts.
    cell_n:   dict[tuple, int] = defaultdict(int)
    cell_w:   dict[tuple, int] = defaultdict(int)
    margin_n: dict[tuple, int] = defaultdict(int)
    margin_w: dict[tuple, int] = defaultdict(int)
    games_seen: set[int] = set()

    for r in rows:
        gid = int(r["game_id"])
        winner = winners.get(gid)
        if winner is None:
            continue
        games_seen.add(gid)
        batting_team = int(r["team_id"])
        home_team    = int(r["home_team_id"])
        batting_is_home = 1 if batting_team == home_team else 0
        won = 1 if winner == batting_team else 0

        ob   = _outs_coarse(r["outs_before"])
        bs   = int(r["bases_before"])
        sd   = _clip_score_diff(r["score_diff_before"])

        key  = (batting_is_home, ob, bs, sd)
        cell_n[key] += 1
        cell_w[key] += won

        mkey = (batting_is_home, sd)
        margin_n[mkey] += 1
        margin_w[mkey] += won

    wp_cells = {
        k: {"n": cell_n[k], "wp": (cell_w[k] / cell_n[k])}
        for k in cell_n
    }
    wp_margin = {
        k: {"n": margin_n[k], "wp": (margin_w[k] / margin_n[k])}
        for k in margin_n
    }

    return {
        "n_pas":     sum(cell_n.values()),
        "n_games":   len(games_seen),
        "wp":        wp_cells,
        "wp_margin": wp_margin,
    }


def lookup_wp(
    wp_table: dict,
    batting_is_home: int,
    outs: int | None,
    bases: int | None,
    score_diff: int | None,
    min_n: int = 8,
) -> float | None:
    """Look up WP for a state. Coarsens via the margin fallback when the
    per-cell sample is below `min_n`. Returns None when even the margin
    cell is too thin (very early in a season).
    """
    ob   = _outs_coarse(outs)
    bs   = int(bases) if bases is not None else 0
    sd   = _clip_score_diff(score_diff)
    cell = wp_table["wp"].get((batting_is_home, ob, bs, sd))
    if cell and cell["n"] >= min_n:
        return float(cell["wp"])
    margin = wp_table["wp_margin"].get((batting_is_home, sd))
    if margin and margin["n"] >= min_n:
        return float(margin["wp"])
    return None


# ---------------------------------------------------------------------------
# Per-player WPA and Leverage aggregates.

def build_player_wpa() -> dict:
    """Walk every PA, compute per-event WPA + leverage, and aggregate by
    batter and pitcher.

    Returns:
        {
          "wp_table_size": int,
          "by_batter":  {player_id: {"wpa": float, "li_avg": float, "n_pa": int}},
          "by_pitcher": {player_id: {"wpa": float, "li_avg": float, "n_pa": int}},
          "top_pa": list[dict] (top |WPA| events, for narrative surfaces),
        }
    """
    wp_table = build_wp_table()
    if wp_table["n_games"] == 0:
        return {"wp_table_size": 0, "by_batter": {}, "by_pitcher": {},
                "top_pa": [], "li_norm": 1.0}

    rows = db.fetchall(
        """
        SELECT pa.id, pa.game_id, pa.team_id, pa.batter_id, pa.pitcher_id,
               pa.outs_before, pa.bases_before, pa.score_diff_before,
               pa.outs_after,  pa.bases_after,  pa.score_diff_after,
               g.home_team_id
        FROM game_pa_log pa
        JOIN games g ON pa.game_id = g.id
        WHERE pa.phase = 0
          AND pa.outs_before IS NOT NULL
          AND pa.outs_after  IS NOT NULL
          AND pa.bases_before IS NOT NULL
          AND pa.bases_after  IS NOT NULL
          AND pa.score_diff_before IS NOT NULL
          AND pa.score_diff_after  IS NOT NULL
        """
    )

    # Pass 1: collect per-event WPA. The half-ending PA (outs_after >= 27)
    # transitions to the next half, so we anchor it to the final WP of
    # the half — same `score_diff_after` but treating it as the start of
    # the opponent's half (bases 0, outs 0, batting_is_home flipped).
    events: list[dict] = []
    state_wpa_sq: dict[tuple, float] = defaultdict(float)
    state_wpa_n:  dict[tuple, int]   = defaultdict(int)

    def _half_end_wp(score_diff_after_from_batter: int, batting_is_home: int) -> float | None:
        # The half ended; flip perspective to the OTHER team's half opening.
        # Their score_diff_before = −score_diff_after_from_batter, fresh slate.
        flipped_home = 1 - batting_is_home
        flipped_sd   = -score_diff_after_from_batter
        wp_other = lookup_wp(wp_table, flipped_home, 0, 0, flipped_sd)
        if wp_other is None:
            return None
        # WP for the batting team that just ended = 1 − WP(opposing half).
        return 1.0 - wp_other

    for r in rows:
        batting_is_home = 1 if int(r["team_id"]) == int(r["home_team_id"]) else 0
        wp_before = lookup_wp(
            wp_table, batting_is_home,
            r["outs_before"], r["bases_before"], r["score_diff_before"],
        )
        if r["outs_after"] is not None and int(r["outs_after"]) >= 27:
            wp_after = _half_end_wp(int(r["score_diff_after"] or 0), batting_is_home)
        else:
            wp_after = lookup_wp(
                wp_table, batting_is_home,
                r["outs_after"], r["bases_after"], r["score_diff_after"],
            )
        if wp_before is None or wp_after is None:
            continue
        wpa = float(wp_after - wp_before)
        state_key = (
            batting_is_home,
            _outs_coarse(r["outs_before"]),
            int(r["bases_before"]),
            _clip_score_diff(r["score_diff_before"]),
        )
        events.append({
            "batter_id":  r["batter_id"],
            "pitcher_id": r["pitcher_id"],
            "wpa":        wpa,
            "game_id":    r["game_id"],
            "state_key":  state_key,
        })
        state_wpa_sq[state_key] += wpa * wpa
        state_wpa_n [state_key] += 1

    # Per-state leverage = stdev of |WPA| at that state, league-normalized
    # so the mean LI across all PAs = 1.0. We use sqrt(E[WPA^2]) (RMS
    # WPA) as the swing magnitude — equivalent to stdev around zero,
    # which is the right baseline since the typical state has E[WPA] ≈ 0.
    state_li_raw: dict[tuple, float] = {}
    total_rms_weighted = 0.0
    total_n = 0
    for k, n in state_wpa_n.items():
        rms = (state_wpa_sq[k] / n) ** 0.5 if n else 0.0
        state_li_raw[k] = rms
        total_rms_weighted += rms * n
        total_n += n
    mean_rms = (total_rms_weighted / total_n) if total_n else 0.0
    li_norm  = mean_rms if mean_rms > 0 else 1.0
    state_li: dict[tuple, float] = {
        k: (v / li_norm) for k, v in state_li_raw.items()
    }

    # Pass 2: aggregate by player.
    by_batter:  dict[int, dict] = defaultdict(lambda: {"wpa": 0.0, "li_sum": 0.0, "n_pa": 0})
    by_pitcher: dict[int, dict] = defaultdict(lambda: {"wpa": 0.0, "li_sum": 0.0, "n_pa": 0})

    # Stamp per-PA leverage onto each event.
    enriched: list[dict] = []
    for ev in events:
        li = state_li.get(ev["state_key"], 1.0)
        wpa = ev["wpa"]
        b = by_batter[ev["batter_id"]]
        b["wpa"]    += wpa
        b["li_sum"] += li
        b["n_pa"]   += 1
        if ev["pitcher_id"] is not None:
            p = by_pitcher[ev["pitcher_id"]]
            # Pitcher WPA is the negation of the batter's gain.
            p["wpa"]    -= wpa
            p["li_sum"] += li
            p["n_pa"]   += 1
        enriched.append({
            "game_id":    ev["game_id"],
            "batter_id":  ev["batter_id"],
            "pitcher_id": ev["pitcher_id"],
            "wpa":        wpa,
            "li":         li,
        })

    def _finalize(d: dict) -> dict:
        out = {}
        for pid, v in d.items():
            n = v["n_pa"]
            out[pid] = {
                "wpa":    round(v["wpa"], 3),
                "li_avg": round((v["li_sum"] / n) if n else 0.0, 3),
                "n_pa":   n,
            }
        return out

    # Top events for narrative surfaces — biggest |WPA| swings.
    enriched.sort(key=lambda e: abs(e["wpa"]), reverse=True)
    top_pa = enriched[:25]

    return {
        "wp_table_size": len(wp_table["wp"]),
        "n_games":       wp_table["n_games"],
        "by_batter":     _finalize(by_batter),
        "by_pitcher":    _finalize(by_pitcher),
        "top_pa":        top_pa,
        "li_norm":       round(li_norm, 4),
    }
