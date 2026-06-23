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
    # "Smart batter" / work-the-count pass. Batters in O27 are disciplined:
    # they swing far less at early-count pitches, and the removed contact weight
    # is routed into TAKEN pitches (called strikes + balls) — NOT fouls. This is
    # the key O27 rule constraint: a foul is NOT free protection (3 fouls in an
    # AB = foul-out, pa.py), so the ONLY rules-legal way to deepen a count is to
    # take. Foul rates stay at/below the prior baseline. With two strikes,
    # trimmed whiff weight goes into CONTACT (put the ball in play — which also
    # feeds deep-count home runs), never into fouls, so the foul-out rate is not
    # inflated. 3-0 is a near-automatic take. Counts deepen (~3.6 pitches/PA),
    # pitchers work harder, and cheap first-pitch home runs collapse. Paired with
    # the count-aware contact authority in resolve_contact, this makes the
    # home-run-by-count distribution earned rather than front-loaded.
    # See docs/aar-hr-by-count-vs-mlb.md.
    # Format: (p_ball, p_called_strike, p_swinging_strike, p_foul, p_contact)
    (0, 0): (0.38, 0.26, 0.10, 0.14, 0.12),
    (1, 0): (0.40, 0.24, 0.08, 0.14, 0.14),
    (2, 0): (0.45, 0.21, 0.05, 0.14, 0.15),
    (3, 0): (0.53, 0.22, 0.03, 0.13, 0.09),
    (0, 1): (0.33, 0.21, 0.13, 0.18, 0.15),
    (1, 1): (0.36, 0.17, 0.12, 0.19, 0.16),
    (2, 1): (0.40, 0.14, 0.09, 0.20, 0.17),
    (3, 1): (0.46, 0.13, 0.07, 0.20, 0.14),
    (0, 2): (0.25, 0.11, 0.13, 0.29, 0.22),
    (1, 2): (0.28, 0.09, 0.13, 0.28, 0.22),
    (2, 2): (0.32, 0.07, 0.11, 0.27, 0.23),
    (3, 2): (0.34, 0.05, 0.10, 0.27, 0.24),
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
PITCHER_DOM_CALLED: float   = +0.015  # K-reduction pass: 0.03 → 0.015 to target 16-18% league K%
PITCHER_DOM_SWINGING: float = +0.025  # K-reduction pass: 0.06 → 0.025; recent bump to 0.06 was the main K-inflation driver
PITCHER_DOM_CONTACT: float  = -0.06   # fewer contact events (exceeds batter's +0.05 promotion)

# ---------------------------------------------------------------------------
# Batter dominance adjustments
# ---------------------------------------------------------------------------
# Applied as:  b_dom = (batter.skill - 0.5) * 2   →  −1.0 to +1.0

BATTER_DOM_SWINGING: float = -0.05   # K-reduction pass: -0.03 → -0.05; high-skill batters whiff materially less
BATTER_DOM_CONTACT: float  = +0.03   # more contact events

# ---------------------------------------------------------------------------
# Pitcher fatigue model
# ---------------------------------------------------------------------------
# Threshold (batters faced) before fatigue degrades performance:
#   threshold = max(FATIGUE_THRESHOLD_BASE,
#                   FATIGUE_THRESHOLD_BASE + round(pitcher_skill * FATIGUE_THRESHOLD_SCALE))
# Fatigue factor grows linearly beyond threshold, capped at FATIGUE_MAX.

FATIGUE_THRESHOLD_BASE: int  = 10    # Fatigue-dominance pass: 24 → 10; everyone enters the fatigue zone in a long outing
# Quadratic stamina (see prob.py): threshold = BASE + round(stamina**2 * SCALE).
# With BASE=10 and SCALE=65 this gives:
#   stamina 0.81 → 10 + round(0.656 * 65) = 53 BF (still goes deep)
#   stamina 0.73 → 10 + round(0.533 * 65) = 45 BF (cracks in arc3)
#   stamina 0.50 → 10 + round(0.250 * 65) = 26 BF (cracks in arc2)
#   stamina 0.30 → 10 + round(0.090 * 65) = 16 BF (opener / one-time-through)
# The quadratic shape makes elite stamina disproportionately valuable — the
# moat between 73 and 81 is now ~8 BF (was ~3 BF under linear formula).
FATIGUE_THRESHOLD_SCALE: int = 65    # higher-stamina pitchers get longer spells (40 → 65)
FATIGUE_MAX: float           = 1.00  # uncapped collapse for low-stamina arms past their limit
FATIGUE_SCALE: float         = 10.0  # spell_count divisor for ramp-up (20 → 10; steeper post-threshold cliff)

FATIGUE_BALL: float     = +0.09   # fatigue-dominance: +0.06 → +0.09
FATIGUE_CONTACT: float  = +0.10   # fatigue-dominance: +0.06 → +0.10 (more hard contact when tired)
FATIGUE_CALLED: float   = -0.06   # fatigue-dominance: -0.04 → -0.06
FATIGUE_SWINGING: float = -0.09   # fatigue-dominance: -0.06 → -0.09 (gassed pitchers lose whiffs)
FATIGUE_FOUL: float     = -0.06   # fatigue-dominance: -0.04 → -0.06

# ---------------------------------------------------------------------------
# In-game injuries (forced mid-game substitutions)
# ---------------------------------------------------------------------------
# Per-plate-appearance probabilities that a participant gets hurt and must
# leave the game. The engine only decides WHO leaves and forces the swap;
# severity (DTD / short / long IL) is drawn post-game in o27v2.injuries.
# Tuned for "moderate" volume — bench/bullpen depth matters, on the order of
# roughly half a forced removal per game. Tune via the engine-tunables
# dashboard. Set INJURY_INGAME_ENABLED=False to disable entirely.
INJURY_INGAME_ENABLED: bool          = True
INJURY_INGAME_PITCHER_BASE: float    = 0.0008  # current pitcher, before fatigue ramp
INJURY_INGAME_FATIGUE_SCALE: float   = 0.06    # per BF past his stamina threshold
INJURY_INGAME_FATIGUE_MULT_MAX: float = 6.0    # cap on the fatigue multiplier
INJURY_INGAME_BATTER_BASE: float     = 0.0006  # the batter due up
INJURY_INGAME_BASERUN_BASE: float    = 0.0011  # each runner on base (running is risky)
INJURY_INGAME_FIELD_BASE: float      = 0.0005  # one random fielder per PA
# Roster floor — never force a sub that would drop the active non-joker
# position pool below this; the player plays through instead.
INJURY_INGAME_ROSTER_FLOOR: int      = 7

# ---------------------------------------------------------------------------
# Contact quality distribution
# ---------------------------------------------------------------------------
# Base probabilities for weak / medium / hard contact.
# Shifted by matchup:  shift = (batter.skill - pitcher.pitcher_skill) * CONTACT_MATCHUP_SHIFT

# --- Physics-first resolver (resolve_batted_ball) -------------------------
# LA band cut points + per-band hit rates. The resolver maps the (EV, LA, spray)
# the batter produced to a base hit_type; these knobs are tuned so the league
# per-BIP mix reproduces the pre-inversion calibration (R/G ~24.2; single ~29%,
# double ~19%, GO ~14%, FO ~13%, LO ~8.5%, HR ~6%, ...). All EV in mph, LA/spray
# in degrees, distances in feet.
# Resolver-specific texture mix (decoupled from BATTED_BALL_WEIGHTS, which the
# runner-erasing OUT_SHIFT lever uses). O27 is a doubles-heavy environment, so
# the LA distribution is liner-dominant — these weights reflect that.
#                   (dribbler, grounder, liner, flyball)
RES_TEXTURE_WEIGHTS = {
    "weak":   (0.22, 0.34, 0.34, 0.10),
    "medium": (0.05, 0.22, 0.53, 0.20),
    "hard":   (0.0,  0.06, 0.53, 0.41),
}
RES_POPUP_LA: float        = 50.0   # above this LA → automatic fly out
RES_FLY_LA: float          = 26.0   # fly-ball band floor
RES_LINER_LA: float        = 10.0   # liner band floor (below → grounder)
# Fly band: distance vs fence decides HR; shortfalls drop for XBH or are caught.
RES_FLY_HIT_FLOOR: float   = 210.0  # drives shorter than this are always caught
RES_FLY_DROP_SCALE: float  = 0.92   # how readily sub-HR fly balls fall for XBH
RES_FLY_TRIPLE_P: float    = 0.13   # deep-alley double → triple chance
RES_HR_MARGIN: float       = 13.0   # ft of carry past the fence required for a HR
# Liner band: highest BABIP.
RES_LINER_EV_MID: float    = 90.0
RES_LINER_EV_SPAN: float   = 25.0
RES_LINER_HIT_BASE: float  = 0.76
RES_LINER_HIT_EVSCALE: float = 0.18
RES_LINER_XBH_EV: float    = 83.0
RES_LINER_XBH_SCALE: float = 1.28
RES_LINER_TRIPLE_EV: float = 95.0
RES_LINER_TRIPLE_P: float  = 0.38
# Grounder band.
RES_GB_EV_MID: float       = 90.0
RES_GB_EV_SPAN: float      = 30.0
RES_GB_HIT_BASE: float     = 0.44
RES_GB_HIT_EVSCALE: float  = 0.40
RES_GB_INFIELD_EV: float   = 72.0   # weak grounders that sneak through → infield single
RES_GB_FC_P: float         = 0.15   # grounder out → fielder's choice share
# Re-homed run-environment levers (were table redistributions): per-half form
# lifts EV (hot half → more carry → XBH/HR at the same hit count), RISP trims it
# (the XBH-suppression decoupler). Both in mph; 0.0 = identity.
RES_FORM_EV_SCALE: float   = 18.0
RES_RISP_EV_TRIM: float    = 3.0
# Count-aware contact authority (the HR-by-count realism fix; see
# docs/aar-hr-by-count-vs-mlb.md). A home run should be EARNED by the count,
# not count-flat: contact made ahead in the count (hitter sitting on a pitch)
# carries; defensive two-strike / first-pitch-behind contact does not. Modeled
# as an EV shift of CONTACT_COUNT_EV_SCALE mph per unit of (balls - strikes),
# clamped to ±CONTACT_COUNT_EV_CLAMP mph. Crucially ZERO at 0-0 (balls==strikes)
# so the realism identity contract at a fresh count is untouched, and ~mean-zero
# over the league BIP-by-count distribution so total HR volume is ~preserved —
# the effect REDISTRIBUTES home runs toward hitters' counts, not inflates them.
CONTACT_COUNT_EV_SCALE: float = 2.0
CONTACT_COUNT_EV_CLAMP: float = 6.0
# Behind-in-the-count contact penalty (the 0-2 fix). The EV shift above moves
# the ball's carry; this degrades the QUALITY of the contact itself. A batter
# behind in the count is defending — whatever talent he brings, the contact is
# less authoritative. Applied as a multiplicative hard-contact penalty (same
# mechanism as the weather hard-contact multiplier): hard-contact probability is
# cut and the lost mass falls to weak, so a would-be-barrel becomes mishit.
#
# Generalized into a full per-count POWER PROFILE: a single multiplier on
# hard-contact at each (balls, strikes) encoding the batter's APPROACH at that
# count. The O27 design intent (project owner): on 0-0 the hitter is optimizing
# for good contact, NOT a bomb, so first-pitch power is suppressed; the deeper /
# fuller / more ahead the count, the more he commits to driving the ball, so a
# full count (3-2) and hitter's counts carry the most power. Behind with two
# strikes he defends (the original 0-2 penalty lives on as the low entries).
# 1.0 = neutral (no change). Zero deviation at the default count used by the
# contact-quality identity test (it calls contact_quality without a count, so
# the multiplier defaults to 1.0 there).
COUNT_POWER_PROFILE: dict[tuple, float] = {
    (0, 0): 0.82,   # first pitch: square it up, don't sell out for power
    (1, 0): 1.00,
    (2, 0): 1.08,   # ahead — can sit on a pitch
    (3, 0): 1.12,
    (0, 1): 0.86,
    (1, 1): 1.00,
    (2, 1): 1.06,
    (3, 1): 1.15,   # premium hitter's count
    (0, 2): 0.71,   # buried — defending, choke up
    (1, 2): 0.86,
    (2, 2): 1.05,
    (3, 2): 1.25,   # FULL COUNT — max commitment, swing to do damage
}

