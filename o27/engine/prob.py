"""
Probabilistic event provider for O27 Phase 2.

All random draws flow through a single random.Random instance (rng) so that
seeding it once produces fully deterministic output.

All tunable parameters are imported from o27.config — edit that file to
retune the simulation without touching engine logic.

Public API
----------
  ProbabilisticProvider(rng)  — callable event_provider for run_game()
  pitch_outcome(rng, pitcher, batter, balls, strikes, spell_count) -> str
  contact_quality(rng, batter, pitcher) -> "weak"|"medium"|"hard"
"""

from __future__ import annotations
import math
import random
from contextlib import contextmanager
from typing import Optional

from .state import GameState, Player
from . import stay as stay_mod
from . import manager as mgr
from . import weather as wx
from o27 import config as cfg


# ---------------------------------------------------------------------------
# Pitch outcome model
# ---------------------------------------------------------------------------

_PITCH_NAMES = ("ball", "called_strike", "swinging_strike", "foul", "contact")


def _sample_quality(
    rng: Optional[random.Random],
    central: float,
    variance: float,
) -> float:
    """Sample a per-pitch effective rating.

    Identity at variance == 0.0 (or rng is None): returns central exactly.
    Otherwise draws uniform in [central - variance, central + variance],
    clamped to [0.0, 1.0]. This is the "static range for each guy" model:
    every pitcher has their own variance — consistent arms repeat their
    stuff, max-effort arms live on the edges of their distribution.
    """
    if rng is None or variance <= 0.0:
        return central
    draw = central + (rng.random() * 2.0 - 1.0) * variance
    if draw < 0.0:
        return 0.0
    if draw > 1.0:
        return 1.0
    return draw


def _select_pitch(
    rng: Optional[random.Random],
    pitcher: Player,
    balls: int,
    strikes: int,
) -> tuple:
    """Select one pitch from the pitcher's repertoire for this pitch event.

    Returns (pitch_type, quality) or (None, 0.5) for legacy pitchers without
    a typed repertoire. Weights are count-biased: 2-strike counts favour
    put-away pitches; behind-in-count favours fastballs.
    """
    repertoire = getattr(pitcher, "repertoire", None)
    if not repertoire or rng is None:
        return None, 0.5

    release_angle = float(getattr(pitcher, "release_angle", 0.5))
    two_strike = (strikes == 2)
    behind     = (balls > strikes)
    ahead      = (strikes > 0 and balls <= strikes)

    pitches: list = []
    weights: list = []

    for entry in repertoire:
        catalog = cfg.PITCH_CATALOG.get(entry.pitch_type)
        if catalog is None:
            continue
        # Hard release-point gate — some pitches simply don't work above a
        # given arm slot (e.g. curve_10_to_2 needs sidearm or below).
        max_rel = catalog.get("max_release")
        if max_rel is not None and release_angle > max_rel:
            continue

        w = float(entry.usage_weight)
        bias = catalog.get("count_bias", "all")
        if two_strike and bias == "2strike":
            w *= 2.2
        elif behind and bias == "behind":
            w *= 1.6
        elif ahead and bias == "ahead":
            w *= 1.4
        # "all" pitches: no weight bump

        pitches.append((entry.pitch_type, float(entry.quality)))
        weights.append(w)

    if not pitches:
        return None, 0.5

    total = sum(weights)
    r = rng.random() * total
    cumsum = 0.0
    for (ptype, pq), w in zip(pitches, weights):
        cumsum += w
        if r < cumsum:
            return ptype, pq
    return pitches[-1]


def _release_quality(release_angle: float, catalog: dict) -> float:
    """Multiplier [0.5, 1.0] on pitch effectiveness based on release compatibility.

    Within the pitch's release_window around its release_optimal: full effect.
    Beyond that: degrades toward 0.5 over another window of distance.
    """
    optimal = catalog.get("release_optimal", 0.5)
    window  = catalog.get("release_window", 0.4)
    dist = abs(release_angle - optimal)
    if dist <= window:
        return 1.0
    excess = dist - window
    return max(0.5, 1.0 - 0.5 * excess / max(window, 0.01))


def _apply_pitch_platoon(
    base: list,
    catalog: dict,
    pitcher: Player,
    batter: Player,
) -> None:
    """Apply pitch-type-specific platoon K/contact shifts to base probability vector.

    Each platoon_mode defines who the pitcher has the bonus against:
      standard     — pitcher advantage vs same-handed (standard slider/fastball split)
      reverse      — pitcher advantage vs opposite-handed (changeup, screwball)
      neutral      — no platoon adjustment
      same_heavy   — large bonus vs same-handed (Sisko slider), slight penalty vs opposite
      opposite_heavy — large bonus vs opposite-handed (vulcan changeup, cutter)
    """
    mode  = catalog.get("platoon_mode", "neutral")
    scale = float(catalog.get("platoon_scale", 1.0))
    if mode == "neutral" or scale == 0.0:
        return

    b, p = batter.bats, pitcher.throws
    if not b or not p:
        return

    same_handed = (b != "S" and b == p)
    base_adj    = cfg.PLATOON_PENALTY * scale

    if mode == "standard":
        if same_handed:
            base[2] += base_adj * 0.5
            base[4] -= base_adj * 0.3
    elif mode == "reverse":
        if not same_handed and b != "S":
            base[2] += base_adj * 0.5
            base[4] -= base_adj * 0.3
    elif mode == "same_heavy":
        if same_handed:
            base[2] += base_adj * 1.0
            base[4] -= base_adj * 0.5
        elif b != "S":
            base[2] -= base_adj * 0.15   # slightly weaker vs opposite
    elif mode == "opposite_heavy":
        if not same_handed and b != "S":
            base[2] += base_adj * 0.8
            base[4] -= base_adj * 0.4
        else:
            base[2] -= base_adj * 0.10


def _platoon_factor(batter: Player, pitcher: Player) -> float:
    """Return the multiplier to apply to batter-side probability shifts.

    Identity invariant: returns 1.0 whenever either side has unknown
    handedness ('' sentinel), so legacy callers that don't populate
    bats/throws are unaffected.

    - Switch hitters always get the platoon advantage (factor > 1.0 by
      a small bonus, configurable via PLATOON_BONUS_SWITCH).
    - Same-handed matchups (RHB vs RHP, LHB vs LHP) eat the penalty.
    - Opposite-handed matchups are neutral.
    """
    b, p = batter.bats, pitcher.throws
    if not b or not p:
        return 1.0
    if b == "S":
        return 1.0 + cfg.PLATOON_BONUS_SWITCH
    if b == p:
        return 1.0 - cfg.PLATOON_PENALTY
    return 1.0


def _joker_decay_factor(prior_pa_count: int) -> float:
    """Joker effectiveness multiplier in [0.5, 1.0].

    Applied to a joker batter's rating-deviation scalar (so skill, eye,
    contact, and power all decay together). Identity (1.0) on their
    first use; floors at 0.50 from the 10th use onward.

    Curve:
      use #1 (count=0):   1.00   fresh
      use #2-4:           0.98 → 0.91   small dip
      use #5 (count=4):   0.85   light penalty
      use #6 (count=5):   0.78   K penalty kicks up
      use #7 (count=6):   0.70
      use #8 (count=7):   0.62   much steeper
      use #9 (count=8):   0.55
      use #10+ (count>=9):0.50   floor — anomaly territory
    """
    if prior_pa_count <= 0:  return 1.00
    if prior_pa_count == 1:  return 0.98
    if prior_pa_count == 2:  return 0.95
    if prior_pa_count == 3:  return 0.91
    if prior_pa_count == 4:  return 0.85
    if prior_pa_count == 5:  return 0.78
    if prior_pa_count == 6:  return 0.70
    if prior_pa_count == 7:  return 0.62
    if prior_pa_count == 8:  return 0.55
    return 0.50


def _resolve_joker_decay(state: "GameState", batter: Player) -> float:
    """Return the joker-decay multiplier for the current AB.

    Returns 1.0 (no decay) unless the batter is up via joker insertion
    (state.batter_override is set to this batter). Reads the prior
    joker_pa count from state.batter_game_stats — counter increments
    AFTER the AB in pa._end_at_bat, so during the AB it reflects the
    joker's PRIOR usage this game.
    """
    if getattr(state, "batter_override", None) is not batter:
        return 1.0
    prior = state.bgs(batter.player_id).get("joker_pa", 0)
    return _joker_decay_factor(prior)


def _pitcher_timing_resistance(pitcher: Player) -> float:
    """How un-timeable this pitcher stays as a lineup sees him repeatedly.

    Usage-weighted mean of each repertoire pitch's `timing_resistance`. A
    knuckleballer (knuckleball 0.95) lands near-immune; a four-seam/slider arm
    lands low and gets solved over a long arc. Repertoire-less (legacy)
    pitchers fall back to a movement-derived value: high-movement arms are
    harder to time even without a typed repertoire. Identity-safe — the value
    only scales the familiarity term, which is zero on the first look anyway.
    """
    repertoire = getattr(pitcher, "repertoire", None)
    if repertoire:
        total_w = 0.0
        acc = 0.0
        for entry in repertoire:
            catalog = cfg.PITCH_CATALOG.get(getattr(entry, "pitch_type", None))
            if catalog is None:
                continue
            w = float(getattr(entry, "usage_weight", 1.0) or 0.0)
            if w <= 0.0:
                continue
            tr = float(catalog.get("timing_resistance", cfg.DEFAULT_TIMING_RESISTANCE))
            acc += tr * w
            total_w += w
        if total_w > 0.0:
            return max(0.0, min(1.0, acc / total_w))
    # Legacy fallback: center on DEFAULT, nudged by movement (0.5 → identity).
    move = float(getattr(pitcher, "movement", 0.5) or 0.5)
    return max(0.0, min(1.0, cfg.DEFAULT_TIMING_RESISTANCE + (move - 0.5) * 0.6))


def _familiarity_dominance(times_faced: int, resistance: float) -> float:
    """Batter-advantage scalar from times-through-the-order.

    Grows with prior PAs vs this pitcher this game, attenuated by the
    pitcher's timing_resistance. Zero on the first look (identity). See
    config FAMILIARITY_* for the shape.
    """
    if times_faced <= 0:
        return 0.0
    looks = min(int(times_faced), cfg.FAMILIARITY_MAX_LOOKS)
    factor = max(0.0, min(2.0, (1.0 - resistance) * 2.0))
    return cfg.FAMILIARITY_PER_LOOK * looks * factor


def _pitch_probs(
    pitcher: Player,
    batter: Player,
    balls: int,
    strikes: int,
    spell_count: int,
    weather: Optional[object] = None,
    rng: Optional[random.Random] = None,
    pitch_type: Optional[str] = None,
    pitch_quality: float = 0.5,
    joker_decay: float = 1.0,
    familiarity: float = 0.0,
) -> tuple:
    """Return adjusted pitch-outcome probability tuple (sums to 1.0)."""
    base = list(cfg.PITCH_BASE.get((balls, strikes), cfg.PITCH_BASE[(0, 0)]))

    # Daily pitcher form — stored on Player for the duration of the spell.
    # Multiplies effective Stuff so the same SP can throw a gem one start
    # and a clunker the next. today_form == 1.0 ⇒ identity.
    form = getattr(pitcher, "today_form", 1.0)
    # Phase 3: per-game wellness multiplier rolled once per player per game
    # in o27v2/sim.py:_roll_today_condition. Stacks with today_form for
    # pitchers (1.0 = identity). Weather-modulated μ so bad-weather days
    # produce more bad performances without flattening every player.
    p_cond = getattr(pitcher, "today_condition", 1.0)
    # Per-pitch quality draw — each pitch samples within the pitcher's
    # static [rating ± pitch_variance] range. At pitch_variance == 0.0 or
    # rng == None this collapses to the legacy central rating (identity).
    pv = float(getattr(pitcher, "pitch_variance", 0.0) or 0.0)
    raw_stuff = _sample_quality(rng, float(pitcher.pitcher_skill), pv)
    # Position-player pitching (extreme blowout fallback): blend in the
    # player's arm rating so a strong-arm bench bat throws better than a
    # noodle-arm one when forced into an emergency outing. Heavily scaled
    # down — they're still amateurs on the mound.
    if not getattr(pitcher, "is_pitcher", True):
        arm = float(getattr(pitcher, "arm", 0.5) or 0.5)
        raw_stuff = 0.55 * arm + 0.45 * raw_stuff
    stuff_eff = max(0.0, min(1.0, raw_stuff * form * p_cond))

    # Pitcher dominance: stuff_eff > 0.5 shifts probability toward strikes.
    p_dom = (stuff_eff - 0.5) * 2   # −1.0 to +1.0
    base[0] += p_dom * cfg.PITCHER_DOM_BALL
    base[1] += p_dom * cfg.PITCHER_DOM_CALLED
    base[2] += p_dom * cfg.PITCHER_DOM_SWINGING
    base[4] += p_dom * cfg.PITCHER_DOM_CONTACT

    # Batter dominance: skill > 0.5 shifts probability toward contact.
    # Release-angle platoon amplifier: submarine pitchers' horizontal
    # movement amplifies the handedness advantage — the ball starts inside
    # and breaks away (or vice-versa) more dramatically than from a higher
    # slot. Identity at release_angle = 0.5 (sidearm).
    release_angle = float(getattr(pitcher, "release_angle", 0.5))
    rel_plat_amp  = 1.0 + (0.5 - release_angle) * cfg.RELEASE_PLATOON_AMP_SCALE
    plat  = _platoon_factor(batter, pitcher) * max(0.5, rel_plat_amp)
    # Phase 3: batter side gets the same per-game wellness multiplier.
    # Scales every batter-rating-driven dominance term so a great hitter
    # can have a 0-fer day and a replacement bat can carry a game. The
    # `(rating - 0.5) * 2 * cond` shape keeps identity at cond=1.0 and
    # symmetrically shrinks both positive and negative dominance toward
    # league-average on bad days (0.85) or amplifies it on good days (1.15).
    b_cond = getattr(batter, "today_condition", 1.0) * joker_decay
    b_dom = (batter.skill - 0.5) * 2 * plat * b_cond  # −1.0 to +1.0
    base[2] += b_dom * cfg.BATTER_DOM_SWINGING
    base[4] += b_dom * cfg.BATTER_DOM_CONTACT

    # --- Realism layer ----------------------------------------------------
    # Each contribution collapses to 0 when the rating == 0.5, preserving
    # the identity invariant against the legacy probability surface.

    # Eye: discipline → more balls taken, fewer called strikes.
    eye_dev = (batter.eye - 0.5) * 2 * plat * b_cond
    base[0] += eye_dev * cfg.BATTER_EYE_BALL
    base[1] += eye_dev * cfg.BATTER_EYE_CALLED

    # Contact (batter): bat-on-ball ability → fewer whiffs, more fouls/in-play.
    con_dev = (batter.contact - 0.5) * 2 * plat * b_cond
    base[2] += con_dev * cfg.BATTER_CONTACT_SWINGING
    base[3] += con_dev * cfg.BATTER_CONTACT_FOUL
    base[4] += con_dev * cfg.BATTER_CONTACT_CONTACT

    # Times-through-the-order familiarity: the more this batter has faced this
    # pitcher this game, the more he's timed the arm up — fewer whiffs, fewer
    # called strikes chased, more balls in play. Attenuated by the pitcher's
    # timing_resistance (computed by the caller). Collapses to 0 on the first
    # look, preserving the legacy probability surface (identity invariant).
    if familiarity:
        base[1] += familiarity * cfg.FAMILIARITY_CALLED
        base[2] += familiarity * cfg.FAMILIARITY_SWINGING
        base[4] += familiarity * cfg.FAMILIARITY_CONTACT

    # Command (pitcher): independent of Stuff → control pitchers walk fewer.
    # Per-pitch quality draw applies here too: the same pitcher with high
    # pitch_variance will paint corners one pitch and bury one in the dirt
    # the next, even at the same average Command rating.
    cmd_eff = _sample_quality(rng, float(pitcher.command), pv)
    cmd_dev = (cmd_eff - 0.5) * 2
    base[0] += cmd_dev * cfg.PITCHER_COMMAND_BALL
    base[1] += cmd_dev * cfg.PITCHER_COMMAND_CALLED

    # Form: signed deviation from 1.0; same shape as p_dom.
    form_dev = form - 1.0   # 0 when neutral
    base[0] += form_dev * cfg.FORM_BALL
    base[1] += form_dev * cfg.FORM_CALLED
    base[2] += form_dev * cfg.FORM_SWINGING
    base[4] += form_dev * cfg.FORM_CONTACT

    # Fatigue: spell_count > threshold degrades pitcher performance.
    # Threshold is Stamina-driven (NOT Stuff): Stuff doesn't make a pitcher
    # endure longer, Stamina does. This is what gives elite-Stamina arms
    # the workhorse moat the user wants — they can grind 27 outs without
    # noticeable late-half degradation.
    fatigue_threshold = max(
        cfg.FATIGUE_THRESHOLD_BASE,
        cfg.FATIGUE_THRESHOLD_BASE + round((pitcher.stamina ** 2) * cfg.FATIGUE_THRESHOLD_SCALE),
    )
    if spell_count > fatigue_threshold:
        # Weather scales how fast fatigue accumulates — hot/humid wears
        # arms down quicker, cool/dry extends them. Multiplier on the
        # ramp magnitude only; the threshold itself is unchanged.
        stam_mult = wx.stamina_decay_multiplier(weather)
        fatigue = min(
            cfg.FATIGUE_MAX,
            (spell_count - fatigue_threshold) / cfg.FATIGUE_SCALE * stam_mult,
        )
        # Grit dampens (or amplifies) the fatigue penalty. Identity at
        # grit == 0.50 (multiplier 1.0). High-grit veterans find another
        # gear when tired; low-grit arms unravel faster than the raw
        # Stamina ramp predicts.
        grit = float(getattr(pitcher, "grit", 0.5))
        grit_mult = max(0.0, 1.0 - (grit - 0.5) * 2.0 * cfg.GRIT_FATIGUE_RESIST)
        fatigue *= grit_mult
        # Release-angle arm ease: submarine deliveries are structurally
        # less taxing. Identity at 0.5 (sidearm). Below 0.5 only.
        rel_ease = max(0.0, 0.5 - release_angle) * cfg.RELEASE_FATIGUE_SCALE
        fatigue *= (1.0 - rel_ease)

        base[0] += fatigue * cfg.FATIGUE_BALL
        base[4] += fatigue * cfg.FATIGUE_CONTACT
        base[1] += fatigue * cfg.FATIGUE_CALLED
        base[2] += fatigue * cfg.FATIGUE_SWINGING
        base[3] += fatigue * cfg.FATIGUE_FOUL

    # Pitch-type adjustments — quality-scaled deltas from the PITCH_CATALOG.
    # Applied after all talent/fatigue terms, before weather. At quality=1.0
    # the full catalog delta fires; at quality=0.0 it collapses to 0 (identity).
    # Release-quality multiplier further scales the effect if the pitcher's arm
    # slot doesn't match this pitch's natural release window.
    if pitch_type is not None:
        catalog = cfg.PITCH_CATALOG.get(pitch_type)
        if catalog is not None:
            pq   = float(pitch_quality)
            rq   = _release_quality(release_angle, catalog)
            eff  = pq * rq           # effective quality after release-match penalty
            base[0] += catalog["bb_delta"]      * eff
            base[2] += catalog["k_delta"]        * eff
            base[3] += catalog.get("foul_delta", 0.0) * eff
            base[4] += catalog["contact_delta"]  * eff
            _apply_pitch_platoon(base, catalog, pitcher, batter)

    # Weather K modifier — applied as a multiplicative shift on the
    # strike-component shares (called + swinging). Foul / contact
    # shares are inversely adjusted to preserve normalisation. Identity
    # at neutral weather (k_mult == 1.0).
    k_mult = wx.k_multiplier(weather)
    if k_mult != 1.0:
        delta_called   = base[1] * (k_mult - 1.0)
        delta_swinging = base[2] * (k_mult - 1.0)
        delta = delta_called + delta_swinging
        base[1] += delta_called
        base[2] += delta_swinging
        # Drain delta from contact + foul (keeps balls roughly fixed).
        if base[3] + base[4] > 0:
            scale = 1.0 - delta / max(0.01, base[3] + base[4])
            base[3] = max(0.01, base[3] * scale)
            base[4] = max(0.01, base[4] * scale)

    # Normalise. Floor lowered 0.01 → 0.001: the 0.01 floor was a talent
    # gate — it kept elite Stuff from suppressing contact below ~1% per
    # component. Dropping it (epsilon for division safety only) lets
    # transcendent talent actually transcend.
    base = [max(0.001, p) for p in base]
    total = sum(base)
    return tuple(p / total for p in base)


