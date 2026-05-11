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

--- Phase 11: Talent-dictates-performance (pitcher retune) ---
Goal: remove artificial weights compressing pitcher talent; let stuff/cmd/
      movement/stamina/grit/variance dictate outcomes more than league-mean
      anchors.
Changes:
  • PITCHER_DOM_BALL      -0.06 → -0.07   (parity with command on walks)
  • PITCHER_DOM_SWINGING  +0.03 → +0.06   (matches BATTER_CONTACT_SWINGING)
  • PITCHER_DOM_CONTACT   -0.04 → -0.06   (exceeds batter promotion)
  • CONTACT_MOVEMENT_TILT  0.06 → 0.10   (parity with CONTACT_POWER_TILT)
  • TODAY_FORM_SIGMA       0.10 → 0.04, bounds [0.80,1.20] → [0.92,1.08]
  • FATIGUE_MAX            0.60 → 1.00   (uncapped collapse for low-stam arms)
  • FATIGUE_DEBT_MAX_PEN.  0.20 → 0.40   (overworked arms suffer more)
  • prob.py probability floors 0.01 → 0.001 (let elite stuff transcend)
New mechanics:
  • Player.pitch_variance — per-guy static range; each pitch samples
    uniformly within [rating ± variance] for stuff/cmd/movement.
  • Player.grit (0.25–0.75) — multiplicative dampener on the fatigue
    ramp; high-grit veterans grind through, low-grit kids unravel.
Result (500 games, default rosters):
  Avg total runs/game   23.52  target 22–24  ✓
  Avg run rate R/out    0.4355 target ~0.43  ✓
  Avg PAs/game          80.2   ref ~79       ✓
  Avg stays/game        0.730  target 0.3–1.0 ✓
  Super-inning freq     5.20%  target <5%    ! (slightly over)
  League K%             18.38% target 17–19% ✓
  League BB%            7.45%  target 9–10%  ! (pitcher command winning)
  League BA / SLG / HR%  .292 / .462 / 2.08% ✓ ✓ ✓
Talent dispersion (the goal):
  Ace (0.85/0.85/0.80, grit 0.70) vs replacement (0.30/0.30/0.30, grit 0.30)
  facing avg bat:
    Ball:     26.0% vs 39.4%   (-13.4 pp)
    Whiff:    16.2% vs  7.7%   (+8.5 pp, 2.1× ratio)
    Hard%:     6.8% vs 30.3%   (base 22%)
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
    # Pass 5 (Realism): another nudge on top of Pass 4 — 2-strike swinging
    # rates trimmed further, with the displaced weight going to fouls so
    # at-bats stay alive longer (contact-era feel). 0-strike ball rates
    # nudged up ~0.01 to lift walks toward the 9-10% band.
    # Format: (p_ball, p_called_strike, p_swinging_strike, p_foul, p_contact)
    (0, 0): (0.34, 0.18, 0.11, 0.14, 0.23),
    (1, 0): (0.38, 0.16, 0.09, 0.14, 0.23),
    (2, 0): (0.43, 0.14, 0.06, 0.14, 0.23),
    (3, 0): (0.47, 0.13, 0.04, 0.13, 0.23),
    (0, 1): (0.31, 0.15, 0.14, 0.18, 0.22),
    (1, 1): (0.34, 0.13, 0.13, 0.19, 0.21),
    (2, 1): (0.38, 0.11, 0.10, 0.20, 0.21),
    (3, 1): (0.42, 0.09, 0.08, 0.20, 0.21),
    (0, 2): (0.25, 0.10, 0.16, 0.29, 0.20),
    (1, 2): (0.28, 0.08, 0.16, 0.28, 0.20),
    (2, 2): (0.32, 0.07, 0.14, 0.27, 0.20),
    (3, 2): (0.36, 0.05, 0.12, 0.27, 0.20),
}

# ---------------------------------------------------------------------------
# Pitcher dominance adjustments
# ---------------------------------------------------------------------------
# Applied as:  p_dom = (pitcher_skill - 0.5) * 2   →  −1.0 to +1.0
# Each constant scales how much p_dom shifts the corresponding probability.
#
# Strong-pitcher-tilt retune: magnitudes bumped ~50% over the legacy values
# so an elite-Stuff pitcher facing an average batter swings the per-pitch
# distribution hard toward strikes / weak contact. Combined with the
# loosened 0.01 floors in contact_quality and the Elite+ talent tier,
# this is what lets aces actually pitch like aces.

PITCHER_DOM_BALL: float     = -0.07   # fewer balls when pitcher dominant
PITCHER_DOM_CALLED: float   = +0.03   # more called strikes
PITCHER_DOM_SWINGING: float = +0.06   # more swinging strikes (matches BATTER_CONTACT_SWINGING magnitude)
PITCHER_DOM_CONTACT: float  = -0.06   # fewer contact events (exceeds batter's +0.05 promotion)

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

FATIGUE_THRESHOLD_BASE: int  = 24    # Phase 10/11 pitcher retune: workhorses fatigue much later
# Bumped 20 → 40 so Stamina actually moats the workhorse archetype.
# Math: an elite-Stamina (0.85) pitcher fatigues at 24 + round(0.85*40) = 58 BF
# threshold — i.e. effectively never within a 27-out half. A sub-replacement
# (0.25) Stamina pitcher fatigues at 24 + 10 = 34 BF, visibly tiring through
# the order. This is what makes Stamina disproportionately valuable in O27.
#
# Earlier branch (Phase 10.2 Decay work) ran BASE=6 to make K%_arc1−arc3
# differentiate between workhorse starters and short relievers. Phase 11
# pitcher retune walked it back to BASE=24 to preserve the workhorse moat;
# the Decay diagnostic is muted as a result but still visible at the
# extremes. Keep an eye on Decay regression in future tuning passes.
FATIGUE_THRESHOLD_SCALE: int = 40    # higher-stamina pitchers get longer spells
FATIGUE_MAX: float           = 1.00  # uncapped collapse for low-stamina arms past their limit
FATIGUE_SCALE: float         = 20.0  # spell_count divisor for ramp-up