CONTACT_WEAK_BASE: float     = 0.18   # offense pass: 0.38 → 0.18; far fewer weak singles
CONTACT_MEDIUM_BASE: float   = 0.50   # offense pass: 0.40 → 0.50
CONTACT_HARD_BASE: float     = 0.32   # offense pass: 0.22 → 0.32; more XBH / HR potential
CONTACT_MATCHUP_SHIFT: float = 0.25   # max ±0.125 swing per unit matchup (unchanged — preserves pitcher differentiation)

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

# H~R variance pass: single weights raised in the WEAK/MEDIUM tiers so balls in
# play turn into hits far more often (the primary ask — more hits, college-ball
# contact levels). Ground-ball volume is kept high enough to feed the now-live
# double-play channel (see prob.py). The hit total is the lever here; the
# game-to-game decoupling of runs from hits lives in the per-half sequencing
# form and the double-play rate further below. See docs/aar-hits-runs-variance.md
# for the full story, including why the H~R correlation is largely structural.
WEAK_CONTACT: list = [
    ("ground_out",      False, False, 0.42),
    ("fly_out",         False, True,  0.13),
    ("line_out",        False, False, 0.07),
    ("single",          True,  False, 0.34),
    ("fielders_choice", True,  False, 0.04),
]

MEDIUM_CONTACT: list = [
    ("ground_out",      False, False, 0.18),
    ("fly_out",         False, True,  0.10),
    ("line_out",        False, False, 0.08),
    ("single",          True,  False, 0.44),
    ("double",          True,  False, 0.14),
    ("fielders_choice", True,  False, 0.06),
]

HARD_CONTACT: list = [
    ("single",   True,  False, 0.20),
    ("double",   True,  False, 0.24),
    ("triple",   True,  False, 0.08),
    ("hr",       True,  False, 0.22),
    ("fly_out",  False, True,  0.19),
    ("line_out", False, False, 0.15),
]

# ---------------------------------------------------------------------------
# Runner advancement model
# ---------------------------------------------------------------------------
# Per-base advancement probabilities on a batted hit. Previously every
# runner auto-advanced (3B/2B always scored on a single; 2B/1B always
# scored on a double), which collapsed H ≈ R and stripped the box score
# of any "wasted runners" signal. These tables drive a probabilistic
# resolution: each runner on base rolls independently for what happens
# to them on contact. Fuzzy off-round percentages intentional — keeps
# the numbers looking observed rather than designed.

# Single, runner on 3B. These are the MEAN conversion rates; the per-half
# sequencing form (below) swings them widely game to game. A moderate mean
# (down from the old near-automatic 0.71) keeps a single from reliably
# dripping in a run, but the real variance now lives in the form — a hot half
# clears 3B on contact, a cold half strands him.
ADVANCE_3B_ON_1B_SCORE: float    = 0.44
ADVANCE_3B_ON_1B_HOLD: float     = 0.50
ADVANCE_3B_ON_1B_OUT: float      = 0.06

# Single, runner on 2B — the classic close play at the plate. Low mean (most
# 2B runners stop at 3B on a single), wide game-to-game swing from the form.
ADVANCE_2B_ON_1B_SCORE: float    = 0.20
ADVANCE_2B_ON_1B_HOLD_3B: float  = 0.65
ADVANCE_2B_ON_1B_OUT: float      = 0.15

# Double, runner on 2B (almost auto, occasionally held with a deep relay
# and the rare turn-2 chase that catches the runner short).
ADVANCE_2B_ON_2B_SCORE: float    = 0.62
ADVANCE_2B_ON_2B_HOLD_3B: float  = 0.34
ADVANCE_2B_ON_2B_OUT: float      = 0.04

# Double, runner on 1B — 1B-to-home on a double. Pulled down off the old 0.40
# so a double with a man on first usually leaves him at third; the run comes
# when the form is hot or the next ball is squared up, not automatically.
ADVANCE_1B_ON_2B_SCORE: float    = 0.26
ADVANCE_1B_ON_2B_TO_3B: float    = 0.48
ADVANCE_1B_ON_2B_HOLD_2B: float  = 0.19
ADVANCE_1B_ON_2B_OUT: float      = 0.07

# Single, runner on 1B — almost always 1B → 2B; some 1B → 3B; TOA risk
# meaningful when the OF charges in to throw behind the runner.
ADVANCE_1B_ON_1B_TO_3B: float    = 0.12
ADVANCE_1B_ON_1B_TO_2B: float    = 0.77
ADVANCE_1B_ON_1B_OUT: float      = 0.11

# Player-modifier scale on the SCORE probability. Speed pushes score up
# (and hold down); outfielder arm pushes score down (and out up). Mods
# are signed deviations from the 0.5 neutral attribute, multiplied by
# 2 so the full range hits the configured limits.
SPEED_ADVANCE_MOD: float         = 0.12
ARM_ADVANCE_MOD: float           = 0.11

# Infield-hit leg-out: on a borderline grounder, batter foot speed vs the
# fielding infield's arm decides whether the throw to first beats the runner.
# Folded into the weak/medium hit-vs-out flex as an additive bonus on GROUND
# balls only (fly/line plays are unaffected — you don't leg out a fly). Scale
# is the deviation (speed - infield_arm), so a burner vs a weak-armed infield
# legs out more infield singles; a plodder vs rocket arms is rung up. Mean-
# neutral across the league (symmetric in speed and arm). Identity at 0.0.
INFIELD_HIT_SPEED_SCALE: float   = 0.20

# Extra-base probability: chance += max(0, (speed - 0.5) * RUNNER_EXTRA_SPEED_SCALE)

RUNNER_EXTRA_SPEED_SCALE: float = 0.35

# ---------------------------------------------------------------------------
# Batted-ball texture (the "wasted hits" mechanism)
# ---------------------------------------------------------------------------
# A hit's TEXTURE — how it was struck — decides how productive it is. Rolled
# from contact quality + batter power into {dribbler, grounder, liner, flyball}
# and carried as outcome_dict["batted_ball"] (NOT a hit_type, so all the hit/AB
# stat-counting that switches on hit_type strings is untouched). Low-power
# contact hitters spray grounders; sluggers hit liners — player-differentiated.
#
# CRITICAL LESSON (learned the hard way): in O27's single continuous 27-out
# inning, making a runner HOLD on a grounder does NOT reduce runs — there is no
# inning-end to strand him, so he scores on a later PA (~87% of baserunners
# score eventually). The only thing that lowers runs-per-hit is ERASING a
# runner. So texture's run effect is primarily an additive bump to the "out"
# (thrown-out-advancing) bucket of the advancement tables, not a score haircut.
# All-zero shifts reproduce pre-texture behavior exactly (identity).
BATTED_BALL_WEIGHTS = {
    # quality:   (dribbler, grounder, liner, flyball)
    "weak":      (0.30, 0.45, 0.20, 0.05),
    "medium":    (0.12, 0.40, 0.38, 0.10),
    "hard":      (0.0,  0.12, 0.53, 0.35),
}
BATTED_BALL_POWER_TILT: float = 0.30   # power_dev shifts grounder→liner→flyball

# Additive bump to the "out" outcome (runner gunned down advancing) in the
# single/double advancement tables, by texture. This ERASES runners → the real
# H/R lever. Grounders/dribblers draw more throws and force plays; liners let
# runners move cleanly (slightly negative).
BATTED_BALL_OUT_SHIFT = {
    "dribbler": 0.30,
    "grounder": 0.40,
    "liner":   -0.03,
    "flyball":  0.00,
}
# Secondary score-bucket nudge (small — mostly flavor / a few held runners).
BATTED_BALL_SCORE_SHIFT = {
    "dribbler": -0.20,
    "grounder": -0.12,
    "liner":    +0.05,
    "flyball":   0.00,
}

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
RUNNER_THROWN_OUT_AT_HOME_BASE: float        = 0.18
RUNNER_THROWN_OUT_AT_HOME_SPEED_SCALE: float = 0.22
RUNNER_THROWN_OUT_AT_HOME_SKILL_SCALE: float = 0.22
RUNNER_THROWN_OUT_AT_HOME_MIN: float         = 0.05

# ---------------------------------------------------------------------------
# Unified per-half "locked in" form  (the shared draw behind the two below)
# ---------------------------------------------------------------------------
# The offensive sequencing form (slugging / baserunning / GIDP) and the RISP
# clutch form (talent penalty / XBH suppression) used to be rolled as TWO
# independent Gaussian draws per batting half. Independent draws de-correlate
# the channels: a hot-slugging half and a hot-converting half rarely lined up,
# so within a game the effects averaged out instead of compounding — which is
# why every prior pass lowered R/H but never widened the game-to-game efficiency
# tails ("blow-it-open vs leave-em-loaded").
#
# This unifies them into ONE latent draw per half — "the lineup is locked in
# tonight" — that feeds ALL of those channels at once. Because one draw now
# moves conversion AND slugging AND baserunning together, a hot night compounds
# into a blowout and a cold night into a stranded-rally loss. The per-channel
# strengths stay as their own constants (SEQ_FORM_*_SCALE, RISP_CLUTCH_*_RELIEF
# below) so each channel is still independently tunable / disable-able; this
# block governs only the shared DRAW.
#
# Same best-hitter + manager-vibes anchor the old clutch form used (performance-
# grounded, not pure noise):
#   best    = max_p [ BAT_POWER_W*p.power + BAT_SKILL_W*p.skill ]
#   quality = (best - 0.5)*BAT_W + (mgr_risp_pressure - 0.5)*MGR_W
#   mean    = 1 + MEAN_SCALE*quality;  form = clamp(Normal(mean, SIGMA), MIN, MAX)
# Set LOCKED_FORM_SIGMA <= 0 to disable the whole mechanism (every half at 1.0).
LOCKED_FORM_SIGMA: float        = 0.66   # shared per-half hot/cold spread
LOCKED_FORM_MIN: float          = 0.08   # coldest possible half
LOCKED_FORM_MAX: float          = 2.15   # hottest possible half
# Base center of the draw before the team-quality shift. The channels are
# asymmetric — a hot half (form>1) relieving the RISP penalty AND slugging adds
# more runs than an equally-cold half strands (floor effects) — so widening the
# spread via SIGMA also drifts mean R/H upward. Setting the base center a touch
# below 1.0 pulls the mean back so we widen the game-to-game SPREAD without
# shifting the league's mean R/H off "a hit ≠ a run" (~0.93). 1.0 = no offset.
LOCKED_FORM_MEAN_BASE: float    = 0.94
LOCKED_FORM_MEAN_SCALE: float   = 0.55   # how strongly team quality shifts the mean
LOCKED_FORM_BAT_W: float        = 0.85   # weight: best hitter's quality (the anchor)
LOCKED_FORM_BAT_POWER_W: float  = 0.5    # best-hitter score: power share
LOCKED_FORM_BAT_SKILL_W: float  = 0.5    # best-hitter score: skill share
LOCKED_FORM_MGR_W: float        = 0.15   # weight: manager persona (small vibes nudge)

