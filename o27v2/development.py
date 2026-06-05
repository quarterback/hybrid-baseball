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
from o27v2.archetypes import (
    classify_position_player,
    classify_roster_slot,
    is_hit_capable,
    is_run_capable,
    is_two_way,
    encode_field_positions,
)


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


# Per-attribute age-curve modifiers. The base _mu_age sets the season's
# growth-or-decline pressure; these multipliers reshape the curve per
# attribute so different ratings peak and fade on different schedules.
# Real-baseball ordering — speed and power crater first, contact holds
# through the early 30s, plate-discipline is the last to go. On the
# pitcher side this is what makes Stamina the workhorse moat: the
# decline magnitude is dampened, so a high-Stamina arm in his mid-30s
# keeps the durability that's structurally most valuable in O27.
#   growth   — multiplier on positive μ_age (younger players)
#   decline  — multiplier on negative μ_age (older players)
# 1.0 = follow the base curve; > 1 = bigger swings; < 1 = stickier.
_ATTR_AGE_PROFILE: dict[str, dict[str, float]] = {
    # Hitters
    "power":              {"growth": 1.20, "decline": 1.30},
    "contact":            {"growth": 0.90, "decline": 0.70},
    "eye":                {"growth": 0.80, "decline": 0.50},
    "speed":              {"growth": 1.10, "decline": 1.50},
    "baserunning":        {"growth": 1.00, "decline": 1.20},
    "run_aggressiveness": {"growth": 0.80, "decline": 0.80},
    "defense":            {"growth": 1.10, "decline": 1.30},
    "arm":                {"growth": 1.00, "decline": 1.20},
    "defense_infield":    {"growth": 1.10, "decline": 1.30},
    "defense_outfield":   {"growth": 1.10, "decline": 1.30},
    "defense_catcher":    {"growth": 0.90, "decline": 0.90},
    # Pitchers — Stamina decline is dampened (workhorse moat per README's
    # "career arcs are longer because sidearm/submarine" theme).
    "pitcher_skill":      {"growth": 1.10, "decline": 1.10},
    "command":            {"growth": 0.80, "decline": 0.50},
    "movement":           {"growth": 1.00, "decline": 1.00},
    "stamina":            {"growth": 0.90, "decline": 0.65},
    # Shared / legacy
    "skill":              {"growth": 1.00, "decline": 1.00},
}


def _mu_for_attr(attr: str, mu_total: float) -> float:
    """Apply the per-attribute profile to the season's base μ. Positive
    μ uses the growth multiplier; negative μ uses the decline multiplier.
    Attributes without an entry fall back to the unmodulated curve."""
    profile = _ATTR_AGE_PROFILE.get(attr)
    if profile is None or mu_total == 0:
        return mu_total
    factor = profile["growth"] if mu_total > 0 else profile["decline"]
    return mu_total * factor


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

# Catcher wear — usage-driven erosion ON TOP of the age curve. Catching is the
# sport's most physically taxing position (hockey/soccer-goalie style): the more
# a backstop is run out there over the 27-out arc, the faster his catching skill
# and arm erode season to season. Resisted by conditioning/character (work
# ethic, work habits, leadership) and a touch by his own catching technique.
# Applied to the PERSISTED catcher skills (defense_catcher, arm) — the ones the
# engine actually reads in a real game. Identity (no extra wear) for
# non-catchers and for catchers who barely caught (usage 0). NOTE: game_calling
# is not yet a DB column, so it can't be eroded here — see the AAR follow-up.
_CATCHER_WEAR_BASE         = -1.4   # grade pts/season at full starter usage
_CATCHER_WEAR_SIGMA        = 0.8    # season-to-season noise on the wear draw
_CATCHER_WEAR_RESIST_SCALE = 0.60   # work-ethic/habits/leadership resistance
_CATCHER_WEAR_SKILL_RESIST = 0.15   # better technique resists a little wear
_CATCHER_WEAR_ATTRS        = ("defense_catcher", "arm")
# Usage tiers by depth-chart rank among a team's catchers. The starter absorbs
# the bulk of the innings (most wear); the rotation/relief backups catch less.
_CATCHER_USAGE_STARTER     = 1.0
_CATCHER_USAGE_BACKUP      = 0.5
_CATCHER_USAGE_THIRD       = 0.3


