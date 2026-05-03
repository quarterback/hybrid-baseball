"""
O27v2 Configuration — Phase 8 tuning parameters and joker archetype definitions.

All v2-specific tunable values live here.  The base engine reads from o27/config.py;
this file is the authoritative source for Phase 8 archetype profiles, PA modifiers,
committee positions, home-field advantage, and PRD v2 validation targets.
league.py, batch.py, and sim.py import directly from this module.  The Phase 8
engine constants (joker triggers, pitcher-change thresholds) are defined in
o27/config.py (where manager.py reads them) and re-exported below so that this
file is the single reference document for all Phase 8 tunables.

=============================================================================
TUNING LOG — Phase 8 targets
=============================================================================

Targets (PRD v2):
  Metric                           Target
  ────────────────────────────── ──────────────
  Avg total runs/game             22–26
  Avg stays/game                  1.0–2.5
  Pitching changes / game         2–4 workhorse   (stints by the workhorse)
                                  6–10 committee  (stints by committee pitchers)
  Joker insertions / game         5–9
  Super-inning rate               <8%

--- Pass 0: Pre-Phase-8 baseline (500 games, v1 triggers, no archetypes) ---
  Avg runs/game       23.99  ✓ (v2 target 22–26)
  Avg stays/game       0.66  ! (v2 target 1.0–2.5)
  Joker insertions     ~1.2  ! (v2 target 5–9; old trigger: RISP+weak+close)
  Super-inning        4.60%  ✓ (v2 target <8%)
  Pitching changes     n/a   (no role tracking yet)

--- Pass 1–2: Phase 8 feature drop + initial tuning ---
  Root causes identified:
    • position player stay_a was ~0.42 (v1 foxes/bears used 0.03–0.10) → high stays
    • joker triggers fired in nearly every eligible AB → 10+ insertions per game
    • workhorse being re-called after pull (sorted above committee) → low committee changes

--- FINAL Phase 8 (power-first / DEFICIT=3 / 3 jokers per archetype): ALL PRD v2 TARGETS MET ---
  500 games, seeds 0–499
  Avg runs/game      25.97  ✓  (target 22–26)
  Avg stays/game      1.85  ✓  (target 1.0–2.5)
  Joker insertions    6.16  ✓  (target 5–9;  power 3.00 / speed 0.03 / contact 3.13)
  Workhorse changes   2.00  ✓  (target 2–4)
  Committee changes   6.00  ✓  (target 6–10)
  Super-inning        4.60% ✓  (target <8%)

Tuned config values (o27/config.py):
  JOKER_POWER_DEFICIT   = 3   (power fires only when batting team is down ≥ 3 runs)
  JOKER_POWER_OUTS_CEIL = 22  (power trigger disabled at or after this out count)
  JOKER_MAX_PER_HALF    = 9   (3 jokers × 3 archetypes; cap never prematurely blocks)

  o27v2/config.py:
  JOKERS_PER_ARCHETYPE  = 3   (3 physical jokers of each archetype per team = 9 total)

Design rationale:
  O27 is a single-inning game.  Visitors bat first at 0-0 and never trail, so
  with DEFICIT=3 power fires only for the home team.  To reach the 5-9/game
  insertion target with power limited to the home half, JOKERS_PER_ARCHETYPE=3
  ensures home can fire the power joker up to 3 times per game (once per
  physical joker, and the home team spends most of the bottom half trailing by
  ≥3).  Visitors fire contact jokers when RISP arises in the top half (also up
  to 3 times), yielding ~6 total insertions/game in the 5–9 target band.

Key architectural points:
  1. Eligibility: jokers_available + jokers_used_this_half (once per half per
     physical joker; §2.3).  No per-archetype fire counter.
  2. Trigger order: power (down ≥ JOKER_POWER_DEFICIT, dominates) → speed
     (corners: 1B+3B, 2B empty, 1 out) → contact (RISP).
  3. Strict no-fallback: each situation type served only by its archetype.
  4. DB migration: init_db() ALTERs the four new player columns onto existing
     DBs: archetype (TEXT), pitcher_role (TEXT), hard_contact_delta (REAL),
     hr_weight_bonus (REAL).

=============================================================================
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Phase 8: Joker archetype stat profiles
# Live source of truth — imported by o27v2/league.py generate_players().
# ---------------------------------------------------------------------------

ARCHETYPE_PROFILES: dict[str, dict] = {
    "power": {
        "skill_mu": 0.70, "skill_sig": 0.07,
        "speed_mu": 0.52, "speed_sig": 0.08,
        "stay_a_mu": 0.18, "stay_a_sig": 0.05,
        "cqt_mu": 0.38,   "cqt_sig": 0.06,
    },
    "speed": {
        "skill_mu": 0.60, "skill_sig": 0.07,
        "speed_mu": 0.82, "speed_sig": 0.07,
        "stay_a_mu": 0.16, "stay_a_sig": 0.05,
        "cqt_mu": 0.40,   "cqt_sig": 0.06,
    },
    "contact": {
        "skill_mu": 0.65, "skill_sig": 0.06,
        "speed_mu": 0.57, "speed_sig": 0.09,
        "stay_a_mu": 0.26, "stay_a_sig": 0.06,
        "cqt_mu": 0.52,   "cqt_sig": 0.06,
    },
}

# ---------------------------------------------------------------------------
# Phase 8: Per-archetype plate-appearance probability modifiers
#
# hard_contact_delta: added to CONTACT_HARD_BASE when computing contact quality
#   (symmetrically subtracted from CONTACT_WEAK_BASE).  Positive → more hard
#   contact events; negative → more weak contact events.
# hr_weight_bonus: added to the "hr" row weight in HARD_CONTACT when the batter
#   makes hard contact.  Positive → more home runs; negative → fewer HR, more XBH.
#
# Applied by prob.py via Player.hard_contact_delta / Player.hr_weight_bonus.
# Non-joker players have both fields default to 0.0.
# ---------------------------------------------------------------------------

ARCHETYPE_PA_MODIFIERS: dict[str, dict] = {
    "power": {
        "hard_contact_delta": +0.10,   # notably more hard contact for power hitters
        "hr_weight_bonus":    +0.08,   # significantly more HR within hard contact
    },
    "speed": {
        "hard_contact_delta": +0.02,   # slight line-drive tendency
        "hr_weight_bonus":     0.00,   # speed jokers use their legs, not power
    },
    "contact": {
        "hard_contact_delta": +0.06,   # good hard-contact rate (fewest weak)
        "hr_weight_bonus":    -0.05,   # fewer HR, more singles and doubles
    },
}

# ---------------------------------------------------------------------------
# Phase 8: Committee pitcher positions
# Live source of truth — imported by o27v2/league.py generate_players().
# ---------------------------------------------------------------------------

COMMITTEE_POSITIONS: frozenset[str] = frozenset({"CF", "SS", "2B"})

# ---------------------------------------------------------------------------
# Phase 8: Joker roster size
# Three jokers per archetype per team (9 jokers total per roster).
# The game engine resets jokers_used_this_half at the start of each
# half-inning, so each joker is eligible to bat once per half (§2.3).
# JOKER_MAX_PER_HALF (9) = JOKERS_PER_ARCHETYPE(3) × archetypes(3) so the
# cap never prematurely blocks a valid insertion.
# ---------------------------------------------------------------------------

JOKERS_PER_ARCHETYPE: int = 3

# ---------------------------------------------------------------------------
# Phase 8: Home-field advantage
# Applied to non-joker batters on the home team in both batch.py and sim.py.
# ---------------------------------------------------------------------------

HOME_ADVANTAGE_SKILL: float = 0.08

# ---------------------------------------------------------------------------
# Re-exports of Phase 8 engine constants
#
# These values are defined in o27/config.py (where manager.py reads them) and
# re-exported here so that o27v2/config.py is the single reference for all
# Phase 8 tunables.  To change a value, edit o27/config.py; the re-export
# keeps this file up-to-date automatically.
# ---------------------------------------------------------------------------

from o27.config import (          # noqa: E402
    JOKER_POWER_DEFICIT,          # team not ahead (deficit ≥ 0: tied or trailing) → power joker
    JOKER_POWER_OUTS_CEIL,        # power trigger disabled at or after this out
    JOKER_SPEED_OUTS,             # corners / lead-runner outs threshold
    JOKER_MAX_PER_HALF,           # cap: at most this many joker insertions per team per half
    WORKHORSE_CHANGE_BASE,        # workhorse BF threshold base
    WORKHORSE_CHANGE_SCALE,       # workhorse BF threshold scale factor
    COMMITTEE_CHANGE_BASE,        # committee BF threshold base
    COMMITTEE_CHANGE_SCALE,       # committee BF threshold scale factor
)

# ---------------------------------------------------------------------------
# PRD v2 target thresholds — used by batch.py for ✓/! flags
# ---------------------------------------------------------------------------

TARGET_RUNS_LO: float = 22.0
TARGET_RUNS_HI: float = 26.0

TARGET_STAYS_LO: float = 1.0
TARGET_STAYS_HI: float = 2.5

TARGET_JOKER_LO: float = 5.0
TARGET_JOKER_HI: float = 9.0

TARGET_SUPER_PCT_MAX: float = 8.0

TARGET_WH_CHANGES_LO: float = 2.0
TARGET_WH_CHANGES_HI: float = 4.0

TARGET_COMM_CHANGES_LO: float = 6.0
TARGET_COMM_CHANGES_HI: float = 10.0