# ---------------------------------------------------------------------------
# Offensive sequencing form (the H~R decoupler)
# ---------------------------------------------------------------------------
# The structural problem: with ~45 PAs per team in a single 27-out arc, the
# law of large numbers crushes per-game conversion variance — no matter where
# the per-event ADVANCE_* rates sit, every game's runs-per-baserunner
# converges to nearly the same ratio, so R tracks H almost 1:1 with no tails.
# Real "few hits / many runs" and "many hits / few runs" box scores come from
# SEQUENCING: whether a lineup strings its hits and walks together with traffic
# on base, or sprays them across dead innings. To reproduce that, each half
# draws ONE offensive sequencing form (a team-wide "we're clicking tonight" vs
# "we left the bases loaded all night" factor). It shifts every base-advancement
# SCORE roll that half in the same direction, correlating conversion across all
# the half's PAs — which is what actually inflates Var(R | H) and pulls the
# H~R correlation down off the structural ceiling.
#
# form ~ Normal(1.0, SEQ_FORM_SIGMA), clamped to [MIN, MAX]. The shift fed to
# the advancement tables is (form - 1.0) * SEQ_FORM_SCORE_SCALE: a hot half
# pushes runners home on contact, a cold half strands them. Set SEQ_FORM_SIGMA
# to 0.0 to disable the mechanism entirely (every half plays at form 1.0).
# NOTE: the DRAW is now the unified LOCKED_FORM_* above; SEQ_FORM_SIGMA/MIN/MAX
# are retained only for reference (no longer roll a separate sequencing draw).
SEQ_FORM_SIGMA: float       = 0.62   # (superseded by LOCKED_FORM_SIGMA)
SEQ_FORM_MIN: float         = 0.08   # (superseded by LOCKED_FORM_MIN)
SEQ_FORM_MAX: float         = 2.10   # (superseded by LOCKED_FORM_MAX)
SEQ_FORM_SCORE_SCALE: float = 1.20   # LIVE: form deviation → additive score-prob shift

# The dominant decoupler. H counts a single and a homer the same; R does not.
# The reason runs track hits ~1:1 in O27 is that per-game contact QUALITY
# (the single↔double↔HR mix) barely varies — large-PA averaging pins every
# game's slugging near the mean. This scale routes the same per-half form into
# a sum-preserving power redistribution (identical machinery to the per-batter
# power rating), so a hot half turns its hits into extra-base hits — runs
# spike while the hit COUNT holds — and a cold half dinks singles that pile up
# as hits but strand. That single↔XBH swing, not baserunning, is what pulls a
# game's runs off its hit total and opens the "8 hits / 11 runs" and
# "16 hits / 4 runs" tails. form 1.0 = identity (no quality shift).
SEQ_FORM_POWER_SCALE: float = 1.45   # LIVE: form deviation → redistribution strength

# Edge scales for the form's dedicated single<->XBH<->HR redistribution. Large
# on purpose (the per-batter POWER_REDIST_* edges are gentle by comparison): at
# a +1 form deviation, `scale` fraction of the `from` row migrates to the `to`
# row. Hot halves move singles/doubles into homers; cold halves (negative dev)
# pull HRs back down into singles. Sum-preserving, so hit count is unchanged.
SEQ_REDIST_HARD_S2HR: float  = 0.55  # hard single   -> hr
SEQ_REDIST_HARD_D2HR: float  = 0.45  # hard double   -> hr
SEQ_REDIST_HARD_LO2HR: float = 0.60  # hard line_out -> hr (squared-up vs caught)
SEQ_REDIST_MED_S2D: float    = 0.75  # medium single -> double

# Form sensitivity of the double-play rate (the runner-erasing channel). A
# cold half (form < 1) hits into MORE double plays — rallies die on the bases
# instead of every runner coming around; a hot half (form > 1) stays out of
# them. This is what lets a cold offense pile up hits that go nowhere.
SEQ_FORM_GIDP_SCALE: float   = 1.20   # LIVE

# ---------------------------------------------------------------------------
# RISP pressure ("the wobble")
# ---------------------------------------------------------------------------
# The engine otherwise fiats hits from talent + dice regardless of situation,
# which — in a 27-out inning where ~87% of baserunners already score — is why
# runs track hits ~1:1. This makes converting runners in scoring position a
# genuine, high-variance struggle instead of a formality:
#
#   1. TALENT WOBBLE. With a runner on 2B or 3B, the hitter's effective
#      capability is knocked down by a per-at-bat random draw in
#      [RISP_TALENT_PENALTY_MIN, MAX] (≈ 29-41%). It's a multiplier folded into
#      the batter's condition term in contact_quality, so it sags matchup, power
#      and eye together — success (a single or more) simply becomes less likely.
#      The per-AB draw IS the wobble: same hitter, different pressure each time.
#   2. WEAKER HITS. When a RISP at-bat does produce a hit, it's mostly a single.
#      A sum-preserving redistribution pulls HR/triple/double weight back into
#      singles, so the big multi-run, bases-clearing hit is rarer with runners
#      on — runners advance station-to-station and pile up instead of being
#      driven in all at once.
#
# Together these cap RISP conversion and let a high-hit offense still strand the
# yard. Set RISP_TALENT_PENALTY_MAX = 0.0 to disable the wobble; set the
# RISP_XBH_* scales to 0.0 to disable the hit-type suppression.
RISP_TALENT_PENALTY_MIN: float = 0.29   # min fraction knocked off batter talent
RISP_TALENT_PENALTY_MAX: float = 0.41   # max fraction (per-AB uniform draw)

# Sum-preserving "XBH → single" suppression at RISP. At dev 1.0 (the neutral
# per-half clutch level), `scale` fraction of the from-row migrates into singles.
RISP_XBH_HARD_HR2S: float   = 0.45   # hard hr     -> single
RISP_XBH_HARD_T2S: float    = 0.55   # hard triple -> single
RISP_XBH_HARD_D2S: float    = 0.35   # hard double -> single
RISP_XBH_MED_D2S: float     = 0.40   # medium double -> single

# ---------------------------------------------------------------------------
# Per-half RISP clutch form (the streak/hot-cold lever)
# ---------------------------------------------------------------------------
# A flat RISP penalty makes clutch conversion uniformly bad, which lowers R/H
# but does NOT create the game-to-game variance the sport wants — teams that
# "click," guys getting hot, good clubs stringing good days into good months.
# This rolls ONE clutch form per batting half (same idea as the offensive
# sequencing form) that scales how hard the RISP wobble bites that half:
#   - a HOT half (form > 1) relieves the talent penalty AND lifts the XBH
#     suppression — the lineup squares up with runners on and clears the bases;
#   - a COLD half (form < 1) deepens the penalty and clamps hits to singles —
#     the rally dies, runners strand.
# Because it's shared across every RISP at-bat in the half, it compounds into
# real blow-it-open vs leave-em-loaded games instead of averaging out.
#
# The form is NOT pure noise. Its MEAN is shifted by team quality so good teams
# get hot more often than bad ones (performance-based, but cold/hot-induced).
# The anchor is the team's BEST HITTER (max over the lineup of a power/skill
# blend, wherever he bats), with the manager persona as a small vibes nudge:
#   best    = max_p [ BAT_POWER_W*p.power + BAT_SKILL_W*p.skill ]
#   quality = (best - 0.5)*BAT_W + (mgr_risp_pressure - 0.5)*MGR_W
#   mean    = 1 + MEAN_SCALE * quality;  form = clamp(Normal(mean, SIGMA), MIN, MAX)
# Over a season a good roster staggers hot halves into good months; a bad one
# tanks the same way. Set RISP_CLUTCH_SIGMA <= 0 to disable (flat penalty).
# NOTE: the DRAW + its anchor are now the unified LOCKED_FORM_* block above.
# The SIGMA/MIN/MAX/MEAN_SCALE/BAT_*/MGR_W constants here are retained only for
# reference; only the two RELIEF constants below are still LIVE (they set how
# hard the shared form modulates the RISP channels).
RISP_CLUTCH_SIGMA: float            = 0.45   # (superseded by LOCKED_FORM_SIGMA)
RISP_CLUTCH_MIN: float              = 0.12   # (superseded by LOCKED_FORM_MIN)
RISP_CLUTCH_MAX: float              = 1.95   # (superseded by LOCKED_FORM_MAX)
RISP_CLUTCH_MEAN_SCALE: float       = 0.55   # (superseded by LOCKED_FORM_MEAN_SCALE)
RISP_CLUTCH_BAT_W: float            = 0.85   # (superseded by LOCKED_FORM_BAT_W)
RISP_CLUTCH_BAT_POWER_W: float      = 0.5    # (superseded by LOCKED_FORM_BAT_POWER_W)
RISP_CLUTCH_BAT_SKILL_W: float      = 0.5    # (superseded by LOCKED_FORM_BAT_SKILL_W)
RISP_CLUTCH_MGR_W: float            = 0.15   # (superseded by LOCKED_FORM_MGR_W)
RISP_CLUTCH_PENALTY_RELIEF: float   = 0.95   # LIVE: hot-form relief on the talent penalty
RISP_CLUTCH_XBH_RELIEF: float       = 1.00   # LIVE: hot-form relief on XBH suppression

# ---------------------------------------------------------------------------
# Inside-the-park home runs
# ---------------------------------------------------------------------------
# Modern MLB sees ~10-20 inside-the-park HRs a SEASON: small, circular,
# regular outfields give the ball nowhere to hide. O27 never had a
# cookie-cutter era — its parks are deadball-era large/irregular (cavernous
# alleys, ovals, triangles, odd corners), so ITPHRs are the deep-park
# release valve and run noticeably hotter than the modern rate.
#
# Trigger: ONLY a clean deep `triple` (errors carry hit_type "error" and
# are scored reached-on-error / "Little League HR", never a HR — so the
# error path can never become an ITPHR). The ball must die in a genuinely
# deep part of the yard (fence at the BIP spray ≥ MIN_FENCE) on a real
# drive (proxy distance ≥ MIN_DISTANCE), and a fast/aggressive batter must
# decide to go for it.
#
# Two-stage resolution:
#   1. P(attempt to circle): base + park-depth + speed + aggressiveness.
#   2. If attempting, P(touches home safely) vs the relay: base + speed +
#      baserunning − OF arm. Success → HR (arms the Walk-Back, exactly like
#      an over-the-fence HR). Failure splits into thrown-out-at-home (an
#      out, no hit) vs scrambled back to 3B (stays a triple), the split
#      driven by aggressiveness.
ITP_HR_MIN_FENCE: float            = 400.0   # fence (ft) at the BIP spray angle
ITP_HR_MIN_DISTANCE: float         = 330.0   # proxy carry (ft) — a genuine deep drive
ITP_HR_BASE_ATTEMPT: float         = 0.16    # base P(attempt) on a qualifying deep triple
ITP_HR_DEPTH_SCALE: float          = 0.0030  # +P(attempt) per ft of fence beyond MIN_FENCE
ITP_HR_ATTEMPT_SPEED_SCALE: float  = 0.55    # +P(attempt) per (speed - 0.5)
ITP_HR_ATTEMPT_AGGRO_SCALE: float  = 0.35    # +P(attempt) per (run_aggressiveness - 0.5)
ITP_HR_ATTEMPT_MAX: float          = 0.85
ITP_HR_BASE_SUCCESS: float         = 0.52    # base P(safe at home) given an attempt
ITP_HR_SUCCESS_SPEED_SCALE: float  = 0.55    # +success per (speed - 0.5)
ITP_HR_SUCCESS_BASERUN_SCALE: float = 0.35   # +success per (baserunning - 0.5)
ITP_HR_SUCCESS_ARM_SCALE: float    = 0.45    # −success per (of_arm - 0.5)
ITP_HR_SUCCESS_MIN: float          = 0.08
ITP_HR_SUCCESS_MAX: float          = 0.92
ITP_HR_FAIL_OUT_BASE: float        = 0.55    # given a failed attempt, P(out at home) vs held at 3B
ITP_HR_FAIL_OUT_AGGRO_SCALE: float = 0.40    # +P(out) per (run_aggressiveness - 0.5) — aggressive runners get gunned

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
#   - Low end (~9%):  3B alone, hard contact, fast batter, weak defense.
#   - Mid (~17-18%):  1B alone, medium contact, neutral attributes.
#   - High end (~30%): bases loaded, weak contact, slow batter, elite defense.
# Rates raised as part of the H~R decoupling pass. With the DP gate fixed to
# fire all half long (see prob.py) instead of only the first 2 outs, the double
# play is now O27's real runner-erasing event — the thing that lets a high-hit
# offense still strand and post a low run total. The band is widened so a cold,
# rally-killing half (with the form multiplier on top) can turn grounders into
# twin-killings at a much higher clip than the old per-inning MLB rate.
GIDP_BASE_PROB: float    = 0.26
GIDP_SPEED_SCALE: float  = 0.20
GIDP_DEFENSE_SCALE: float = 0.15
GIDP_MIN_PROB: float     = 0.09
GIDP_MAX_PROB: float     = 0.50

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
# and room left in the half to record three outs. Conditional on a DP
# firing in the eligible base config, this probability promotes it to a
# TP. Set to 0 to disable.
# NOTE (O27 gate): like the DP gate, the trigger is NOT MLB's per-inning
# "nobody out" rule. There are no innings — one continuous 27-out half —
# so the literal `outs == 0` gate let a TP fire only on the very first out
# of the entire half, making triple plays effectively dead code (0 in a
# 400-game sample). The eligibility now mirrors the DP gate: the half just
# has to have room for the three outs a TP records. The base rate is kept
# low so that, fired all half long, TPs land at a believably rare clip.
# Baserunner errors can also induce a TP — a runner with low baserunning
# skill (poor read off the bat, late tag-up) inflates the TP probability
# via the SKILL bonus below.
TRIPLE_PLAY_GIVEN_DP_PROB: float       = 0.05
TRIPLE_PLAY_BASERUNNING_BONUS: float   = 0.06   # added when lead runner is below-average

