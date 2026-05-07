"""
Phase 5 — Multi-season development engine.

Runs once per league at season rollover (the day the user advances to
the next season). Every player gets:
  1. age += 1
  2. Per-attribute development draw — gauss(μ_age + μ_org, σ=1.5),
     applied to each engine-relevant rating. Some attributes can push
     past 80 here; that's how Elite+ talent emerges over multiple
     seasons (the seed cap at 80 is intentional — see league.py).
  3. Small chance of a "bust" draw — gauss(-3, 1.5) — independent of
     age/org. Real baseball has unexplained collapses; this gives the
     league a few of them per season.

Org_strength rolls year-over-year via a bond-market formula:
    new_org = 0.7 × old_org + 0.3 × 50 + perf_bonus + N(0, 3)
where perf_bonus = (winpct - 0.500) × 60. Mean reversion + performance
feedback keeps dynasties from running forever and makes cellar orgs
climb back when they stop losing.

Pitcher decline is grit-modulated: high-grit veterans (>0.6 unit) age
slower (decline magnitude × 0.6); low-grit (<0.4) age faster (×1.4).
Identity at grit=0.5.
"""
from __future__ import annotations

import random as _random
from typing import Optional

from o27v2 import db


# ---------------------------------------------------------------------------
# Tuning knobs — exposed at module level so they're easy to find later.
# ---------------------------------------------------------------------------

# Age curves (μ_age). Sport's pitchers last longer than MLB's, so
# decline shifts later — meaningful drop only kicks in past 33.
def _mu_age(age: int) -> float:
    if age < 21:  return  2.5   # prospect breakout band
    if age < 26:  return  1.5   # prime growth
    if age < 31:  return  0.5   # late-prime, modest improvement
    if age < 34:  return  0.0   # plateau
    if age < 37:  return -0.7   # gentle decline
    return -1.8                  # sharp decline (37+)


# Org bonus (μ_org). Bracketed so the labels stay legible in the AAR.
def _mu_org(org_strength: int) -> float:
    if org_strength >= 75: return  1.0
    if org_strength >= 60: return  0.4
    if org_strength >= 45: return  0.0
    if org_strength >= 30: return -0.4
    return -1.0


_DEV_SIGMA          = 1.5      # base spread on the per-attribute draw
_BUST_PROB          = 0.015    # 1.5% chance per attribute per season
_BUST_MU            = -3.0     # mean of bust draw
_BUST_SIGMA         = 1.5      # spread of bust draw

# Org-strength bond-market roll.
_ORG_MEAN_REVERSION = 0.30
_ORG_PERF_SCALE     = 60.0
_ORG_NOISE_SIGMA    = 3.0


# Attributes that follow the development curve. Speed and stamina
# decline with age in real life, so they're in here too. baserunning is
# a learned skill so it follows the same curve. Defense is included
# because in real baseball range / first-step / route-reading peaks
# young and declines with mobility.
_HITTER_DEV_ATTRS = (
    "skill", "speed", "contact", "power", "eye",
    "defense", "arm", "defense_infield", "defense_outfield", "defense_catcher",
    "baserunning", "run_aggressiveness",
)

_PITCHER_DEV_ATTRS = (
    "skill",          # at-bat skill (rarely matters but keep symmetric)
    "pitcher_skill",  # Stuff
    "command", "movement", "stamina",
    "defense", "arm",
)


# ---------------------------------------------------------------------------
# Player-side development pass
# ---------------------------------------------------------------------------

def _grit_modulator(grit_unit: Optional[float]) -> float:
    """Pitchers with high grit (>0.6 unit) age slower; low grit (<0.4)
    ages faster. Identity at 0.5. Used to scale the magnitude of
    decline draws (post-31 ages where μ_age <= 0)."""
    if grit_unit is None:
        return 1.0
    g = max(0.0, min(1.0, float(grit_unit)))
    # Linear from 0.4 at grit=1.0 to 1.6 at grit=0.0. Identity at 0.5.
    return 1.0 - (g - 0.5) * 1.2


def _draw_delta(rng: _random.Random, mu: float, sigma: float = _DEV_SIGMA,
                grit_mod: float = 1.0) -> float:
    """One per-attribute development delta. Includes the bust event."""
    if rng.random() < _BUST_PROB:
        return rng.gauss(_BUST_MU, _BUST_SIGMA)
    # Decline magnitude scales by grit modulator; growth is unmodulated
    # (a high-grit kid doesn't develop faster — they just hold onto it
    # longer). So apply grit_mod only when μ is negative.
    if mu < 0:
        return rng.gauss(mu * grit_mod, sigma)
    return rng.gauss(mu, sigma)