FATIGUE_BALL: float     = +0.06   # more balls as fatigue grows
FATIGUE_CONTACT: float  = +0.06   # more contact (was +0.04 — sharper late-arc slap-hit profile)
FATIGUE_CALLED: float   = -0.04   # fewer called strikes
FATIGUE_SWINGING: float = -0.06   # fewer swinging strikes (was -0.03 — the K-suppression term)
FATIGUE_FOUL: float     = -0.04   # fewer fouls (was -0.03)

# ---------------------------------------------------------------------------
# Contact quality distribution
# ---------------------------------------------------------------------------
# Base probabilities for weak / medium / hard contact.
# Shifted by matchup:  shift = (batter.skill - pitcher.pitcher_skill) * CONTACT_MATCHUP_SHIFT

# Run-environment calibration step (HANDOFF Bug 1): full-season sim was
# producing 24.42 R/G (~12.2 per team) vs the design target of 14-18
# R/G (7-9 per team). Shift contact mass from HARD and MEDIUM into
# WEAK to cut XBH frequency without retuning per-hit-type weights.
# Earlier values: WEAK 0.38 / MEDIUM 0.40 / HARD 0.22.
CONTACT_WEAK_BASE: float     = 0.56
CONTACT_MEDIUM_BASE: float   = 0.34
CONTACT_HARD_BASE: float     = 0.10
CONTACT_MATCHUP_SHIFT: float = 0.25   # max ±0.125 swing per unit matchup

# Second-swing modifier: on the 2nd+ contact event within the same AB
# (i.e., after a non-terminal 2C), tilt the contact_quality distribution
# by (batter.eye - pitcher.command). Reads as: a high-eye batter is
# reading the pitcher across multiple swings; a high-command pitcher
# knows what's coming on swing 2 and shuts it down. Net shift (positive
# = batter advantage) feeds into contact_quality's `shift` term.
SECOND_SWING_EYE_SCALE: float     = 0.20   # batter.eye contribution
SECOND_SWING_COMMAND_SCALE: float = 0.20   # pitcher.command contribution (subtracted)

# Talent-weighted 2C outcome resolution (Phase 11D / Path A — applies on
# every 2C-chosen event, including swing 1, where Path 2's swing-2+ scope
# couldn't reach). On a stay-chosen contact:
#   - WEAK quality: hit-credit gate. Talent_factor shifts a base 50%
#     credit_p; gate failure forces runner_advances to [0,0,0] (no advance,
#     no hit credit — the FC-flavored downgrade per spec).
#   - MEDIUM quality: advancement-magnitude gate. Talent_factor shifts a
#     base 50% upgrade_p from [1,1,1] (low-talent) to [2,2,2] (high-talent
#     — the Phase 11C aggressive advance, now talent-conditional).
#   - HARD quality: no modification (rare for 2C; auto-runs typically).
# talent_factor = (eye_dev + contact_dev) / 2 - command_dev,
# range roughly -1.5 (worst matchup) to +1.5 (best matchup).
TALENT_2C_SHIFT_SCALE: float = 1.00   # ±1.5 nominal swing; bounded 0.05-0.95

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

# Baseline extra-base attempt probability for the runner on 1B on a double.
# Without this baseline, every double produced an identical [2, 2, 1] runner
# advancement and runs scored were rigidly tied to the hit type — fast and
# slow runners alike stopped at 3B. The baseline + speed/baserunning/
# aggressiveness scaling decouples runs from hits at the most common
# extra-base-hit type. Tuned to roughly match MLB rates of "1B runner scores
# on a double" (~40%) at league-average attributes.
RUNNER_EXTRA_DOUBLE_FROM_1B: float = 0.30

# Thrown-out-at-home — for runners whose default base_advance already
# carries them across the plate (2B runner on a single, 1B runner on a
# triple). Distinct from TOOTBLAN, which only fires when an "extra base"
# attempt above base_advance is rolled. Tuning targets at the relevant
# attribute axes (speed and baserunning, both at the listed value):
#   fast/skilled (0.95):   ~2%   — never automatic, floor enforced
#   neutral      (0.50):   ~9%
#   slow/raw     (0.10):  ~25%
RUNNER_THROWN_OUT_AT_HOME_BASE: float        = 0.09
RUNNER_THROWN_OUT_AT_HOME_SPEED_SCALE: float = 0.20
RUNNER_THROWN_OUT_AT_HOME_SKILL_SCALE: float = 0.20
RUNNER_THROWN_OUT_AT_HOME_MIN: float         = 0.02

# GIDP — ground-ball double plays. With at least one runner on base
# and < 2 outs, a share of ground outs become double plays. The exact
# probability depends on:
#   1. WHICH bases are occupied — a runner on 1B gives the defense a
#      free force at 2B; a lone runner on 2B or 3B requires a tag, which
#      is rarer. Multiple runners (especially with 1B) multiply the
#      force-out options.
#   2. The CONTACT QUALITY — weak-contact ground balls (slow choppers,
#      6-4-3 setups) are DP-prone; hard-contact ground balls tend to be
#      too fast for the relay or skip through the infield entirely.
#   3. BATTER SPEED — slow batters lose the relay race.
#   4. TEAM DEFENSE — strong infields turn more DPs.
# Tuning targets (after all factors compose, before clamp):
#   - Low end (~6%):  3B alone, hard contact, fast batter, weak defense.
#   - Mid (~13-14%):  1B alone, medium contact, neutral attributes.
#   - High end (~23%): bases loaded, weak contact, slow batter, elite defense.
GIDP_BASE_PROB: float    = 0.13
GIDP_SPEED_SCALE: float  = 0.20
GIDP_DEFENSE_SCALE: float = 0.15
GIDP_MIN_PROB: float     = 0.06
GIDP_MAX_PROB: float     = 0.23

# Force-factor table — multiplier applied based on which bases are
# occupied. The (1B-only) case is the canonical 1.0 baseline.
GIDP_FORCE_1B_ONLY: float        = 1.00   # runner on 1B only
GIDP_FORCE_2B_ONLY: float        = 0.40   # runner on 2B only — no force, tag required
GIDP_FORCE_3B_ONLY: float        = 0.40   # runner on 3B only — no force, tag required
GIDP_FORCE_1B_2B: float          = 1.20   # 1B + 2B (force at 3B + 2B)
GIDP_FORCE_1B_3B: float          = 1.10   # 1B + 3B (force at 2B; 3B tag)
GIDP_FORCE_2B_3B: float          = 0.50   # 2B + 3B — no force; tag plays
GIDP_FORCE_LOADED: float         = 1.40   # bases loaded — most options