def pitch_outcome(
    rng: random.Random,
    pitcher: Player,
    batter: Player,
    balls: int,
    strikes: int,
    spell_count: int,
    weather: Optional[object] = None,
    pitch_type: Optional[str] = None,
    pitch_quality: float = 0.5,
    joker_decay: float = 1.0,
    familiarity: float = 0.0,
) -> str:
    """Draw one pitch outcome. Returns a string matching one of _PITCH_NAMES."""
    probs = _pitch_probs(
        pitcher, batter, balls, strikes, spell_count, weather,
        rng=rng, pitch_type=pitch_type, pitch_quality=pitch_quality,
        joker_decay=joker_decay, familiarity=familiarity,
    )
    r = rng.random()
    cumulative = 0.0
    for name, p in zip(_PITCH_NAMES, probs):
        cumulative += p
        if r < cumulative:
            return name
    return "contact"


# ---------------------------------------------------------------------------
# Contact quality model
# ---------------------------------------------------------------------------

def _fielding_catcher(state):
    """The player currently behind the plate for the fielding team: the
    lineup's non-pitcher with the best defense_catcher rating. Resolving from
    the live lineup means a catcher substitution automatically changes who is
    "the catcher" — no separate bookkeeping needed. Returns None if none."""
    team = getattr(state, "fielding_team", None)
    if team is None:
        return None
    lineup = getattr(team, "lineup", None) or getattr(team, "roster", []) or []
    best = None
    best_dc = -1.0
    for p in lineup:
        if getattr(p, "is_pitcher", False):
            continue
        dc = float(getattr(p, "defense_catcher", 0.5) or 0.5)
        if dc > best_dc:
            best_dc = dc
            best = p
    return best


def _catcher_gc_shift(state) -> float:
    """Contact-quality shift from the fielding team's catcher game-calling,
    degraded by catcher fatigue. Positive = good caller suppressing contact.
    Identity (0.0) when no catcher / neutral rating."""
    c = _fielding_catcher(state)
    if c is None:
        return 0.0
    gc = float(getattr(c, "game_calling", 0.5) or 0.5)
    # Fatigue decay — a gassed catcher calls a worse game. The fielding team's
    # catcher_outs_caught accumulates per out behind the plate; absent → no decay.
    outs = int(getattr(getattr(state, "fielding_team", None),
                       "catcher_outs_caught", 0) or 0)
    thr = getattr(cfg, "CATCHER_FATIGUE_THRESHOLD", 10 ** 9)
    if outs > thr:
        fatigue = min(getattr(cfg, "CATCHER_FATIGUE_MAX", 0.0),
                      (outs - thr) / getattr(cfg, "CATCHER_FATIGUE_SCALE", 1.0))
        gc -= fatigue * getattr(cfg, "CATCHER_FATIGUE_GAME_CALLING_SCALE", 0.0)
    return (gc - 0.5) * 2.0 * getattr(cfg, "CATCHER_GAME_CALLING_SHIFT_SCALE", 0.0)


def contact_quality(
    rng: random.Random,
    batter: Player,
    pitcher: Player,
    weather: Optional[object] = None,
    swings_in_ab: int = 0,
    pitch_type: Optional[str] = None,
    pitch_quality: float = 0.5,
    target_pressure_shift: float = 0.0,
    joker_decay: float = 1.0,
    familiarity: float = 0.0,
    risp_penalty: float = 1.0,
    catcher_shift: float = 0.0,
) -> str:
    """
    Determine whether contact is weak, medium, or hard.

    Base distribution from config.CONTACT_*_BASE.
    Adjusted by batter.skill vs pitcher.pitcher_skill matchup.

    Realism layer:
      - Today's form multiplies effective Stuff for the matchup term.
      - Power tilts toward hard contact; movement (pitcher) tilts toward weak.
      - Platoon penalty applied to batter-side terms.
    """
    plat = _platoon_factor(batter, pitcher)
    form = getattr(pitcher, "today_form", 1.0)
    # Phase 3: per-game wellness multipliers (see _pitch_probs above).
    # Joker decay folds into b_cond so power_tilt and matchup both feel
    # the rating sag from successive joker insertions in one game.
    p_cond = getattr(pitcher, "today_condition", 1.0)
    b_cond = getattr(batter,  "today_condition", 1.0) * joker_decay * risp_penalty
    # Per-pitch quality draws — same model as _pitch_probs. Each batted-
    # ball event samples within the pitcher's static stuff/movement range.
    pv = float(getattr(pitcher, "pitch_variance", 0.0) or 0.0)
    stuff_draw = _sample_quality(rng, float(pitcher.pitcher_skill), pv)
    move_draw  = _sample_quality(rng, float(pitcher.movement), pv)
    stuff_eff = max(0.0, min(1.0, stuff_draw * form * p_cond))

    # Phase 3: batter's effective skill is also condition-scaled in the
    # matchup term. Identity at b_cond=1.0; off-day batters lose ground
    # in the matchup, hot batters gain.
    matchup = (batter.skill * plat * b_cond) - stuff_eff   # +ve → batter advantage
    shift = matchup * cfg.CONTACT_MATCHUP_SHIFT    # up to ±0.125 swing

    # Target-pressure tilt (symmetric by role). Whichever team bats second
    # gets a small early-half contact bonus: they know the number to beat,
    # they're locked in. Identity at 0.0 so first-batting teams or PAs past
    # the fade window see no change.
    shift += target_pressure_shift

    # Catcher game-calling: a good caller (positive catcher_shift) sequences
    # the pitcher to suppress hard contact; a poor one lets hitters square up.
    # Subtracts from the batter-advantage shift. Computed at the call site from
    # whoever is behind the plate (and their fatigue). Identity at 0.0.
    shift -= catcher_shift

    # Times-through-the-order familiarity: a hitter who has timed this pitcher
    # up doesn't just make more contact (handled in _pitch_probs) — he squares
    # it up better. Tilts the bucket toward hard contact. Zero on first look.
    shift += familiarity * cfg.FAMILIARITY_HARD_TILT

    # Second-swing modifier: on swings 2+ within the same AB, tilt the
    # contact distribution by eye-vs-command. High-eye batter reads the
    # pitcher; high-command pitcher disrupts the read. Competing forces.
    if swings_in_ab >= 1:
        eye_dev = (batter.eye - 0.5) * 2 * plat * b_cond
        cmd_dev = (pitcher.command - 0.5) * 2 * p_cond
        shift += (eye_dev * cfg.SECOND_SWING_EYE_SCALE
                  - cmd_dev * cfg.SECOND_SWING_COMMAND_SCALE)

    # Power → harder contact (collapses to 0 at power=0.5).
    power_tilt = (batter.power - 0.5) * 2 * plat * b_cond * cfg.CONTACT_POWER_TILT
    # Movement → weaker contact (collapses to 0 at movement=0.5).
    move_tilt  = (move_draw - 0.5) * 2 * cfg.CONTACT_MOVEMENT_TILT

    # Floor at 0.001 (epsilon for probability sanity only). Was 0.05, then
    # 0.01; lowered again to 0.001 to remove the last soft talent gate. An
    # elite-Stuff / elite-movement pitcher should be able to push hard-contact
    # rate vanishingly low against a replacement bat, and vice-versa.
    weak_p   = max(0.001, cfg.CONTACT_WEAK_BASE   - shift - power_tilt + move_tilt)
    hard_p   = max(0.001, cfg.CONTACT_HARD_BASE   + shift + power_tilt - move_tilt)

    # Pitch-type contact-quality shifts — applied quality-scaled, release-matched.
    if pitch_type is not None:
        catalog = cfg.PITCH_CATALOG.get(pitch_type)
        if catalog is not None:
            release_angle = float(getattr(pitcher, "release_angle", 0.5))
            eff = float(pitch_quality) * _release_quality(release_angle, catalog)
            weak_p = max(0.001, weak_p + catalog.get("weak_contact_shift", 0.0) * eff)
            hard_p = max(0.001, hard_p + catalog.get("hard_contact_shift", 0.0) * eff)

    # Weather multiplier on hard-contact share. Excess (or deficit) is
    # absorbed by weak; medium is computed by complement so the three
    # sum to 1.0 regardless.
    hc_mult = wx.hard_contact_multiplier(weather)
    if hc_mult != 1.0:
        new_hard = max(0.001, hard_p * hc_mult)
        delta = new_hard - hard_p
        hard_p = new_hard
        weak_p = max(0.001, weak_p - delta)

    medium_p = max(0.001, 1.0 - weak_p - hard_p)

    total = weak_p + medium_p + hard_p
    weak_p /= total
    medium_p /= total

    r = rng.random()
    if r < weak_p:
        return "weak"
    elif r < weak_p + medium_p:
        return "medium"
    return "hard"


# ---------------------------------------------------------------------------
# Runner advancement model
# ---------------------------------------------------------------------------

def _batter_clutch(batter: "Player") -> float:
    """Combined batter-side clutch for the RISP-pressure roll.

    Hard skills (eye + contact) give a baseline; the two mental
    attributes (leadership + grit) STACK on top as additive bonuses
    above neutral. A low-eye/contact bench guy with maxed
    leadership + grit lifts to roughly the same clutch as a star
    hitter with neutral mental — that's the joker archetype the
    sport rewards. Returns a value in [0.0, 1.0].

    When a leadership flare is active (`leadership_flare_scope`), the
    batter's eye/contact have already been mutated upward, so the
    `base` calculation here picks up the lift automatically — no
    explicit lift parameter needed. That's how the flare broadens its
    impact: every read of every rating during the PA sees the lifted
    value.
    """
    base   = (float(getattr(batter, "eye",     0.5) or 0.5)
              + float(getattr(batter, "contact", 0.5) or 0.5)) / 2.0
    lead   = float(getattr(batter, "leadership", 0.5) or 0.5)
    bgrit  = float(getattr(batter, "grit",       0.5) or 0.5)
    mental = (lead - 0.5) + (bgrit - 0.5)   # ±1; stacks both attrs
    return max(0.0, min(1.0, base + 0.5 * mental))


# Rating attributes the per-PA leadership flare temporarily lifts.
# When a flare fires, EVERY downstream system that reads these fields
# sees the lifted value for the duration of the PA — that's how the
# lift broadens beyond just offense (pitch_probs / contact_quality
# / fielding rolls all read the same fields). At PA end the context
# manager restores originals via try/finally, so no state leaks.
_BATTER_LIFT_ATTRS    = ("eye", "contact", "power", "skill")
_PITCHER_LIFT_ATTRS   = ("command", "pitcher_skill", "movement", "grit")
_DEFENSE_LIFT_ATTRS   = ("defense_rating",)


def _lift_attr(obj, attr: str, delta: float) -> None:
    cur = getattr(obj, attr, None)
    if cur is None:
        return
    try:
        setattr(obj, attr, max(0.0, min(1.0, float(cur) + float(delta))))
    except (TypeError, ValueError):
        return


def apply_pa_leadership_flares(
    rng: random.Random,
    state: "GameState",
    batter: "Player",
    pitcher: "Player",
) -> None:
    """Called once at PA start. Rolls leadership flares for both the
    batter and the pitcher and, if either fires, mutates the relevant
    rating fields in-place so EVERY downstream system reads the
    lifted values for the duration of the PA. Originals are stashed
    on `state.flare_originals` so `release_pa_leadership_flares` can
    restore them at PA end.

    Affected fields (when the corresponding side's flare fires):
      Batter — eye, contact, power, skill
      Pitcher — command, pitcher_skill (Stuff), movement, grit
      Fielding team — defense_rating (rallied by the pitcher's flare)

    Skipped attrs (intentional): `leadership` itself and (batter-side)
    `grit` are the meta-attributes that DRIVE the flare; lifting them
    would be circular. Pitcher-side `grit` IS lifted because there
    grit reads as fatigue resistance, not a mental-flare input.

    This is what makes leadership impact the WHOLE GAME, not just
    offense. A high-leadership pitcher whose flare fires gets better
    pitches (lifted Stuff/movement/command), bears down through
    fatigue (lifted grit), AND lifts the fielders behind him
    (lifted defense_rating) — for that single PA.

    Idempotent on already-active flares (no-op if state.flare_lift_active
    is already True) — guards against accidental double-application
    across re-entrant pitch calls inside the same PA.
    """
    if state.flare_lift_active:
        return

    batter_lift  = _apply_leadership_lift(rng, state, batter)
    pitcher_lift = _apply_leadership_lift(rng, state, pitcher)
    if batter_lift <= 0.0 and pitcher_lift <= 0.0:
        return

    originals: list = []
    if batter_lift > 0.0:
        for a in _BATTER_LIFT_ATTRS:
            if hasattr(batter, a):
                originals.append((batter, a, getattr(batter, a)))
                _lift_attr(batter, a, batter_lift)
    if pitcher_lift > 0.0:
        for a in _PITCHER_LIFT_ATTRS:
            if hasattr(pitcher, a):
                originals.append((pitcher, a, getattr(pitcher, a)))
                _lift_attr(pitcher, a, pitcher_lift)
        fld = getattr(state, "fielding_team", None)
        if fld is not None:
            for a in _DEFENSE_LIFT_ATTRS:
                if hasattr(fld, a):
                    originals.append((fld, a, getattr(fld, a)))
                    _lift_attr(fld, a, pitcher_lift)

    state.flare_originals = originals
    state.flare_lift_active = True


def release_pa_leadership_flares(state: "GameState") -> None:
    """Called at PA end (`pa._end_at_bat`). Restores all originally-stored
    rating values from `state.flare_originals`. Safe to call when no
    flare is active — no-op in that case. Always called via try/finally
    semantics so a mid-PA exception still rolls back the mutations."""
    if not state.flare_lift_active and not state.flare_originals:
        return
    for obj, attr, val in state.flare_originals:
        try:
            setattr(obj, attr, val)
        except Exception:
            pass
    state.flare_originals = []
    state.flare_lift_active = False


@contextmanager
def leadership_flare_scope(
    rng: random.Random,
    state: "GameState",
    batter: "Player",
    pitcher: "Player",
):
    """Test/utility context manager — wrap a block in the same per-PA
    flare semantics that production uses. Mostly for tests; production
    drives flares via apply_pa_leadership_flares (PA start) and
    release_pa_leadership_flares (PA end) so the lifts persist across
    multiple pitches in the same PA."""
    try:
        apply_pa_leadership_flares(rng, state, batter, pitcher)
        yield
    finally:
        release_pa_leadership_flares(state)


