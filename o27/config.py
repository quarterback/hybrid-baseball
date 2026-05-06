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

PITCHER_DOM_BALL: float     = -0.06   # fewer balls when pitcher dominant
PITCHER_DOM_CALLED: float   = +0.03   # more called strikes
PITCHER_DOM_SWINGING: float = +0.03   # more swinging strikes
PITCHER_DOM_CONTACT: float  = -0.04   # fewer contact events

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

FATIGUE_THRESHOLD_BASE: int  = 6     # Phase 10.2: lower further — round 1 (BASE=10) was still dominated by lineup-cycling drift
# Phase 10 bump (10 → 24) was an over-correction. With BASE=24 and
# SCALE=40, an avg-stamina (0.5) pitcher's threshold landed at BF=44 —
# past the end of a 27-out arc (~36-40 BF) — so fatigue literally never
# fired inside a single appearance. Result: Decay (K%_arc1 - K%_arc3)
# was indistinguishable between workhorse starters (+0.35 mean) and
# short relievers (+0.50 mean). The stat existed; the mechanic didn't.
#
# At BASE=10 / SCALE=40 the curve becomes:
#   stamina 0.3 (low)        → threshold BF=22  (fatigues mid arc-2)
#   stamina 0.5 (avg)        → threshold BF=30  (fatigues late arc-2)
#   stamina 0.7 (high)       → threshold BF=38  (fatigues late arc-3)
#   stamina 0.9 (workhorse)  → threshold BF=46  (rarely fatigues — moat preserved)
# So the workhorse moat survives; the rest of the league actually gets
# tired across an arc, which produces the K%-by-arc differential the
# Decay stat is supposed to surface.
FATIGUE_THRESHOLD_SCALE: int = 40    # higher-stamina pitchers get longer spells
FATIGUE_MAX: float           = 0.80  # raised 0.60 → 0.80 so the cliff actually bites
FATIGUE_SCALE: float         = 12.0  # tightened 20 → 12 — flat-then-cliff curve

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

CONTACT_WEAK_BASE: float     = 0.38
CONTACT_MEDIUM_BASE: float   = 0.40
CONTACT_HARD_BASE: float     = 0.22
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
CONTACT_MOVEMENT_TILT: float = 0.06

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
TODAY_FORM_SIGMA: float = 0.10
TODAY_FORM_MIN:   float = 0.80
TODAY_FORM_MAX:   float = 1.20

# Multi-game fatigue (workload-debt) penalty applied on top of today_form.
# Identity invariant: at pitch_debt = 0, all of these collapse to no penalty.
# A pitcher's stamina-derived "budget" is `stamina * 100` pitches over the
# rolling 5-day window. Excess pitches above that scale the form down by
# `excess * FATIGUE_DEBT_PER_PITCH`, capped at FATIGUE_DEBT_MAX_PENALTY.
FATIGUE_DEBT_MIN_BUDGET:    float = 30.0   # pitches; floor for low-stamina arms
FATIGUE_DEBT_BUDGET_SCALE:  float = 100.0  # stamina (0-1) * this = budget pitches
FATIGUE_DEBT_PER_PITCH:     float = 0.005  # form penalty per pitch over budget
FATIGUE_DEBT_MAX_PENALTY:   float = 0.20   # cap on the form penalty
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