# Contact-quality multiplier. Weak ground balls feed DPs (slow rollers,
# 6-4-3 setups); hard contact is too fast for the relay or punches through.
GIDP_QUALITY_WEAK: float    = 1.30
GIDP_QUALITY_MEDIUM: float  = 1.00
GIDP_QUALITY_HARD: float    = 0.55

# Stay (2C) plays still see fielders' choice / lead-runner-tag-out events,
# just at a reduced rate — the batter isn't running so there's no force at
# 1B, but a fielder can still tag out a runner who broke for the next base.
# This keeps the run-game alive on stays without making 2C a free pass on
# the bases. Multiplier on GIDP_BASE_PROB; only the lead runner is at risk
# (no double play through 1B since the batter is at the plate).
GIDP_STAY_MULTIPLIER: float = 0.30

# Triple play — at least 2 forceable runners on (1B+2B or bases loaded)
# and 0 outs. Real MLB rate is ~1 per 700 opportunities; we keep it
# rare. Conditional on a DP firing in the eligible base config, this
# probability promotes it to a TP. Set to 0 to disable.
# Baserunner errors can also induce a TP — a runner with low baserunning
# skill (poor read off the bat, late tag-up) inflates the TP probability
# via the SKILL bonus below.
TRIPLE_PLAY_GIVEN_DP_PROB: float       = 0.04
TRIPLE_PLAY_BASERUNNING_BONUS: float   = 0.06   # added when lead runner is below-average

# ---------------------------------------------------------------------------
# TOOTBLAN — thrown out trying for the extra base on a hit / fly / grounder.
# When a runner ATTEMPTS the extra base (probability driven by speed +
# baserunning + aggressiveness in prob._runner_advance), this layer decides
# whether the slide beats the throw. Identity preserved at neutral inputs:
# at speed = baserunning = aggressiveness = 0.5 the attempt probability
# from RUNNER_EXTRA_SPEED_SCALE is already 0, so TOOTBLAN never fires.
TOOTBLAN_SAFE_BASE: float  = 0.78   # baseline safe rate when an attempt fires
TOOTBLAN_SKILL_SCALE: float = 0.40  # +(baserunning - 0.5) * this
TOOTBLAN_SPEED_SCALE: float = 0.20  # +(speed       - 0.5) * this
TOOTBLAN_SAFE_MIN: float    = 0.45  # floor — even bad runners aren't always out
TOOTBLAN_SAFE_MAX: float    = 0.96  # ceiling — even elite runners aren't auto-safe

# ---------------------------------------------------------------------------
# Pickoff model — pitcher attempts to back-pick a runner. Fires as a
# between-pitch event in prob.between_pitch_event when a runner is on
# 1B (or 2B). Probability scales with run_aggressiveness (a leaning
# runner is exploitable), inversely with baserunning (smart runners
# don't get caught), and with pitcher Stuff (good moves matter). LHP
# adds a structural bonus vs runners on 1B (better look-back angle).
# Tuning note: real-MLB pickoff outs are rare (~0.05/game per side).
# Keep attempt rate low and success rate modest so they're a flavor
# event, not a CS-rate inflator.
PICKOFF_ATTEMPT_BASE: float        = 0.004  # per pitch, 1B with avg-aggression runner
PICKOFF_AGGRESSION_SCALE: float    = 0.012  # +(run_aggressiveness - 0.5) * this
PICKOFF_LHP_1B_BONUS: float        = 0.005  # absolute boost vs 1B runner from LHP
PICKOFF_2B_DAMPENER: float         = 0.40   # 2B pickoffs much rarer than 1B
PICKOFF_SUCCESS_BASE: float        = 0.10   # baseline catch rate when a move fires
PICKOFF_SUCCESS_PITCHER_SCALE: float = 0.25 # +pitcher.pitcher_skill * this
PICKOFF_SUCCESS_AGGRESSION_SCALE: float = 0.30  # +(aggression - 0.5) * this
PICKOFF_SUCCESS_BR_SCALE: float    = 0.30   # -(baserunning - 0.5) * this
PICKOFF_SUCCESS_MIN: float         = 0.03
PICKOFF_SUCCESS_MAX: float         = 0.40

# ---------------------------------------------------------------------------
# Hit-by-pitch — wildness converts a fraction of "ball" outcomes into HBP.
# Pre-this-config the engine never produced HBP outside scripted tests
# (pitch_outcome's _PITCH_NAMES list didn't include it). We layer HBP on
# in _generate_pitch as a post-pitch-outcome conversion so the realism
# identity invariant on _pitch_probs stays intact. Lower command -> more
# HBP; identity at command = 0.5 yields the base rate.
HBP_FROM_BALL_BASE: float          = 0.018  # fraction of balls converted to HBP at neutral cmd
HBP_COMMAND_SCALE: float           = 0.030  # +(0.5 - command) * this

# ---------------------------------------------------------------------------
# Hit-and-run — a manager-called SB attempt where the batter is asked to
# swing at any pitch to protect the runner. Bypasses the SB speed gate
# (the runner goes regardless) and gives a small success bump because
# the catcher's read is on the batter, not the runner. Probability of
# being called scales with the batting team's mgr_run_game tendency
# AND count-awareness — managers don't call hit-and-run in 0-2 holes
# or 3-0 take counts; the canonical spots are 1-0 / 2-0 / 2-1 / 3-1
# (hitter's counts where the pitcher wants a strike and the runner can
# safely commit).
HIT_AND_RUN_BASE_PROB: float       = 0.012  # per pitch with a 1B runner in a favorable count
HIT_AND_RUN_RUNGAME_SCALE: float   = 0.030  # +(mgr_run_game - 0.5) * this
HIT_AND_RUN_SUCCESS_BONUS: float   = 0.08   # added to SB success_p
# Counts where hit-and-run is realistic (balls, strikes). Other counts
# get a heavy dampener (still possible but rare) so the call rate
# concentrates in the right situations.
HIT_AND_RUN_FAVORED_COUNTS: tuple  = ((1, 0), (2, 0), (2, 1), (3, 1))
HIT_AND_RUN_OFF_COUNT_DAMPENER: float = 0.20  # multiplies prob in non-favored counts
# Contact-side benefit: when a hit-and-run successfully puts the runner
# in motion (SB succeeded), the next contact event gets a small bonus.
# Specifically, the batter is more likely to make contact (lower K rate)
# because they're swinging at most pitches. We model this by setting
# state.hit_and_run_active = True on success; prob.py reduces K weight
# for the rest of this PA.
HIT_AND_RUN_CONTACT_K_REDUCTION: float = 0.25  # multiplicative on K probability

