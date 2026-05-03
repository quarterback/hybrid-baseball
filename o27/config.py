"""
O27 Simulation Configuration — all tunable parameters in one place.

Every magic number that drives probability models, contact outcomes, baserunning,
stolen-base behaviour, and manager heuristics is defined here.  Engine files
import from this module so a single-file edit re-tunes the whole simulation.

=============================================================================
TUNING LOG
=============================================================================

--- Pass 0: Baseline (pre-config, 20-game sample) ---
Params:  original values hard-coded in prob.py / manager.py
Result:  mean 21.6 R/game — slightly below the 22–24 target
         Full 500-game batch needed for stable estimates.

--- Pass 1: Initial 500-game baseline (original parameters, verbatim) ---
Command: python o27/tune.py --games 500   (seeds 0–499)
Result:
  Avg total runs/game   23.71  target 22–24  ✓
  Avg run rate R/out    0.4390 target ~0.43  ✓
  Avg PAs/game          80.0   ref ~79       ✓
  Avg stays/game        0.642  target 0.3–1  ✓
  Super-inning freq     5.40%  target <5%    ! (just over)
Action:  Super-innings marginally high; increase HR weight on hard contact
         (more variance → fewer exact-score ties) and lower WILD_PITCH_PROB.

--- Pass 2: Super-inning tuning ---
Changes vs Pass 1:
  • HARD_CONTACT hr weight:      0.12 → 0.15  (higher XBH variance)
  • HARD_CONTACT fly_out weight: 0.21 → 0.18  (compensate total weight)
  • WILD_PITCH_PROB:             0.02 → 0.015 (fewer cheap-run ties)
Result (500 games):
  Avg total runs/game   24.14  ⚠ just above ceiling; HR weight trimmed
  Super-inning freq     4.80%  ✓

--- Pass 3: Final trim (500-game confirmation) ---
Changes vs Pass 2:
  • HARD_CONTACT hr weight:      0.15 → 0.14  (tiny trim to bring mean ≤ 24)
  • HARD_CONTACT fly_out weight: 0.18 → 0.19  (compensate)
Result (500 games, seeds 0–499):
  Avg total runs/game   23.99  target 22–24  ✓
  Avg run rate R/out    0.4443 target ~0.43  ✓
  Avg PAs/game          80.4   ref ~79       ✓
  Avg stays/game        0.660  target 0.3–1  ✓
  Super-inning freq     4.60%  target <5%    ✓
  → ALL PRD TARGETS MET

Target summary (PRD §5 / §7):
  Metric                  Target          Pass-3 result (500 games)
  ─────────────────────── ─────────────── ──────────────────────────
  Avg total runs/game     22–24           23.99  ✓
  Avg stays/game          0.3–1.0          0.660 ✓
  Super-inning freq       <5%              4.60% ✓
  Avg run rate R/out      ~0.43            0.444 ✓
  Avg PAs/game            ~79             80.4   ✓
=============================================================================
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Pitch outcome base probability table
# ---------------------------------------------------------------------------
# Format per count (balls, strikes):
#   (p_ball, p_called_strike, p_swinging_strike, p_foul, p_contact)
# Must sum to 1.0; engine normalises after adjustments.

PITCH_BASE: dict[tuple, tuple] = {
    (0, 0): (0.32, 0.18, 0.13, 0.14, 0.23),
    (1, 0): (0.36, 0.16, 0.11, 0.14, 0.23),
    (2, 0): (0.40, 0.14, 0.09, 0.14, 0.23),
    (3, 0): (0.44, 0.13, 0.07, 0.13, 0.23),
    (0, 1): (0.29, 0.15, 0.18, 0.16, 0.22),
    (1, 1): (0.32, 0.13, 0.17, 0.17, 0.21),
    (2, 1): (0.36, 0.11, 0.14, 0.18, 0.21),
    (3, 1): (0.40, 0.09, 0.12, 0.18, 0.21),
    (0, 2): (0.22, 0.10, 0.26, 0.22, 0.20),
    (1, 2): (0.25, 0.08, 0.26, 0.21, 0.20),
    (2, 2): (0.29, 0.07, 0.23, 0.21, 0.20),
    (3, 2): (0.33, 0.05, 0.21, 0.21, 0.20),
}

# ---------------------------------------------------------------------------
# Pitcher dominance adjustments
# ---------------------------------------------------------------------------
# Applied as:  p_dom = (pitcher_skill - 0.5) * 2   →  −1.0 to +1.0
# Each constant scales how much p_dom shifts the corresponding probability.

PITCHER_DOM_BALL: float     = -0.04   # fewer balls when pitcher dominant
PITCHER_DOM_CALLED: float   = +0.02   # more called strikes
PITCHER_DOM_SWINGING: float = +0.02   # more swinging strikes
PITCHER_DOM_CONTACT: float  = -0.03   # fewer contact events

# ---------------------------------------------------------------------------
# Batter dominance adjustments
# ---------------------------------------------------------------------------
# Applied as:  b_dom = (batter.skill - 0.5) * 2   →  −1.0 to +1.0

BATTER_DOM_SWINGING: float = -0.03   # fewer swinging strikes (better contact)
BATTER_DOM_CONTACT: float  = +0.03   # more contact events

# ---------------------------------------------------------------------------
# Pitcher fatigue model
# ---------------------------------------------------------------------------
# Threshold (batters faced) before fatigue degrades performance:
#   threshold = max(FATIGUE_THRESHOLD_BASE,
#                   FATIGUE_THRESHOLD_BASE + round(pitcher_skill * FATIGUE_THRESHOLD_SCALE))
# Fatigue factor grows linearly beyond threshold, capped at FATIGUE_MAX.

FATIGUE_THRESHOLD_BASE: int  = 10
FATIGUE_THRESHOLD_SCALE: int = 20    # higher-skill pitchers get longer spells
FATIGUE_MAX: float           = 0.60  # maximum fatigue multiplier
FATIGUE_SCALE: float         = 20.0  # spell_count divisor for ramp-up

FATIGUE_BALL: float     = +0.06   # more balls as fatigue grows
FATIGUE_CONTACT: float  = +0.04   # more contact
FATIGUE_CALLED: float   = -0.04   # fewer called strikes
FATIGUE_SWINGING: float = -0.03   # fewer swinging strikes
FATIGUE_FOUL: float     = -0.03   # fewer fouls

# ---------------------------------------------------------------------------
# Contact quality distribution
# ---------------------------------------------------------------------------
# Base probabilities for weak / medium / hard contact.
# Shifted by matchup:  shift = (batter.skill - pitcher.pitcher_skill) * CONTACT_MATCHUP_SHIFT

CONTACT_WEAK_BASE: float     = 0.38
CONTACT_MEDIUM_BASE: float   = 0.40
CONTACT_HARD_BASE: float     = 0.22
CONTACT_MATCHUP_SHIFT: float = 0.25   # max ±0.125 swing per unit matchup

# ---------------------------------------------------------------------------
# Contact outcome tables
# ---------------------------------------------------------------------------
# Format per row: (hit_type, batter_safe, caught_fly, weight)
# Weights are relative (do not need to sum to 1.0; engine normalises).

WEAK_CONTACT: list = [
    ("ground_out",      False, False, 0.50),
    ("fly_out",         False, True,  0.18),
    ("line_out",        False, False, 0.10),
    ("single",          True,  False, 0.18),
    ("fielders_choice", True,  False, 0.04),
]

MEDIUM_CONTACT: list = [
    ("ground_out",      False, False, 0.22),
    ("fly_out",         False, True,  0.14),
    ("line_out",        False, False, 0.12),
    ("single",          True,  False, 0.32),
    ("double",          True,  False, 0.12),
    ("fielders_choice", True,  False, 0.08),
]

HARD_CONTACT: list = [
    ("single",   True,  False, 0.20),
    ("double",   True,  False, 0.24),
    ("triple",   True,  False, 0.08),
    ("hr",       True,  False, 0.14),   # raised from 0.12, then trimmed to 0.14 for mean control
    ("fly_out",  False, True,  0.19),   # compensates HR weight adjustment
    ("line_out", False, False, 0.15),
]

# ---------------------------------------------------------------------------
# Runner advancement model
# ---------------------------------------------------------------------------
# Extra-base probability: chance += max(0, (speed - 0.5) * RUNNER_EXTRA_SPEED_SCALE)

RUNNER_EXTRA_SPEED_SCALE: float = 0.35

# ---------------------------------------------------------------------------
# Stolen base model
# ---------------------------------------------------------------------------

SB_ATTEMPT_SPEED_THRESHOLD: float = 0.62   # min runner speed to attempt steal
SB_ATTEMPT_PROB_PER_PITCH: float  = 0.06
SB_SUCCESS_BASE: float            = 0.55   # base success probability
SB_SUCCESS_SPEED_SCALE: float     = 0.50   # (speed - 0.5) * this adds to success
SB_SUCCESS_PITCHER_SCALE: float   = 0.15   # pitcher_skill * this subtracts from success
SB_SUCCESS_MIN: float             = 0.25   # floor on steal success
SB_SUCCESS_MAX: float             = 0.90   # ceiling on steal success

# ---------------------------------------------------------------------------
# Wild pitch probability
# ---------------------------------------------------------------------------

WILD_PITCH_PROB: float = 0.015  # per pitch with runners on base (tuned: 0.02→0.015, reduces cheap-run ties)

# ---------------------------------------------------------------------------
# Player attribute defaults (used as dataclass field defaults in state.py)
# ---------------------------------------------------------------------------
# These are the fallback values when a player is created without explicit attrs.
# Per-player overrides in main.py intentionally deviate from these baselines.

PLAYER_DEFAULT_SKILL: float                    = 0.50
PLAYER_DEFAULT_SPEED: float                    = 0.50
PLAYER_DEFAULT_PITCHER_SKILL: float            = 0.50
PLAYER_DEFAULT_STAY_AGGRESSIVENESS: float      = 0.40   # 0.0–1.0 tendency to choose stay
PLAYER_DEFAULT_CONTACT_QUALITY_THRESHOLD: float = 0.45  # P(stay | medium contact) gate

# ---------------------------------------------------------------------------
# Manager heuristics — joker insertion (§4.6)
# ---------------------------------------------------------------------------

JOKER_WEAK_BATTER_THRESHOLD: float = 0.44   # batter.skill < this → weak hitter (contact trigger gate)
JOKER_SCORE_DIFF_MAX: int          = 4      # |score_diff| ≤ this → high leverage
JOKER_OUTS_CEILING: int            = 22     # state.outs < this → not too late

# ---------------------------------------------------------------------------
# Manager heuristics — pitching change
# ---------------------------------------------------------------------------
# Threshold (BF) = max(PITCHER_CHANGE_BASE,
#                      PITCHER_CHANGE_BASE + round(pitcher_skill * PITCHER_CHANGE_SCALE))
# Phase 8: role-aware thresholds override the generic values.
#   Workhorse: goes deeper into the game.
#   Committee: shorter stints, replaced sooner.

PITCHER_CHANGE_BASE: int  = 10
PITCHER_CHANGE_SCALE: int = 20

WORKHORSE_CHANGE_BASE: int   = 8    # workhorse starter goes longer than committee
WORKHORSE_CHANGE_SCALE: int  = 8    # at skill 0.52 → threshold ~12 BF
COMMITTEE_CHANGE_BASE: int   = 2    # committee relief enters for short stints
COMMITTEE_CHANGE_SCALE: int  = 5    # at skill 0.52 → threshold ~5 BF

# ---------------------------------------------------------------------------
# Manager heuristics — situational joker insertion (Phase 8 archetype triggers)
# ---------------------------------------------------------------------------
# Evaluation order (power first — it dominates when down ≥ JOKER_POWER_DEFICIT):
#   1. Power   — batting team down ≥ JOKER_POWER_DEFICIT and outs < JOKER_POWER_OUTS_CEIL.
#   2. Speed   — corners: 1B+3B occupied, 2B empty, exactly 1 out (§4.6).
#   3. Contact — runners in scoring position (2B or 3B occupied).
# No cross-archetype fallback: if the required joker is unavailable, nothing fires.

JOKER_POWER_DEFICIT: int    = 3     # power fires only when batting team is down ≥ 3 runs
JOKER_POWER_OUTS_CEIL: int  = 22    # power trigger disabled at or after this out count
JOKER_SPEED_OUTS: int       = 2     # retained for config compatibility; unused by engine
JOKER_MAX_PER_HALF: int     = 9     # cap: JOKERS_PER_ARCHETYPE(3) × archetypes(3) per team per half

# ---------------------------------------------------------------------------
# Manager heuristics — pinch hit (fallback when jokers exhausted)
# ---------------------------------------------------------------------------

PINCH_HIT_SCORE_DIFF_MAX: int  = 1     # only PH in very tight games
PINCH_HIT_SKILL_EDGE: float    = 0.05  # replacement must be this much better