def _apply_leadership_lift(
    rng: random.Random,
    state: "GameState",
    player: "Player",
) -> float:
    """Per-PA leadership flare — a transient, one-off ratings bump.

    BOTH SIDES fire flares. The same function is called for the batter
    and the pitcher at PA start; whoever has more leadership and rolls
    higher gets the bigger transient lift. A high-leadership pitcher
    facing a bases-loaded jam bears down and lifts his own command/grit
    momentarily; a high-leadership batter in the same spot lifts his
    own eye/contact momentarily. The two lifts duel through downstream
    systems (the RISP pressure roll consumes both; the talent gate
    consumes both), so leadership is a leverage-symmetric attribute,
    not a batter-only one.

    Mental model: leadership reads not as a static input to one formula
    but as a flare that fires under pressure. The fire probability
    accumulates from a series of in-game LEVERAGE conditions (this is
    the "progressive" piece — more conditions stacking on the same PA
    raise the trigger chance), and is scaled by the player's leadership
    rating itself. When the flare fires, a one-off magnitude is rolled
    from a uniform band and scaled by leadership again, so high-
    leadership players not only fire more often but lift bigger when
    they do. Returns 0.0 when no flare fires.

    The returned lift is threaded through DOWNSTREAM systems as an
    additive offset to the player's effective ratings — it deliberately
    does NOT mutate `player` so per-PA state can't leak into the next
    PA. The lift stacks with:
      - `_batter_clutch`  (batter lift → Stage 1 pressure firing prob)
      - composure         (pitcher lift → reduces pitcher vulnerability)
      - the post-contact `talent_run` gate (batter lift → eye/contact;
        pitcher lift → command — both shift the hit/out balance)
      - the `hit` pressure manifestation's per-batter magnitude bump

    Stacking is intentional. Leadership is the "captain's moment" attr:
    when it fires, every downstream resolution that consults that
    player's ratings treats them as if their hard skills were briefly
    higher. A clutch slugger whose flare fires AND whose RISP-pressure
    roll also fires gets BOTH lifts on the same PA — that's the
    decisive "took over the game" moment the design wants. The pitcher
    side mirrors that — a clutch pitcher's flare can fire on the same
    PA to fight back.

    GATING — the flare ONLY fires under a leverage context. Leadoff
    PAs in tied innings shouldn't flare every time; flare is for the
    big spots. Requires at least one of:
      - RISP (runner on 2nd or 3rd)
      - late game (outs_done >= 18, ≈ inning 7+ in O27's 27-out half)
      - super-inning

    Triggering conditions (each adds to fire probability):
      - RISP:                                              +0.06
      - bases loaded:                                      +0.06 (additional)
      - late game (outs_done ≥ 18) or super-inning:        +0.04
      - close game (score gap ≤ 2):                        +0.03
      - tied game (gap == 0):                              +0.03 (additional)

    Lift magnitude band: 0.05-0.20 at neutral leadership, scaled by
    `1 + 1.5 * (leadership - 0.5)` so a 0.85-leadership player peaks
    around +0.30, a 0.15-leadership player caps near +0.05.
    """
    leadership = float(getattr(player, "leadership", 0.5) or 0.5)

    bases = state.bases
    risp   = (bases[1] is not None) or (bases[2] is not None)
    loaded = all(b is not None for b in bases)
    outs_done = int(getattr(state, "outs", 0) or 0)
    late = (outs_done >= 18) or bool(getattr(state, "is_super_inning", False))

    if not (risp or late):
        return 0.0    # no pressure context, no flare

    p_fire = 0.0
    if risp:
        p_fire += 0.06
    if loaded:
        p_fire += 0.06
    if late:
        p_fire += 0.04
    # Close-game leverage — leadership flares more readily when the
    # win probability swings most per PA. Side-agnostic by design:
    # both teams feel the pressure in a tight game.
    try:
        score = getattr(state, "score", {}) or {}
        gap = abs(int(score.get("visitors", 0)) - int(score.get("home", 0)))
    except Exception:
        gap = 0
    if gap <= 2:
        p_fire += 0.03
    if gap == 0:
        p_fire += 0.03

    p_fire *= (1.0 + 1.5 * (leadership - 0.5))
    p_fire = max(0.0, min(0.45, p_fire))
    if rng.random() >= p_fire:
        return 0.0

    base_lift = 0.05 + rng.random() * 0.15
    lift = base_lift * (1.0 + 1.5 * (leadership - 0.5))
    return max(0.0, min(0.30, lift))


def _resolve_risp_pressure(
    rng: random.Random,
    state: "GameState",
    batter: "Player",
    pitcher: "Player",
) -> Optional[str]:
    """Two-stage talent-driven RISP-pressure roll.

    Stage 1 — does the moment fire? Probability composes from the
    situation (RISP vs. RISP+3 vs. bases-loaded), the pitcher's
    composure `(command + grit) / 2`, and the batter's clutch (see
    _batter_clutch: derived hard-skill baseline plus a stacking
    leadership + grit bonus). A clutch batter facing a low-composure
    pitcher with bases loaded fires often; a flat batter against a
    poised veteran almost never does. Returns None if pressure
    doesn't manifest.

    Stage 2 — which manifestation fires? A talent-weighted draw between
    {hit, error, leave_up}, mutually exclusive (no stacking). Weights:
        hit       ∝ batter clutch (derived + leadership + grit)
        error     ∝ defensive frailty (1 - team_defense_rating)
        leave_up  ∝ pitcher uncomposure (1 - composure)

    Caller applies the chosen manifestation:
      - "hit"      → bumps the post-contact talent gate's hit_bonus
      - "error"    → forces an outed BIP to flip into a reach-on-error
      - "leave_up" → re-rolls the contact quality one tier up BEFORE
                     `resolve_contact` is invoked (mistake-pitch hit)

    O27 design note: bases loaded is the highest situational tier
    BECAUSE the 2C stay mechanic lets a batter iteratively clear bases
    without needing a grand slam — a 2C+1 chain plates two runs by
    itself, so the pressure-event payoff is even larger than MLB.
    """
    bases = state.bases
    on1 = bases[0] is not None
    on2 = bases[1] is not None
    on3 = bases[2] is not None
    if not (on2 or on3):
        return None

    if on1 and on2 and on3:
        situational = 0.35     # bases loaded — see design note above
    elif on2 and on3:
        situational = 0.24
    else:
        situational = 0.16     # _2_ or __3 alone, the baseline RISP case

    # composure & clutch read the player's CURRENT field values; the
    # caller's leadership_flare_scope (if active) has already lifted
    # those fields transiently, so the flare effects propagate without
    # needing parameters here.
    composure = (float(getattr(pitcher, "command", 0.5) or 0.5)
                 + float(getattr(pitcher, "grit",    0.5) or 0.5)) / 2.0
    clutch    = _batter_clutch(batter)
    # Pitcher vulnerability and batter pressure-creation, both centered
    # at 0 for neutral attributes. ±0.5 at the player rating extremes.
    pitch_vuln  = (0.5 - composure)
    bat_press   = (clutch - 0.5)
    p_fires = situational * (1.0 + 2.0 * pitch_vuln) * (1.0 + 2.0 * bat_press)
    p_fires = max(0.0, min(0.9, p_fires))
    if rng.random() >= p_fires:
        return None

    # Stage 2 — talent-weighted manifestation pick.
    team_def = float(getattr(state.fielding_team, "defense_rating", 0.5) or 0.5)
    hit_w      = max(0.0, clutch)
    error_w    = max(0.0, 1.0 - team_def)
    leave_up_w = max(0.0, 1.0 - composure)
    total_w    = hit_w + error_w + leave_up_w
    if total_w <= 0.0:
        return None
    r = rng.random() * total_w
    if r < hit_w:
        return "hit"
    if r < hit_w + error_w:
        return "error"
    return "leave_up"


def _runner_advance(
    rng: random.Random,
    base_advance: int,
    speed: float,
    extra_chance: float = 0.0,
    baserunning: float = 0.5,
    aggressiveness: float = 0.5,
) -> tuple[int, bool]:
    """Compute bases advanced by one runner; may take an extra base.

    Three player levers contribute to the extra-base probability:
      - speed         — raw foot speed (kept for back-compat)
      - baserunning   — read-off-bat / route / slide skill
      - aggressiveness — willingness to risk the extra base

    Returns (advance, thrown_out). If thrown_out is True the runner is
    OUT (TOOTBLAN). The base case — runner advances `base_advance` and is
    safe — returns (base_advance, False).

    Identity: speed = baserunning = aggressiveness = 0.5 → exactly the
    pre-baserunning-attribute behavior (no extra-base attempts beyond
    the explicit `extra_chance` baseline; no outs on the bases).
    """
    p_attempt = extra_chance
    p_attempt += max(0.0, (speed - 0.5) * cfg.RUNNER_EXTRA_SPEED_SCALE)
    p_attempt += max(0.0, (baserunning - 0.5) * cfg.RUNNER_EXTRA_SPEED_SCALE)
    p_attempt += max(0.0, (aggressiveness - 0.5) * 0.5 * cfg.RUNNER_EXTRA_SPEED_SCALE)

    if rng.random() >= p_attempt:
        return base_advance, False

    # Attempt fired. Resolve safe vs out (TOOTBLAN). Safe probability
    # scales with baserunning skill and modestly with speed; aggressive
    # runners attempt MORE often (above) but each individual attempt
    # has the same skill-driven safe rate, so the asymmetry reads as
    # "aggressive guys run into outs more than passive guys do".
    safe_p = (
        cfg.TOOTBLAN_SAFE_BASE
        + (baserunning - 0.5) * cfg.TOOTBLAN_SKILL_SCALE
        + (speed       - 0.5) * cfg.TOOTBLAN_SPEED_SCALE
    )
    safe_p = max(cfg.TOOTBLAN_SAFE_MIN, min(cfg.TOOTBLAN_SAFE_MAX, safe_p))
    if rng.random() < safe_p:
        return base_advance + 1, False
    # Thrown out trying for the extra base.
    return base_advance, True


def _gidp_force_factor(bases: list) -> float:
    """Multiplier on GIDP probability based on which bases are occupied.
    Reflects how many force-out options the defense has."""
    has_1b, has_2b, has_3b = (b is not None for b in bases)
    if has_1b and has_2b and has_3b:
        return cfg.GIDP_FORCE_LOADED
    if has_1b and has_2b:
        return cfg.GIDP_FORCE_1B_2B
    if has_1b and has_3b:
        return cfg.GIDP_FORCE_1B_3B
    if has_2b and has_3b:
        return cfg.GIDP_FORCE_2B_3B
    if has_1b:
        return cfg.GIDP_FORCE_1B_ONLY
    if has_2b:
        return cfg.GIDP_FORCE_2B_ONLY
    if has_3b:
        return cfg.GIDP_FORCE_3B_ONLY
    return 0.0


def _gidp_quality_factor(quality: Optional[str]) -> float:
    """Contact-quality multiplier on GIDP probability."""
    if quality == "weak":
        return cfg.GIDP_QUALITY_WEAK
    if quality == "hard":
        return cfg.GIDP_QUALITY_HARD
    return cfg.GIDP_QUALITY_MEDIUM


def _gidp_probability(state: GameState, batter: Player, quality: Optional[str]) -> float:
    """Compose the per-event GIDP probability and clamp to the configured
    [MIN, MAX] band. Used by both the run-path GIDP and the stay-path
    fielders'-choice block (the latter scales it down further)."""
    bat_speed = float(getattr(batter, "speed", 0.5) or 0.5)
    inf_def = float(getattr(state.fielding_team, "defense_rating", 0.5) or 0.5)
    p = (cfg.GIDP_BASE_PROB
         * _gidp_force_factor(state.bases)
         * _gidp_quality_factor(quality)
         - (bat_speed - 0.5) * cfg.GIDP_SPEED_SCALE
         + (inf_def - 0.5) * cfg.GIDP_DEFENSE_SCALE)
    return max(cfg.GIDP_MIN_PROB, min(cfg.GIDP_MAX_PROB, p))


def _thrown_out_at_home(rng: random.Random, speed: float, baserunning: float) -> bool:
    """Probability the runner is thrown out at the plate on a play where
    their default base_advance carries them across home (e.g. 2B runner
    on a single, 1B runner on a triple). Returns True if they're out.

    Distinct from the TOOTBLAN model in `_runner_advance`, which only fires
    when an EXTRA base is attempted above the default. This is for the
    routine-but-not-automatic case: a runner who's expected to score, but
    who can be cut down by a fielder's relay if they're slow / unaware.

    Symmetric in sign — fast/skilled runners shave the probability toward
    zero; slow/raw runners inflate it.
    """
    out_p = (cfg.RUNNER_THROWN_OUT_AT_HOME_BASE
             - (speed - 0.5) * cfg.RUNNER_THROWN_OUT_AT_HOME_SPEED_SCALE
             - (baserunning - 0.5) * cfg.RUNNER_THROWN_OUT_AT_HOME_SKILL_SCALE)
    # Floor: even an elite runner has a small chance of being thrown out —
    # a perfect throw, a slip rounding third, etc. Never automatic.
    out_p = max(cfg.RUNNER_THROWN_OUT_AT_HOME_MIN, out_p)
    return rng.random() < out_p


def _resolve_inside_park_hr(
    rng: random.Random,
    outcome_dict: dict,
    batter,
    state: GameState,
    ev,
    la,
    spray,
    park_dims,
) -> None:
    """Maybe convert a clean deep triple into an inside-the-park HR.

    Mutates outcome_dict in place. Fires ONLY on hit_type == 'triple':
    a ball misplayed for an error carries hit_type 'error' and is scored
    reached-on-error ("Little League home run"), never a HR, so the error
    path can never reach here. See config ITP_HR_* and
    docs/aar-inside-the-park-hr-and-pbp.md.

    Three terminal shapes:
      * inside_park HR  → hit_type 'hr' + inside_park flag (arms Walk-Back,
        scores like any HR).
      * out at home     → hit_type 'itp_out', batter_safe False (an out,
        no hit; runners ahead still score).
      * held at 3B      → untouched triple.
    """
    if not park_dims:
        return
    if outcome_dict.get("hit_type") != "triple":
        return
    if ev is None or la is None or spray is None:
        return
    from o27.engine.park_effects import _fence_at_angle, _proxy_distance
    fence = _fence_at_angle(spray, park_dims)
    dist = _proxy_distance(ev, la)
    if fence < cfg.ITP_HR_MIN_FENCE or dist < cfg.ITP_HR_MIN_DISTANCE:
        return

    speed = float(getattr(batter, "speed", 0.5) or 0.5)
    brun  = float(getattr(batter, "baserunning", 0.5) or 0.5)
    aggro = float(getattr(batter, "run_aggressiveness", 0.5) or 0.5)

    # Stage 1 — does the batter try to circle, or pull up at third?
    p_attempt = (cfg.ITP_HR_BASE_ATTEMPT
                 + (fence - cfg.ITP_HR_MIN_FENCE) * cfg.ITP_HR_DEPTH_SCALE
                 + (speed - 0.5) * cfg.ITP_HR_ATTEMPT_SPEED_SCALE
                 + (aggro - 0.5) * cfg.ITP_HR_ATTEMPT_AGGRO_SCALE)
    p_attempt = max(0.0, min(cfg.ITP_HR_ATTEMPT_MAX, p_attempt))
    if rng.random() >= p_attempt:
        return  # pulls up at third — stays a triple

    # Stage 2 — beat the relay home? OF arm of the fielder who ran it down.
    of_arm = 0.5
    fid = outcome_dict.get("fielder_id")
    if fid is not None:
        f = state.fielding_team.get_player(fid)
        if f is not None:
            of_arm = float(getattr(f, "arm", 0.5) or 0.5)
    p_success = (cfg.ITP_HR_BASE_SUCCESS
                 + (speed - 0.5) * cfg.ITP_HR_SUCCESS_SPEED_SCALE
                 + (brun  - 0.5) * cfg.ITP_HR_SUCCESS_BASERUN_SCALE
                 - (of_arm - 0.5) * cfg.ITP_HR_SUCCESS_ARM_SCALE)
    p_success = max(cfg.ITP_HR_SUCCESS_MIN, min(cfg.ITP_HR_SUCCESS_MAX, p_success))
    if rng.random() < p_success:
        # Inside-the-park HOME RUN. Scores exactly like an over-the-fence
        # HR (everyone in, batter in) and arms the Walk-Back downstream.
        outcome_dict["hit_type"] = "hr"
        outcome_dict["batter_safe"] = True
        outcome_dict["caught_fly"] = False
        outcome_dict["runner_advances"] = [4, 4, 4]
        outcome_dict["runner_out_idx"] = None
        outcome_dict["inside_park"] = True
        return

    # Failed the attempt: gunned at the plate, or scrambled back to 3B.
    p_out = cfg.ITP_HR_FAIL_OUT_BASE + (aggro - 0.5) * cfg.ITP_HR_FAIL_OUT_AGGRO_SCALE
    p_out = max(0.0, min(1.0, p_out))
    if rng.random() < p_out:
        # Thrown out at home. Runners ahead of the batter already crossed
        # and score; the batter is out — no hit credited.
        outcome_dict["hit_type"] = "itp_out"
        outcome_dict["batter_safe"] = False
        outcome_dict["caught_fly"] = False
        outcome_dict["runner_advances"] = [4, 4, 4]
        outcome_dict["runner_out_idx"] = None
        outcome_dict["itp_out_at_home"] = True
    # else: held at 3B — leave the triple as-is.


def _get_speed(pid: Optional[str], state: GameState) -> float:
    if pid is None:
        return 0.5
    p = state.batting_team.get_player(pid) or state.fielding_team.get_player(pid)
    return p.speed if p else 0.5


def _get_baserunning(pid: Optional[str], state: GameState) -> tuple[float, float]:
    """Return (baserunning_skill, run_aggressiveness) for the runner at pid."""
    if pid is None:
        return 0.5, 0.5
    p = state.batting_team.get_player(pid) or state.fielding_team.get_player(pid)
    if p is None:
        return 0.5, 0.5
    return (
        float(getattr(p, "baserunning", 0.5) or 0.5),
        float(getattr(p, "run_aggressiveness", 0.5) or 0.5),
    )


