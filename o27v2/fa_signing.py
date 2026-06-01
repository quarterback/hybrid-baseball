"""
Free-agent signing round — non-destructive league-wide FA pickup.

Different from `auction.apply_auction` in three important ways:

  1. Doesn't touch existing rosters. The auction wipes everyone down
     to keepers and re-allocates the league pool from scratch — useful
     for a clean off-season reset, but destructive if you just want
     the new graduating class to land on teams. This module signs
     ONLY the FA pool (team_id IS NULL); rostered players are left
     alone.

  2. Phased, by `scope`:
        'prospects' — players freshly-signed from the college tier,
                      identified via college_players.signed_pro_player_id
                      backlink. Runs first by convention so the new
                      class clears before the older FA pool gets worked.
        'existing'  — every other FA (no college backlink). The
                      veteran market.
        'all'       — both rounds back-to-back: prospects first,
                      then existing.

  3. Per-team need + valuation, but no full auction-purse model.
     Teams keep signing FAs until they hit ROSTER_TARGET active
     players or run out of pool. Each pick uses the auction module's
     `_team_valuation_noisefree` so the same star-bias /
     position-need / aggression personality drives decisions.

The output report mirrors apply_auction's so a UI can reuse the
existing auction-result tables without much surgery.
"""
from __future__ import annotations

import random
from typing import Any

from o27v2 import db
from o27v2 import auction as _au


# Active roster target — sourced from the auction module so the two
# modules stay in lockstep (auction.ROSTER_TARGET = 34 active; +13
# reserve = 47 total). The old hard-coded 25 here was stale and
# capped FA signings short of the real active-roster size.
ROSTER_TARGET = _au.ROSTER_TARGET


def _fa_pool(scope: str) -> list[dict]:
    """Free-agent player rows filtered by signing scope.

    The prospects vs existing split depends on the college_players
    table. If that table doesn't exist (a pro-only save), we collapse
    everything to 'existing' since no players have a college backlink."""
    has_college = bool(db.fetchone(
        "SELECT 1 AS x FROM sqlite_master "
        "WHERE type='table' AND name='college_players'"
    ))
    if has_college:
        base_sql = (
            "SELECT p.* FROM players p "
            " LEFT JOIN college_players cp ON cp.signed_pro_player_id = p.id "
            "WHERE p.team_id IS NULL"
        )
        if scope == "prospects":
            sql = base_sql + " AND cp.id IS NOT NULL"
        elif scope == "existing":
            sql = base_sql + " AND cp.id IS NULL"
        else:
            sql = base_sql
    else:
        # No college tier — every FA is "existing"; prospects scope
        # legitimately returns empty.
        if scope == "prospects":
            return []
        sql = "SELECT * FROM players WHERE team_id IS NULL"
    rows = db.fetchall(sql + " ORDER BY p.id" if has_college else sql + " ORDER BY id")
    return [dict(r) for r in rows]


def _team_active_count(team_id: int) -> int:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM players "
        "WHERE team_id = ? AND is_active = 1", (team_id,)
    )
    return (row or {}).get("n") or 0


def _resolve_season() -> int:
    """Best-effort season-number lookup; falls back to 1."""
    try:
        from o27v2.transactions import current_season
        return current_season()
    except Exception:
        return 1


def _all_teams() -> list[dict]:
    rows = db.fetchall(
        "SELECT id, name, abbrev, org_strength, "
        "       mgr_quick_hook, mgr_bullpen_aggression, mgr_joker_aggression "
        "FROM teams ORDER BY id"
    )
    return [dict(r) for r in rows]