# ---------------------------------------------------------------------------
# TOOTBLAN — thrown out trying for the extra base on a hit / fly / grounder.
# When a runner ATTEMPTS the extra base (probability driven by speed +
# baserunning + aggressiveness in prob._runner_advance), this layer decides
# whether the slide beats the throw. Identity preserved at neutral inputs:
# at speed = baserunning = aggressiveness = 0.5 the attempt probability
# from RUNNER_EXTRA_SPEED_SCALE is already 0, so TOOTBLAN never fires.
TOOTBLAN_SAFE_BASE: float  = 0.46   # baseline safe rate when an attempt fires
TOOTBLAN_SKILL_SCALE: float = 0.40  # +(baserunning - 0.5) * this
TOOTBLAN_SPEED_SCALE: float = 0.20  # +(speed       - 0.5) * this
TOOTBLAN_SAFE_MIN: float    = 0.32  # floor — even bad runners aren't always out
TOOTBLAN_SAFE_MAX: float    = 0.88  # ceiling — even elite runners aren't auto-safe

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
PICKOFF_ATTEMPT_BASE: float        = 0.035  # per pitch, 1B with avg-aggression runner
PICKOFF_AGGRESSION_SCALE: float    = 0.012  # +(run_aggressiveness - 0.5) * this
PICKOFF_LHP_1B_BONUS: float        = 0.005  # absolute boost vs 1B runner from LHP
PICKOFF_2B_DAMPENER: float         = 0.40   # 2B pickoffs much rarer than 1B
PICKOFF_SUCCESS_BASE: float        = 0.38   # baseline catch rate when a move fires
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
SAC_BUNT_BASE_PROB: float          = 0.085  # base call rate when conditions align
                                            # (2026 recalibration: position players
                                            # were sacrificing ~5x too often; pure
                                            # surrender sacs are mostly -EV, so the
                                            # weak-bat sac is now a smaller slice and
                                            # the pitcher carries the sac load below)
SAC_BUNT_RUNGAME_SCALE: float      = 0.50   # mgr_run_game * this multiplies
SAC_BUNT_LEVERAGE_DAMPER: float    = 0.50   # (1 - leverage_aware) * this multiplies
SAC_BUNT_HIT_BASE: float           = 0.21   # baseline bunt-for-hit rate (raised:
                                            # good bunts reach base ~a third of the
                                            # time; old 0.10 made bunts near-useless)
SAC_BUNT_HIT_SPEED_SCALE: float    = 0.36   # +(speed - 0.5) * this
SAC_BUNT_FAIL_RATE: float          = 0.10   # popups / runner forced at lead

# Pitchers bat in O27 (no DH), and the weak-hitting pitcher is the classic
# sacrifice bunter — historically why bunting was far more common when pitchers
# hit. They give themselves up to move a runner; they don't drag (too slow) or
# squeeze (rarely asked to execute under pressure). This is their own elevated
# sacrifice rate, kept separate from the position-player SAC_BUNT_BASE_PROB.
PITCHER_SAC_BUNT_BASE_PROB: float  = 0.34   # base sac call rate for a pitcher at bat

# ---------------------------------------------------------------------------
# Expanded bunting (multi-type). Layered on top of the legacy SAC_* knobs
# above (kept for the sacrifice outcome roll). A single `bunt` player rating
# (bat control, 0..1, 0.5 neutral) governs execution across every type.
#
# Outcome model is shared: a per-PA "execution" score = bunt_skill, tilted by
# the pitcher's difficulty (stuff/command) and the batter's speed for the
# beat-it-out leg, decides between a clean bunt, a bunt single, and the
# failure modes (popup, or the lead runner thrown out on a forced play).
BUNT_SKILL_EXEC_SCALE: float       = 0.60   # how much bunt skill swings execution
BUNT_PITCHER_DIFFICULTY_SCALE: float = 0.25 # elite stuff/command suppresses success
# Sacrifice: chance the lead runner is thrown out (FC) instead of advancing,
# scaling DOWN with bunt skill. Replaces part of the old flat "fail".
SAC_LEAD_OUT_BASE: float           = 0.16   # poor bunters cost the lead runner
SAC_BUNT_SKILL_SCALE: float        = 0.25   # +(bunt - 0.5) * this to clean-sac odds
# Bunt-for-hit / drag: speed + skill beat-out attempt, no give-up intent.
DRAG_BUNT_BASE_PROB: float         = 0.095  # base call rate (fast, weak-power bat).
                                            # Tilted UP relative to sac/squeeze so
                                            # the bunt mix favors bunt-for-hit, the
                                            # one bunt that meaningfully reaches base.
DRAG_BUNT_SPEED_GATE: float        = 0.52   # min speed to consider a drag
DRAG_BUNT_HIT_BASE: float          = 0.47   # baseline safe rate (skill/speed add).
                                            # Raised so drags hit at a realistic
                                            # clip and lift the overall bunt-hit rate.
# Squeeze (runner on 3B, < 2 outs). Suicide = runner breaks (run scores on any
# bunt down, but a missed bunt hangs him out); safety = runner holds (scores
# only on a good bunt, never thrown out).
SQUEEZE_BASE_PROB: float           = 0.025  # base call rate in a squeeze spot (rare,
                                            # high-leverage). Cut hard: squeezes were
                                            # ~40% of all bunts (wildly over-represented);
                                            # the squeeze is a small, situational slice.
SQUEEZE_SUICIDE_SHARE: float       = 0.45   # of squeezes, fraction that are suicide
SUICIDE_MISS_BASE: float           = 0.20   # missed/popped bunt → runner out at home
SAFETY_SQUEEZE_SCORE_BASE: float   = 0.60   # baseline run-scores rate on a safety

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
SB_SUCCESS_BASE: float            = 0.58   # baseline success — catchers now win meaningfully
SB_SUCCESS_SPEED_SCALE: float     = 0.50   # (speed - 0.5) * this adds to success
SB_SUCCESS_PITCHER_SCALE: float   = 0.20   # pitcher_skill * this subtracts from success
SB_SUCCESS_DEBT_SCALE: float      = 0.0008 # pitcher.pitch_debt * this ADDS to success
                                           # — tired battery = easier steal
SB_SUCCESS_CATCHER_ARM_SCALE: float = 0.35 # catcher.arm * this SUBTRACTS from success
                                           # — elite catcher arm shuts down the running game
SB_SUCCESS_MIN: float             = 0.18   # floor on steal success
SB_SUCCESS_MAX: float             = 0.86   # ceiling on steal success

# 2C / stay defense-read — chance the defense breaks up a valid stay by
# nailing the lead runner. Catcher pickoff at second, OF charge-and-throw,
# IF rotation catching the runner napping. Without this, every valid 2C
# advanced runners unopposed; now the defense gets a real shot at the
# baserunner. Scales with team defense and catcher arm so elite defenses
# meaningfully suppress the 2C running game.
STAY_DEFENSE_READ_BASE: float          = 0.07   # baseline lead-runner-out rate
                                                # (was 0.10 — trimmed so the
                                                # 2C average RV moves closer
                                                # to neutral; mechanic stays
                                                # high-variance but no longer
                                                # ~as bad as taking an out)
STAY_DEFENSE_READ_TEAM_SCALE: float    = 0.20   # (team_def - 0.5) * this
STAY_DEFENSE_READ_CATCHER_SCALE: float = 0.20   # (catcher_arm - 0.5) * this
STAY_DEFENSE_READ_MIN: float           = 0.03
STAY_DEFENSE_READ_MAX: float           = 0.28

# ---------------------------------------------------------------------------
# Defense model
# ---------------------------------------------------------------------------
# Identity: at team_defense_rating = 0.5 (neutral) every term collapses to 0.

# Range modifier — better team defense converts more BIPs into outs.
# Applied as a probabilistic flip: when an outcome resolves to an out OR a
# single, a small fraction of cases flip in proportion to (defense - 0.5).
DEFENSE_RANGE_SHIFT_SCALE: float = 0.15   # max ±15% out↔single conversion swing

# Error rate — share of would-be-out plays that become a "reached on
# error" instead. Scales inversely with team defense. The earlier ~1.8%
# baseline left league-wide fielding pct essentially at 1.000 because so
# few BIPs ever resolve to a would-be-out (Pesäpallo-style 2C drives the
# safe-rate up); this floor was lifted so visible errors actually appear
# on the leaderboard.
DEFENSE_ERROR_BASE: float        = 0.045   # ~4.5% of would-be-outs at neutral D
DEFENSE_ERROR_SCALE: float       = 0.060   # (0.5 - team_def) * this adds to E rate
DEFENSE_ERROR_MIN: float         = 0.010
DEFENSE_ERROR_MAX: float         = 0.090

# ---------------------------------------------------------------------------
# Defensive gems — fielding ratings turn would-be hits into outs
# ---------------------------------------------------------------------------
# A fielder makes a spectacular play that erases a hit. Deliberately
# PROBABILISTIC and per-FIELDER, not a fixed trait: a base rate means anyone
# in the position with a decent glove can flash one, and the individual
# fielder's defense/arm scales the rate up (elite) or toward zero (poor). So
# you get "even a guy you don't think of as a defensive wizard robs one
# sometimes," while the genuine glove does it far more often. Surfaced in the
# play-by-play as a "ROBBED!" / great-play line (see render). All-zero base =
# identity (no gems). These erase a hit, so they also nudge BA/H down slightly.
GEM_BASE_XBH: float      = 0.075  # base chance an extra-base hit is run down / robbed
GEM_BASE_SINGLE: float   = 0.050  # base chance a single is turned into an out
GEM_HARD_MULT: float     = 1.35   # hard contact is more rob-able (carry / hang time)
GEM_FIELDER_SCALE: float = 1.30   # (fielder_def - 0.5)*2 * this scales the base rate
GEM_ARM_SCALE: float     = 0.40   # (fielder_arm - 0.5)*2 * this adds (arm → more outs)
GEM_MAX: float           = 0.42   # cap — even elite fielders don't rob everything

