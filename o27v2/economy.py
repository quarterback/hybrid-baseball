"""
League economy — fantasy-sports-style fixed per-team budgets +
configurable player-demand and bid-aggression knobs.

Replaces ad-hoc per-auction purse math. The model:

  league_economy   — one row per season. Knobs the user sets in the
                     UI before the draft:
                        budget_per_team — default season budget each
                                          team gets to spend on signings
                        demand_scale    — multiplier on player asking
                                          prices (1.0 = baseline; >1
                                          inflates demand, <1 cools)
                        bid_aggression  — multiplier on how hard teams
                                          chase players (interacts with
                                          remaining budget — teams will
                                          stretch to bid_aggression of
                                          their cap on a target)

  team_budgets     — per (season, team_id) total + spent. Spent
                     accumulates from signings; reset at rollover.

Spend paths (auction + fa_signing) read remaining budget and refuse to
bid above it. Player asks scale with overall AND with demand_scale, so
a high-demand league produces big numbers and a low-demand league lets
mediocre teams compete for elite talent.
"""
from __future__ import annotations

from o27v2 import db


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_ECONOMY = """
CREATE TABLE IF NOT EXISTS league_economy (
    season           INTEGER PRIMARY KEY,
    budget_per_team  INTEGER NOT NULL DEFAULT 100000,
    demand_scale     REAL    NOT NULL DEFAULT 1.0,
    bid_aggression   REAL    NOT NULL DEFAULT 1.0,
    updated_at       TEXT    DEFAULT CURRENT_TIMESTAMP
)
"""

_SCHEMA_BUDGETS = """
CREATE TABLE IF NOT EXISTS team_budgets (
    season       INTEGER NOT NULL,
    team_id      INTEGER NOT NULL REFERENCES teams(id),
    total_budget INTEGER NOT NULL,
    spent        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (season, team_id)
)
"""

DEFAULT_BUDGET_PER_TEAM = 100_000
DEFAULT_DEMAND_SCALE    = 1.0
DEFAULT_BID_AGGRESSION  = 1.0


def init_schema() -> None:
    db.execute(_SCHEMA_ECONOMY)
    db.execute(_SCHEMA_BUDGETS)


# ---------------------------------------------------------------------------
# Config get / set
# ---------------------------------------------------------------------------

def get_config(season: int) -> dict:
    """Return the league economy config for `season`. Creates and
    returns defaults if no row exists yet."""
    init_schema()
    row = db.fetchone(
        "SELECT * FROM league_economy WHERE season = ?", (season,)
    )
    if row:
        return dict(row)
    set_config(season, DEFAULT_BUDGET_PER_TEAM,
               DEFAULT_DEMAND_SCALE, DEFAULT_BID_AGGRESSION)
    return dict(db.fetchone(
        "SELECT * FROM league_economy WHERE season = ?", (season,)
    ))


def set_config(season: int,
               budget_per_team: int,
               demand_scale: float,
               bid_aggression: float) -> None:
    """Insert-or-update the per-season config knobs."""
    init_schema()
    db.execute(
        "INSERT INTO league_economy (season, budget_per_team, demand_scale, bid_aggression) "
        "VALUES (?,?,?,?) "
        "ON CONFLICT(season) DO UPDATE SET "
        "  budget_per_team = excluded.budget_per_team, "
        "  demand_scale    = excluded.demand_scale, "
        "  bid_aggression  = excluded.bid_aggression, "
        "  updated_at      = CURRENT_TIMESTAMP",
        (int(season), int(budget_per_team),
         float(demand_scale), float(bid_aggression)),
    )


# ---------------------------------------------------------------------------
# Per-team budgets
# ---------------------------------------------------------------------------