# ---------------------------------------------------------------------------
# Sacrifice bunt — manager pre-PA decision. Trades an out for a base,
# situationally valuable in close games with weak hitters and runners on.
# Lower-`mgr_leverage_aware` skippers are more likely to call this (it's
# generally a -EV play in modern analytics). Speed influences whether
# the bunt becomes a hit.
SAC_BUNT_BASE_PROB: float          = 0.05   # base call rate when conditions align
SAC_BUNT_RUNGAME_SCALE: float      = 0.20   # mgr_run_game * this multiplies
SAC_BUNT_LEVERAGE_DAMPER: float    = 0.50   # (1 - leverage_aware) * this multiplies
SAC_BUNT_HIT_BASE: float           = 0.10   # baseline bunt-for-hit rate
SAC_BUNT_HIT_SPEED_SCALE: float    = 0.30   # +(speed - 0.5) * this
SAC_BUNT_FAIL_RATE: float          = 0.10   # popups / runner forced at lead

# ---------------------------------------------------------------------------
# Stolen base model
# ---------------------------------------------------------------------------
# Recalibrated for O27 reality (vs MLB defaults):
#   - 12-batter lineups + 27-out continuous halves = more PAs / runner-on-base
#     situations per game, so a per-pitch attempt rate that looks "high" by
#     MLB standards is actually correct here.
#   - Catcher arm fatigues throughout the half (no inter-inning rest), so
#     late-half steals against a tired battery are easier than MLB analogs.
#   - Hitters optimizing for stays/contact (not 3-true-outcomes) leaves more
#     space-creation value on the table — runners taking it is right.

SB_ATTEMPT_SPEED_THRESHOLD: float = 0.52   # was 0.62 — lower gate so above-avg speed attempts
SB_ATTEMPT_PROB_PER_PITCH: float  = 0.045  # was 0.015 — ~3x MLB attempt rate
SB_SUCCESS_BASE: float            = 0.72   # was 0.62 — pulled up to match MLB ~75% success
SB_SUCCESS_SPEED_SCALE: float     = 0.50   # (speed - 0.5) * this adds to success
SB_SUCCESS_PITCHER_SCALE: float   = 0.15   # pitcher_skill * this subtracts from success
SB_SUCCESS_DEBT_SCALE: float      = 0.0008 # pitcher.pitch_debt * this ADDS to success
                                           # — tired battery = easier steal
SB_SUCCESS_CATCHER_ARM_SCALE: float = 0.20 # catcher.arm * this SUBTRACTS from success
                                           # — elite catcher arm shuts down the running game
SB_SUCCESS_MIN: float             = 0.25   # floor on steal success
SB_SUCCESS_MAX: float             = 0.92   # ceiling on steal success

# ---------------------------------------------------------------------------
# Defense model
# ---------------------------------------------------------------------------
# Identity: at team_defense_rating = 0.5 (neutral) every term collapses to 0.

# Range modifier — better team defense converts more BIPs into outs.
# Applied as a probabilistic flip: when an outcome resolves to an out OR a
# single, a small fraction of cases flip in proportion to (defense - 0.5).
DEFENSE_RANGE_SHIFT_SCALE: float = 0.10   # max ±10% out↔single conversion swing

# Error rate — share of would-be-out plays that become a "reached on
# error" instead. Scales inversely with team defense.
DEFENSE_ERROR_BASE: float        = 0.018   # ~1.8% of would-be-outs at neutral D
DEFENSE_ERROR_SCALE: float       = 0.025   # (0.5 - team_def) * this adds to E rate
DEFENSE_ERROR_MIN: float         = 0.003
DEFENSE_ERROR_MAX: float         = 0.045

# ---------------------------------------------------------------------------
# Emergency position-player pitcher (PP-pitching)
# ---------------------------------------------------------------------------
# In an absolute blowout, a strong-arm position player can come in to
# preserve the bullpen — like real-life mop-up usage but tuned for O27.
# These thresholds are intentionally tight: in O27 with continuous
# 27-out halves, putting a position player on the mound EARLIER than
# this would just compound the deficit. Per the user's framing:
# "down 17+ with 6 outs to go or something."
#
# Identity: if either threshold is unmet, normal pitcher selection runs.

PP_PITCH_DEFICIT_MIN: int = 17    # losing team must trail by >= this run gap
PP_PITCH_OUTS_LEFT_MAX: int = 6   # AND have <= this many outs left to record
PP_PITCH_ARM_MIN: float    = 0.55 # AND a position player with at least this arm

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
# Pitch-quality range (per-pitch sampling around central rating)
# ---------------------------------------------------------------------------
# Each pitch samples uniformly in [rating - pitch_variance, rating + pitch_variance]
# for stuff / command / movement. Identity at pitch_variance = 0.0.
# Roster generation rolls each pitcher their own pitch_variance — some arms
# repeat (low variance), others live on the edges (high variance).
PITCH_VARIANCE_MIN: float = 0.03   # league min — every arm has a touch of variance
PITCH_VARIANCE_MAX: float = 0.12   # max-effort / inconsistent mechanics archetype
PITCH_VARIANCE_MEAN: float = 0.06  # league mean used by roster generators