def _avg_outfielder_arm(state: GameState) -> float:
    """Average `arm` rating across the fielding team's LF / CF / RF.

    Falls back to 0.5 (neutral) when game_position isn't stamped (legacy
    engine-only paths) — the DB-driven sim plays via _assign_game_positions
    so every fielder carries a position string.
    """
    arms = []
    for p in getattr(state.fielding_team, "lineup", None) or []:
        gp = (getattr(p, "game_position", "") or getattr(p, "position", "") or "")
        if any(tag in gp for tag in ("LF", "CF", "RF")):
            arms.append(float(getattr(p, "arm", 0.5) or 0.5))
    if not arms:
        return 0.5
    return sum(arms) / len(arms)


def _resolve_table(
    rng: random.Random,
    table: list[tuple[str, float]],
    speed_dev: float,
    arm_dev: float,
    has_out: bool,
    score_shift: float = 0.0,
    out_shift: float = 0.0,
) -> str:
    """Pick an outcome from a (label, base_prob) table after applying
    speed (raises the first/score outcome) and arm (raises the out
    outcome where present) modifiers.

    `score_shift` is an extra additive bump to the score outcome (e.g. the
    per-half offensive sequencing form). Positive = runners more likely to
    take the extra base / score.

    `out_shift` is an extra additive bump to the "out" (runner thrown out
    advancing) outcome — the batted-ball texture lever. Positive = the runner
    is more likely to be erased (a grounder draws the throw); this is what
    actually lowers runs-per-hit, since holding a runner in a 27-out inning
    only delays his run. A positive out_shift also lifts the out cap so the
    texture can bite past the default 0.30 ceiling.

    Identity: speed_dev = arm_dev = score_shift = out_shift = 0 returns the
    base table draw.
    """
    speed_shift = speed_dev * cfg.SPEED_ADVANCE_MOD * 2.0  # ±0.12 at extremes
    arm_shift   = arm_dev   * cfg.ARM_ADVANCE_MOD   * 2.0  # ±0.11 at extremes

    # First entry is the "score" outcome — speed pushes it up, arm down.
    # Last entry is "out" (if has_out) — arm pushes it up, speed down.
    adjusted = [(name, p) for name, p in table]
    score_name, score_p = adjusted[0]
    score_p_adj = score_p + speed_shift - arm_shift + score_shift
    score_p_adj = max(0.02, min(0.97, score_p_adj))
    adjusted[0] = (score_name, score_p_adj)

    if has_out:
        out_name, out_p = adjusted[-1]
        out_p_adj = out_p + arm_shift - 0.5 * speed_shift + out_shift
        out_cap = min(0.55, 0.30 + max(0.0, out_shift))
        out_p_adj = max(0.0, min(out_cap, out_p_adj))
        adjusted[-1] = (out_name, out_p_adj)

    # Whatever is left between score and out goes to the middle "hold"
    # buckets, distributed proportionally to their original weights so
    # a three-way table (e.g. 1B-on-double SCORE/TO-3B/HOLD-2B) keeps
    # its mid-bucket shape.
    fixed_total = adjusted[0][1] + (adjusted[-1][1] if has_out else 0.0)
    remaining = max(0.0, 1.0 - fixed_total)
    mid_start = 1
    mid_end   = len(adjusted) - 1 if has_out else len(adjusted)
    mid_slice = adjusted[mid_start:mid_end]
    mid_base_sum = sum(p for _, p in mid_slice) or 1.0
    for i, (name, p) in enumerate(mid_slice):
        adjusted[mid_start + i] = (name, remaining * (p / mid_base_sum))

    # Sample.
    r = rng.random()
    cum = 0.0
    for name, p in adjusted:
        cum += p
        if r < cum:
            return name
    return adjusted[-1][0]


def _is_risp(state: GameState) -> bool:
    """Runner in scoring position — a man on 2B or 3B."""
    b = state.bases
    return (b[1] is not None) or (b[2] is not None)


def _locked_in_form(rng: random.Random, state: GameState) -> float:
    """The current batting half's unified "locked in tonight" form.

    ONE draw per batting half, cached on the state and re-rolled when the half
    label changes, returned to EVERY per-half channel — slugging redistribution,
    baserunner advancement, GIDP rate, RISP talent penalty, and RISP XBH
    suppression. Sharing a single latent draw across all of them is the whole
    point: a hot night relieves the RISP wobble AND slugs AND runs AND stays out
    of double plays together, so the effects compound into real blow-it-open /
    leave-em-loaded games instead of averaging out across the ~45 PAs.

    Returns 1.0 (neutral — every channel identity) when disabled
    (LOCKED_FORM_SIGMA <= 0). >1 = the lineup is locked in; <1 = nothing's
    falling. The mean is shifted by team quality so good teams run hot more
    often than bad ones (performance-grounded, not pure noise): the anchor is
    the team's BEST HITTER (max over the lineup of a power/skill blend, wherever
    he bats), with the manager persona as a small vibes nudge. Rolled lazily off
    the same rng stream as the rest of contact resolution, so it stays
    seed-deterministic.
    """
    sigma = getattr(cfg, "LOCKED_FORM_SIGMA", 0.0)
    if not sigma or sigma <= 0.0:
        return 1.0
    half = getattr(state, "half", None)
    if getattr(state, "_locked_form_half", object()) != half:
        team = getattr(state, "batting_team", None)
        # Performance anchor: the team's best hitter, scored as a power/skill
        # blend, taken as the MAX over the lineup — the streak rides on whether
        # the club has a real bat to carry it, not on a fixed lineup slot.
        lineup = list(getattr(team, "lineup", []) or [])
        best = 0.5
        if lineup:
            best = max(
                cfg.LOCKED_FORM_BAT_POWER_W * float(getattr(p, "power", 0.5) or 0.5)
                + cfg.LOCKED_FORM_BAT_SKILL_W * float(getattr(p, "skill", 0.5) or 0.5)
                for p in lineup
            )
        # Manager persona = the small vibes nudge (see Team.mgr_risp_pressure).
        risp_resp = float(getattr(team, "mgr_risp_pressure", 0.5) or 0.5)
        quality = (
            (best - 0.5) * cfg.LOCKED_FORM_BAT_W
            + (risp_resp - 0.5) * cfg.LOCKED_FORM_MGR_W
        )
        mean = getattr(cfg, "LOCKED_FORM_MEAN_BASE", 1.0) + quality * cfg.LOCKED_FORM_MEAN_SCALE
        draw = rng.gauss(mean, sigma)
        state._locked_form = max(cfg.LOCKED_FORM_MIN, min(cfg.LOCKED_FORM_MAX, draw))
        state._locked_form_half = half
    return getattr(state, "_locked_form", 1.0)


def _risp_clutch_form(rng: random.Random, state: GameState) -> float:
    """Back-compat shim — the RISP clutch form is now the unified per-half
    "locked in" draw (see _locked_in_form). Kept as a named entry point so the
    RISP read sites read clearly as "the clutch form."
    """
    return _locked_in_form(rng, state)


def _risp_talent_penalty(rng: random.Random, state: GameState) -> float:
    """Per-at-bat batter-talent multiplier for the RISP wobble.

    Returns 1.0 with no runner in scoring position. With RISP, returns
    1 - penalty, where penalty = uniform(MIN, MAX) (the per-AB "wobble") scaled
    by the per-half clutch form: a hot half shrinks the penalty toward zero
    (the team converts), a cold half deepens it (the team strands). So the same
    hitter's clutch capability swings both within the half (jitter) and across
    halves (the streak). Folded into contact_quality's batter-condition term,
    so it pulls matchup, power and eye down together. Disabled when
    RISP_TALENT_PENALTY_MAX <= 0.
    """
    hi = getattr(cfg, "RISP_TALENT_PENALTY_MAX", 0.0)
    if hi <= 0.0 or not _is_risp(state):
        return 1.0
    lo = getattr(cfg, "RISP_TALENT_PENALTY_MIN", 0.0)
    penalty = rng.uniform(min(lo, hi), hi)
    # Clutch form modulates the bite. form>1 (hot) relieves; form<1 (cold) deepens.
    form_dev = _risp_clutch_form(rng, state) - 1.0
    penalty *= max(0.0, 1.0 - form_dev * getattr(cfg, "RISP_CLUTCH_PENALTY_RELIEF", 0.0))
    return 1.0 - max(0.0, min(0.75, penalty))


def _risp_xbh_edges(quality: str) -> list[tuple[str, str, float]]:
    """Sum-preserving XBH→single edges applied when RISP — so the hits that do
    fall in with runners on are mostly singles, not bases-clearing extra-base
    hits. Empty (identity) for weak contact, which has no XBH to suppress."""
    if quality == "hard":
        return [
            ("hr",     "single", cfg.RISP_XBH_HARD_HR2S),
            ("triple", "single", cfg.RISP_XBH_HARD_T2S),
            ("double", "single", cfg.RISP_XBH_HARD_D2S),
        ]
    if quality == "medium":
        return [("double", "single", cfg.RISP_XBH_MED_D2S)]
    return []


def _batting_seq_form(rng: random.Random, state: GameState) -> float:
    """The current batting half's offensive sequencing form (slugging /
    baserunning / GIDP channels).

    Now an alias onto the unified per-half "locked in" draw (_locked_in_form):
    the sequencing channels share the SAME latent form as the RISP clutch
    channels, so a hot half slugs, runs, AND converts with runners on together.
    The dedicated SEQ_FORM_*_SCALE constants still set each sequencing channel's
    strength at the call sites; only the DRAW is shared now. Returns 1.0 when
    the mechanism is disabled (LOCKED_FORM_SIGMA <= 0).
    """
    return _locked_in_form(rng, state)


def _roll_batted_ball(rng: random.Random, quality: str, hit_type: str,
                      batter) -> str:
    """Roll the batted-ball texture for a HIT (the 'wasted hits' mechanism).

    Returns one of {dribbler, grounder, liner, flyball}. Rolled from contact
    quality + batter power so it's player-differentiated: low-power contact
    hitters spray grounders (hits that clog the bases and don't score runners),
    sluggers hit liners/flyballs (hits that drive runs). HR/triple are always
    well-struck; the texture mainly matters for singles and doubles. Carried as
    outcome_dict["batted_ball"] — NOT a hit_type — so stat-counting is untouched.
    """
    if hit_type == "hr":
        return "flyball"
    if hit_type == "triple":
        return "liner"
    base = getattr(cfg, "BATTED_BALL_WEIGHTS", {}).get(quality)
    if not base:
        return "liner"
    dribbler, grounder, liner, flyball = base
    # Power tilts weight grounder→liner→flyball (high power) or reverse (low).
    pdev = (float(getattr(batter, "power", 0.5) or 0.5) - 0.5) * 2.0
    tilt = pdev * getattr(cfg, "BATTED_BALL_POWER_TILT", 0.0)
    if tilt > 0:
        moved = min(grounder, grounder * tilt) + min(dribbler, dribbler * tilt)
        grounder -= grounder * tilt
        dribbler -= dribbler * tilt
        liner += moved * 0.6
        flyball += moved * 0.4
    elif tilt < 0:
        t = -tilt
        moved = min(liner, liner * t) + min(flyball, flyball * t)
        liner -= liner * t
        flyball -= flyball * t
        grounder += moved * 0.6
        dribbler += moved * 0.4
    weights = [max(0.0, w) for w in (dribbler, grounder, liner, flyball)]
    total = sum(weights) or 1.0
    r = rng.random() * total
    for label, w in zip(("dribbler", "grounder", "liner", "flyball"), weights):
        r -= w
        if r <= 0:
            return label
    return "liner"


def runner_advances_for_hit(
    rng: random.Random,
    hit_type: str,
    bases: list,
    state: GameState,
    batted_ball: str = "",
) -> tuple[list, list[int]]:
    """Return ([adv_1B, adv_2B, adv_3B], runner_out_idxs).

    runner_out_idxs is a list of base indices (0=1B, 1=2B, 2=3B) of runners
    thrown out advancing on this play. Empty list when all advancements
    were clean. Multiple TOAs on the same play are allowed (e.g. 2B-runner
    nailed at home AND 1B-runner nailed at second).
    """
    # Per-half offensive sequencing form → additive shift on every score roll
    # this half. This is the lever that decouples a game's runs from its hits:
    # a hot half scores runners on contact that a cold half strands, and the
    # shift is shared across all the half's PAs so it compounds into real
    # game-to-game run variance instead of averaging out.
    seq_shift = (_batting_seq_form(rng, state) - 1.0) * getattr(
        cfg, "SEQ_FORM_SCORE_SCALE", 0.0
    )
    # Batted-ball texture shifts. The SCORE shift (small) folds into the score
    # roll below; the OUT shift is the real lever — it raises the chance a
    # runner is thrown out advancing on a grounder, ERASING him (the only thing
    # that lowers runs-per-hit in a 27-out inning, since a held runner just
    # scores later). Empty/unknown texture → 0.0 (identity).
    seq_shift += getattr(cfg, "BATTED_BALL_SCORE_SHIFT", {}).get(batted_ball, 0.0)
    bb_out = getattr(cfg, "BATTED_BALL_OUT_SHIFT", {}).get(batted_ball, 0.0)

    s1 = _get_speed(bases[0], state)
    s2 = _get_speed(bases[1], state)
    s3 = _get_speed(bases[2], state)
    br1, ag1 = _get_baserunning(bases[0], state)
    br2, ag2 = _get_baserunning(bases[1], state)
    br3, ag3 = _get_baserunning(bases[2], state)

    out_idxs: list[int] = []
    of_arm = _avg_outfielder_arm(state)
    arm_dev = of_arm - 0.5

    def _spd_dev(idx: int) -> float:
        return [s1, s2, s3][idx] - 0.5

    if hit_type == "single":
        # Resolve each runner independently with the probability tables.
        # Order: 3B (closest to home, easiest call) → 2B → 1B.
        adv = [0, 0, 0]
        if bases[2] is not None:
            outcome = _resolve_table(
                rng,
                [("score", cfg.ADVANCE_3B_ON_1B_SCORE),
                 ("hold",  cfg.ADVANCE_3B_ON_1B_HOLD),
                 ("out",   cfg.ADVANCE_3B_ON_1B_OUT)],
                _spd_dev(2), arm_dev, has_out=True,
                score_shift=seq_shift, out_shift=bb_out,
            )
            if outcome == "score":
                adv[2] = 1
            elif outcome == "hold":
                adv[2] = 0
            else:
                out_idxs.append(2)
                adv[2] = 0
        if bases[1] is not None:
            outcome = _resolve_table(
                rng,
                [("score",   cfg.ADVANCE_2B_ON_1B_SCORE),
                 ("hold_3b", cfg.ADVANCE_2B_ON_1B_HOLD_3B),
                 ("out",     cfg.ADVANCE_2B_ON_1B_OUT)],
                _spd_dev(1), arm_dev, has_out=True,
                score_shift=seq_shift, out_shift=bb_out,
            )
            if outcome == "score":
                adv[1] = 2
            elif outcome == "hold_3b":
                adv[1] = 1
            else:
                out_idxs.append(1)
                adv[1] = 1   # cleared by advance_runners via out_idx
        if bases[0] is not None:
            outcome = _resolve_table(
                rng,
                [("to_3b", cfg.ADVANCE_1B_ON_1B_TO_3B),
                 ("to_2b", cfg.ADVANCE_1B_ON_1B_TO_2B),
                 ("out",   cfg.ADVANCE_1B_ON_1B_OUT)],
                _spd_dev(0), arm_dev, has_out=True,
                out_shift=bb_out,
            )
            if outcome == "to_3b":
                adv[0] = 2
            elif outcome == "to_2b":
                adv[0] = 1
            else:
                out_idxs.append(0)
                adv[0] = 1
        return adv, out_idxs

    elif hit_type == "double":
        adv = [0, 0, 0]
        # 3B runner on a double — almost auto-scores. Keep deterministic.
        if bases[2] is not None:
            adv[2] = 1
        if bases[1] is not None:
            outcome = _resolve_table(
                rng,
                [("score",   cfg.ADVANCE_2B_ON_2B_SCORE),
                 ("hold_3b", cfg.ADVANCE_2B_ON_2B_HOLD_3B),
                 ("out",     cfg.ADVANCE_2B_ON_2B_OUT)],
                _spd_dev(1), arm_dev, has_out=True,
                score_shift=seq_shift, out_shift=bb_out,
            )
            if outcome == "score":
                adv[1] = 2
            elif outcome == "hold_3b":
                adv[1] = 1
            else:
                out_idxs.append(1)
                adv[1] = 1
        if bases[0] is not None:
            outcome = _resolve_table(
                rng,
                [("score",     cfg.ADVANCE_1B_ON_2B_SCORE),
                 ("to_3b",     cfg.ADVANCE_1B_ON_2B_TO_3B),
                 ("hold_2b",   cfg.ADVANCE_1B_ON_2B_HOLD_2B),
                 ("out",       cfg.ADVANCE_1B_ON_2B_OUT)],
                _spd_dev(0), arm_dev, has_out=True,
                score_shift=seq_shift, out_shift=bb_out,
            )
            if outcome == "score":
                adv[0] = 3
            elif outcome == "to_3b":
                adv[0] = 2
            elif outcome == "hold_2b":
                adv[0] = 1
            else:
                out_idxs.append(0)
                adv[0] = 1
        return adv, out_idxs

    elif hit_type == "triple":
        # 1B runner is the close play at the plate on a triple. Almost
        # always scores, but a slow / unaware runner can be cut down by
        # a strong relay throw.
        adv1 = 3
        if bases[0] is not None and _thrown_out_at_home(rng, s1, br1):
            out_idxs.append(0)
            adv1 = 2   # cleared by advance_runners via out_idxs
        return [adv1, 3, 3], out_idxs

    elif hit_type == "hr":
        return [3, 3, 3], []

    elif hit_type in ("ground_out", "fielders_choice"):
        def _resolve(idx: int, base: int, speed: float, extra: float, br: float, ag: float) -> int:
            adv_n, thrown_out = _runner_advance(
                rng, base, speed, extra_chance=max(0.0, extra + seq_shift),
                baserunning=br, aggressiveness=ag,
            )
            if thrown_out and bases[idx] is not None:
                out_idxs.append(idx)
            return adv_n

        adv1 = 1   # 1B runner always forced to 2B on ground ball
        adv2 = _resolve(1, 0, s2, 0.25, br2, ag2)
        adv3 = _resolve(2, 0, s3, 0.35, br3, ag3)
        return [adv1, adv2, adv3], out_idxs

    elif hit_type == "fly_out":
        # Sac fly: skill matters as much as speed (timing the tag-up). The
        # sequencing form rides on top — a hot half gets the runner home from
        # 3B on contact, a cold half leaves him standing there.
        adv3, thrown_out = _runner_advance(
            rng, 0, s3, extra_chance=max(0.0, 0.55 + seq_shift),
            baserunning=br3, aggressiveness=ag3,
        )
        if thrown_out and bases[2] is not None:
            out_idxs.append(2)
        return [0, 0, adv3], out_idxs

    elif hit_type == "line_out":
        return [0, 0, 0], []   # runners freeze

    else:
        return [1, 1, 1], []   # default


