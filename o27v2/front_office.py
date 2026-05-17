"""
Front-office personas.

Each team has a persistent front-office identity that drives trade behavior
across the season — distinct from the in-game manager persona in
`managers.py`. Where managers decide pitching changes and pinch hits, the
front office decides who to trade, who to chase, and how much to overpay
for a star.

Stored on the `teams` table; re-rolled per league seed; drifts year-over-year
based on the team's record (see `drift_fo_strategies`).

Columns owned here:
  fo_strategy        — one of 'contend' / 'win_now' / 'rebuild' /
                       'develop' / 'balanced'. Drives motivation weighting
                       inside trades.py.
  fo_aggression      — [0..1] scalar; multiplies per-tick trade initiation
                       probability. High-aggression FOs trade twice as often.
  fo_archetype_bias  — '' / 'power' / 'speed' / 'contact'. When set, the
                       FO overvalues incoming players matching the archetype
                       (acceptance threshold gets a +15% boost on those).
  fo_losing_streak   — current consecutive losses (read-only; maintained
                       inside trades._load_teams_with_fo()).
  fo_last_trade_date — bookkeeping for once-per-date trade gating.
"""
from __future__ import annotations

import random
from typing import Optional


STRATEGY_KEYS = ["contend", "win_now", "rebuild", "develop", "balanced"]

# Per-30-teams distribution. Sums to 30; renormalized per league size.
STRATEGY_DIST: dict[str, int] = {
    "win_now":  4,
    "contend":  5,
    "balanced": 8,
    "develop":  8,
    "rebuild":  5,
}

# Most teams have no archetype bias. The 4 archetype-biased options are
# weighted 6:1:1:1 in favor of "no bias".
ARCHETYPE_BIAS_OPTIONS: tuple[str, ...] = ("", "power", "speed", "contact")
_ARCHETYPE_BIAS_WEIGHTS: tuple[int, ...] = (6, 1, 1, 1)


def _weighted_pick_from_dist(rng: random.Random, dist: dict[str, int]) -> str:
    keys    = list(dist.keys())
    weights = list(dist.values())
    return rng.choices(keys, weights=weights, k=1)[0]


def roll_fo(rng: random.Random) -> dict:
    """Roll a front-office persona for one team. Returns a dict with the
    three columns ready to insert/update on the `teams` table."""
    strategy   = _weighted_pick_from_dist(rng, STRATEGY_DIST)
    aggression = max(0.0, min(1.0, rng.gauss(0.5, 0.2)))
    bias       = rng.choices(ARCHETYPE_BIAS_OPTIONS, weights=_ARCHETYPE_BIAS_WEIGHTS, k=1)[0]
    return {
        "fo_strategy":       strategy,
        "fo_aggression":     round(aggression, 3),
        "fo_archetype_bias": bias,
    }


# Drift table: (current_strategy, win_pct_bucket) -> new_strategy.
# Bucket labels: 'good' (>=.550), 'ok' (.470-.550), 'bad' (<.470).
_DRIFT_TABLE: dict[tuple[str, str], str] = {
    ("rebuild",  "good"): "develop",
    ("develop",  "good"): "contend",
    ("contend",  "good"): "win_now",
    ("balanced", "good"): "contend",

    ("win_now",  "bad"):  "balanced",
    ("contend",  "bad"):  "develop",
    ("balanced", "bad"):  "rebuild",
    ("develop",  "bad"):  "rebuild",
}


def _wp_bucket(win_pct: float) -> str:
    if win_pct >= 0.550:
        return "good"
    if win_pct < 0.470:
        return "bad"
    return "ok"


def drift_fo_strategies(rng: random.Random) -> dict[int, tuple[str, str]]:
    """End-of-season drift pass. For each team, look at its W-L record and
    move the FO strategy along the drift table. 25% of would-be moves are
    blocked (sticky GM) so the league doesn't oscillate every season.

    Returns {team_id: (old_strategy, new_strategy)} for the teams that
    actually changed (callers can log these as offseason news items).
    """
    from o27v2 import db
    moves: dict[int, tuple[str, str]] = {}
    teams = db.fetchall("SELECT id, fo_strategy, wins, losses FROM teams")
    for t in teams:
        wins   = t["wins"]   if t["wins"]   is not None else 0
        losses = t["losses"] if t["losses"] is not None else 0
        if wins + losses == 0:
            continue
        wp = wins / (wins + losses)
        cur = t["fo_strategy"] or "balanced"
        new = _DRIFT_TABLE.get((cur, _wp_bucket(wp)), cur)
        if new == cur:
            continue
        if rng.random() < 0.25:
            continue   # sticky GM
        db.execute("UPDATE teams SET fo_strategy = ? WHERE id = ?", (new, t["id"]))
        moves[t["id"]] = (cur, new)
    return moves


def strategy_label(key: Optional[str]) -> str:
    return {
        "contend":  "Contender",
        "win_now":  "Win-Now",
        "rebuild":  "Rebuilding",
        "develop":  "Developing",
        "balanced": "Balanced",
    }.get(key or "balanced", "Balanced")