# ---------------------------------------------------------------------------
# Grit — pitcher fatigue resistance
# ---------------------------------------------------------------------------
# Bounded 0.25–0.75 across the league (some guys have it, some don't).
# Applied as a multiplicative dampener on the FATIGUE term:
#   fatigue_eff = fatigue * max(0.0, 1.0 - (grit - 0.5) * 2 * GRIT_FATIGUE_RESIST)
# At grit = 0.50 the term collapses to fatigue * 1.0 (identity).
# At grit = 0.75 fatigue is reduced by GRIT_FATIGUE_RESIST × 0.5 = 30% (default).
# At grit = 0.25 fatigue is *amplified* by the same magnitude — low-grit arms
# unravel quicker than the raw Stamina ramp would suggest.
GRIT_BOUND_MIN: float          = 0.25
GRIT_BOUND_MAX: float          = 0.75
GRIT_FATIGUE_RESIST: float     = 0.60   # max grit dampens 30% of fatigue; min grit amplifies 30%
PLAYER_DEFAULT_GRIT: float     = 0.50   # identity

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

# Phase 10: pre-1980s / Japanese-style usage — workhorse SPs go DEEP
# (target: AOR ~22 outs, ~30+ BF per start). With reach rate ~33%, 30 BF ≈
# 20 outs, so a base of 28 + scale of 8 yields ~32 BF threshold at skill 0.5,
# i.e. starters are pulled around the 7th/8th of a 9-frame side.
WORKHORSE_CHANGE_BASE: int   = 28   # workhorse starter goes very deep
WORKHORSE_CHANGE_SCALE: int  = 8    # at skill 0.5 → threshold ~32 BF (~22 outs)

# Legacy committee role — retained so old DBs keep working but no longer
# generated by league.py.
COMMITTEE_CHANGE_BASE: int   = 2
COMMITTEE_CHANGE_SCALE: int  = 5

# Phase 10: dedicated relievers — only enter LATE in the half (after the
# workhorse blows up or after RELIEVER_ENTRY_OUTS_MIN). Once in, they
# typically finish the half (AOR ~5-7 outs).
RELIEVER_CHANGE_BASE: int    = 12   # short-relief stints
RELIEVER_CHANGE_SCALE: int   = 6
RELIEVER_ENTRY_OUTS_MIN: int = 18   # do not pull SP for an RP before this

# Emergent strategy thresholds — the manager AI derives an SP's role from
# their CURRENT stamina rating (not a stored tag). This is what lets a
# team naturally use "openers" when its rotation is short on stamina, and
# stick with workhorses when it has the arms.
WORKHORSE_STAMINA_THRESHOLD: float = 0.62  # >= this → workhorse pull threshold
OPENER_STAMINA_THRESHOLD:    float = 0.40  # <= this → opener (pulled fast)

# Opener-mode thresholds — pull the "starter" after a short stint and let
# the bullpen-by-committee finish the half. Lets a stamina-poor staff be
# strategically viable rather than just bad.
OPENER_CHANGE_BASE:  int = 7
OPENER_CHANGE_SCALE: int = 3

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

# ===========================================================================
# Realism layer (1990s–2000s O27 flavor)
# ===========================================================================
# All constants in this block follow the identity invariant:
#   when every realism input lands at its neutral value (rating = 0.5,
#   handedness same/opposite cancels, today_form = 1.0, park = 1.0)
#   each `(x - 0.5) * 2` term is 0 and `(today_form - 1.0)` is 0, so the
#   engine collapses back to its pre-realism behavior bit-for-bit.
#
# Keep magnitudes small (≤ 0.05 each) so the realism layer adds texture
# rather than overwhelming the calibrated `PITCHER_DOM_*` / `BATTER_DOM_*`
# scaffolding above. The tuning loop should adjust THESE knobs first
# (cheap) before retouching the legacy probability tables.

# --- Batter eye: discipline -----------------------------------------------
# Higher eye → more balls taken, fewer called strikes (deeper counts, more BB).
BATTER_EYE_BALL:    float = +0.04   # added to p_ball
BATTER_EYE_CALLED:  float = -0.03   # subtracted from p_called_strike

# --- Batter contact: bat-on-ball ability ----------------------------------
# Higher contact → fewer swinging strikes, more fouls / balls in play.
BATTER_CONTACT_SWINGING: float = -0.05
BATTER_CONTACT_FOUL:     float = +0.02
BATTER_CONTACT_CONTACT:  float = +0.02

# --- Pitcher command ------------------------------------------------------
# Higher command → fewer balls (Maddux). Independent of Stuff.
# Magnitudes bumped along with the strong-pitcher-tilt retune above —
# elite Command should produce visibly elite walk-rate suppression.
PITCHER_COMMAND_BALL:   float = -0.07
PITCHER_COMMAND_CALLED: float = +0.03

# --- Contact-quality shifts -----------------------------------------------
# Power tilts contact toward hard; movement (pitcher) tilts toward weak.
CONTACT_POWER_TILT:    float = 0.10
CONTACT_MOVEMENT_TILT: float = 0.10   # parity with power tilt — high-movement pitcher suppresses hard contact as strongly as a slugger creates it

# --- Power → in-play distribution redistribution --------------------------
# Phase 10.2: power was previously a one-trick HR additive (POWER_HR_WEIGHT_
# SCALE=0.08 was added to the HR row weight, INCREASING total HARD weight
# rather than redistributing). That broke the "redistribute, don't multiply"
# rule — high-power hitters were getting *extra* offense on top of the
# existing distribution, which inflated total league HR. The new model
# redistributes weight along power-axis edges (sum-preserving), so high-
# power produces more XBH at the EXPENSE of singles / line outs / grounders,
# and low-power produces the inverse, leaving league totals stable.
#
# Edges (positive power shifts in named direction; negative power reverses):
#   HARD:    line_out  →  hr           (HR redistribution; replaces additive)
#   HARD:    single    →  double
#   HARD:    double    →  triple
#   MEDIUM:  single    →  double
#   MEDIUM:  ground_out → fly_out
#   WEAK:    single    →  fly_out      (low power: solid contact → infield pop)
#
# Magnitude is the FRACTION of `from`-row weight shifted to `to`-row at
# (power - 0.5)*2 = ±1.0. So 0.50 means at full +/− power, half the
# from-row weight moves to the to-row. Calibrated to keep league HR /
# 2B / 3B rates stable while opening per-player spread.
POWER_REDIST_HR:        float = 0.50  # HARD line_out → hr (replaces POWER_HR_WEIGHT_SCALE additive)
POWER_REDIST_HARD_S2D:  float = 0.30
# D2T must be small enough that net flow into doubles (from single→double)
# isn't cancelled by flow out to triples. Coefficients calibrated so that
# doubles rise net-positive at power_dev=+1 — see
# `o27/tests/test_power_redistribute.py::test_directionality`.
POWER_REDIST_HARD_D2T:  float = 0.10
POWER_REDIST_MED_S2D:   float = 0.20
POWER_REDIST_MED_GO2FO: float = 0.15
POWER_REDIST_WEAK_S2FO: float = 0.20

