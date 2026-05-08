"""
Promotion / relegation engine for the tiered league config.

Used by configs whose `schedule_mode == "tiered"`. Reads the per-tier
final standings from the live DB and computes the team-tier reassignments
to apply between seasons:

  * Top N teams in each lower tier auto-promote one tier up.
  * Bottom M teams in each upper tier auto-relegate one tier down.
  * Optional relegation playoff: seeds 11 vs 12, loser plays 13; the
    loser of the second match is also relegated. So with N=2, M=1 and
    the playoff enabled, 2 teams move down per tier-edge to make room
    for the 2 promoted teams above (a balanced exchange).

The playoff is decided by a win-pct-weighted Bernoulli draw, NOT by
simulating real O27 games — intentional, so the demo doesn't depend on
the per-game sim being available at off-season time. Callers that want
an honest game-sim playoff can swap `_decide_playoff_match` later; the
shape of the public API doesn't change.

Public API:
  apply_promotion_relegation(config, *, rng_seed=...) -> ReassignmentReport

The report is a dict-shaped record of who moved where, suitable for
flashing to a UI or printing from the CLI. Persisting across seasons is
the caller's responsibility (this module updates teams.league/division
in place; archival of "team X was Premier in 2026, Galactic in 2027"
belongs in season_archive).
"""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Any


def _team_standings_by_tier(
    tier_order: list[str],
) -> dict[str, list[dict]]:
    """Return {tier_name: [team_row, ...]} ordered by win pct (desc) within
    each tier. Ties broken by wins desc, then losses asc, then team id asc
    so the seeding is deterministic given the underlying numbers."""
    from o27v2 import db

    rows = db.fetchall(
        "SELECT id, name, abbrev, league, division, wins, losses "
        "FROM teams ORDER BY league, wins DESC, losses ASC, id ASC"
    )
    by_tier: dict[str, list[dict]] = {tier: [] for tier in tier_order}
    for r in rows:
        tier = r["league"]
        if tier in by_tier:
            by_tier[tier].append(dict(r))
    return by_tier


def _win_pct(team: dict) -> float:
    games = (team.get("wins") or 0) + (team.get("losses") or 0)
    if games <= 0:
        return 0.5
    return team["wins"] / games


def _decide_playoff_match(
    a: dict, b: dict, rng: random.Random
) -> tuple[dict, dict]:
    """Return (winner, loser). Probability of `a` winning is its share of
    the (a, b) win-pct mass, with a 0.4–0.6 floor/ceiling so even a much
    worse team has live odds in a one-game knockout."""
    pa = _win_pct(a)
    pb = _win_pct(b)
    total = pa + pb
    if total <= 0:
        prob_a = 0.5
    else:
        prob_a = pa / total
    prob_a = max(0.4, min(0.6, prob_a))
    if rng.random() < prob_a:
        return a, b
    return b, a


def _resolve_relegation_playoff(
    standings: list[dict],
    seeds: list[int],
    rng: random.Random,
) -> tuple[list[dict], list[dict]]:
    """Run the bottom-of-table relegation playoff.

    `seeds` is the list of standings positions (1-indexed) entered into
    the playoff — typically [11, 12, 13]. Format: 11 vs 12, loser plays
    13; loser of the second match is the additional relegation. Returns
    (extra_relegations, playoff_log) where `extra_relegations` is the
    list of team-rows that lose their tier slot via the playoff (in
    addition to the auto-relegations) and `playoff_log` is a list of
    dicts describing each match for UI display."""
    if not seeds or len(seeds) < 2:
        return [], []

    log: list[dict] = []

    def _team_at_seed(seed: int) -> dict | None:
        idx = seed - 1
        if idx < 0 or idx >= len(standings):
            return None
        return standings[idx]

    a = _team_at_seed(seeds[0])
    b = _team_at_seed(seeds[1])
    if a is None or b is None:
        return [], log

    winner1, loser1 = _decide_playoff_match(a, b, rng)
    log.append({
        "round":  1,
        "matchup": f"#{seeds[0]} {a['name']} vs #{seeds[1]} {b['name']}",
        "winner": winner1["name"],
        "loser":  loser1["name"],
    })

    extras: list[dict] = []
    if len(seeds) >= 3:
        c = _team_at_seed(seeds[2])
        if c is not None:
            winner2, loser2 = _decide_playoff_match(loser1, c, rng)
            log.append({
                "round":  2,
                "matchup": f"#{seeds[1]} loser ({loser1['name']}) vs #{seeds[2]} {c['name']}",
                "winner": winner2["name"],
                "loser":  loser2["name"],
            })
            extras.append(loser2)
    else:
        # No second-round opponent → loser of round 1 is the lone extra.
        extras.append(loser1)

    return extras, log