# ---------------------------------------------------------------------------
# Catcher game-calling — "calling a good O27 game" as a real lever
# ---------------------------------------------------------------------------
# The catcher's game_calling rating shifts contact_quality away from hard
# contact: a great caller sequences pitches to the pitcher's strengths and the
# batter's holes, a poor one lets hitters square it up. Applies only to whoever
# is currently behind the plate (the fielding team's catcher). Identity at 0.5.
# This is the offense-suppressing counterweight that rewards a defensive,
# pitch-and-catch club. (NOT framing — O27 skips framing by design.)
CATCHER_GAME_CALLING_SHIFT_SCALE: float = 0.16   # (gc-0.5)*2 * this → contact shift

# Catcher fatigue + rotation. No catcher squats for all 27 outs — as the outs
# pile up behind the plate his game-calling slips, which is the PRESSURE that
# forces a manager to spend a bench catcher. Fatigue ramps once outs caught
# pass the threshold; it degrades game_calling (and could be extended to arm).
# A manager with a rested backup rotates the tiring starter out; how a club
# prioritizes its catching corps swings the late innings.
CATCHER_FATIGUE_THRESHOLD: int           = 18    # outs caught before fatigue bites
CATCHER_FATIGUE_SCALE: float             = 9.0   # (outs-threshold)/this → fatigue
CATCHER_FATIGUE_MAX: float               = 0.80  # cap on the fatigue fraction
CATCHER_FATIGUE_GAME_CALLING_SCALE: float = 0.30 # fatigue * this drops game_calling
CATCHER_FATIGUE_ARM_SCALE: float         = 0.25  # fatigue * this drops catcher arm
CATCHER_ROTATION_OUT_GATE: int           = 6     # no swap before this (first-batter guard)

# ---------------------------------------------------------------------------
# Defensive substitution timing
# ---------------------------------------------------------------------------
# A defensive replacement is a late-inning lock-in, not an early-game move.
# Two timing gates on should_defensive_sub keep them from churning early:
#   - a hard floor (no defensive subs in the opening outs of any game), and
#   - a rarity window before the late-game out where they normally belong:
#     even when leverage clears, an early defensive sub only fires on a small
#     probability roll.
DEFENSIVE_SUB_MIN_OUTS: int    = 3      # never before this many outs
DEFENSIVE_SUB_LATE_OUT: int    = 16     # the late-game window opens here
DEFENSIVE_SUB_EARLY_RATE: float = 0.05  # P(allow) when leverage clears pre-window

# ---------------------------------------------------------------------------
# Blowout management — rest the starters once the game is decided
# ---------------------------------------------------------------------------
# Nobody rides a starter to a 130-pitch complete game in a laugher, and nobody
# bats the same nine 8 times while up 40 — the bench and bullpen come in. These
# gate "rest the regulars" behavior so it ONLY fires when the lead is decisive
# (not in close games) and the starters have already banked real work.
BLOWOUT_PULL_LEAD: int       = 10   # pitcher's team lead to start resting the SP
BLOWOUT_PULL_MIN_OUTS: int   = 12   # ...and only after this many outs (so a reliever fits)
BLOWOUT_REST_LEAD: int       = 10   # batting team's lead to start resting position regulars
BLOWOUT_REST_MIN_CYCLE: int  = 2    # ...and only once the order has turned this many times

# ---------------------------------------------------------------------------
# Last-licks leverage — deploy bats in the decisive half
# ---------------------------------------------------------------------------
# The team batting SECOND is in O27's bottom-of-the-9th: its at-bats are
# do-or-die. In a close, late spot the manager should reach for the bench to
# manufacture situational runs (a non-star shouldn't just bat through a key
# spot) — even if it doesn't pan out. This boosts the pinch-hit / pinch-run
# leverage score there, leaving the first-batting team (building a total) and
# blowouts (gap too large) untouched.
DECISIVE_HALF_LEVERAGE_BONUS: float = 0.12   # added to leverage in the decisive chase
DECISIVE_HALF_MIN_OUTS: int         = 12     # only this deep into the half
DECISIVE_HALF_MAX_GAP: int          = 3      # only when the game is this close

# Late-game platoon pinch-hitting: when a bench bat flips an unfavorable
# handedness matchup to favorable, the manager values it more the later it
# gets. Scaled by the game's lateness (out/27), so it's a non-factor early and
# a real pull late. Added on top of the matchup factor.
PLATOON_LATE_BONUS: float = 0.10

# Pinch-run specialist preference: nudge a dedicated burner (roster_slot
# 'pr_specialist' or role_run) to the top of the pinch-run candidate pool so
# the manager sends the right legs, not just any faster bat.
PR_SPECIALIST_BONUS: float = 0.10




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
# Semi-rare rule probabilities
# ---------------------------------------------------------------------------

# Pitcher's balk: illegal motion with runners on base. All runners advance 1B.
# Fires per pitch when runners are on. Tuned for ~1 per team per 4-5 seasons.
BALK_PROB_PER_PITCH: float = 0.00015

# Catcher's balk: illegal pre-pitch catcher positioning or timing.
# Penalty: automatic ball (walk if count reaches 4 balls). Rarer than pitcher's balk.
CATCHERS_BALK_PROB_PER_PITCH: float = 0.00008

# Catcher's interference: catcher's mitt contacts the bat during a swing.
# Fires on swinging_strike or contact outcomes. Batter awarded 1B.
CATCHER_INTERFERENCE_PROB: float = 0.00035

# Dropped third strike: catcher drops strike 3 with 1B unoccupied.
# Pitcher retains the K; batter races to 1B.
DROPPED_THIRD_STRIKE_BASE_PROB: float = 0.038
DROPPED_THIRD_STRIKE_OUT_AT_FIRST: float = 0.72  # fraction thrown out at 1B

# Defensive indifference: fielding team doesn't contest a stolen base.
# Fires when deep in the half AND run differential is large.
# Scored as DI — no SB or CS credited.
DI_MIN_OUTS: int = 20
DI_RUN_DIFF_THRESHOLD: int = 6

# Fielder's obstruction: fielder blocks the base path on a thrown-out runner.
# Runner is ruled safe; charged as a defensive error.
FIELDER_OBSTRUCTION_PROB: float = 0.004

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

# --- EV-driven 2C decision (should_stay_prob) ------------------------------
# The 2C now resolves through the real hitting engine and an out on a stay
# retires the batter, so the decision is pure expected-value: stay only when
# advancing/scoring runners beats the out risk. These constants tune that math
# (see docs/design-2c-hitting-engine-rework.md). Tuned to keep the run
# environment near O27's ~10 R/half baseline.
STAY_OUT_RISK_WEAK: float        = 0.45   # P(out) staying on weak contact
STAY_OUT_RISK_MEDIUM: float      = 0.25   # P(out) staying on medium contact
STAY_OUT_RISK_SKILL_SCALE: float = 0.40   # contact+eye−command lowers out risk
STAY_REWARD_3B: float            = 1.00   # value of advancing the 3B runner (scores)
STAY_REWARD_2B: float            = 0.55   # value of advancing the 2B runner
STAY_REWARD_1B: float            = 0.25   # value of advancing the 1B runner
STAY_OUT_COST: float             = 0.80   # run-cost of making the out
# Value of RUNNING — i.e. the hit you'd be forgoing by staying. A second chance
# is a bet that the NEXT contact beats this one, so you don't waste a good hit:
# a clean (medium) single is worth more on the bases than a weak one, which
# raises the bar to stay on it. Staying only pays when the RISP upside clearly
# beats the forgone hit (high risk → must pay off).
STAY_RUN_BASELINE_WEAK: float    = 0.30   # forgoing a weak single — low bar
STAY_RUN_BASELINE_MEDIUM: float  = 0.60   # forgoing a clean single — high bar
STAY_EDGE_TO_PROB: float         = 0.90   # EV-edge → stay-probability slope
STAY_MAX_PROB: float             = 0.85   # saturation cap
STAY_JOKER_MULT: float           = 1.50   # jokers leverage 2C the most

# (Legacy frequency multipliers — retained for any callers/tests; no longer
# used by the EV-driven should_stay_prob.)
STAY_RISP_MULT: float          = 1.40   # 2B or 3B occupied
STAY_1B_ONLY_MULT: float       = 0.70   # only 1B occupied

# Count-aware 2C frequency. A patient hitter ahead in the count is
# selective — he's "waiting for his pitch." If marginal contact comes
# while he's still hunting, he stays so runners move and he gets
# another swing at a better pitch. Note: 2-strike counts do NOT get
# a "foul-off" lift in O27 — 3 fouls is a FOUL OUT, not survival, so
# the MLB foul-off metaphor doesn't apply here.
STAY_AHEAD_IN_COUNT_MULT: float    = 1.15   # balls > strikes (patient, waiting)

# Late-game push: in the last third of the half (outs ≥ 18), the batting
# team plays aggressively to manufacture runs. Lift is large enough that
# it OVERCOMES the 1B-only damper — late game with only 1B occupied
# (0.70 × 1.55 ≈ 1.09) ends up slightly above baseline, modeling the
# "get this runner into scoring position somehow" tactic.
LATE_GAME_OUTS_THRESHOLD: int      = 20     # 20+ outs = late arc (matches
                                            # the user's "20-27 outs" frame)
STAY_LATE_GAME_MULT: float         = 1.55

# ---------------------------------------------------------------------------
# Defensive shifts. O27 design philosophy: offense is aggressive (lots of
# contact, lots of 2C), and defense's counter is being nimble — including
# defensive shifts. Shifts are decided per-AB at AB start based on the
# batter's spray (pull_pct) and the fielding manager's mgr_shift_aggression.
#
# Mechanic:
#   shift fires when:   rng < |pull_pct - 0.5| * 2 * mgr_shift_aggression
#   on contact, direction roll: rng < batter.pull_pct → pull-side, else oppo
#   if shifted + ground-ball outcome + pull-side: single → ground_out
#       at SHIFT_PULL_OUT_PROB
#   if shifted + ground-ball outcome + oppo-side:  ground_out → single
#       at SHIFT_OPPO_HIT_PROB
# Telemetry: state.fielding_team.shift_outs_added / shift_hits_lost
# accumulate per game, so we can see exactly how much each shift call
# contributed.
SHIFT_PULL_OUT_PROB: float       = 0.42   # infield shift: pull single → out
SHIFT_OPPO_HIT_PROB: float       = 0.25   # infield shift: oppo gnd_out → single
SHIFT_DECISION_SCALE: float      = 1.8    # tunable knob on decision frequency
SHIFT_BASE_PROB: float           = 0.35   # floor so even neutral-spray batters get shifted
SHIFT_DECISION_MAX: float        = 0.95   # cap on per-AB shift probability

# Outfield shift (4-man OF / infielders shallow). Trades infield coverage
# for outfield range against pull-power FB hitters. Effects:
#   pull-side double/triple → single   (the 4th OFer cuts off the gap)
#   pull-side fly_out stays            (already an out)
#   oppo-side ground_out → single      (one fewer IFer = more gaps)
SHIFT_OF_XBH_HELD_PROB: float    = 0.40   # OF shift: pull double → single
SHIFT_OF_OPPO_HIT_PROB: float    = 0.35   # OF shift: oppo gnd_out → single
# Threshold for picking outfield shift over infield shift: pull-heavy
# batter with this much power or more goes to outfield shift.
SHIFT_OF_POWER_THRESHOLD: float  = 0.55

# Leverage multiplier: shifts are a "prevent defense" tool the manager
# leans on harder in critical situations (RISP + late game). Models the
# tennis-scoring leverage: shifts are routine all game, but they ratchet
# in the moments that decide the result.
SHIFT_LEVERAGE_MULT: float       = 1.45   # RISP + late-arc combined boost

# Adaptability erosion. When the manager keeps the SAME shift alignment
# against the SAME batter across consecutive ABs, the batter's adaptability
# rating progressively reads the gaps. Each streak step subtracts this
# fraction from the shift's effective probability (capped at streak=3).
ADAPTABILITY_SCALE: float        = 0.10