# ---------------------------------------------------------------------------
# Contact outcome (fielding resolution) model
# ---------------------------------------------------------------------------

_CONTACT_TABLES = {
    "weak":   cfg.WEAK_CONTACT,
    "medium": cfg.MEDIUM_CONTACT,
    "hard":   cfg.HARD_CONTACT,
}


# ---------------------------------------------------------------------------
# Per-fielder play attribution
# ---------------------------------------------------------------------------
# When a BIP becomes an out (or an error), we pick the fielder responsible
# for the play using position-weighted probability tables. The picked
# fielder's player_id is stamped on the outcome dict so the renderer can
# credit them with PO (or E for errors). Spray-angle / handedness aren't
# yet tracked per-pitch, so the distributions are coarse — they just
# match the rough per-position frequencies of where balls in play land.

_FIELDER_WEIGHTS_BY_HIT: dict[str, dict[str, float]] = {
    # Grounders cluster at SS / 2B; corners + pitcher get fewer.
    "ground_out":      {"SS": 0.30, "2B": 0.25, "3B": 0.20, "1B": 0.18, "P": 0.04, "C": 0.03},
    "fielders_choice": {"SS": 0.28, "2B": 0.27, "3B": 0.20, "1B": 0.16, "P": 0.05, "C": 0.04},
    # Fly balls go to outfielders, CF most often.
    "fly_out":         {"CF": 0.50, "LF": 0.25, "RF": 0.25},
    # Liners split roughly between OF and corner IF.
    "line_out":        {"CF": 0.20, "LF": 0.18, "RF": 0.18, "1B": 0.12, "3B": 0.12, "SS": 0.10, "2B": 0.10},
    # Errors follow the same distribution as the play would have — whoever
    # was supposed to make the play muffed it. Default to ground-ball weights
    # since most errors are infield miscues.
    "error":           {"SS": 0.30, "2B": 0.25, "3B": 0.20, "1B": 0.18, "P": 0.04, "C": 0.03},
}


def _select_fielder(rng: random.Random, hit_type: str, fielding_team) -> Optional[str]:
    """Return the player_id of the fielder credited with the play, or None
    if no per-fielder attribution is meaningful (hits, walks, K's, etc.).

    Looks up the fielding team's lineup to find a player at the chosen
    position; falls back to None silently if no such position exists in
    the lineup (e.g. a roster missing a SS).
    """
    weights = _FIELDER_WEIGHTS_BY_HIT.get(hit_type)
    if not weights:
        return None
    # Sample a position by weight.
    total = sum(weights.values())
    r = rng.random() * total
    cumulative = 0.0
    chosen_pos: Optional[str] = None
    for pos, w in weights.items():
        cumulative += w
        if r < cumulative:
            chosen_pos = pos
            break
    if chosen_pos is None:
        return None
    # Find a player in the fielding lineup with that canonical position.
    # Position is stamped on the Player as `position` (currently only
    # set on engine players via legacy paths) — so we look at the lineup
    # in roster order. The engine doesn't carry position on Player today;
    # we use the lineup index as a proxy: with the standard 8-fielders +
    # SP layout, slots correspond loosely to positions. Until per-Player
    # position is plumbed, return the lineup member whose stored
    # `position` matches (Player has no .position currently — fall back
    # to roster lookup via attribute on Team if available).
    for p in fielding_team.roster:
        if getattr(p, "position", "") == chosen_pos:
            return p.player_id
    return None


def _redistribute(table: list, edges: list[tuple[str, str, float]],
                  power_dev: float) -> list:
    """Sum-preserving redistribution along (from, to, scale) edges.

    `power_dev` ∈ [-1, +1].  At power_dev = +1, `scale` fraction of the
    `from` row's weight moves to the `to` row.  At power_dev = -1, the
    flow reverses: `scale` fraction of `to` moves back to `from`.  At
    power_dev = 0 the table is unchanged (identity).

    Sum-preserving: the total table weight is invariant under this
    redistribution, so league-wide event totals stay stable while
    per-player profiles diverge with their power rating.
    """
    if power_dev == 0 or not edges:
        return table
    weights = {row[0]: row[3] for row in table}
    for from_name, to_name, scale in edges:
        if from_name not in weights or to_name not in weights:
            continue
        if power_dev > 0:
            shift = weights[from_name] * scale * power_dev
        else:
            shift = -weights[to_name] * scale * (-power_dev)
        weights[from_name] -= shift
        weights[to_name]   += shift
    return [(r[0], r[1], r[2], max(0.01, weights[r[0]])) for r in table]


def _apply_park(table: list, quality: str, park_hr: float,
                park_hits: float) -> list:
    """Park factors as multipliers on specific rows. Not sum-preserving
    (parks really do create / destroy events — that's what park factors
    mean), so this is multiplicative by design.
    """
    if park_hr == 1.0 and park_hits == 1.0:
        return table
    out = []
    for name, batter_safe, caught_fly, w in table:
        if quality == "hard" and name == "hr":
            w *= park_hr
        elif name in ("single", "double"):
            w *= park_hits
        out.append((name, batter_safe, caught_fly, max(0.01, w)))
    return out


# Power-axis redistribution edges per quality. Read as (from, to, scale):
# at +1 power_dev, `scale` fraction of `from` weight shifts to `to`.
def _hard_edges() -> list[tuple[str, str, float]]:
    return [
        ("line_out", "hr",     cfg.POWER_REDIST_HR),
        ("single",   "double", cfg.POWER_REDIST_HARD_S2D),
        ("double",   "triple", cfg.POWER_REDIST_HARD_D2T),
    ]

def _medium_edges() -> list[tuple[str, str, float]]:
    return [
        ("single",     "double",  cfg.POWER_REDIST_MED_S2D),
        ("ground_out", "fly_out", cfg.POWER_REDIST_MED_GO2FO),
    ]

def _weak_edges() -> list[tuple[str, str, float]]:
    return [
        ("single", "fly_out", cfg.POWER_REDIST_WEAK_S2FO),
    ]


# Per-half sequencing-form redistribution edges. Much larger scales than the
# per-batter power edges: the form is the decoupler and needs to swing the
# single↔XBH↔HR mix hard. Sum-preserving (single→double→hr just relabels hit
# mass), so the hit COUNT is untouched while slugging — and thus runs — moves.
def _seq_hard_edges() -> list[tuple[str, str, float]]:
    return [
        ("single",   "hr",     cfg.SEQ_REDIST_HARD_S2HR),
        ("double",   "hr",     cfg.SEQ_REDIST_HARD_D2HR),
        ("line_out", "hr",     cfg.SEQ_REDIST_HARD_LO2HR),
    ]

def _seq_medium_edges() -> list[tuple[str, str, float]]:
    return [
        ("single", "double", cfg.SEQ_REDIST_MED_S2D),
    ]


def _pick_from_table(rng: random.Random, table: list) -> tuple:
    """Pick a row from a (name, batter_safe, caught_fly, weight) table."""
    total = sum(row[3] for row in table)
    r = rng.random() * total
    cumulative = 0.0
    for row in table:
        cumulative += row[3]
        if r < cumulative:
            return row
    return table[-1]


def _lead_runner_idx(bases: list) -> Optional[int]:
    """Return the index (2=3B, 1=2B, 0=1B) of the lead runner, or None."""
    for idx in (2, 1, 0):
        if bases[idx] is not None:
            return idx
    return None


def resolve_contact(
    rng: random.Random,
    quality: str,
    batter: Player,
    state: GameState,
    launch_angle_bias: float = 0.0,
) -> dict:
    """
    Resolve a ball-in-play event into a full fielding outcome dict.

    Returns an outcome dict compatible with apply_event / advance_runners.

    Phase 10.2 power model:
      - batter.power redistributes weight along power-axis edges INSIDE
        each contact-quality table (sum-preserving). High power moves
        weight from singles → doubles, doubles → triples, line_outs → HRs
        on hard contact; from singles → doubles and ground_outs → fly_outs
        on medium contact; from singles → fly_outs (infield pops) on weak
        contact when power is *low*. Low power reverses the flow.
      - Total table weight is invariant under this redistribution, so
        league-wide event totals stay stable while per-player profiles
        diverge with their power rating.
    Park factors are applied separately as multipliers (parks really do
    create / destroy events, so they're multiplicative by design).
    """
    table = _CONTACT_TABLES.get(quality, cfg.WEAK_CONTACT)

    # Power-axis redistribution driven purely by the rating. The legacy
    # archetype `hr_weight_bonus` boost was removed — its HR inflation
    # double-counted what the modern `power` rating already models.
    # Joker decay shrinks the deviation as the joker accumulates ABs
    # this game, so a tired joker stops driving extra power outcomes.
    power_dev = (batter.power - 0.5) * 2.0 * _resolve_joker_decay(state, batter)

    if quality == "hard":
        table = _redistribute(table, _hard_edges(), power_dev)
    elif quality == "medium":
        table = _redistribute(table, _medium_edges(), power_dev)
    elif quality == "weak":
        table = _redistribute(table, _weak_edges(), power_dev)

    # Per-half offensive form → its OWN strong, sum-preserving single↔XBH↔HR
    # redistribution. This is the primary H~R decoupler. The per-batter power
    # edges above are deliberately gentle; the form needs a much wider swing to
    # move a half's run total off its hit count, so it gets dedicated big-scale
    # edges. A hot half turns singles into doubles and homers (runs spike, hit
    # COUNT unchanged — a single and a homer are both one hit); a cold half
    # leaves nothing but singles that pile up and strand. Total table weight is
    # invariant, so this shifts slugging, not how many balls fall in.
    seq_power_dev = (_batting_seq_form(rng, state) - 1.0) * getattr(
        cfg, "SEQ_FORM_POWER_SCALE", 0.0
    )
    if seq_power_dev:
        if quality == "hard":
            table = _redistribute(table, _seq_hard_edges(), seq_power_dev)
        elif quality == "medium":
            table = _redistribute(table, _seq_medium_edges(), seq_power_dev)

    # RISP hit-type suppression: with a runner in scoring position, the hits
    # that fall in are mostly singles — pull HR/triple/double weight back into
    # singles (sum-preserving, so hit count is untouched, only slugging). The
    # big bases-clearing swing becomes the exception with runners on, so runners
    # advance station-to-station and pile up rather than scoring in bunches.
    if _is_risp(state):
        risp_edges = _risp_xbh_edges(quality)
        if risp_edges:
            # Clutch form scales the suppression: a hot half lifts it (XBH allowed
            # — the lineup clears the bases), a cold half pushes past full (every
            # RISP hit a single). Neutral/disabled → dev 1.0 (flat suppression).
            form_dev = _risp_clutch_form(rng, state) - 1.0
            xbh_dev = max(0.0, 1.0 - form_dev * getattr(cfg, "RISP_CLUTCH_XBH_RELIEF", 0.0))
            if xbh_dev > 0.0:
                table = _redistribute(table, risp_edges, xbh_dev)

    # Pitch launch-angle bias: roll ground_out↔fly_out weight by the pitch's
    # launch_angle_bias (sum-preserving). Negative bias (sinker, peeled_drop,
    # drop_knuck) drives grounders; positive (riseball, rise_knuck) drives
    # fly balls / popups. This is what makes the grounder/popup split a REAL
    # outcome difference between pitches, not just weak-contact flavor.
    # Identity at bias = 0.0.
    if launch_angle_bias:
        table = _redistribute(
            table,
            [("ground_out", "fly_out", cfg.LAUNCH_REDIST_GO2FO)],
            float(launch_angle_bias),
        )

    # Park factors (multiplicative) applied AFTER redistribution.
    park_hr   = getattr(state.home, "park_hr", 1.0) if state.home else 1.0
    park_hits = getattr(state.home, "park_hits", 1.0) if state.home else 1.0

    # Weather HR multiplier stacks onto park_hr (multiplicative).
    weather = getattr(state, "weather", None)
    park_hr = park_hr * wx.hr_multiplier(weather)

    table = _apply_park(table, quality, park_hr, park_hits)

    hit_type, batter_safe, caught_fly, _ = _pick_from_table(rng, table)

    # ---- Defense layer ----------------------------------------------------
    # The fielding team's `defense_rating` modulates whether borderline
    # plays end as outs or hits, and whether would-be-outs become errors
    # (batter reaches, possibly UER charged).
    fielding = state.fielding_team
    team_def = float(getattr(fielding, "defense_rating", 0.5) or 0.5)
    def_dev = team_def - 0.5   # neutral 0; +0.35 for elite team; -0.35 for awful

    # Fielding fatigue (item 3 from the bat-second viability pass):
    # the team that batted FIRST has now been in the field for the
    # entire second half. By the late arc (out 20+ of regulation), their
    # range / arm / glovework slips. Symmetric by role — if the visitor
    # batted first they get the penalty in the bottom; if home batted
    # first they get it in the top. Identity preserved in super-innings
    # and seconds rounds (separate phases, lineups reset).
    if (not state.is_super_inning
            and not state.in_seconds_phase
            and state.first_batting_team is not None
            and fielding is state.first_batting_team
            and state.outs >= cfg.FIELDING_FATIGUE_OUT_GATE):
        def_dev -= cfg.FIELDING_FATIGUE_PENALTY

    is_error = False
    gem_effect = None        # set when a fielder robs a hit (great-play render hook)
    gem_fielder_id = None    # the specific fielder credited with the gem

    # Range shift: probabilistically flip a single-or-out outcome.
    # Better defense (def_dev > 0) → some "single" results flip to ground_out.
    # Worse defense (def_dev < 0) → some "ground_out" / "fly_out" / "line_out"
    # results flip to "single".
    range_shift = abs(def_dev) * cfg.DEFENSE_RANGE_SHIFT_SCALE * 2
    if range_shift > 0 and rng.random() < range_shift:
        if def_dev > 0 and hit_type == "single":
            hit_type = "ground_out"
            batter_safe = False
            caught_fly = False
        elif def_dev < 0 and hit_type in ("ground_out", "fly_out", "line_out"):
            hit_type = "single"
            batter_safe = True
            caught_fly = False

    # Defensive shift outcome. Two alignments handled separately:
    #   infield  — pull-side single → ground_out, oppo grounder → single
    #   outfield — pull-side XBH → single,        oppo grounder → single
    # Direction is rolled per event using batter.pull_pct as the bias.
    # Adaptability erosion: if the manager has called the SAME alignment
    # against this batter multiple consecutive ABs, the batter's
    # adaptability rating progressively reads the gaps and the shift
    # probabilities erode (capped at 3 streak steps).
    shift_effect = None
    shift_type = getattr(state, "current_ab_shift_type", "none")
    if shift_type != "none":
        pull = float(getattr(batter, "pull_pct", 0.5) or 0.5)
        went_pull = rng.random() < pull
        # Adaptation factor — non-negative reduction applied to both
        # shift-effective probabilities. Streak=1 (first AB of this
        # alignment) contributes 0; streak=2 contributes 1*scale; etc.
        adapt_dev = (float(getattr(batter, "adaptability", 0.5) or 0.5) - 0.5) * 2.0
        streak = max(0, getattr(batter, "shift_streak", 1) - 1)
        streak = min(streak, 3)                # cap erosion at 3 streak steps
        adapt_reduction = max(0.0, adapt_dev * streak * cfg.ADAPTABILITY_SCALE)

        if shift_type == "infield" and hit_type in (
            "single", "ground_out", "fielders_choice"
        ):
            p_out = max(0.05, cfg.SHIFT_PULL_OUT_PROB - adapt_reduction)
            p_hit = max(0.05, cfg.SHIFT_OPPO_HIT_PROB - adapt_reduction)
            if went_pull and hit_type == "single":
                if rng.random() < p_out:
                    hit_type = "ground_out"
                    batter_safe = False
                    caught_fly = False
                    shift_effect = "out_added"
            elif not went_pull and hit_type == "ground_out":
                if rng.random() < p_hit:
                    hit_type = "single"
                    batter_safe = True
                    caught_fly = False
                    shift_effect = "hit_lost"
        elif shift_type == "outfield":
            p_xbh = max(0.05, cfg.SHIFT_OF_XBH_HELD_PROB - adapt_reduction)
            p_hit = max(0.05, cfg.SHIFT_OF_OPPO_HIT_PROB - adapt_reduction)
            if went_pull and hit_type in ("double", "triple"):
                if rng.random() < p_xbh:
                    hit_type = "single"
                    batter_safe = True
                    caught_fly = False
                    shift_effect = "out_added"   # bookkeeping: defensive gain
            elif not went_pull and hit_type == "ground_out":
                if rng.random() < p_hit:
                    hit_type = "single"
                    batter_safe = True
                    caught_fly = False
                    shift_effect = "hit_lost"

    # ---- Defensive gem -----------------------------------------------------
    # A fielder turns a would-be hit into an out with a spectacular play.
    # Per-FIELDER + probabilistic: a base rate lets anyone in the position with
    # a decent glove flash one occasionally, and the individual fielder's
    # defense/arm scales the rate up (elite) or toward zero (poor) — so the
    # great glove robs hits far more often, but it's never a fixed "only this
    # guy" trait. A robbed extra-base hit is run down (caught fly); a robbed
    # single is a diving liner grab. Surfaced as a "ROBBED!" play in the
    # play-by-play via outcome["gem_effect"]. Identity when GEM_BASE_* = 0.
    if batter_safe and not is_error and hit_type in ("single", "double", "triple"):
        if hit_type in ("double", "triple"):
            gem_base, gem_out_type, gem_caught, gem_pos, gem_label = (
                cfg.GEM_BASE_XBH, "fly_out", True, "fly_out", "robbed_xbh")
        else:
            gem_base, gem_out_type, gem_caught, gem_pos, gem_label = (
                cfg.GEM_BASE_SINGLE, "line_out", False, "line_out", "robbed_liner")
        if quality == "hard":
            gem_base *= cfg.GEM_HARD_MULT
        # Pick the fielder who makes the play from the position-player pool,
        # weighted toward better gloves. Positions aren't plumbed onto Players
        # in O27, so we weight by the relevant defense rating rather than a
        # fixed slot — which IS the "anyone in that spot with a good glove can
        # do it, the elite glove does it more" model. _ = gem_pos (kept for
        # readability of which alignment the play came from).
        _ = gem_pos
        def_key = "defense_outfield" if gem_label == "robbed_xbh" else "defense_infield"
        pool = [p for p in getattr(fielding, "roster", [])
                if not getattr(p, "is_pitcher", False)]
        gem_fid = None
        f_def = f_arm = 0.5
        if pool:
            def _grate(p):
                r = getattr(p, def_key, None)
                if r is None:
                    r = getattr(p, "defense", 0.5)
                return float(r or 0.5)
            weights = [0.5 + _grate(p) for p in pool]
            gp = rng.choices(pool, weights=weights, k=1)[0]
            gem_fid = gp.player_id
            f_def = _grate(gp)
            f_arm = float(getattr(gp, "arm", 0.5) or 0.5)
        gem_p = gem_base * max(0.0, 1.0
                               + (f_def - 0.5) * 2.0 * cfg.GEM_FIELDER_SCALE
                               + (f_arm - 0.5) * 2.0 * cfg.GEM_ARM_SCALE)
        gem_p = min(cfg.GEM_MAX, gem_p)
        if rng.random() < gem_p:
            hit_type = gem_out_type
            batter_safe = False
            caught_fly = gem_caught
            gem_effect = gem_label
            gem_fielder_id = gem_fid

    # Error chance — only on plays that resolved as an out. Worse defense =
    # higher error rate. Caught flies don't generate errors at this layer
    # (they're clean catches by the time we get here).
    if not batter_safe and hit_type != "fielders_choice" and not caught_fly:
        err_p = cfg.DEFENSE_ERROR_BASE - def_dev * cfg.DEFENSE_ERROR_SCALE
        err_p *= wx.error_multiplier(weather)
        err_p = max(cfg.DEFENSE_ERROR_MIN, min(cfg.DEFENSE_ERROR_MAX, err_p))
        if rng.random() < err_p:
            is_error = True
            hit_type = "error"      # synthetic outcome — pa.py + render handle
            batter_safe = True
            caught_fly = False

    # Compute runner advances based on (possibly flipped) hit type.
    # An "error" advances runners like a single — same conservative shape.
    advance_type = "single" if hit_type == "error" else hit_type
    # Roll batted-ball texture for hits — a grounder single advances runners
    # worse than a liner single (the "wasted hits" mechanism). Only meaningful
    # for actual hits; outs/errors get no texture.
    batted_ball = ""
    if hit_type in ("single", "double", "triple", "hr"):
        batted_ball = _roll_batted_ball(rng, quality, hit_type, batter)
    runner_adv, br_out_idxs = runner_advances_for_hit(
        rng, advance_type, state.bases, state, batted_ball=batted_ball)

    # For fielder's choice: throw out the lead runner. TOA outs from the
    # probabilistic advancement table get mapped onto runner_out_idx /
    # extra_runner_outs and are flagged via toa_runner_idxs so the
    # renderer can credit each runner with the TOA stat (distinct from
    # FC, GIDP-style force-outs).
    runner_out_idx: Optional[int] = None
    extra_runner_outs: list[int] = []
    toa_runner_idxs: list[int] = []
    if hit_type == "fielders_choice" and state.runners_on_base:
        runner_out_idx = _lead_runner_idx(state.bases)
        # Any extra TOAs from the advancement roll still apply (e.g.
        # trail runner trying to take an extra base on the FC throw).
        extra_runner_outs = [i for i in br_out_idxs if i != runner_out_idx]
        toa_runner_idxs = list(extra_runner_outs)
    elif br_out_idxs:
        runner_out_idx = br_out_idxs[0]
        extra_runner_outs = br_out_idxs[1:]
        toa_runner_idxs = list(br_out_idxs)

    # Per-fielder play attribution. Stamps the fielder_id of the player
    # credited with this play (PO for outs, E for errors). Returns None
    # for hits — those don't get a fielder credit.
    fielder_id = _select_fielder(rng, hit_type, fielding)
    # A defensive gem credits the specific fielder who made the play.
    if gem_fielder_id is not None:
        fielder_id = gem_fielder_id

    return {
        "hit_type": hit_type,
        "batted_ball": batted_ball,
        "batter_safe": batter_safe,
        "caught_fly": caught_fly,
        "runner_advances": runner_adv,
        "runner_out_idx": runner_out_idx,
        "extra_runner_outs": extra_runner_outs,
        "toa_runner_idxs": toa_runner_idxs,
        "is_error": is_error,
        "fielder_id": fielder_id,
        "quality": quality,
        "shift_effect": shift_effect,
        "gem_effect": gem_effect,
    }