# Legacy alias kept for the archetype `hr_weight_bonus` field, which the
# data layer hands in for joker / slugger archetypes. Now applied as a
# redistribute scalar (line_out → HR) on top of the power axis.
POWER_HR_WEIGHT_SCALE: float = 0.08

# --- Movement → HARD_CONTACT GB bias --------------------------------------
# Groundball pitcher: shifts hard-contact weight from XBH toward fly_out.
MOVEMENT_GB_WEIGHT_SCALE: float = 0.04

# --- Platoon split ---------------------------------------------------------
# Same-handedness penalty applied as multiplicative factor on (b_dom + new
# batter terms). MLB observed split is ~10–15 wOBA pts; 0.06 here lands in
# range without overpowering the rest of the model.
PLATOON_PENALTY:        float = 0.06
PLATOON_BONUS_SWITCH:   float = 0.0   # switch hitters always face advantage

# --- Daily pitcher form ---------------------------------------------------
# Per-spell N(mu, sigma) clipped roll. today_form = 1.0 ⇒ legacy parity.
TODAY_FORM_MU:    float = 1.00
TODAY_FORM_SIGMA: float = 0.04   # was 0.10 — slashed so daily-form noise stops overriding talent
TODAY_FORM_MIN:   float = 0.92   # was 0.80 — tight bounds keep ratings (not RNG) in charge of outcomes
TODAY_FORM_MAX:   float = 1.08   # was 1.20

# Multi-game fatigue (workload-debt) penalty applied on top of today_form.
# Identity invariant: at pitch_debt = 0, all of these collapse to no penalty.
# A pitcher's stamina-derived "budget" is `stamina * 100` pitches over the
# rolling 5-day window. Excess pitches above that scale the form down by
# `excess * FATIGUE_DEBT_PER_PITCH`, capped at FATIGUE_DEBT_MAX_PENALTY.
FATIGUE_DEBT_MIN_BUDGET:    float = 30.0   # pitches; floor for low-stamina arms
FATIGUE_DEBT_BUDGET_SCALE:  float = 100.0  # stamina (0-1) * this = budget pitches
FATIGUE_DEBT_PER_PITCH:     float = 0.005  # form penalty per pitch over budget
FATIGUE_DEBT_MAX_PENALTY:   float = 0.40   # was 0.20 — let chronically overworked arms really suffer; Stamina becomes a real moat
# Shift toward strikes on top of the existing pitcher_dom term, scaled by
# (today_form - 1.0). Magnitudes are deliberately small.
FORM_BALL:     float = -0.04   # good day → fewer balls
FORM_CALLED:   float = +0.02
FORM_SWINGING: float = +0.02
FORM_CONTACT:  float = -0.03

# --- Park factors ---------------------------------------------------------
# Multipliers on relevant rows of HARD_CONTACT (HR) and the contact tables
# (singles/doubles for park_hits). Bounded so even a Coors-like park doesn't
# create cartoon stat lines.
PARK_HR_MIN:    float = 0.85
PARK_HR_MAX:    float = 1.20
PARK_HITS_MIN:  float = 0.93
PARK_HITS_MAX:  float = 1.08

# --- Attribute blend weights ----------------------------------------------
# When folding the new multi-D ratings into the existing probability code,
# we blend them with the legacy single-skill / single-stuff terms so that
# new rolls don't overwhelm the calibrated dominance scaffolding above.
# Set to 0.0 to disable a new attribute entirely.
BLEND_BATTER_CONTACT_VS_SKILL: float = 0.6   # contact-quality matchup blend
BLEND_PITCHER_STUFF_VS_FORM:   float = 1.0   # today_form already a multiplier

# ---------------------------------------------------------------------------
# Release-point model
# ---------------------------------------------------------------------------
# O27 is a sidearm/submarine sport — the conventional overhand delivery is
# a lore-level structural fact, not enforced mechanically. The release_angle
# attribute encodes position within the sidearm spectrum:
#   0.0 = submarine  (extreme downward angle, strongest platoon, least arm stress)
#   0.5 = sidearm    (default; league centre-mass)
#   1.0 = three-quarter sidearm  (highest slot available in O27)
#
# Effects compound with the pitch catalog below. Identity at release_angle=0.5.

# Platoon amplifier: submarine pitchers' side-to-side movement amplifies the
# handedness advantage. (0.5 - release_angle) positive for sub, negative for 3q.
RELEASE_PLATOON_AMP_SCALE: float = 0.60   # submarine adds 30% more platoon effect

# Arm-stress reducer: lower arm slot less taxing on the shoulder/elbow.
# Only fires for release_angle < 0.5 (sub side); identity at 0.5+.
RELEASE_FATIGUE_SCALE: float = 0.20   # submarine (0.0) cuts fatigue by 10%