def init_budgets(season: int, *, force: bool = False) -> int:
    """Set each team's `total_budget` to the season's
    `budget_per_team`, with `spent` reset to 0. Skips teams already
    initialized for this season unless `force=True`.

    Returns the number of rows created or reset."""
    cfg = get_config(season)
    teams = db.fetchall("SELECT id FROM teams")
    n = 0
    for t in teams:
        existing = db.fetchone(
            "SELECT 1 AS x FROM team_budgets WHERE season=? AND team_id=?",
            (season, t["id"]),
        )
        if existing and not force:
            continue
        if existing and force:
            db.execute(
                "UPDATE team_budgets SET total_budget=?, spent=0 "
                "WHERE season=? AND team_id=?",
                (cfg["budget_per_team"], season, t["id"]),
            )
        else:
            db.execute(
                "INSERT INTO team_budgets (season, team_id, total_budget, spent) "
                "VALUES (?,?,?,0)",
                (season, t["id"], cfg["budget_per_team"]),
            )
        n += 1
    return n


def budget_for_team(team_id: int, season: int) -> dict:
    """Return {total, spent, remaining} for one team/season. Auto-
    creates the row at default budget if absent."""
    init_schema()
    row = db.fetchone(
        "SELECT total_budget, spent FROM team_budgets "
        "WHERE season=? AND team_id=?",
        (season, team_id),
    )
    if not row:
        cfg = get_config(season)
        db.execute(
            "INSERT INTO team_budgets (season, team_id, total_budget, spent) "
            "VALUES (?,?,?,0)",
            (season, team_id, cfg["budget_per_team"]),
        )
        return {"total": cfg["budget_per_team"], "spent": 0,
                "remaining": cfg["budget_per_team"]}
    total = row["total_budget"] or 0
    spent = row["spent"] or 0
    return {"total": total, "spent": spent, "remaining": total - spent}


def deduct(team_id: int, season: int, amount: int,
           *, allow_overdraft: bool = False) -> bool:
    """Subtract `amount` from `team_id`'s remaining budget. Returns
    True on success, False if it would overdraft and `allow_overdraft`
    is False (no-op in that case)."""
    b = budget_for_team(team_id, season)
    if amount > b["remaining"] and not allow_overdraft:
        return False
    db.execute(
        "UPDATE team_budgets SET spent = spent + ? "
        "WHERE season=? AND team_id=?",
        (int(amount), season, team_id),
    )
    return True


def all_budgets(season: int) -> list[dict]:
    """Per-team budget table for the season — used by the /economy UI."""
    return [dict(r) for r in db.fetchall(
        """SELECT tb.season, tb.team_id, t.name, t.abbrev, t.league,
                  tb.total_budget, tb.spent,
                  (tb.total_budget - tb.spent) AS remaining
             FROM team_budgets tb
             JOIN teams t ON t.id = tb.team_id
            WHERE tb.season = ?
            ORDER BY t.league, t.abbrev""",
        (season,),
    )]


# ---------------------------------------------------------------------------
# Player demand pricing
# ---------------------------------------------------------------------------

def player_ask(overall: int, season: int) -> int:
    """Suggested asking price for a player given their overall grade,
    scaled by the season's demand_scale.

    Curve:
      overall 30  → ~0.5%  of budget (deep org filler)
      overall 50  → ~2%    of budget (league-average bench)
      overall 65  → ~8%    of budget (everyday starter)
      overall 75  → ~20%   of budget (star)
      overall 80  → ~30%   of budget (top of the food chain)

    These are the BASELINE asks; demand_scale multiplies them
    league-wide so the user can tune economy heat.
    """
    cfg = get_config(season)
    budget = cfg["budget_per_team"]
    # Convex curve: each grade point above 50 ~20% more expensive
    # than the last; below 50 falls off fast.
    delta = max(-30, min(30, overall - 50))
    frac = 0.02 * (1.20 ** delta)
    frac = max(0.001, min(0.40, frac))
    return int(round(budget * frac * cfg["demand_scale"]))


# ---------------------------------------------------------------------------
# Bid resolution (used by fa_signing + auction integration)
# ---------------------------------------------------------------------------

def max_bid(team_id: int, season: int) -> int:
    """Hard cap on what a team can bid right now — remaining budget
    times the season's bid_aggression (capped at remaining)."""
    cfg = get_config(season)
    b = budget_for_team(team_id, season)
    cap = int(b["remaining"] * cfg["bid_aggression"])
    return min(b["remaining"], cap)