def apply_promotion_relegation(
    config: dict,
    *,
    rng_seed: int = 0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compute and (unless dry_run) apply tier reassignments after a
    completed season.

    Reads `tier_order` (top → bottom) and `promotion_relegation` block
    from the config. Returns a report dict describing every move plus
    the relegation-playoff log.

    DB writes (when dry_run=False) update `teams.league` and
    `teams.division` for moved teams. Wins / losses are NOT zeroed
    here — that's an off-season step the caller drives separately.
    """
    from o27v2 import db

    tier_order = list(config.get("tier_order") or config.get("leagues") or [])
    if len(tier_order) < 2:
        raise ValueError(
            "promotion/relegation requires tier_order with at least 2 tiers."
        )
    pr_cfg = dict(config.get("promotion_relegation") or {})
    n_promote = int(pr_cfg.get("auto_promote_top_n", 2))
    n_auto_relegate = int(pr_cfg.get("auto_relegate_bottom_n", 1))
    use_playoff = bool(pr_cfg.get("playoff_relegation", True))
    playoff_seeds = list(pr_cfg.get("playoff_seeds") or [11, 12, 13])

    rng = random.Random(rng_seed)
    by_tier = _team_standings_by_tier(tier_order)

    moves: list[dict] = []   # one entry per team that changes tier
    playoff_logs: dict[str, list[dict]] = {}

    # Compute the relegation set (auto + playoff loser) per tier first;
    # promotions out of the tier below are paired against this.
    relegated_per_tier: dict[str, list[dict]] = defaultdict(list)
    promoted_per_tier:  dict[str, list[dict]] = defaultdict(list)

    for upper_tier in tier_order[:-1]:
        # Lower tier sits one slot down in tier_order.
        idx = tier_order.index(upper_tier)
        lower_tier = tier_order[idx + 1]
        upper_standings = by_tier.get(upper_tier, [])
        lower_standings = by_tier.get(lower_tier, [])

        # Auto-relegations: bottom N of upper tier.
        auto_rel = upper_standings[-n_auto_relegate:] if n_auto_relegate > 0 else []
        relegated_set = {t["id"] for t in auto_rel}

        # Relegation playoff (seeds 11/12/13 by default).
        if use_playoff:
            extras, log = _resolve_relegation_playoff(
                upper_standings, playoff_seeds, rng
            )
            playoff_logs[upper_tier] = log
            for t in extras:
                if t["id"] not in relegated_set:
                    auto_rel = [*auto_rel, t]
                    relegated_set.add(t["id"])

        relegated_per_tier[upper_tier] = auto_rel

        # Promotions: top N of lower tier.
        promoted = lower_standings[:n_promote]
        promoted_per_tier[lower_tier] = promoted

    # Build the move list. Each promoted team moves up; each relegated
    # team moves down.
    for tier in tier_order:
        idx = tier_order.index(tier)
        for t in promoted_per_tier.get(tier, []):
            new_tier = tier_order[idx - 1] if idx - 1 >= 0 else tier
            if new_tier != tier:
                moves.append({
                    "team_id":  t["id"],
                    "name":     t["name"],
                    "abbrev":   t.get("abbrev", ""),
                    "from":     tier,
                    "to":       new_tier,
                    "reason":   "promotion",
                })
        for t in relegated_per_tier.get(tier, []):
            new_tier = tier_order[idx + 1] if idx + 1 < len(tier_order) else tier
            if new_tier != tier:
                moves.append({
                    "team_id":  t["id"],
                    "name":     t["name"],
                    "abbrev":   t.get("abbrev", ""),
                    "from":     tier,
                    "to":       new_tier,
                    "reason":   "relegation",
                })

    if not dry_run and moves:
        for m in moves:
            db.execute(
                "UPDATE teams SET league = ?, division = ? WHERE id = ?",
                (m["to"], m["to"], m["team_id"]),
            )

    # Per-tier summary for the UI.
    summary = []
    for tier in tier_order:
        idx = tier_order.index(tier)
        ups   = sum(1 for m in moves if m["from"] == tier and m["reason"] == "promotion")
        downs = sum(1 for m in moves if m["from"] == tier and m["reason"] == "relegation")
        summary.append({
            "tier":     tier,
            "promoted_out":   ups,
            "relegated_out":  downs,
        })

    return {
        "tier_order":    tier_order,
        "moves":         moves,
        "playoff_logs":  playoff_logs,
        "summary":       summary,
        "dry_run":       dry_run,
    }