# ---------------------------------------------------------------------------
# Pitch catalog — O27 pitch types
# ---------------------------------------------------------------------------
# Each entry describes one pitch type's structural effects on per-pitch
# probabilities (via _pitch_probs) and contact quality (via contact_quality).
#
# Keys:
#   k_delta           added to swinging_strike probability (positive = more Ks)
#   bb_delta          added to ball probability
#   contact_delta     added to contact probability (negative = fewer balls in play)
#   hard_contact_shift  contact-quality shift (negative = suppresses hard contact)
#   weak_contact_shift  contact-quality shift (positive = drives ground balls)
#   platoon_mode      "standard" | "reverse" | "neutral" | "same_heavy" | "opposite_heavy"
#   platoon_scale     magnitude multiplier on PLATOON_PENALTY (0 = no platoon)
#   release_optimal   [0,1] — release angle where this pitch works best
#   release_window    half-width of the "full effectiveness" range around optimal
#   arm_stress        multiplier on per-pitch fatigue contribution (>1 = harder on arm)
#   max_release       Optional upper bound — pitch doesn't work above this angle
#   count_bias        "all" | "ahead" | "behind" | "2strike" — usage weight bias
#
# All deltas are quality-scaled: they represent the shift at quality=1.0 and
# collapse to 0 at quality=0.0. Identity at pitch_type=None.