def _run_one_round(scope: str, rng: random.Random) -> dict:
    """Sign one scope's pool. Iterates highest-overall first; each
    player attracts a noise-free valuation from every team with an
    open roster slot, top valuer wins.
    """
    pool = _fa_pool(scope)
    if not pool:
        return {"scope": scope, "signings": [], "remaining_in_pool": 0}

    pool.sort(key=lambda p: -_au._player_overall(p))

    teams = _all_teams()
    profiles = {t["id"]: _au._team_auction_profile(t) for t in teams}
    active_count = {t["id"]: _team_active_count(t["id"]) for t in teams}

    # Pull the active season + economy config so we can ask + deduct
    # against budgets. Falls back to no-budget mode if economy table
    # doesn't exist (legacy saves).
    season = _resolve_season()
    try:
        from o27v2 import economy as _econ
        _econ.init_schema()
        _econ.init_budgets(season)   # idempotent — only initializes new teams
        has_budgets = True
    except Exception:
        has_budgets = False

    signings: list[dict] = []
    tx_events: list[dict] = []
    for p in pool:
        # Bidders: teams that still have an open active roster slot
        # AND enough remaining budget to meet the player's asking price.
        if has_budgets:
            ask = _econ.player_ask(_au._player_overall(p), season)
            eligible = [t for t in teams
                        if active_count[t["id"]] < ROSTER_TARGET
                        and _econ.budget_for_team(t["id"], season)["remaining"] >= ask]
        else:
            ask = None
            eligible = [t for t in teams if active_count[t["id"]] < ROSTER_TARGET]
        if not eligible:
            continue   # nobody can afford this player today; leave in FA
        # Each eligible team computes a noise-free valuation; light
        # tie-breaking jitter so equal-valuing teams alternate winners.
        best_val = -1
        best_team = None
        for t in eligible:
            val = _au._team_valuation_noisefree(p, t["id"], profiles[t["id"]])
            jitter = rng.uniform(0.95, 1.05)
            val_j = int(val * jitter)
            if val_j > best_val:
                best_val, best_team = val_j, t
        # Sign price = max(ask, best valuation × bid_aggression budget cap)
        # — the player asks for `ask`; the winning team pays the higher
        # of ask or their internal valuation (clipped to remaining budget).
        if has_budgets:
            cap = _econ.max_bid(best_team["id"], season)
            price = max(int(ask or 0), min(best_val, cap))
            ok = _econ.deduct(best_team["id"], season, price)
            if not ok:
                # Shouldn't happen — pre-filter already gated affordability,
                # but be defensive in case of race.
                continue
        else:
            price = best_val
        # Sign the player to best_team
        db.execute(
            "UPDATE players SET team_id = ?, is_active = 1 WHERE id = ?",
            (best_team["id"], p["id"]),
        )
        active_count[best_team["id"]] += 1
        signings.append({
            "player_id":  p["id"],
            "player_name": p["name"],
            "team_id":    best_team["id"],
            "team_abbrev": best_team["abbrev"],
            "valuation":  best_val,
            "ask":        ask,
            "price":      price,
            "overall":    _au._player_overall(p),
        })
        # Queue a transaction event — scope tagging in the type so
        # prospects vs existing FAs can be filtered separately.
        et = "prospect_sign" if scope == "prospects" else "fa_sign"
        detail = (f"Signed from FA pool · ƒ{price}"
                  + (f" (ask ƒ{ask})" if ask else ""))
        tx_events.append({
            "event_type": et,
            "team_id":    best_team["id"],
            "player_id":  p["id"],
            "detail":     detail,
        })

    # Emit transactions for the round.
    if tx_events:
        from o27v2.transactions import log_many, current_season
        from datetime import date as _date
        log_many(current_season(), _date.today().isoformat(), tx_events)

    remaining = sum(1 for p in pool
                    if p["id"] not in {s["player_id"] for s in signings})
    return {"scope": scope, "signings": signings,
            "remaining_in_pool": remaining,
            "teams_full": sum(1 for c in active_count.values() if c >= ROSTER_TARGET)}




def run_signing_round(*, scope: str = "all",
                      rng_seed: int = 0) -> dict[str, Any]:
    """Run an FA signing round across the named scope.

    'all' splits into prospects → existing in two passes so the new
    college class gets first crack at empty slots before the veteran
    market clears.

    Returns:
      {
        "rounds": [
          {"scope": "prospects", "signings": [...], "remaining_in_pool": N},
          {"scope": "existing",  "signings": [...], "remaining_in_pool": N},
        ],
        "total_signed": N,
      }
    """
    rng = random.Random(rng_seed ^ 0x_F_A_5_1_6_E_E if False else
                        (rng_seed ^ 0xFA1517E))   # "FA SIGN"
    if scope == "all":
        rounds = [_run_one_round("prospects", rng),
                  _run_one_round("existing",  rng)]
    else:
        rounds = [_run_one_round(scope, rng)]
    total = sum(len(r["signings"]) for r in rounds)
    return {"rounds": rounds, "total_signed": total}