# ---------------------------------------------------------------------------
# Stay decision (probabilistic — Phase 2)
# ---------------------------------------------------------------------------

def should_stay_prob(
    rng: random.Random,
    state: GameState,
    batter: Player,
    quality: str,
    caught_fly: bool = False,
    is_hr: bool = False,
    is_triple: bool = False,
) -> bool:
    """
    Phase 2 probabilistic stay decision.

    Applies all §4.5 hard rules first, then uses batter.stay_aggressiveness
    and batter.contact_quality_threshold as probabilistic gates.
    """
    # Hard rule: stay unavailable (no runners).
    if not state.runners_on_base:
        return False
    # Hard rule: home run → always run (forfeiting 4 bases for a single
    # is never worth a strike-and-hit credit).
    if is_hr:
        return False
    # Hard rule: triple → run (3 bases > 1 base of hit credit + a strike).
    if is_triple:
        return False
    # Hard rule: hard contact → run (likely XBH; same forfeit logic).
    if quality == "hard":
        return False
    # Hard rule: caught fly → batter is out on contact; stay decision moot.
    if caught_fly:
        return False
    # NOTE: 2-strike and 2-out cases are NOT hard rules. Per the corrected
    # stay rule:
    #   - Stay credits a hit AND uses 1 strike. At 2 strikes, that 3rd-
    #     strike-from-stay just ends the AB (with the hit credited, NOT
    #     as a batter-out). So 2-strike stays are *good* on weak/medium
    #     contact — you trade an AB-end for a free hit credit.
    #   - 2 outs in the half: same logic. Stay never produces an out, so
    #     it doesn't end the half. The runners advance, hit credited,
    #     AB ends if strikes hit 3.
    # Removing these hard rules lets the AI take the strategically right
    # action in late-count / late-half situations.

    # Probabilistic gate: stay_aggressiveness scaled by leverage signals.
    # 2C is the engine's "advance runners / bring them home" mechanic,
    # plus a "work the count / foul off pitches" mechanic for skilled
    # hitters. Frequency lifts compose multiplicatively from:
    #   - RISP leverage (real run-driving opportunity)
    #   - Count state (patient hitter hunting; 2-strike protect)
    #   - Late game push (manufacture runs even without RISP)
    stay_p = batter.stay_aggressiveness

    # RISP leverage.
    if state.bases[1] is not None or state.bases[2] is not None:
        stay_p *= cfg.STAY_RISP_MULT
    elif state.bases[0] is not None:
        stay_p *= cfg.STAY_1B_ONLY_MULT

    # Count awareness: patient hitter ahead in the count is "waiting for
    # his pitch" — more inclined to stay on marginal contact and get
    # another swing. NB: no 2-strike lift — O27 has no foul-off survival
    # mechanic (3 fouls = FOUL OUT), so the MLB protect-mode metaphor
    # doesn't apply.
    if state.count.balls > state.count.strikes:
        stay_p *= cfg.STAY_AHEAD_IN_COUNT_MULT

    # Late-game push: last third of the half, manufacture-runs mode.
    if state.outs >= cfg.LATE_GAME_OUTS_THRESHOLD:
        stay_p *= cfg.STAY_LATE_GAME_MULT

    return rng.random() < stay_p


# ---------------------------------------------------------------------------
# Between-pitch events (stolen base, wild pitch)
# ---------------------------------------------------------------------------

def between_pitch_event(rng: random.Random, state: GameState) -> Optional[dict]:
    """
    Optionally return a between-pitch event (pickoff, stolen-base, wild pitch).

    Called before each pitch draw; returns None if no event fires.
    Resolution order: pickoff → wild pitch → stolen base → hit-and-run.
    Pickoff fires first because in real ball it's the pitcher's first
    chance to act after seeing the runner's lead.
    """
    pitcher = state.get_current_pitcher()
    p_throws = (getattr(pitcher, "throws", "") or "") if pitcher else ""
    p_stuff  = float(getattr(pitcher, "pitcher_skill", 0.5) or 0.5) if pitcher else 0.5

    # Pickoff attempt: only meaningful with a runner on 1B (idx=0) or 2B.
    # 3B pickoffs do exist but are rare; we ignore them.
    for base_idx in (0, 1):
        pid = state.bases[base_idx]
        if pid is None:
            continue
        br_skill, aggression = _get_baserunning(pid, state)
        attempt_p = (
            cfg.PICKOFF_ATTEMPT_BASE
            + (aggression - 0.5) * cfg.PICKOFF_AGGRESSION_SCALE
        )
        if base_idx == 0 and p_throws == "L":
            attempt_p += cfg.PICKOFF_LHP_1B_BONUS
        if base_idx == 1:
            attempt_p *= cfg.PICKOFF_2B_DAMPENER
        if attempt_p <= 0:
            continue
        if rng.random() >= attempt_p:
            continue
        # Move fires — does it pick the runner off?
        success_p = (
            cfg.PICKOFF_SUCCESS_BASE
            + p_stuff * cfg.PICKOFF_SUCCESS_PITCHER_SCALE
            + (aggression - 0.5) * cfg.PICKOFF_SUCCESS_AGGRESSION_SCALE
            - (br_skill   - 0.5) * cfg.PICKOFF_SUCCESS_BR_SCALE
        )
        success_p = max(cfg.PICKOFF_SUCCESS_MIN,
                        min(cfg.PICKOFF_SUCCESS_MAX, success_p))
        success = rng.random() < success_p
        return {
            "type": "pickoff_attempt",
            "base_idx": base_idx,
            "success": success,
        }

    # Wild pitch: small chance per pitch with runners on base.
    if state.runners_on_base and rng.random() < cfg.WILD_PITCH_PROB:
        return {"type": "wild_pitch"}

    batting_team = state.batting_team
    run_game = float(getattr(batting_team, "mgr_run_game", 0.5))

    # Hit-and-run: manager-called play where the runner goes and the batter
    # protects. We model it as a flagged SB attempt that bypasses the speed
    # gate AND gets a small success bonus (catcher's eyes on the batter).
    # Real managers concentrate hit-and-run in specific counts — a 0-2 hole
    # is the worst possible spot, while 1-0 / 2-1 / 3-1 are canonical. Skip
    # entirely with two strikes (batter can't protect a borderline pitch).
    if (state.bases[0] is not None and state.bases[1] is None
            and state.count.strikes < 2):
        count_tup = (state.count.balls, state.count.strikes)
        h_and_r_p = (
            cfg.HIT_AND_RUN_BASE_PROB
            + (run_game - 0.5) * cfg.HIT_AND_RUN_RUNGAME_SCALE
        )
        if count_tup not in cfg.HIT_AND_RUN_FAVORED_COUNTS:
            h_and_r_p *= cfg.HIT_AND_RUN_OFF_COUNT_DAMPENER
        if h_and_r_p > 0 and rng.random() < h_and_r_p:
            pid = state.bases[0]
            speed = _get_speed(pid, state)
            pitcher_skill = pitcher.pitcher_skill if pitcher else 0.5
            cat_arm = float(getattr(state.fielding_team, "catcher_arm", 0.5) or 0.5)
            success_p = (
                cfg.SB_SUCCESS_BASE
                + (speed - 0.5) * cfg.SB_SUCCESS_SPEED_SCALE
                - pitcher_skill * cfg.SB_SUCCESS_PITCHER_SCALE
                - (cat_arm - 0.5) * cfg.SB_SUCCESS_CATCHER_ARM_SCALE
                + cfg.HIT_AND_RUN_SUCCESS_BONUS
            )
            success = rng.random() < max(cfg.SB_SUCCESS_MIN,
                                         min(cfg.SB_SUCCESS_MAX, success_p))
            return {
                "type": "stolen_base_attempt",
                "base_idx": 0,
                "success": success,
                "hit_and_run": True,
            }

    # Stolen base attempt: check 1B and 2B runners. The batting team's
    # manager run_game tendency scales the per-pitch attempt probability
    # AND the speed threshold — an aggressive run-game manager will run
    # with average speed, a passive one waits for elite speed only.
    # Threshold: lerps from speed_threshold * 1.30 (passive) to * 0.65 (aggressive).
    speed_thresh = cfg.SB_ATTEMPT_SPEED_THRESHOLD * (1.30 - 0.65 * run_game)
    # Per-pitch attempt prob: lerps from base * 0.4 (passive) to * 1.8 (aggressive).
    attempt_prob = cfg.SB_ATTEMPT_PROB_PER_PITCH * (0.4 + 1.4 * run_game)
    for base_idx in (0, 1):
        pid = state.bases[base_idx]
        if pid is None:
            continue
        # Can't steal a base that's already occupied (e.g. a Walk-Back bonus
        # runner on 3B blocks a steal of third from second).
        if state.bases[base_idx + 1] is not None:
            continue
        speed = _get_speed(pid, state)
        if speed < speed_thresh:
            continue
        if rng.random() < attempt_prob:
            # Probability of success: speed + tired-battery + catcher-arm aware.
            pitcher = state.get_current_pitcher()
            pitcher_skill = pitcher.pitcher_skill if pitcher else 0.5
            # Pitch debt = recent rolling pitches across last 5 days. A tired
            # battery has reduced arm strength on throws to second/third —
            # late-half / heavy-workload steals get noticeably easier.
            pitch_debt = float(getattr(pitcher, "pitch_debt", 0) or 0)
            # Catcher arm — stamped on the fielding Team at game start.
            # An elite-arm catcher (arm ≥ 0.85) shuts down the running game;
            # a noodle-arm (≤ 0.30) is exploited mercilessly. Identity at
            # arm = 0.5 → no shift on success_p.
            cat_arm = float(getattr(state.fielding_team, "catcher_arm", 0.5) or 0.5)
            success_p = (
                cfg.SB_SUCCESS_BASE
                + (speed - 0.5) * cfg.SB_SUCCESS_SPEED_SCALE
                - pitcher_skill * cfg.SB_SUCCESS_PITCHER_SCALE
                + pitch_debt * cfg.SB_SUCCESS_DEBT_SCALE
                - (cat_arm - 0.5) * cfg.SB_SUCCESS_CATCHER_ARM_SCALE
            )
            success = rng.random() < max(cfg.SB_SUCCESS_MIN, min(cfg.SB_SUCCESS_MAX, success_p))
            return {
                "type": "stolen_base_attempt",
                "base_idx": base_idx,
                "success": success,
            }
    return None


# ---------------------------------------------------------------------------
# Probabilistic event provider
# ---------------------------------------------------------------------------