PITCH_CATALOG: dict = {

    # ── FASTBALLS ─────────────────────────────────────────────────────────────
    "four_seam": {
        "velocity_class":     "high",
        # From sidearm, the four-seam lacks the downward plane that creates
        # swing-and-miss in MLB. It's a setup pitch more than a put-away.
        "k_delta":            +0.02,
        "bb_delta":           -0.01,
        "contact_delta":      -0.01,
        "hard_contact_shift": +0.04,   # HR-prone at lower quality — batters key on it
        "weak_contact_shift": -0.03,
        "platoon_mode":       "standard",
        "platoon_scale":       1.0,
        "release_optimal":     0.80,   # works better from higher slot
        "release_window":      0.30,
        "arm_stress":          1.10,
        "max_release":         None,
        "count_bias":          "behind",   # throw when behind — need a strike
    },
    "sinker": {
        "velocity_class":     "high",
        # O27's workhorse fastball. All movement, GB-heavy, HR-suppressing.
        "k_delta":            -0.02,
        "bb_delta":           -0.01,
        "contact_delta":      +0.01,
        "hard_contact_shift": -0.05,
        "weak_contact_shift": +0.06,
        "platoon_mode":       "standard",
        "platoon_scale":       1.0,
        "release_optimal":     0.45,   # natural sidearm pitch
        "release_window":      0.30,
        "arm_stress":          0.90,
        "max_release":         None,
        "count_bias":          "all",
    },
    "cutter": {
        "velocity_class":     "high",
        # Late-breaking. Drives weak contact, bonus vs opposite-handed.
        "k_delta":            +0.01,
        "bb_delta":           -0.01,
        "contact_delta":      -0.01,
        "hard_contact_shift": -0.03,
        "weak_contact_shift": +0.04,
        "platoon_mode":       "opposite_heavy",
        "platoon_scale":       0.8,
        "release_optimal":     0.60,
        "release_window":      0.30,
        "arm_stress":          1.00,
        "max_release":         None,
        "count_bias":          "all",
    },
    "palmball": {
        "velocity_class":     "low",
        # Deception over velocity. 78–82 mph that plays up because the arm
        # action looks like a regular fastball. Suppresses Ks but induces
        # unusual soft contact from the velocity mismatch. The "lost velocity"
        # veteran's fastball equivalent.
        "k_delta":            -0.03,
        "bb_delta":           +0.01,
        "contact_delta":      +0.02,
        "hard_contact_shift": -0.04,
        "weak_contact_shift": +0.05,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.5,
        "release_optimal":     0.50,   # works from any arm slot — it's the grip, not the slot
        "release_window":      0.50,
        "arm_stress":          0.75,
        "max_release":         None,
        "count_bias":          "ahead",   # keep the batter off-balance, not a strike-getter
    },

    # ── BREAKING BALLS ────────────────────────────────────────────────────────
    "slider": {
        "velocity_class":     "mid",
        # Standard hard slider. K-driving. Genuinely neutral platoon.
        "k_delta":            +0.04,
        "bb_delta":           +0.01,
        "contact_delta":      -0.02,
        "hard_contact_shift": -0.02,
        "weak_contact_shift": +0.02,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.0,
        "release_optimal":     0.50,
        "release_window":      0.40,
        "arm_stress":          1.00,
        "max_release":         None,
        "count_bias":          "2strike",
    },
    "sisko_slider": {
        "velocity_class":     "mid",
        # O27-original. Reverse-breaking — breaks INTO same-handed batters
        # rather than away. From sidearm the surprise is amplified: batters
        # expect the natural side-arm break to go away; the Sisko goes the
        # other direction. High K same-handed, neutral opposite-handed.
        "k_delta":            +0.03,
        "bb_delta":           +0.01,
        "contact_delta":      -0.02,
        "hard_contact_shift": -0.01,
        "weak_contact_shift": +0.01,
        "platoon_mode":       "same_heavy",
        "platoon_scale":       1.8,   # massive same-handed advantage
        "release_optimal":     0.25,  # best from sidearm / low sidearm
        "release_window":      0.25,
        "arm_stress":          1.05,
        "max_release":         0.70,  # loses its break magic above this angle
        "count_bias":          "2strike",
    },
    "walking_slider": {
        "velocity_class":     "mid",
        # Slow lateral slider — doesn't snap sharply but walks across the zone.
        # Batter commits before it arrives; the crafty veteran's version.
        "k_delta":            +0.02,
        "bb_delta":           +0.02,
        "contact_delta":      -0.01,
        "hard_contact_shift": -0.03,
        "weak_contact_shift": +0.05,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.3,
        "release_optimal":     0.35,
        "release_window":      0.30,
        "arm_stress":          0.90,
        "max_release":         0.75,
        "count_bias":          "ahead",
    },
    "curveball": {
        "velocity_class":     "mid",
        # Standard 12-to-6 curve. Hard-contact suppression, moderate K.
        # Worse from sidearm — the 12-to-6 topspin requires height to load.
        # Less common in O27 precisely because of the structural sidearm world.
        "k_delta":            +0.02,
        "bb_delta":           +0.02,
        "contact_delta":      -0.01,
        "hard_contact_shift": -0.04,
        "weak_contact_shift": +0.02,
        "platoon_mode":       "standard",
        "platoon_scale":       1.0,
        "release_optimal":     0.90,   # wants height — best from 3q sidearm
        "release_window":      0.25,   # quality degrades quickly toward sidearm
        "arm_stress":          1.05,
        "max_release":         None,
        "count_bias":          "ahead",
    },
    "curve_10_to_2": {
        "velocity_class":     "mid",
        # Sidearm/submarine specialist curve. Breaks diagonally — the pitcher
        # "steers" the batter's eye across the plate. Extreme weak contact,
        # GB-heavy. From sidearm a righty produces grounders to the right side.
        # Structurally incompatible with three-quarter release.
        "k_delta":            +0.01,
        "bb_delta":           +0.02,
        "contact_delta":      +0.01,
        "hard_contact_shift": -0.06,
        "weak_contact_shift": +0.09,   # extreme groundball
        "platoon_mode":       "standard",
        "platoon_scale":       1.3,    # amplified platoon from the weird break
        "release_optimal":     0.20,
        "release_window":      0.25,
        "arm_stress":          0.85,
        "max_release":         0.50,   # sidearm or below — won't break correctly above
        "count_bias":          "ahead",
    },

    # ── OFF-SPEED ─────────────────────────────────────────────────────────────
    "changeup": {
        "velocity_class":     "low",
        # Velocity differential. Reverse-platoon advantage (same-sided pitcher
        # changeup arm-side, boring in on the same-handed batter — works like
        # a screwball at reduced arm stress).
        "k_delta":            +0.01,
        "bb_delta":           +0.01,
        "contact_delta":      -0.01,
        "hard_contact_shift": -0.03,
        "weak_contact_shift": +0.04,
        "platoon_mode":       "reverse",
        "platoon_scale":       1.0,
        "release_optimal":     0.50,
        "release_window":      0.40,
        "arm_stress":          0.85,
        "max_release":         None,
        "count_bias":          "ahead",
    },
    "vulcan_changeup": {
        "velocity_class":     "low",
        # Tumbling action from the split-finger grip (middle+ring finger).
        # Higher K than regular changeup, devastating opposite-handed.
        "k_delta":            +0.03,
        "bb_delta":           +0.01,
        "contact_delta":      -0.02,
        "hard_contact_shift": -0.04,
        "weak_contact_shift": +0.03,
        "platoon_mode":       "opposite_heavy",
        "platoon_scale":       1.5,
        "release_optimal":     0.45,
        "release_window":      0.35,
        "arm_stress":          0.90,
        "max_release":         None,
        "count_bias":          "2strike",
    },
    "splitter": {
        "velocity_class":     "mid",
        # Hard off-speed with sharp downward break. K-driving, GB-heavy.
        "k_delta":            +0.04,
        "bb_delta":           +0.01,
        "contact_delta":      -0.02,
        "hard_contact_shift": -0.03,
        "weak_contact_shift": +0.04,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.3,
        "release_optimal":     0.50,
        "release_window":      0.35,
        "arm_stress":          1.10,   # stressful grip over a long season
        "max_release":         None,
        "count_bias":          "2strike",
    },

    # ── SPECIALTY / O27-REVIVED ───────────────────────────────────────────────
    "knuckleball": {
        "velocity_class":     "low",
        # Velocity-independent. Durability monster — knuckleballers pitch into
        # their late 40s. 2C-suppressing because nobody extends on a knuckler.
        "k_delta":            -0.01,
        "bb_delta":           +0.03,   # command is the challenge
        "contact_delta":      +0.01,
        "hard_contact_shift": -0.05,
        "weak_contact_shift": +0.03,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.0,    # handedness is irrelevant to a knuckleball
        "release_optimal":     0.50,
        "release_window":      0.50,   # works from any arm slot
        "arm_stress":          0.60,   # the easiest sustained pitch on the arm
        "max_release":         None,
        "count_bias":          "all",
    },
    "spitter": {
        "velocity_class":     "mid",
        # Legal in O27 lore. Extreme weak contact / GB, low K, low BB —
        # it tumbles into the zone and batters can't get under it.
        "k_delta":            -0.02,
        "bb_delta":           -0.01,
        "contact_delta":      +0.02,
        "hard_contact_shift": -0.07,
        "weak_contact_shift": +0.08,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.2,
        "release_optimal":     0.40,
        "release_window":      0.35,
        "arm_stress":          0.80,
        "max_release":         None,
        "count_bias":          "all",
    },
    "eephus": {
        "velocity_class":     "low",
        # 2C-disruption weapon when used selectively — never a primary pitch.
        # The batter can't reconcile the velocity with the arm action. When it
        # works, it works big; the rest of the time it's a ball or a foul.
        "k_delta":            +0.05,
        "bb_delta":           +0.03,
        "contact_delta":      -0.03,
        "hard_contact_shift": -0.02,
        "weak_contact_shift": +0.02,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.3,
        "release_optimal":     0.50,
        "release_window":      0.50,
        "arm_stress":          0.50,   # the most arm-friendly pitch in existence
        "max_release":         None,
        "count_bias":          "2strike",
    },
    "screwball": {
        "velocity_class":     "mid",
        # Reverse-breaking. Reverse-platoon advantage (righty screwball is the
        # righty's weapon against lefty bats). Higher arm stress.
        "k_delta":            +0.02,
        "bb_delta":           +0.01,
        "contact_delta":      -0.01,
        "hard_contact_shift": -0.03,
        "weak_contact_shift": +0.02,
        "platoon_mode":       "reverse",
        "platoon_scale":       1.2,
        "release_optimal":     0.40,
        "release_window":      0.30,
        "arm_stress":          1.25,   # genuinely punishing on the forearm
        "max_release":         None,
        "count_bias":          "ahead",
    },
    "gyroball": {
        "velocity_class":     "high",
        # Bullet gyrospin — minimal break but extreme deception. The ball
        # arrives at a different location than the batter's eye predicted.
        # Rare: maybe 3-4% of O27 pitchers throw one at elite quality.
        "k_delta":            +0.03,
        "bb_delta":           +0.01,
        "contact_delta":      -0.02,
        "hard_contact_shift": -0.06,   # can't square it up
        "weak_contact_shift": +0.02,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.4,
        "release_optimal":     0.50,
        "release_window":      0.40,
        "arm_stress":          1.10,
        "max_release":         None,
        "count_bias":          "2strike",
    },
}