def _develop_player(p: dict, org_strength: int, rng: _random.Random,
                    is_pitcher: bool) -> tuple[dict, int]:
    """Apply one season of development to a player. Returns
    (updated_attribute_dict, new_age). Caller writes back to DB."""
    new_age = (p.get("age") or 27) + 1
    mu_age  = _mu_age(new_age)
    mu_org  = _mu_org(org_strength)
    mu_total = mu_age + mu_org

    grit_unit = None
    if is_pitcher:
        # Grit is a derived/optional attribute on engine-side; here we
        # approximate from stamina since the DB doesn't persist grit.
        # (A real grit column lives only on the engine Player object.)
        grit_unit = (p.get("stamina") or 50) / 95.0

    grit_mod = _grit_modulator(grit_unit) if is_pitcher else 1.0
    attrs = _PITCHER_DEV_ATTRS if is_pitcher else _HITTER_DEV_ATTRS
    updated: dict[str, int] = {}
    for attr in attrs:
        cur = p.get(attr)
        if cur is None:
            continue
        delta = _draw_delta(rng, mu_total, grit_mod=grit_mod)
        new_val = round(cur + delta)
        # Clamp to [20, 95] — Elite+ tier is reachable here, that's
        # the whole point of the dev engine.
        updated[attr] = max(20, min(95, new_val))
    return updated, new_age


def develop_players_for_team(team_id: int, org_strength: int,
                             rng: _random.Random) -> int:
    """Run the dev pass for every player on a team. Returns the count
    of players updated."""
    rows = db.fetchall("SELECT * FROM players WHERE team_id = ?", (team_id,))
    n = 0
    for p in rows:
        is_pitcher = bool(p.get("is_pitcher"))
        updated, new_age = _develop_player(p, org_strength, rng, is_pitcher)
        if not updated and (p.get("age") or 0) == new_age:
            continue
        cols   = list(updated.keys()) + ["age"]
        values = [updated[k] for k in updated.keys()] + [new_age, p["id"]]
        sql = "UPDATE players SET " + ", ".join(f"{c} = ?" for c in cols) + " WHERE id = ?"
        db.execute(sql, tuple(values))
        n += 1
    return n


def develop_free_agents(rng: _random.Random) -> int:
    """Free agents develop too — their baseline org is league-average
    (50). They age, they grow or decline based on their age, and they
    can bust. No team bonus."""
    rows = db.fetchall("SELECT * FROM players WHERE team_id IS NULL")
    n = 0
    for p in rows:
        is_pitcher = bool(p.get("is_pitcher"))
        updated, new_age = _develop_player(p, 50, rng, is_pitcher)
        if not updated and (p.get("age") or 0) == new_age:
            continue
        cols   = list(updated.keys()) + ["age"]
        values = [updated[k] for k in updated.keys()] + [new_age, p["id"]]
        sql = "UPDATE players SET " + ", ".join(f"{c} = ?" for c in cols) + " WHERE id = ?"
        db.execute(sql, tuple(values))
        n += 1
    return n


# ---------------------------------------------------------------------------
# Org-strength bond-market roll
# ---------------------------------------------------------------------------

def _roll_new_org_strength(old: int, win_pct: float,
                           rng: _random.Random) -> int:
    """new = 0.7×old + 0.3×50 + (winpct - 0.5)×60 + N(0, 3)
    Clamped to [20, 95]. Mean-reverts toward 50 over time, rewards
    sustained winning, penalises sustained losing."""
    base = (1 - _ORG_MEAN_REVERSION) * old + _ORG_MEAN_REVERSION * 50
    perf = (win_pct - 0.5) * _ORG_PERF_SCALE
    shock = rng.gauss(0, _ORG_NOISE_SIGMA)
    return max(20, min(95, round(base + perf + shock)))


def update_org_strengths(rng: _random.Random) -> dict[int, tuple[int, int]]:
    """Roll new org_strength for every team. Returns
    {team_id: (old, new)}."""
    teams = db.fetchall("SELECT id, org_strength, wins, losses FROM teams")
    moves: dict[int, tuple[int, int]] = {}
    for t in teams:
        old = t["org_strength"] or 50
        g = (t["wins"] or 0) + (t["losses"] or 0)
        wp = (t["wins"] or 0) / max(1, g)
        new = _roll_new_org_strength(old, wp, rng)
        moves[t["id"]] = (old, new)
        db.execute("UPDATE teams SET org_strength = ? WHERE id = ?", (new, t["id"]))
    return moves


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def run_offseason(season: int, rng_seed: Optional[int] = None) -> dict:
    """Apply the full off-season pass: every team develops its players,
    free agents develop with no org bonus, then org_strengths roll
    forward via the stock-market formula. Returns a summary dict."""
    rng = _random.Random(rng_seed if rng_seed is not None else 0)

    teams = db.fetchall("SELECT id, abbrev, org_strength FROM teams")
    team_summary: dict[str, int] = {}
    for t in teams:
        n = develop_players_for_team(t["id"], t["org_strength"] or 50, rng)
        team_summary[t["abbrev"]] = n

    fa_count = develop_free_agents(rng)
    org_moves = update_org_strengths(rng)

    return {
        "season":         season,
        "teams_developed":  len(team_summary),
        "players_developed_by_team": team_summary,
        "free_agents_developed":      fa_count,
        "org_moves":      [
            {"team_id": tid, "old": old, "new": new}
            for tid, (old, new) in org_moves.items()
        ],
    }