class ProbabilisticProvider:
    """
    Callable event provider for run_game() that drives plate appearances
    probabilistically using the supplied seeded RNG.

    On each call the provider:
      1. Checks for manager decisions at the start of each new batter's PA.
      2. Optionally inserts a between-pitch event (stolen base / wild pitch).
      3. Generates the next pitch (or full contact event if contact occurs).
    """

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self._last_batter_id: Optional[str] = None
        self._manager_checked: bool = False
        # Daily form tracking — every time a fresh pitcher takes the mound
        # (half start or pitching change) we re-roll their today_form so
        # the same SP can throw a gem one start and a clunker the next.
        self._last_pitcher_id: Optional[str] = None

    def _maybe_roll_form(self, state: GameState) -> None:
        """If the fielding pitcher changed, roll a new today_form for them.

        Identity invariant: at TODAY_FORM_SIGMA=0 + days_rest=99 +
        pitch_debt=0 the form collapses to 1.0 and the engine reduces to
        legacy behavior.

        Also folds in a multi-game fatigue penalty: a pitcher who threw
        recently has their effective form reduced proportional to their
        rolling pitch debt minus their stamina-derived budget. This is
        what makes a real workhorse different from a glass-arm reliever
        AT THE GAME LEVEL — within an appearance, the existing FATIGUE_*
        within-game model still applies.
        """
        pitcher = state.get_current_pitcher()
        if pitcher is None:
            return
        if pitcher.player_id == self._last_pitcher_id:
            return
        self._last_pitcher_id = pitcher.player_id
        # Pitch-variance widening: high-variance ("max-effort, frayed
        # mechanics") arms swing wider day-to-day; low-variance arms hold
        # consistent. Grit damps both ends — a gritty veteran with high
        # variance still finds his stuff more often than a low-grit
        # young arm. Identity at pitch_variance=0 and grit=0.5.
        pv   = float(getattr(pitcher, "pitch_variance", 0.0) or 0.0)
        grit = float(getattr(pitcher, "grit", 0.5) or 0.5)
        grit_damp = 1.0 - 0.6 * (grit - 0.5)           # 0.7 (gritty) … 1.3 (fragile)
        sigma_eff = cfg.TODAY_FORM_SIGMA + pv * 0.50 * grit_damp + max(0.0, (0.5 - grit)) * 0.06
        widen     = pv * 1.50 * grit_damp
        min_eff   = cfg.TODAY_FORM_MIN - widen
        max_eff   = cfg.TODAY_FORM_MAX + widen
        form = self.rng.gauss(cfg.TODAY_FORM_MU, sigma_eff)
        form = max(min_eff, min(max_eff, form))

        # Multi-game fatigue: scale form down by pitch-debt overrun.
        debt = int(getattr(pitcher, "pitch_debt", 0) or 0)
        if debt > 0:
            # Stamina-relative budget: a 0.5-stamina pitcher absorbs ~50
            # debt pitches over the rolling window before the penalty kicks
            # in; an elite 0.85-stamina arm absorbs ~85.
            budget = max(cfg.FATIGUE_DEBT_MIN_BUDGET,
                         pitcher.stamina * cfg.FATIGUE_DEBT_BUDGET_SCALE)
            excess = max(0, debt - budget)
            penalty = min(cfg.FATIGUE_DEBT_MAX_PENALTY,
                          excess * cfg.FATIGUE_DEBT_PER_PITCH)
            form *= (1.0 - penalty)

        pitcher.today_form = max(min_eff, form)

    def __call__(self, state: GameState) -> Optional[dict]:
        # Detect new batter (new PA or batter changed by joker insertion).
        current_batter_id = state.current_batter.player_id
        if current_batter_id != self._last_batter_id:
            self._last_batter_id = current_batter_id
            self._manager_checked = False
            state.current_ab_shift_decided = False
            state.current_ab_shift_type = "none"

        # Defensive shift decision — fires once per AB before the first
        # pitch. Two alignments available:
        #   infield  — standard pull-shift; convert pull GBs to outs
        #   outfield — 4-man OF; convert pull XBHs to singles, sacrifices
        #              infield coverage on oppo grounders
        # Alignment picked from batter's power: pull-heavy + high power
        # invites outfield shift; pull-heavy + low power invites infield.
        # Probability scales with batter's spray extremity, manager's
        # mgr_shift_aggression, and a leverage multiplier (RISP +
        # late-arc) — shifts are a prevent-defense tool that ratchets
        # in critical moments, matching the tennis-scoring design.
        if not state.current_ab_shift_decided:
            state.current_ab_shift_decided = True
            batter = state.current_batter
            mgr_shift = float(getattr(state.fielding_team,
                                      "mgr_shift_aggression", 0.5) or 0.5)
            batter_pull = float(getattr(batter, "pull_pct", 0.5) or 0.5)
            extremity = abs(batter_pull - 0.5) * 2.0
            # Aggressive baseline: a floor (SHIFT_BASE_PROB) means even
            # neutral-spray batters draw a shift sometimes, and pull-heavy
            # batters get shifted nearly every time. O27 defenses shift
            # constantly — it's a primary lever, not a situational gimmick.
            shift_p = (cfg.SHIFT_BASE_PROB + extremity) * mgr_shift * cfg.SHIFT_DECISION_SCALE

            # Leverage ratchet — RISP AND late arc both fire, the shift
            # decision lifts to "prevent defense" mode. Either alone is
            # routine; both together is when the game gets decided.
            risp = state.bases[1] is not None or state.bases[2] is not None
            late = state.outs >= cfg.LATE_GAME_OUTS_THRESHOLD
            if risp and late:
                shift_p *= cfg.SHIFT_LEVERAGE_MULT

            shift_p = min(cfg.SHIFT_DECISION_MAX, shift_p)
            if self.rng.random() < shift_p:
                power = float(getattr(batter, "power", 0.5) or 0.5)
                if power >= cfg.SHIFT_OF_POWER_THRESHOLD:
                    state.current_ab_shift_type = "outfield"
                else:
                    state.current_ab_shift_type = "infield"
            else:
                state.current_ab_shift_type = "none"

            # Adaptation streak — count consecutive ABs the manager has
            # called the SAME alignment against this batter. High-streak +
            # high-adaptability erodes the shift's effectiveness in
            # resolve_contact. Manager who wants to keep the shift live
            # has to vary it occasionally to reset the streak.
            last = getattr(batter, "last_shift_alignment", "none")
            if state.current_ab_shift_type == last:
                batter.shift_streak = getattr(batter, "shift_streak", 0) + 1
            else:
                batter.shift_streak = 1
                batter.last_shift_alignment = state.current_ab_shift_type

        # Refresh today_form whenever the pitcher changes (half start or
        # mid-game change). Cheap; a single deterministic gauss draw.
        self._maybe_roll_form(state)

        # Manager decisions fire once at the start of each batter's PA.
        if not self._manager_checked:
            self._manager_checked = True
            mgr_event = self._try_manager_action(state)
            if mgr_event:
                event_type = mgr_event.get("type")
                if event_type == "pitching_change":
                    # May need another check after the change.
                    self._manager_checked = False
                return mgr_event
            # Declared-Seconds short-circuit: the declaration handler in
            # _try_manager_action returns None but mutates state.outs=27
            # to end the half. If the half is now over, return None so
            # run_half exits BEFORE we fall through to pitch generation
            # (which would otherwise process a phantom PA at out 27+).
            if state.is_half_over():
                return None

        # Between-pitch chance (stolen base, wild pitch).
        bp = between_pitch_event(self.rng, state)
        if bp is not None:
            return bp

        # Generate the next pitch.
        return self._generate_pitch(state)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_manager_action(self, state: GameState) -> Optional[dict]:
        """Return one manager event if conditions are met, else None.

        Priority order:
          1. Pitching change (fielding team decision).
          2. Joker insertion (preferred over pinch hit when jokers remain).
          3. Pinch hit (fallback when jokers exhausted and pitcher is up in
             a tie-game, runners-in-scoring-position situation).
        """
        # Declared Seconds — checked first so a declaration doesn't waste
        # a pitching change / joker / pinch hit. Recomputed every PA in
        # the eligible window (out 22+); fires when the AI's target save
        # count meets the current outs-remaining. Returns a "declaration"
        # event so run_half processes it through apply_event + render_event
        # like any other event (gets a play-by-play line, then the half
        # ends naturally because apply_event sets state.outs = 27).
        declared, banked = mgr.evaluate_declaration(state, rng=self.rng)
        if declared:
            return {
                "type": "declaration",
                "team": state.batting_team.team_id,
                "team_name": state.batting_team.name,
                "at_out": state.outs,
                "outs_banked": banked,
            }

        # Pitching change check.
        if mgr.should_change_pitcher(state):
            new_p = mgr.pick_new_pitcher(state)
            if new_p is not None:
                return {"type": "pitching_change", "new_pitcher": new_p}

        # Joker insertion — leverage-aware, optional. Returns None most
        # of the time; fires only when the situational value is high
        # enough to justify burning one of the cycle's joker uses.
        joker = mgr.should_insert_joker(state, rng=self.rng)
        if joker is not None:
            return {"type": "joker_insertion", "joker": joker}

        # Intentional walk — fielding team refuses to pitch to a hot or
        # elite batter. Decided AFTER joker insertion so the IBB call
        # reflects who is actually batting (including a just-inserted
        # joker). The pa.apply_event handler routes through _walk so BB
        # stats and force-advances behave identically to a 4-ball walk.
        if mgr.should_intentional_walk(state, rng=self.rng):
            return {"type": "intentional_walk"}

        # Pinch hit check (separate mechanic; permanently replaces a
        # regular hitter — survives joker insertions).
        replacement = mgr.should_pinch_hit(state, rng=self.rng)
        if replacement is not None:
            return {"type": "pinch_hit", "replacement": replacement}

        # Defensive substitution by the FIELDING team. O27-specific
        # tactic: swap a bench glove in once the team has banked some
        # defensive workload, locking in better range for the rest of
        # the fielding half. Capped at one sub per team per game.
        def_sub = mgr.should_defensive_sub(state, rng=self.rng)
        if def_sub is not None:
            return {
                "type": "defensive_sub",
                "player_out": def_sub["player_out"],
                "player_in":  def_sub["player_in"],
            }

        # Catcher rotation — pull a gassed catcher for a fresh one from the
        # corps. Reuses the defensive_sub event/handler; resets the fatigue
        # accumulator so the new catcher starts rested.
        cat_swap = mgr.should_swap_catcher(state, rng=self.rng)
        if cat_swap is not None:
            state.fielding_team.catcher_outs_caught = 0
            return {
                "type": "defensive_sub",
                "player_out": cat_swap["player_out"],
                "player_in":  cat_swap["player_in"],
            }

        # Pinch runner — late-game, close score, slow runner on base.
        # Burns the slow batter's lineup slot for a fresh set of legs.
        pr = mgr.should_pinch_run(state, rng=self.rng)
        if pr is not None:
            return {
                "type": "pinch_runner",
                "base_idx": pr["base_idx"],
                "runner_in": pr["runner_in"],
            }

        # Joker-to-field — VERY rare. Only fires very late game with the
        # fielding team trailing badly and a notable defense upgrade
        # available from a joker. Standard MLB analog: functionally
        # never happens; in O27 the mechanic exists for completeness.
        j2f = mgr.should_joker_to_field(state, rng=self.rng)
        if j2f is not None:
            return {
                "type": "joker_to_field",
                "joker": j2f["joker"],
                "player_out": j2f["player_out"],
            }

        # Wholesale offensive→defensive UNIT swap at the phase boundary.
        # First-batting team only, late in their offensive phase, fires at
        # most once per game. Swaps in a unit of defensive specialists
        # (size scales with mgr_platoon_aggression) before the team fields.
        # Checked before the single-player tactical_def_swap so the bigger
        # re-tool gets first claim on the bench.
        unit_swaps = mgr.should_phase_transition_swap(state, rng=self.rng)
        if unit_swaps:
            return {"type": "phase_transition_swap", "swaps": unit_swaps}

        # Mid-batting-half offensive→defensive swap. Road team only
        # (state.half == "top"), once the lineup has cycled at least
        # once. Pulls a slugger and brings in a defensive specialist
        # who'll cover the field for the team's fielding half.
        # Symmetric to should_defensive_sub but operates on the
        # BATTING team — they're banking defense for later.
        off_to_def = mgr.should_swap_offensive_for_defense(state, rng=self.rng)
        if off_to_def is not None:
            return {"type": "tactical_def_swap", "replacement": off_to_def}

        # Sac-bunt check. Trades an out for a base; old-school / small-ball /
        # high-run-game managers will call it in the right spots, modern /
        # sabermetric skippers basically never. Resolves directly to an
        # outcome (bunt out, bunt for hit, or popup) — pa.py treats it
        # like a contact event with a synthetic outcome dict.
        bunt = mgr.should_bunt(state, rng=self.rng)
        if bunt is not None:
            return bunt

        return None

    def _generate_pitch(self, state: GameState) -> dict:
        """Draw one pitch and, if contact, resolve it fully."""
        pitcher = state.get_current_pitcher()
        batter  = state.current_batter
        rng     = self.rng

        # Safe fallback if pitcher not assigned.
        if pitcher is None:
            pitcher = batter  # use batter's own stats as a stub

        balls   = state.count.balls
        strikes = state.count.strikes
        spell   = state.pitcher_spell_count

        # Per-PA leadership flare — fired ONCE at PA start (first pitch
        # of the PA, before pitch_outcome or contact_quality run). The
        # flare mutates rating fields IN PLACE on the batter and pitcher
        # (and the fielding team) so every downstream read this PA — the
        # pitch model, the contact model, the talent gate, the RISP
        # pressure roll, the fielding rolls — picks up the lifted value
        # uniformly. Restored at PA end in pa._end_at_bat. This is the
        # mechanism that makes leadership impact the WHOLE game for
        # whichever side flares, not just offense. See
        # apply_pa_leadership_flares / release_pa_leadership_flares.
        if (balls == 0 and strikes == 0
                and state.current_at_bat_swings == 0
                and not state.flare_lift_active):
            apply_pa_leadership_flares(rng, state, batter, pitcher)

        weather = getattr(state, "weather", None)

        # Select one pitch from the repertoire (if the pitcher has one).
        # The same pitch drives both the pitch-outcome model and, if contact
        # results, the contact-quality model. Legacy pitchers (no repertoire)
        # return (None, 0.5) and the aggregate Stuff/Command/Movement path fires.
        sel_pitch, sel_quality = _select_pitch(rng, pitcher, balls, strikes)

        # Joker decay — looked up once per pitch (cheap dict read). Applied
        # to both pitch-outcome and contact-quality so the joker's effective
        # ratings sag together as their PA count climbs this game.
        joker_decay = _resolve_joker_decay(state, batter)

        # Times-through-the-order familiarity. How many PAs this batter has
        # already completed against THIS pitcher this game drives how far the
        # matchup has tilted toward the hitter, scaled down by the pitcher's
        # repertoire timing_resistance (deception arms stay un-timeable). Zero
        # on the first look → identity. Threaded into both outcome models.
        times_faced = state.matchup_count(pitcher.player_id, batter.player_id)
        familiarity = _familiarity_dominance(
            times_faced, _pitcher_timing_resistance(pitcher)
        )

        outcome = pitch_outcome(
            rng, pitcher, batter, balls, strikes, spell, weather,
            pitch_type=sel_pitch, pitch_quality=sel_quality,
            joker_decay=joker_decay, familiarity=familiarity,
        )

        # HBP: a fraction of balls become hit-by-pitches, scaled by pitcher
        # command. Converting after pitch_outcome instead of teaching
        # _pitch_probs about HBP keeps the realism identity invariant on
        # the underlying probability surface intact.
        if outcome == "ball":
            cmd = float(getattr(pitcher, "command", 0.5) or 0.5)
            hbp_p = cfg.HBP_FROM_BALL_BASE + (0.5 - cmd) * cfg.HBP_COMMAND_SCALE
            if hbp_p > 0 and rng.random() < hbp_p:
                outcome = "hit_by_pitch"

        # Hit-and-run protection: when the runner has already gone on
        # an h&r, the batter is swinging at most pitches to put the
        # ball in play. We approximate by re-rolling a swinging strike
        # against a contact-bias probability — a non-trivial fraction
        # of would-be Ks become fouls or weak contact instead. Only
        # consumes the flag (one shot per success).
        if state.hit_and_run_active:
            if outcome == "swinging_strike" and rng.random() < cfg.HIT_AND_RUN_CONTACT_K_REDUCTION:
                # Batter fouls it off to stay alive.
                outcome = "foul"
            elif outcome == "ball" and rng.random() < cfg.HIT_AND_RUN_CONTACT_K_REDUCTION:
                # Batter swings at a borderline pitch to protect.
                outcome = "foul"
            # Flag persists until contact (single h&r call only protects
            # the runner once the play resolves).
            if outcome in ("contact", "swinging_strike"):
                state.hit_and_run_active = False

        if outcome != "contact":
            return {"type": outcome, "pitch_type": sel_pitch}

        # --- Contact resolution ---
        # Target pressure (item 2 from the bat-second viability pass):
        # whichever team bats second gets a small contact tilt for their
        # first ~12 PAs of the half — they know the number to beat. Fades
        # to zero linearly. Symmetric by role: doesn't matter whether home
        # or visitor is in the bat-second seat.
        tp_shift = 0.0
        if (not state.is_super_inning
                and not state.in_seconds_phase
                and state.second_batting_team is not None
                and state.batting_team is state.second_batting_team):
            pa_idx = int(state.total_pa_this_half or 0)
            fade = max(0.0, 1.0 - pa_idx / max(1, cfg.TARGET_PRESSURE_FADE_PAS))
            tp_shift = cfg.TARGET_PRESSURE_SHIFT * fade

        # Rebuttal-phase offense tilt (item 1): seconds rounds are a slightly
        # higher-offense environment because the pitcher cools off during the
        # declaration break while the batter's timing stays sharp. Super-
        # innings are normal extra-inning baseball (no declaration break), so
        # they get no such boost.
        if state.in_seconds_phase:
            tp_shift += cfg.REBUTTAL_OFFENSE_SHIFT

        quality = contact_quality(
            rng, batter, pitcher, weather,
            swings_in_ab=state.current_at_bat_swings,
            pitch_type=sel_pitch, pitch_quality=sel_quality,
            target_pressure_shift=tp_shift,
            joker_decay=joker_decay,
            familiarity=familiarity,
            risp_penalty=_risp_talent_penalty(rng, state),
            catcher_shift=_catcher_gc_shift(state),
        )
        is_hr     = False
        is_triple = False

        # RISP pressure — two-stage talent-driven roll. Pitcher composure
        # vs. batter clutch drives whether the moment manifests; if it
        # does, exactly one of {hit, error, leave_up} fires — those
        # three are mutually exclusive. The per-PA leadership flares
        # (rolled at PA start via apply_pa_leadership_flares above) have
        # already mutated the relevant ratings in place, so composure
        # and clutch read the LIFTED values automatically here — no
        # parameter threading needed. See _resolve_risp_pressure
        # docstring. "leave_up" must fire BEFORE resolve_contact so
        # the mistake pitch actually changes the contact-quality bucket.
        risp_event = _resolve_risp_pressure(rng, state, batter, pitcher)
        if risp_event == "leave_up":
            if quality == "weak":
                quality = "medium"
            elif quality == "medium":
                quality = "hard"

        # Resolve fielding outcome. The selected pitch's launch_angle_bias
        # tilts the ground_out↔fly_out split so grounder pitches (sinker,
        # peeled_drop, drop_knuck) and popup pitches (riseball, rise_knuck)
        # produce genuinely different batted-ball profiles.
        _pitch_lab = 0.0
        if sel_pitch:
            _pitch_lab = float(
                (cfg.PITCH_CATALOG.get(sel_pitch, {}) or {}).get("launch_angle_bias", 0.0)
                or 0.0
            )
        outcome_dict = resolve_contact(rng, quality, batter, state,
                                       launch_angle_bias=_pitch_lab)

        # "error" manifestation — if the contact resolved as a routine
        # out, the defender bobbles it under pressure and the batter
        # reaches. Skipped on caught flies (the catch already happened)
        # and on fielders' choice (the lead runner is already out).
        if (risp_event == "error"
                and not outcome_dict.get("batter_safe", True)
                and outcome_dict.get("hit_type") in ("ground_out", "fly_out", "line_out")
                and not outcome_dict.get("caught_fly")):
            outcome_dict["hit_type"] = "error"
            outcome_dict["batter_safe"] = True
            outcome_dict["is_error"] = True
            outcome_dict["runner_out_idx"] = None
            outcome_dict["extra_runner_outs"] = []
            outcome_dict["toa_runner_idxs"] = []
            # Errors advance runners like a single; re-roll the advance
            # vector against the now-cleared state of the BIP.
            adv, outs = runner_advances_for_hit(
                rng, "single", state.bases, state
            )
            outcome_dict["runner_advances"] = adv
            if outs:
                outcome_dict["runner_out_idx"] = outs[0]
                outcome_dict["extra_runner_outs"] = outs[1:]
                outcome_dict["toa_runner_idxs"] = list(outs)

        # Talent-weighted hit-vs-out resolution (applies BEFORE the run/stay
        # decision so it affects both paths). On weak / medium contact, the
        # underlying hit_type from resolve_contact is talent-flexed: a
        # marginal-talent batter is more likely to see a borderline hit
        # downgrade to a ground_out, and a star is more likely to see a
        # borderline ground_out upgrade to an infield_single. The gate uses
        # the same talent_factor as the 2C fractional advance — eye + contact
        # − command — so a single coherent talent signal flows through every
        # contact event in O27, not just stays.
        if quality in ("weak", "medium"):
            # The leadership flares (if any) have already mutated
            # batter.eye / batter.contact / pitcher.command at PA start,
            # so the reads below pick up the lifted values uniformly.
            eye_dev_run = (batter.eye - 0.5) * 2
            con_dev_run = (batter.contact - 0.5) * 2
            cmd_dev_run = (pitcher.command - 0.5) * 2
            talent_run  = eye_dev_run + con_dev_run - cmd_dev_run
            # "hit" manifestation — clutch batter sees the mistake pitch
            # and capitalizes. Lift scales with the batter's own clutch
            # (already includes flare lift since eye/contact are mutated).
            if risp_event == "hit":
                _clutch = (batter.eye + batter.contact) / 2.0
                talent_run += 2.0 * (_clutch - 0.5)
            # Bonus is bidirectional: positive shifts toward more hits,
            # negative toward more outs. Weak gets a wider swing because
            # the underlying outcome is more often borderline; medium has
            # mostly already-hit outcomes so the swing is narrower.
            hit_bonus = (0.15 if quality == "weak" else 0.10) * talent_run
            ht = outcome_dict.get("hit_type", "")
            is_safety   = ht in ("single", "infield_single", "double", "triple")
            is_clean_out = (ht in ("ground_out", "fly_out", "line_out")
                            and not outcome_dict.get("batter_safe", True)
                            and not outcome_dict.get("caught_fly"))
            if is_safety and hit_bonus < 0:
                # Marginal talent can lose a borderline hit.
                if rng.random() < min(0.6, abs(hit_bonus)):
                    outcome_dict["hit_type"] = "ground_out"
                    outcome_dict["batter_safe"] = False
                    outcome_dict["runner_advances"] = [0, 0, 0]
            elif is_clean_out and hit_bonus > 0:
                # Star talent can flip a borderline out into an infield_single.
                if rng.random() < min(0.6, hit_bonus):
                    new_type = "infield_single" if quality == "weak" else "single"
                    outcome_dict["hit_type"] = new_type
                    outcome_dict["batter_safe"] = True
                    outcome_dict["runner_advances"] = [1, 1, 1]

        # Batted-ball physics + park-shape gameplay hook. Sample synthetic
        # (EV, LA, spray) from contact quality + power + pitch metadata,
        # then mutate the categorical hit_type against the home park's
        # actual fence geometry. Polo-Grounds bathtub → HR factory down
        # the lines; oval cricket-ground → pull HRs vanish, gappers
        # become triples. Identity no-op when state.park_dimensions is
        # None or hit_type is non-BIP.
        #
        # Done BEFORE the Stay decision so the runner sees the final
        # hit_type (a fly_out → HR upgrade doesn't leave a runner who
        # decided to stay on a caught fly).
        from o27.engine.batted_ball import sample_batted_ball as _sample_bb
        from o27.engine.park_effects import apply_park_effects as _apply_park
        _pitch_hcs = 0.0
        if sel_pitch:
            _pmeta = cfg.PITCH_CATALOG.get(sel_pitch, {}) or {}
            _pitch_hcs = float(_pmeta.get("hard_contact_shift", 0.0) or 0.0)
        ev_v, la_v, spray_v = _sample_bb(
            rng,
            quality=quality,
            hit_type=outcome_dict.get("hit_type", "") or "",
            batter_power=float(getattr(batter, "power", 0.5) or 0.5),
            pitch_hard_contact_shift=_pitch_hcs,
            batter_bats=str(getattr(batter, "bats", "") or ""),
            pitch_launch_bias=_pitch_lab,
        )
        _apply_park(
            rng,
            outcome_dict,
            ev=ev_v, la=la_v, spray=spray_v,
            park_dims=getattr(state, "park_dimensions", None),
        )

        # Inside-the-park HR contest. A clean deep triple in a deep/irregular
        # park may become an ITPHR (scores + arms the Walk-Back), an out at
        # home, or stay a triple. Resolved before the Stay decision: a batter
        # circling the bases is a run outcome by definition, never a stay.
        _resolve_inside_park_hr(
            rng, outcome_dict, batter, state,
            ev_v, la_v, spray_v,
            getattr(state, "park_dimensions", None),
        )

        hit_type = outcome_dict["hit_type"]
        caught_fly = outcome_dict["caught_fly"]

        # Rich descriptive name from the (EV, LA, spray) we just sampled +
        # the now-final hit_type. Purely for display (box score / play-by-
        # play); never read back by mechanics.
        from o27.engine.batted_ball import classify_batted_ball as _classify_bb
        outcome_dict["batted_ball_name"] = _classify_bb(
            ev_v, la_v, spray_v, hit_type if not caught_fly else "fly_out"
        )

        is_hr     = (hit_type == "hr")
        is_triple = (hit_type == "triple")
        # ITPHR / out-at-home are terminal run outcomes — force "run" so the
        # Stay mechanic can't strand a batter who's already circling.
        _itp_terminal = bool(outcome_dict.get("inside_park")
                             or outcome_dict.get("itp_out_at_home"))

        # Stay-vs-run decision.
        if not _itp_terminal and stay_mod.stay_available(state):
            stay = should_stay_prob(
                rng, state, batter, quality,
                caught_fly=caught_fly,
                is_hr=is_hr,
                is_triple=is_triple,
            )
            choice = "stay" if stay else "run"
        else:
            choice = "run"

        # Talent-weighted 2C outcome resolution (Path A). Replaces Phase 11C's
        # unconditional [2,2,2] for medium stays with a talent-gated version
        # and adds a hit-credit gate for weak stays. Applies on every 2C
        # event (including swing 1, which Path 2's swing-2+ scope didn't
        # reach), so the eye/contact-vs-command signal differentiates the
        # bulk of the 2C population.
        if choice == "stay" and quality in ("weak", "medium"):
            eye_dev = (batter.eye - 0.5) * 2
            con_dev = (batter.contact - 0.5) * 2
            cmd_dev = (pitcher.command - 0.5) * 2
            # Talent factor — full batter contribution (no averaging),
            # so eye and contact each contribute their full signed range
            # to the gate. Theoretical range ±3.0; typical ±1.0.
            talent_factor = eye_dev + con_dev - cmd_dev
            # Talent-driven fractional advance. talent_factor maps to an
            # EXPECTED advance value (continuous, talent-diverse), then
            # one rng draw resolves the fractional part to an integer.
            #   weak quality   expected ≈ 1.0 + 0.5*talent_factor
            #     → low-talent  (factor ≈ -1):  expected ~0.5 → ~50% credit
            #       neutral     (factor ≈  0):  expected ~1.0 → always +1
            #       high-talent (factor ≈ +2):  expected ~2.0 → always +2
            #   medium quality expected ≈ 1.5 + 0.75*talent_factor
            #     → low-talent:  expected ~0.75 → mostly 1, sometimes 0
            #       neutral:     expected ~1.5  → 50/50 between 1 and 2
            #       high-talent: expected ~3.0  → always max (score from 1B)
            # Floors are higher than Path A originals — earned 2Cs reliably
            # move runners, so chained hits produce runs instead of just
            # credit-only "free" hits. Pitchers pay via pitch count.
            if quality == "weak":
                # Floor lifted from 0.55 → 0.70: successful weak stays now
                # average ~0.7 bases of advancement (vs ~0.55), bringing the
                # mechanic's mean RV closer to neutral. Successful 2Cs are
                # supposed to move runners; the previous floor underdelivered.
                expected = 0.70 + 0.5 * talent_factor
            else:  # medium
                # Floor lifted from 1.05 → 1.20 for the same reason — a medium-
                # contact 2C should reliably move runners more than one base
                # when it succeeds.
                expected = 1.20 + 0.75 * talent_factor
            expected = max(0.0, min(3.0, expected))
            floor_v = int(expected)
            frac = expected - floor_v
            adv = floor_v + (1 if rng.random() < frac else 0)
            adv = max(0, min(3, adv))
            outcome_dict["runner_advances"] = [adv, adv, adv]

            # Defense-read on the 2C: a fraction of valid stays get
            # broken up by the defense reading the play — catcher snaps
            # a throw down, OF charges in to nip the lead runner at a
            # bag, infielders rotate to a tag at second. Probability
            # scales with team defense rating so good defenses make
            # the 2C feel risky; bad defenses let it run wild.
            if adv > 0 and any(state.bases):
                team_def = float(getattr(state.fielding_team, "defense_rating", 0.5) or 0.5)
                cat_arm = float(getattr(state.fielding_team, "catcher_arm", 0.5) or 0.5)
                p_read = (cfg.STAY_DEFENSE_READ_BASE
                          + (team_def - 0.5) * cfg.STAY_DEFENSE_READ_TEAM_SCALE
                          + (cat_arm  - 0.5) * cfg.STAY_DEFENSE_READ_CATCHER_SCALE)
                p_read = max(cfg.STAY_DEFENSE_READ_MIN,
                             min(cfg.STAY_DEFENSE_READ_MAX, p_read))
                if rng.random() < p_read:
                    # Pick the lead runner (highest occupied base) — that's
                    # the one most likely to get nailed on a heads-up play.
                    lead_idx = next(
                        (i for i in (2, 1, 0) if state.bases[i] is not None),
                        None,
                    )
                    if lead_idx is not None:
                        outcome_dict["runner_out_idx"] = lead_idx

        # GIDP / triple play. A ground out with at least one runner on base
        # and < 2 outs can become a double play. Probability composes:
        #   base × force_factor(bases) × quality_factor(quality)
        #     ± speed/defense bonuses, clamped to [GIDP_MIN_PROB, GIDP_MAX_PROB].
        # The force_factor encodes that 1B-occupancy gives a free force at
        # 2B (canonical DP), while a lone 2B or 3B runner requires a tag
        # (rarer). The quality_factor encodes that weak grounders are
        # DP-prone and hard contact rarely DPs.
        # Lead-runner identity for the force-out:
        #   - 1B occupied → 1B runner forced at 2B. (Standard DP.)
        #   - 1B empty, 2B occupied → 2B runner thrown out at 3B. (Tag play.)
        #   - 1B empty, 2B empty, 3B occupied → 3B runner thrown out at home.
        # With 1B+2B both occupied AND 0 outs, a slice of DPs promote to
        # triple plays (force at 3B AND force at 2B, then batter at 1B).
        # Bases loaded extends this. Errors short-circuit the whole thing.
        # Stay (2C) plays don't allow a true DP — the batter isn't running
        # so there's no force at 1B — but a separate reduced-rate fielders'
        # choice block below tags out the lead runner.
        # O27 gate: NOT MLB's per-inning "< 2 outs". There are no innings —
        # one continuous 27-out half — so the only real constraint is that the
        # half has room to record the two outs a DP turns. Gating on `outs < 2`
        # (the literal MLB rule) made double plays dead code for 25 of every 27
        # outs, which is the single biggest reason ~87% of baserunners came
        # around to score and runs tracked hits almost 1:1. Letting DPs fire all
        # half long is the structurally-correct runner-erasing event — and the
        # per-half form rides on top: a cold ("rally-killer") half hits into
        # more twin-killings, a hot half stays out of them.
        if (choice == "run"
                and outcome_dict.get("hit_type") == "ground_out"
                and not outcome_dict.get("is_error")
                and any(state.bases)
                and state.outs <= state.out_cap() - 2):
            form = _batting_seq_form(rng, state)
            dp_form_mult = 1.0 + (1.0 - form) * getattr(cfg, "SEQ_FORM_GIDP_SCALE", 0.0)
            dp_p = _gidp_probability(state, batter, quality) * max(0.1, dp_form_mult)
            if rng.random() < dp_p:
                # Identify the lead force/tag-out target. Prefer 1B (force
                # at 2B); fall back to 2B (tag at 3B), then 3B (tag at home).
                lead_idx = (0 if state.bases[0] is not None
                            else 1 if state.bases[1] is not None else 2)
                outcome_dict["hit_type"] = "double_play"
                outcome_dict["runner_out_idx"] = lead_idx
                adv = list(outcome_dict.get("runner_advances", [1, 1, 1]))
                adv[lead_idx] = 0
                outcome_dict["runner_advances"] = adv
                # Triple play: 1B+2B both occupied + 0 outs. Force-chain:
                # batter forces 1B runner at 2B; 1B runner forces 2B runner
                # at 3B. Lead runner from 3B (if loaded) holds. Bonus from
                # poor baserunning on the lead forced runner (errors
                # induce TPs in real baseball).
                if (state.bases[0] is not None
                        and state.bases[1] is not None
                        and state.outs == 0):
                    tp_p = cfg.TRIPLE_PLAY_GIVEN_DP_PROB
                    lead_pid = state.bases[1]   # 2B runner — most exposed
                    lead_br, _ = _get_baserunning(lead_pid, state)
                    if lead_br < 0.5:
                        tp_p += (0.5 - lead_br) * cfg.TRIPLE_PLAY_BASERUNNING_BONUS
                    if rng.random() < tp_p:
                        outcome_dict["hit_type"] = "triple_play"
                        outcome_dict["extra_runner_outs"] = [1]
                        # TP ends the play before any runner can score.
                        outcome_dict["runner_advances"] = [0, 0, 0]

        # Stay (2C) lead-runner tag-out. On a stay there's no force at 1B
        # (the batter is still at the plate) so a true DP isn't physical,
        # but a fielder can still tag out a lead runner who broke. Same
        # composition as GIDP scaled down by GIDP_STAY_MULTIPLIER.
        if (choice == "stay"
                and outcome_dict.get("hit_type") == "ground_out"
                and not outcome_dict.get("is_error")
                and any(state.bases)
                and state.outs < state.out_cap()
                and outcome_dict.get("runner_out_idx") is None):
            tag_p = _gidp_probability(state, batter, quality) * cfg.GIDP_STAY_MULTIPLIER
            if rng.random() < tag_p:
                lead_idx = (0 if state.bases[0] is not None
                            else 1 if state.bases[1] is not None else 2)
                outcome_dict["runner_out_idx"] = lead_idx
                outcome_dict["hit_type"] = "fielders_choice"

        # A home run records no defensive outs — everyone scores. Earlier
        # conversion paths (over-the-fence flex, inside-the-park HR) can leave
        # stale runner-out indices behind from the pre-HR hit type; the engine
        # ignores them (advance_runners scores everyone on an HR) but the
        # renderer would charge a phantom TOA out, over-counting batter outs.
        # Strip them so both ledgers see zero outs on the HR.
        if outcome_dict.get("hit_type") in ("hr", "home_run"):
            outcome_dict["runner_out_idx"] = None
            outcome_dict["extra_runner_outs"] = []
            outcome_dict["toa_runner_idxs"] = []

        return {
            "type": "ball_in_play",
            "choice": choice,
            "outcome": outcome_dict,
            "pitch_type": sel_pitch,
            "exit_velocity": ev_v,
            "launch_angle":  la_v,
            "spray_angle":   spray_v,
        }