# Bunt-against-shift. When an infield shift is on, a speedy batter can
# push a bunt the other way for an easy hit. This adds a no-runner bunt
# path on top of the existing sac-bunt logic.
BUNT_AGAINST_SHIFT_BASE_PROB: float = 0.18   # baseline scaled by speed dev

# ---------------------------------------------------------------------------
# Power Play (optional league rule)
# ---------------------------------------------------------------------------
# An opt-in, per-league rule. When enabled, the FIELDING manager may deploy a
# 10th defender — the "nickel fielder" (NF / scorekeeping position 10), a
# middle outfielder — for a use-or-lose window of up to POWER_PLAY_WINDOW_OUTS
# outs. The nickel covers the outfield gaps, suppressing extra-base hits and
# running some would-be singles down. The window:
#   - opens at most once per defensive half (use it or lose it),
#   - lasts up to 4 outs but always ends when the half ends (no carryover),
#   - is available again, fresh, in a Declared Seconds frame,
#   - is NEVER available in extra (super) innings.
#
# POWER_PLAY_ENABLED is a plain bool, so o27v2.engine_config auto-exposes it as
# a dashboard toggle that saves per environment — that IS the per-league
# checkbox (off by default, so identical talent can be A/B-tested on vs. off).
POWER_PLAY_ENABLED: bool = False     # league opt-in; off = zero behavior change

POWER_PLAY_WINDOW_OUTS: int = 4      # max outs the nickel stays on the field

# Fielding effect while the window is active (applied in resolve_contact,
# after the shift layer). The nickel covers center-outfield gaps.
POWER_PLAY_XBH_HELD_PROB: float   = 0.35   # double/triple → single (gap cut off)
POWER_PLAY_SINGLE_OUT_PROB: float = 0.12   # outfield single → fly_out (run down)
# Share of outfield putouts re-credited to the nickel while active (PO logged
# under position "NF"). Roughly the slice of the outfield the nickel patrols.
POWER_PLAY_NICKEL_PO_SHARE: float = 0.33

# Presence effect — the *mere* arrival of the 10th defender tightens the whole
# unit for as long as the window is open, beyond just balls hit at the nickel.
# While active we apply a small MULTIPLICATIVE lift to the fielding team's
# defense_rating (the "across the lineup" knob — error chance, ground-out
# conversion, borderline plays all read it) and to the active pitcher's
# effectiveness attrs (command / Stuff / movement / grit), so every downstream
# roll sees a settled defense and a pitcher who can work the zone. The lift is
# stashed-and-restored per PA (same lifecycle as leadership flares), so nothing
# drifts and it's inert the instant the window closes.
#
# Banded 0.1%–4.4% PER POWER PLAY, scaled by the nickel's glove: a replacement-
# grade nickel barely moves the needle, an elite one lands near the top. It is
# deliberately NOT a magic pill — the sport advantages the runner everywhere
# else, so offense still wins most exchanges; this just makes the defense's one
# lever measurable.
POWER_PLAY_PRESENCE_MIN: float = 0.001   # 0.1% — floor (any eligible nickel)
POWER_PLAY_PRESENCE_MAX: float = 0.044   # 4.4% — cap (elite-glove nickel)

# Nickel eligibility. A rostered player NOT currently on the field, eligible at
# OF or SS, who clears BOTH bars below. Pitchers qualify only as a wild card
# (lightly-used arms) and only if they have not already appeared in the game.
POWER_PLAY_NICKEL_ARM_MIN: float   = 0.62  # strong throwing arm
POWER_PLAY_NICKEL_FIELD_MIN: float = 0.58  # good glove at OF/SS

# Manager deployment behavior. Rolled per game per fielding team (not a sticky
# manager trait), so the same skipper varies game to game.
POWER_PLAY_SKIP_GAME_PROB: float = 0.05    # team never deploys this game
POWER_PLAY_MISTIME_PROB: float   = 0.09    # team deploys too early / too late
# Suppress deployment when the game is out of hand (no good reason to spend it).
POWER_PLAY_BLOWOUT_MARGIN: int   = 8       # |run diff| ≥ this → hold the window
# Per-AB deploy-probability ramp across the out arc (engine settles naturally).
POWER_PLAY_DEPLOY_BASE_EARLY: float = 0.03   # outs < 12
POWER_PLAY_DEPLOY_BASE_MID: float   = 0.15   # 12 ≤ outs < late threshold
POWER_PLAY_DEPLOY_BASE_LATE: float  = 0.50   # late arc
POWER_PLAY_DEPLOY_BASE_FORCED: float = 0.90  # ≤ window outs remain (use-or-lose)
POWER_PLAY_CLOSE_GAME_MULT: float   = 1.4    # tight game raises deploy urgency

# ---------------------------------------------------------------------------
# Cricket Batting Order (optional league rule)
# ---------------------------------------------------------------------------
# An opt-in, per-league rule. When enabled, the batting order FLIPS at the end
# of each trip through the order — the 1-9 order becomes 9-1 for the next cycle,
# so the tail rotates to the top — BUT only on a trip in which the manager
# deployed no joker. Using a joker locks the order for that cycle (no flip), so
# the rule trades a tactical joker insertion against keeping a favorable
# top-of-order. The flip persists across phases (regulation / Declared Seconds /
# super-innings) exactly as the order does today.
#
# CRICKET_BATTING_ORDER_ENABLED is a plain bool, so o27v2.engine_config
# auto-exposes it as a dashboard toggle that saves per environment — that IS the
# global per-league default (off by default, so identical talent can be A/B-
# tested on vs. off, and the per-league checkbox composes via "league opt-in OR
# global default"). See o27/engine/cricket_order.py for the gate + flip logic.
CRICKET_BATTING_ORDER_ENABLED: bool = False   # league opt-in; off = zero change

# Manager flip decision (manager.should_use_flip). An earned flip is use-or-lose
# at the top of the new cycle; whether the skipper spends it is persona-driven
# (mgr_flip_aggression, 0.5 neutral) and situational (score + out-arc). The
# probability is BASE × persona_mult × situational, capped at MAX.
CRICKET_FLIP_BASE_PROB: float   = 0.55   # neutral-persona spend rate when earned
CRICKET_FLIP_AGG_SCALE: float   = 1.4    # persona span: mult = (1-S/2)..(1+S/2)·… (see code)
CRICKET_FLIP_TRAIL_SCALE: float = 0.30   # trailing (need offense) raises spend desire
CRICKET_FLIP_ARC_SCALE: float   = 0.25   # later in the 27-out arc raises spend desire
CRICKET_FLIP_MAX_PROB: float    = 0.97   # ceiling so it's never a certainty

# Joker opportunity cost. While the rule is on, in regulation, and the current
# trip is still joker-free, deploying a joker forfeits the chance to EARN this
# cycle's flip. Flip-minded skippers (high mgr_flip_aggression) therefore damp
# their joker insertion rate by up to this fraction; joker-happy skippers
# (low flip aggression) are barely affected, so they keep spending jokers and
# rarely flip. The first joker of a cycle pays this cost; once the flip is
# already forfeited, further jokers that cycle are undamped.
CRICKET_JOKER_FLIP_DAMP: float  = 0.60   # max fractional cut to joker-insert prob

# Flip-aware lineup construction. A flip-minded skipper (mgr_flip_aggression at
# or above this bar) whose league runs the rule builds his order to read well in
# BOTH directions — strongest bats at the ends, weakest (the pitcher) buried in
# the MIDDLE — so a flip doesn't hand the next cycle a tail-led order. Below the
# bar, the order is built normally (best-to-worst, pitcher 9th).
CRICKET_FLIP_LINEUP_AGG_MIN: float = 0.60

# Flip-aware lineups optimize handedness alternation as a TIEBREAKER, but only
# within arrangements that keep the valley's directional balance. This is the
# hard cap: a candidate is rejected if its forward-vs-reverse talent disparity
# exceeds this fraction of the standard (best-to-worst) order's disparity. So
# platoon weighting can never reopen the directional gap the valley closes.
CRICKET_FLIP_DISPARITY_MAX_RATIO: float = 0.25

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
GRIT_FATIGUE_RESIST: float     = 0.30   # fatigue-dominance: 0.60 → 0.30; grit now caps at ±15% modifier so stamina drives the variance
PLAYER_DEFAULT_GRIT: float     = 0.50   # identity

# ---------------------------------------------------------------------------
# Manager heuristics — joker insertion (§4.6)
# ---------------------------------------------------------------------------

JOKER_SCORE_DIFF_MAX: int          = 4      # |score_diff| ≤ this → high leverage
JOKER_OUTS_CEILING: int            = 22     # state.outs < this → not too late

# NOTE: the old weak-hitter override (JOKER_WEAK_BATTER_THRESHOLD /
# JOKER_WEAK_INSERT_BASE / JOKER_WEAK_INSERT_AGG_SCALE) was removed. It fired
# at 0.75-0.95 every cycle on the worst bats, which benched them all game.
# Joker insertion is now purely the leverage path in should_insert_joker(): a
# joker comes in only when it out-hits the batter due up AND the spot is
# high-leverage (tight, late, runners on). See o27/engine/manager.py.

# ---------------------------------------------------------------------------
# Joker rating decay — applied to a joker's effective ratings each AB
# ---------------------------------------------------------------------------
# Each successive use of the same joker in one game shrinks their rating
# deviations (skill, eye, contact, power) toward replacement level. The
# manager can keep inserting them, but the realistic outcome is that
# they stop being productive — which is the behavior we want without a
# hard cap. Curve is hard-coded in prob._joker_decay_factor; these
# constants name the breakpoints so they're visible / tunable.
JOKER_DECAY_FLOOR: float          = 0.50  # multiplier on the 10th+ use
JOKER_DECAY_K_BREAKPOINT_PA: int  = 5     # K penalty kicks up here
JOKER_DECAY_STEEP_BREAKPOINT_PA: int = 7  # steeper drop from here

# ---------------------------------------------------------------------------
# Intentional walks — manager refuses to pitch to a hot or elite batter
# ---------------------------------------------------------------------------
# Decision flow in manager.should_intentional_walk:
#   hard gates → 1B must be empty, not 2-out bases-empty, score gap ≤ MAX
#   probability = BASE + (hot + elite + leverage) * (AGG_FLOOR + AGG_SCALE * mgr_ibb_aggression)
#   capped at MAX_PROB
# Tuned so a hot bat (3+ hits today) with RISP and 1B open against an
# aggressive skipper gets walked ~30-45% of the time, while a cold bat
# in low-leverage spots almost never does.

IBB_ENABLE: bool             = True
IBB_BASE_PROB: float         = 0.00   # bare-context IBB rate (rises with hot+leverage)
IBB_MAX_PROB: float          = 0.35   # ceiling per-PA (tuned down from 0.55 — see below)
IBB_HOT_HITS_THRESHOLD: int  = 3      # 3+ hits today triggers multi-hit bonus
IBB_HOT_HITS_BONUS: float    = 0.30   # added to "hot" factor when threshold met
IBB_AVG_FLOOR: float         = 0.350  # only in-game AVG above this contributes
IBB_HOT_SCALE: float         = 0.70   # scales (avg − floor) → hot factor
IBB_SKILL_FLOOR: float       = 0.70   # only batter.skill above this contributes
IBB_SKILL_SCALE: float       = 0.40   # scales (skill − floor) → elite factor
IBB_AGG_FLOOR: float         = 0.12   # baseline aggression multiplier
IBB_AGG_SCALE: float         = 0.45   # additional from mgr_ibb_aggression
IBB_MAX_SCORE_GAP: int       = 6      # skip IBB beyond this score gap (blowout)
# Rate note (measured over 40-game samples): ~0.8 IBBs/game at
# mgr_ibb_aggression=0.1, ~1.3 at 0.5 (neutral), ~2.2 at 0.9. Still
# above MLB's ~0.3/game, but the design intent is "the mechanic is
# visibly present and persona-differentiated," not strict real-world
# frequency. The original 0.55 ceiling / wider scales produced
# ~2.2/game even at neutral, which read as spammy.

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
# Declared Seconds — declaration mechanics
# ---------------------------------------------------------------------------
SECONDS_MIN_DECLARE_OUT: int = 22       # earliest out (out 22 = bank up to 5)
SECONDS_MAX_DECLARE_OUT: int = 26       # latest out that still banks at least 1
SECONDS_MAX_BANKED:      int = 6        # cap; declared at out 22 banks 5, etc.
SECONDS_MAX_ROUNDS_PER_TEAM: int = 2    # initial half + at most one seconds