# Attributes that follow the development curve. Speed and stamina
# decline with age in real life, so they're in here too. baserunning is
# a learned skill so it follows the same curve. Defense is included
# because in real baseball range / first-step / route-reading peaks
# young and declines with mobility.
_HITTER_DEV_ATTRS = (
    "skill", "speed", "contact", "power", "eye",
    "defense", "arm", "defense_infield", "defense_outfield", "defense_catcher",
    "game_calling",
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


# Per-season development-trajectory bias: a league's playing style shapes
# not just the talent it generates but how players grow within it. We reuse
# the same per-attribute _STYLE_PROFILES bundle (in 20-95 grade points) used
# at generation, scaled WAY down into a per-season μ nudge — so a Nippon-style
# arm gains command a touch faster than velocity year over year, and over a
# career the trajectories diverge. Kept small so it shapes, not dominates.
_STYLE_DEV_SCALE = 0.05   # ±12 grade-point profile → ±0.6 μ per season


def _style_dev_bias(style_profile: Optional[str]) -> dict[str, float]:
    """Map a league style-profile to a small per-attribute development μ
    nudge dict. `style_profile` is a preset key, a JSON custom-bias dict
    (authored in the builder), or empty. Empty/unknown → no bias."""
    if not style_profile:
        return {}
    prof = None
    if style_profile.startswith("{"):
        try:
            import json as _json
            prof = _json.loads(style_profile)
        except (ValueError, TypeError):
            prof = None
    if prof is None:
        try:
            from o27v2.league import _STYLE_PROFILES
        except Exception:
            return {}
        prof = _STYLE_PROFILES.get(style_profile)
    if not isinstance(prof, dict) or not prof:
        return {}
    return {attr: pts * _STYLE_DEV_SCALE for attr, pts in prof.items()}


def _develop_player(p: dict, org_strength: int, rng: _random.Random,
                    is_pitcher: bool,
                    style_dev: Optional[dict[str, float]] = None,
                    catcher_usage: float = 0.0) -> tuple[dict, int]:
    """Apply one season of development to a player. Returns
    (updated_attribute_dict, new_age). Caller writes back to DB.

    `style_dev` (optional) is a per-attribute μ nudge derived from the
    player's league style profile, so league culture imprints on career
    trajectories (see _style_dev_bias).

    `catcher_usage` (0.0–1.0) drives usage-based catcher wear on top of the age
    curve: the team's primary catcher (usage 1.0) erodes his catching skill and
    arm fastest, backups less. 0.0 = no catcher wear (non-catchers / FAs)."""
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
        # Per-attribute curve modulation: Power peaks earlier than
        # Contact, Speed falls off fastest, Stamina holds longest.
        mu_attr = _mu_for_attr(attr, mu_total)
        if style_dev:
            mu_attr += style_dev.get(attr, 0.0)
        delta = _draw_delta(rng, mu_attr, grit_mod=grit_mod)
        new_val = round(cur + delta)
        # Clamp to [20, 95] — Elite+ tier is reachable here, that's
        # the whole point of the dev engine.
        updated[attr] = max(20, min(95, new_val))

    # Catcher wear — usage-driven erosion applied ON TOP of the age draw above.
    # Only for catchers who actually caught (catcher_usage > 0). Resisted by
    # conditioning/character (work ethic, work habits, leadership) and a touch
    # by the catcher's own technique; a fragile, low-character everyday backstop
    # wears out years before a durable, high-character one.
    if catcher_usage > 0 and not is_pitcher:
        we = float(p.get("work_ethic") or 50)
        wh = float(p.get("work_habits") or 50)
        ld = float(p.get("leadership") or 50)
        cond_dev = ((we + wh + ld) / 3.0 - 50.0) / 45.0   # ~[-0.67, +0.67]
        resist = 1.0 - cond_dev * _CATCHER_WEAR_RESIST_SCALE
        resist = max(0.40, min(1.80, resist))
        skill_dev = (float(p.get("defense_catcher") or 50) - 50.0) / 45.0
        resist *= max(0.70, 1.0 - skill_dev * _CATCHER_WEAR_SKILL_RESIST)
        for attr in _CATCHER_WEAR_ATTRS:
            if attr in updated:
                wear = _CATCHER_WEAR_BASE * catcher_usage * resist
                wear += rng.gauss(0.0, _CATCHER_WEAR_SIGMA)
                updated[attr] = max(20, min(95, round(updated[attr] + wear)))

    # Phase 5e — work-ethic / work-habits offseason re-roll with age
    # locks. Both use a "soft re-roll": new value blends 60% of the
    # carry-forward with 40% of a fresh tier-roll, giving year-over-
    # year persistence without making either attribute static. After
    # the lock age, the attribute is frozen at its last carried value.
    cur_ethic = p.get("work_ethic")
    cur_habits = p.get("work_habits")
    if cur_ethic is not None:
        if new_age < 30:
            fresh = _fresh_ethic_roll(rng)
            updated["work_ethic"] = max(20, min(80, round(0.6 * cur_ethic + 0.4 * fresh)))
        # else: locked — leave as-is
    if cur_habits is not None:
        if new_age < 27:
            fresh = _fresh_ethic_roll(rng)
            updated["work_habits"] = max(20, min(80, round(0.6 * cur_habits + 0.4 * fresh)))
        # else: locked

    # Reset habit_cup to neutral (0.5) at season start regardless.
    # Stored as REAL so cast to float.
    updated["habit_cup"] = 0.5

    # Re-derive position-player archetype against post-development grades.
    # Pitchers and jokers carry their own archetype dimension elsewhere
    # and the classifier already short-circuits on them.
    if not is_pitcher and not p.get("is_joker"):
        merged = {**p, **updated}
        updated["archetype"] = classify_position_player(merged)
        # Re-derive substitution-economy role tags off the same merged
        # post-development view so a player who drifted past a defense
        # threshold lands in the right deployment slot for the new season.
        updated["role_hit"]       = 1 if is_hit_capable(merged) else 0
        updated["role_run"]       = 1 if is_run_capable(merged) else 0
        updated["role_two_way"]   = 1 if is_two_way(merged) else 0
        updated["role_field_pos"] = encode_field_positions(merged)
        updated["roster_slot"]    = classify_roster_slot(merged)

    return updated, new_age


def _fresh_ethic_roll(rng: _random.Random) -> int:
    """Tier-rolled grade clamped to [20, 80] for the offseason work-
    ethic / work-habits re-rolls. Mirrors the seed-time roll shape."""
    from o27v2.league import _TALENT_TIERS
    r = rng.random()
    cumulative = 0.0
    for prob, lo, hi in _TALENT_TIERS:
        cumulative += prob
        if r < cumulative:
            seed_lo = min(lo, 80)
            seed_hi = min(hi, 80)
            return rng.randint(seed_lo, seed_hi)
    lo, hi = _TALENT_TIERS[-1][1], _TALENT_TIERS[-1][2]
    return rng.randint(min(lo, 80), min(hi, 80))


def _catcher_season_usage(team_id: int) -> dict:
    """Real in-season catcher usage from the game log: {player_id: usage 0..1}
    where usage = games this player started at catcher (game_batter_stats rows
    with game_position='C') / his team's games played. The everyday catcher
    lands near 1.0, backups lower — whatever the manager's rotation produced.
    Empty dict when no games are logged (fresh league) so the caller falls back
    to a depth-chart proxy. Defensive against a missing game_batter_stats table."""
    try:
        gp = db.fetchall(
            "SELECT COUNT(DISTINCT game_id) AS g FROM game_batter_stats "
            "WHERE team_id = ?", (team_id,))
        team_games = int(gp[0]["g"]) if gp and gp[0].get("g") else 0
        if team_games <= 0:
            return {}
        caught = db.fetchall(
            "SELECT player_id, COUNT(DISTINCT game_id) AS gc "
            "FROM game_batter_stats "
            "WHERE team_id = ? AND game_position = 'C' "
            "GROUP BY player_id", (team_id,))
    except Exception:
        return {}
    usage: dict = {}
    for r in caught:
        pid = r.get("player_id")
        gc = int(r.get("gc") or 0)
        if pid is not None and gc > 0:
            usage[pid] = min(1.0, gc / team_games)
    return usage


def develop_players_for_team(team_id: int, org_strength: Optional[int],
                             rng: _random.Random,
                             style_dev: Optional[dict[str, float]] = None) -> int:
    """Run the dev pass for every player on a team. Returns the count
    of players updated.

    `org_strength` drives the μ_org development bonus. Pass `None` (the
    normal path) to derive it live from the team's current roster via
    `league.compute_org_strength` — the same roster+bench grade shown on
    the team page — so what you see is what grows the prospects. An
    explicit int is honoured as an override (used by tests).

    `style_dev` (optional) is the league's development-trajectory bias,
    applied to every player on the team so league culture shapes careers."""
    rows = db.fetchall("SELECT * FROM players WHERE team_id = ?", (team_id,))
    if org_strength is None:
        from o27v2.league import compute_org_strength
        org_strength = compute_org_strength(rows)
    # Catcher usage map — drives usage-based catcher erosion below. Catching is
    # a wear position; the more a backstop actually caught this season, the more
    # his skills erode. Use REAL in-season usage (games started at C / team
    # games played, from the game log) so how a manager rotates a 3-4 catcher
    # corps genuinely shapes careers — ride your starter every day and he wears
    # fast; spread the load and the corps lasts. Falls back to a depth-chart
    # proxy only when no season has been logged yet (fresh league rollover).
    usage_by_id: dict = _catcher_season_usage(team_id)
    if not usage_by_id:
        catchers = [r for r in rows if r.get("position") == "C"]
        catchers.sort(key=lambda r: (r.get("defense_catcher") or 0), reverse=True)
        for rank, c in enumerate(catchers):
            usage_by_id[c["id"]] = (
                _CATCHER_USAGE_STARTER if rank == 0
                else _CATCHER_USAGE_BACKUP if rank == 1
                else _CATCHER_USAGE_THIRD)
    n = 0
    for p in rows:
        is_pitcher = bool(p.get("is_pitcher"))
        updated, new_age = _develop_player(p, org_strength, rng, is_pitcher,
                                           style_dev=style_dev,
                                           catcher_usage=usage_by_id.get(p["id"], 0.0))
        if not updated and (p.get("age") or 0) == new_age:
            continue
        cols   = list(updated.keys()) + ["age"]
        values = [updated[k] for k in updated.keys()] + [new_age, p["id"]]
        sql = "UPDATE players SET " + ", ".join(f"{c} = ?" for c in cols) + " WHERE id = ?"
        db.execute(sql, tuple(values))
        n += 1
    # Re-derive the crew roles now that a season's worth of development /
    # decay has reshuffled the staff — an arm who lost Stamina to age slides
    # out of the Helms tier, a riser climbs the depth chart. Roles are always
    # relative to the current staff. (o27v2/rotation.py)
    from o27v2 import rotation as _rotation
    _rotation.assign_roles_for_team(team_id)
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

    teams = db.fetchall(
        "SELECT id, abbrev, org_strength, COALESCE(style_profile,'') AS style_profile FROM teams")
    team_summary: dict[str, int] = {}
    _bias_cache: dict[str, dict[str, float]] = {}
    for t in teams:
        sp = t.get("style_profile") or ""
        if sp not in _bias_cache:
            _bias_cache[sp] = _style_dev_bias(sp)
        # org_strength=None → develop_players_for_team derives the live
        # roster+bench grade itself, the same figure the team page shows.
        n = develop_players_for_team(
            t["id"], None, rng, style_dev=_bias_cache[sp])
        team_summary[t["abbrev"]] = n

    fa_count = develop_free_agents(rng)
    # The persisted teams.org_strength column no longer drives development
    # (that now reads the live roster grade). It survives as a front-office
    # knob — auction bidding discipline, FA/trade behaviour — so we keep
    # rolling it forward on win% for those AI consumers.
    org_moves = update_org_strengths(rng)

    from o27v2.front_office import drift_fo_strategies
    fo_moves = drift_fo_strategies(rng)

    return {
        "season":         season,
        "teams_developed":  len(team_summary),
        "players_developed_by_team": team_summary,
        "free_agents_developed":      fa_count,
        "org_moves":      [
            {"team_id": tid, "old": old, "new": new}
            for tid, (old, new) in org_moves.items()
        ],
        "fo_moves":       [
            {"team_id": tid, "old": old, "new": new}
            for tid, (old, new) in fo_moves.items()
        ],
    }