# Declaration AI thresholds for hard-accept / hard-reject branches
SECONDS_INSURMOUNTABLE: int = 25        # lead at out <= 21 triggers insurance declare
SECONDS_BLOWOUT_MARGIN: int = 20        # |score_diff| >= this → game decided

# Declaration AI — soft probability formula (marginal cases)
DECLARE_BASE:          float = 0.04
DECLARE_LEAD_SCALE:    float = 0.18
DECLARE_PERSONA_SCALE: float = 0.25

# Pre-game bat-order choice — home manager. Held at 0.50 so the bat-first
# decision is genuinely close-to-even at neutral inputs; the park / starter
# / persona scalars below still let strong situational signals push it
# one way or the other. The earlier 0.65 baseline was a "retcon" for the
# pre-Declared-Seconds home-scores-more asymmetry, but with the new
# baserunning friction in place that retcon now bakes in a 71% home
# bat-first rate, which translates into a ~4 R/g home advantage in
# practice. 0.50 lets bat-first be a genuine choice driven by context.
BAT_FIRST_BASE:          float = 0.50
# Tight cap on how far the persona / park / starter / bullpen / weather
# scalars are allowed to push the home manager's bat-first probability
# away from BAT_FIRST_BASE. With a wide range, those scalars become a
# strategic lever ONLY the home manager gets — visitors have no
# equivalent — and that hidden tactical edge re-creates the home
# advantage even when the base is symmetric. Holding the deviation
# under 1 percentage point makes the bat-order call essentially a coin
# flip with vanishing situational influence.
BAT_FIRST_HOME_EDGE_CAP: float = 0.01

# -----------------------------------------------------------------------
# Bat-second viability — symmetric-by-role nudges
# -----------------------------------------------------------------------
# Without these, batting first is a near-solved strategy in O27: the
# first-batting team sets a target and declares with a lead, while the
# second-batting team chases from behind. The mechanics are symmetric
# but the context isn't, so bat-second is ~4 R/g worse in practice.
# These two knobs make the environment marginally friendlier to whoever
# is in the bat-second role (regardless of home/visitor identity).

# (1) Target pressure — the team batting second knows the number to
# beat. Their first cycle through the order gets a small contact-quality
# tilt (locked-in, target-aware hitters). Fades to zero over the next
# few PAs as the half becomes routine.
TARGET_PRESSURE_SHIFT:     float = 0.030   # added to contact-quality shift
                                           # (positive = harder contact)
TARGET_PRESSURE_FADE_PAS:  int   = 12      # bonus is full at PA 1, 0 by PA 13

# (1b) Rebuttal-phase offense tilt — when the game pauses for a
# declaration, pitchers cool off but batters keep their timing. So the
# seconds round (and any super-inning rebuttal where the same pause
# logic applies) is a slightly higher-offense environment than
# regulation. Implemented as a small contact-quality shift active for
# every PA in a seconds round. Helps the trailing team marginally more
# (they're the ones needing the rebuttal runs), without making any
# rule asymmetric — leading teams that come into seconds for insurance
# get the same tilt, they just need it less.
REBUTTAL_OFFENSE_SHIFT:    float = 0.035   # added to contact-quality shift
                                           # during seconds (and SI) phases

# (2) Fielding fatigue — the team that batted first must field the
# entire second half. By the late arc of that fielding stint, their
# range / glovework / arm have slipped. Subtracted from defense_rating
# for the would-be-out / error rolls when state.outs hits this threshold.
FIELDING_FATIGUE_PENALTY:  float = 0.030   # subtracted from def_dev late half
FIELDING_FATIGUE_OUT_GATE: int   = 20      # applies once state.outs ≥ this
BAT_FIRST_PARK_SCALE:    float = 0.15
BAT_FIRST_STARTER_SCALE: float = 0.20
BAT_FIRST_PERSONA_SCALE: float = 0.30

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
BATTER_CONTACT_SWINGING: float = -0.08   # K-reduction pass: -0.05 → -0.08; high-contact batters now rarely whiff
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

# --- Times-through-the-order familiarity ----------------------------------
# "Any sport has arbitrage — guys eventually crack the code." In a 27-out arc
# the top of the order can face one pitcher 5–7 times in a single game (vs 2–3
# in MLB), so batter familiarity is a first-class pitching dynamic here, not a
# rounding error. Each prior PA a batter has had against THIS pitcher this game
# tilts the matchup toward the hitter — he's timing the arm up. The penalty is
# attenuated by the pitcher's repertoire-weighted timing_resistance: a
# knuckleballer / eephus artist / junkballer stays un-timeable (near-zero
# penalty), while a pure-velocity "flamethrower" gets solved by the 4th look.
# This is the lever that makes the deception archetypes the sport's arbitrage —
# the workhorse who keeps showing the lineup something un-timeable out-survives
# the velocity arm over a marathon half-inning.
#
# fam dominance = FAMILIARITY_PER_LOOK * min(looks, MAX) * factor, where
#   factor = clamp((1 - timing_resistance) * 2, 0, 2)
#   (resistance 0.5 → factor 1.0 baseline; 1.0 → 0 immune; 0.0 → 2.0 amplified)
# At looks=0 (first time facing this pitcher) fam == 0 → all terms collapse to
# the legacy surface, preserving the realism identity invariant.
FAMILIARITY_PER_LOOK:   float = 0.18    # familiarity-dominance accrued per prior PA (pre-attenuation)
FAMILIARITY_MAX_LOOKS:  int   = 7       # cap on prior PAs counted (top-of-order can see an SP this often)
FAMILIARITY_SWINGING:   float = -0.022  # fewer whiffs as the batter times the arm up
FAMILIARITY_CALLED:     float = -0.009  # lays off borderline pitches he's now seen
FAMILIARITY_CONTACT:    float = +0.020  # more balls in play
FAMILIARITY_HARD_TILT:  float = +0.028  # contact-quality batter-advantage shift per unit fam (mistakes get punished)
DEFAULT_TIMING_RESISTANCE: float = 0.5  # repertoire-less pitchers fall back to a movement-derived value around this

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

# --- Pitch launch-angle redistribution ------------------------------------
# Per-pitch launch_angle_bias rolls ground_out↔fly_out weight inside each
# contact-quality table (sum-preserving, same mechanism as the power axis).
# This is what turns "grounder pitch" vs "popup pitch" into a real outcome
# split instead of flavor on top of weak contact. FRACTION of the from-row
# weight moved at |launch_angle_bias| = 1.0:
#   bias < 0 (grounder inducer): fly_out → ground_out
#   bias > 0 (fly/popup inducer): ground_out → fly_out
# Identity at bias = 0.0. Applied AFTER power redistribution, BEFORE park.
LAUNCH_REDIST_GO2FO: float = 0.35

# ---------------------------------------------------------------------------
# Walk-Back sponsorship
# ---------------------------------------------------------------------------
# The Walk-Back rule (HR → next batter can drive in the HR-hitter from 3B
# for a bonus run) creates a ~15-second dead-time ritual after every HR
# where the HR-hitter physically walks back from home to third. In a sport
# this commodified, that's unsold inventory; the rule manufactures a
# sponsorable moment. This list seeds the rotating sponsor pool the
# play-by-play log uses to caption each Walk-Back. The pool is drawn from
# real defunct / dormant consumer brands and companies — the joke being
# that dead brands sponsor the dead-time ritual of a HR-hitter trudging
# back to third.
#
# Purely cosmetic — no stat impact. Picked deterministically per PA
# from (game_id, total_pa_this_half) so the sponsor a fan sees on a
# given Walk-Back is stable across renders.
WALK_BACK_SPONSORS: list[str] = [
    "Oldsmobile",
    "Pontiac",
    "Saturn",
    "Plymouth",
    "De Soto",
    "Studebaker",
    "Packard",
    "American Motors",
    "Blockbuster Video",
    "Borders Books",
    "Circuit City",
    "CompUSA",
    "RadioShack",
    "Montgomery Ward",
    "Toys “R” Us",
    "Compaq",
    "Commodore",
    "Gateway 2000",
    "Zune",
    "Beatrice Foods",
    "Borden",
    "Burger Chef",
    "ShowBiz Pizza",
    "Crystal Pepsi",
    "OK Soda",
    "Jolt Cola",
    "TaB",
    "Surge",
    "Quisp Cereal",
    "King Vitaman",
    "Burma-Shave",
    "Wisk",
    "Coleco",
    "Kenner",
    "Pan American World Airways",
    "Enron",
    "Lehman Brothers",
    "Bear Stearns",
    "Washington Mutual",
    "Arthur Andersen",
]

# Legacy POWER_HR_WEIGHT_SCALE retained as a stub. The archetype
# `hr_weight_bonus` HR boost it scaled was removed because it
# double-counted what the modern `power` rating already drives via
# _redistribute. Constant kept so external configs that reference it
# don't crash on import.
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
TODAY_FORM_SIGMA: float = 0.25   # variance pass v2: 0.10 → 0.25; required for the wide 0.71-1.84 clamp band to be reachable rather than theoretical
TODAY_FORM_MIN:   float = 0.71   # variance pass v2: 0.82 → 0.71; deep off-days are real
TODAY_FORM_MAX:   float = 1.84   # variance pass v2: 1.18 → 1.84; transcendent days possible (rare; Gaussian sampling makes upper-clamp hits ~3.4σ)

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

# --- Exit-velocity BABIP texture (park_effects, physics → outcome) ---------
# Beyond fence geometry, the (EV, LA) sample reaches in to re-decide a small
# slice of *marginal* balls in play, so contact quality — not just the
# categorical roll — drives whether a borderline ball falls. The four rules
# are deliberately paired so league offense stays ~flat: two turn an out into
# a hit (scorched grounder through the hole; soft fly that dies for a bloop),
# two turn a hit into an out (scorched liner snared; soft roller fielded
# routinely). Net effect is BABIP *variance* tied to EV, not a run-environment
# shift. EV cuts are read off the live league distribution (p~92 median,
# ~108 = top decile, ~76 = bottom decile), NOT MLB's 95-mph anchor.
# Set any probability to 0.0 to disable that rule. Identity is preserved when
# park_dims is None (the whole hook no-ops), so legacy DBs are unaffected.
EV_SCORCHED:        float = 108.0   # mph — "hit it on the screws"
EV_SOFT:            float = 78.0    # mph — "dying quail / weak roller"
EV_SCORCH_THRU_P:   float = 0.35    # scorched grounder → seeing-eye single
EV_ATEM_P:          float = 0.18    # scorched liner → lineout (at-'em ball)
EV_BLOOP_P:         float = 0.28    # soft fly/liner → bloop single
EV_ROLLER_P:        float = 0.25    # soft grounder hit → routine ground out

# Tier-1 extensions (rules 10-14). Real batted-ball mechanics, still gated on
# (EV, LA, spray) only. The two hit-count movers are paired: the lazy-fly
# can-of-corn (hit→out) offsets the legged-out tapper (out→hit). The three
# slug movers (frozen rope, down-the-line, wall-ball carom) change only the
# extra-base mix, not hits/BIP. EV cuts come off the live league spread.
EV_TAPPER_MAX:      float = 62.0    # mph — dribbler the batter can leg out
EV_FROZEN:          float = 102.0   # mph — line-drive one-hopper to the wall
EV_LAZYFLY_P:       float = 0.45    # lazy fly (LA 36-48, EV≤88) → caught fly out
EV_TAPPER_P:        float = 0.40    # dribbler ground_out → infield single
EV_FROZENROPE_P:    float = 0.18    # frozen-rope single → double
EV_DOWNLINE_P:      float = 0.30    # single down the line (|spray|≥40) → double
EV_WALLBALL_P:      float = 0.30    # double off a tall, deep wall → carom triple

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
RELEASE_FATIGUE_SCALE: float = 0.10   # fatigue-dominance: 0.20 → 0.10; submarine bonus halved so stamina dominates

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
#   timing_resistance [0,1] — how un-timeable the pitch stays as a hitter sees it
#                     repeatedly across a 27-out arc. 1.0 = a hitter never "times
#                     it up" no matter how many looks (knuckleball, eephus); 0.0 =
#                     fully solved after one look (raw velocity). ~0.5 = neutral.
#                     Drives the times-through-the-order familiarity penalty: a
#                     pitcher's repertoire-weighted mean sets how fast the lineup
#                     cracks his code on the 4th–7th time through. See FAMILIARITY_*.
#   launch_angle_bias [-1,1] — pushes BATTED-BALL launch angle. Negative = grounder
#                     inducer (rolls ground_out↔fly_out weight toward ground_out in
#                     resolve_contact, sum-preserving; e.g. sinker, peeled_drop,
#                     drop_knuck); positive = fly-ball / popup inducer (riseball,
#                     rise_knuck, four-seam). 0.0 (default) = neutral / identity.
#                     This is the lever that makes "grounder pitch" vs "popup pitch"
#                     a REAL outcome split, not just flavor on top of weak contact.
#   foul_delta        added to foul probability (positive = more fouls). In O27
#                     every foul spends one of the batter's 3 contact events, so a
#                     positive foul_delta literally burns down the AB toward a
#                     foul-out — the mechanism behind "rise"-type popup/foul pitches.
#                     0.0 (default) = identity.
#
# All deltas are quality-scaled: they represent the shift at quality=1.0 and
# collapse to 0 at quality=0.0. Identity at pitch_type=None.

PITCH_CATALOG: dict = {

    # ── FASTBALLS ─────────────────────────────────────────────────────────────
    "four_seam": {
        "velocity_class":     "high",
        "timing_resistance":  0.20,   # pure velocity — the lineup times it up fast
        "launch_angle_bias":  +0.30,  # flyball-prone from the higher slot
        "foul_delta":         +0.01,  # high heat gets fouled straight back

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
        "timing_resistance":  0.40,   # movement helps, but it's still a fastball
        "launch_angle_bias":  -0.45,  # the GB-heavy workhorse fastball

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
        "timing_resistance":  0.45,   # late break buys some resistance

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
        "timing_resistance":  0.70,   # velocity mismatch keeps hitters off-balance

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
        "timing_resistance":  0.50,   # neutral

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
        "timing_resistance":  0.55,   # reverse break stays surprising

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
        "timing_resistance":  0.60,   # slow lateral drift defeats timing

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
        "timing_resistance":  0.50,   # neutral

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
        "timing_resistance":  0.60,   # diagonal break is hard to square repeatedly
        "launch_angle_bias":  -0.50,  # diagonal break drives grounders to the pull side

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
        "timing_resistance":  0.55,   # velocity differential resists timing

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
        "timing_resistance":  0.60,   # tumbling action on top of the velo gap

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
        "timing_resistance":  0.50,   # neutral — sharp but readable arm action
        "launch_angle_bias":  -0.25,  # sharp downward break → grounders

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
        "timing_resistance":  0.95,   # un-timeable — even the pitcher can't predict it

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
        "timing_resistance":  0.65,   # erratic tumble — never the same twice
        "launch_angle_bias":  -0.55,  # tumbles in → extreme groundball

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
        "timing_resistance":  0.85,   # timing disruption is the entire point
        "launch_angle_bias":  +0.20,  # high arc → weak fly / popup when hit
        "foul_delta":         +0.02,  # mistimed hacks foul it off

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
        "timing_resistance":  0.60,   # reverse break keeps hitters guessing

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
        "timing_resistance":  0.75,   # arrives where the eye didn't predict

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

    # ── SOFTBALL-DERIVED / UNDERHAND ──────────────────────────────────────────
    # A true underhand or ultra-low submarine slot shares the mechanical path of
    # a fastpitch-softball delivery, which opens spin axes that are physically
    # impossible from a normal baseball arm slot. These pitches are HARD-gated to
    # low release angles (max_release ~0.30–0.45): they simply don't exist above
    # sidearm. They trade velocity for un-timeable movement and are extremely
    # arm-friendly (low arm_stress) — the structural basis for the "fatigue-immune
    # ace" who can carry elite movement all the way to out 27. All carry very high
    # timing_resistance: the whole point is that a lineup can't crack the code on
    # them no matter how many times through they get in a 27-out arc.
    #
    # ENGINE-MODELING NOTE: the catalog's only batted-ball lever is contact
    # quality (weak/medium/hard → EV); the engine has no per-pitch launch-angle
    # field, so "induces grounders" vs "induces popups" both reduce to weak
    # contact + EV suppression here. The grounder/popup distinction in the design
    # notes is flavor the batted-ball model can't yet separate — see the AAR.
    "riseball": {
        "velocity_class":     "low",
        "timing_resistance":  0.85,   # pure backspin off the dirt — un-timeable ladder
        "launch_angle_bias":  +0.70,  # climbs → swing-under, weak fly, popups
        "foul_delta":         +0.04,  # the ladder pitch: fouled up, burns the AB
        # Heavy pure backspin from a dead-underhand slot. Climbs through the top
        # of the zone against a level swing plane → swing-and-miss and weak fly.
        # The anti-power "ladder" pitch: hunts Ks and popups, punishes uppercut.
        "k_delta":            +0.06,
        "bb_delta":           +0.02,
        "contact_delta":      -0.03,
        "hard_contact_shift": -0.06,
        "weak_contact_shift": +0.03,
        "platoon_mode":       "standard",
        "platoon_scale":       0.8,
        "release_optimal":     0.10,   # dead underhand
        "release_window":      0.18,
        "arm_stress":          0.55,
        "max_release":         0.32,   # impossible above low submarine
        "count_bias":          "2strike",
    },
    "peeled_drop": {
        "velocity_class":     "mid",
        "timing_resistance":  0.70,
        "launch_angle_bias":  -0.70,  # dives into the dirt → extreme groundball
        # Pure topspin rolled over the top out of the underhand whip — a 12-to-6
        # break that happens entirely below the waist. Dives into the dirt:
        # extreme weak contact, big EV suppression. The grounder/runner-freeze
        # tool that counters pesäpallo-style stay advancement.
        "k_delta":            -0.01,
        "bb_delta":           +0.01,
        "contact_delta":      +0.02,
        "hard_contact_shift": -0.08,
        "weak_contact_shift": +0.09,
        "platoon_mode":       "standard",
        "platoon_scale":       1.0,
        "release_optimal":     0.08,
        "release_window":      0.18,
        "arm_stress":          0.55,
        "max_release":         0.30,
        "count_bias":          "ahead",
    },
    "backhand_changeup": {
        "velocity_class":     "low",
        "timing_resistance":  0.80,
        "launch_angle_bias":  -0.10,  # slight under-the-ball weak contact
        # Flipped hand at release, ball pushed out of the back of the hand. Mimics
        # fastball arm speed but arrives 15–20 mph slower with near-zero spin.
        # Punishes aggressive hitters who pull the trigger early on second-chance
        # contact — racks up strikes without spending outs.
        "k_delta":            +0.03,
        "bb_delta":           +0.02,
        "contact_delta":      -0.01,
        "hard_contact_shift": -0.04,
        "weak_contact_shift": +0.05,
        "platoon_mode":       "reverse",
        "platoon_scale":       1.0,
        "release_optimal":     0.15,
        "release_window":      0.25,
        "arm_stress":          0.55,
        "max_release":         0.45,
        "count_bias":          "2strike",
    },
    "sky_eephus": {
        "velocity_class":     "low",
        "timing_resistance":  0.92,   # ~50 mph vertical parabola — timing window in ms
        "launch_angle_bias":  +0.30,  # near-vertical drop → mistimed pop-ups
        "foul_delta":         +0.03,  # batters foul off the slow arc
        # The "Sky-Drop": a slowpitch-softball arc launched from the underhand
        # slot, apex above the batter's eye, dropping near-vertically through the
        # back of the zone at roughly half a sidearm fastball's speed. The most
        # extreme timing-disruption weapon in the catalog; a surprise put-away,
        # never a primary. More extreme and slot-locked than the standard eephus.
        "k_delta":            +0.07,
        "bb_delta":           +0.04,
        "contact_delta":      -0.04,
        "hard_contact_shift": -0.02,
        "weak_contact_shift": +0.03,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.2,
        "release_optimal":     0.12,
        "release_window":      0.40,
        "arm_stress":          0.45,   # the most arm-friendly pitch in the game
        "max_release":         0.35,
        "count_bias":          "2strike",
    },

    # ── 3-PITCH KNUCKLEBALL ACE ("Chaos Elite") ───────────────────────────────
    # The pure visual-illusionist knuckleballer discards velocity entirely and
    # carries three distinct knuckle variations off the low slot. All near-immune
    # to timing (timing_resistance ~0.93) and arm-friendly; handedness is
    # irrelevant (platoon_scale 0). Command is the price — elevated bb_delta.
    "slither_knuck": {
        "velocity_class":     "low",
        "timing_resistance":  0.93,
        "launch_angle_bias":   0.0,   # horizontal break — no vertical tilt
        # Heavy horizontal deflection — breaks sideways on seam-catch. The
        # put-away knuck that misses bats, aimed at high-eye contact hitters.
        "k_delta":            +0.05,
        "bb_delta":           +0.04,
        "contact_delta":      -0.03,
        "hard_contact_shift": -0.04,
        "weak_contact_shift": +0.02,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.0,
        "release_optimal":     0.20,
        "release_window":      0.40,
        "arm_stress":          0.55,
        "max_release":         None,   # works from any slot like a knuckler
        "count_bias":          "2strike",
    },
    "drop_knuck": {
        "velocity_class":     "low",
        "timing_resistance":  0.93,
        "launch_angle_bias":  -0.60,  # tumbles into the dirt → groundball knuck
        # Dead-stalling action that tumbles into the dirt at the plate. Extreme
        # weak contact, low whiff — the groundball knuck that neutralizes the
        # "stay" mechanic by denying the offense anything to advance on.
        "k_delta":            -0.01,
        "bb_delta":           +0.03,
        "contact_delta":      +0.01,
        "hard_contact_shift": -0.06,
        "weak_contact_shift": +0.08,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.0,
        "release_optimal":     0.18,
        "release_window":      0.45,
        "arm_stress":          0.55,
        "max_release":         None,
        "count_bias":          "all",
    },
    "rise_knuck": {
        "velocity_class":     "low",
        "timing_resistance":  0.93,
        "launch_angle_bias":  +0.50,  # hovers at the letters → popups
        "foul_delta":         +0.03,  # burns the AB's 3-contact limit with foul pops
        # Released low on an upward path, hovers at the letters. Triggers weak
        # popups and whiffs up — burns through an AB's 3-contact limit.
        "k_delta":            +0.04,
        "bb_delta":           +0.03,
        "contact_delta":      -0.02,
        "hard_contact_shift": -0.05,
        "weak_contact_shift": +0.02,
        "platoon_mode":       "neutral",
        "platoon_scale":       0.0,
        "release_optimal":     0.15,
        "release_window":      0.40,
        "arm_stress":          0.55,
        "max_release":         0.35,
        "count_bias":          "2strike",
    },
}
