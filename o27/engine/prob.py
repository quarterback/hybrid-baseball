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
    b_cond = getattr(batter, "today_condition", 1.0)
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
        cfg.FATIGUE_THRESHOLD_BASE + round(pitcher.stamina * cfg.FATIGUE_THRESHOLD_SCALE),
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
) -> str:
    """Draw one pitch outcome. Returns a string matching one of _PITCH_NAMES."""
    probs = _pitch_probs(
        pitcher, batter, balls, strikes, spell_count, weather,
        rng=rng, pitch_type=pitch_type, pitch_quality=pitch_quality,
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

def contact_quality(
    rng: random.Random,
    batter: Player,
    pitcher: Player,
    weather: Optional[object] = None,
    swings_in_ab: int = 0,
    pitch_type: Optional[str] = None,
    pitch_quality: float = 0.5,
) -> str:
    """
    Determine whether contact is weak, medium, or hard.

    Base distribution from config.CONTACT_*_BASE.
    Adjusted by batter.skill vs pitcher.pitcher_skill matchup.
    Phase 8: further shifted by batter.hard_contact_delta (joker archetype modifier).
      Positive delta → more hard contact / fewer weak contacts.
      Sourced from o27v2.config.ARCHETYPE_PA_MODIFIERS via Player.hard_contact_delta.

    Realism layer:
      - Today's form multiplies effective Stuff for the matchup term.
      - Power tilts toward hard contact; movement (pitcher) tilts toward weak.
      - Platoon penalty applied to batter-side terms.
    """
    plat = _platoon_factor(batter, pitcher)
    form = getattr(pitcher, "today_form", 1.0)
    # Phase 3: per-game wellness multipliers (see _pitch_probs above).
    p_cond = getattr(pitcher, "today_condition", 1.0)
    b_cond = getattr(batter,  "today_condition", 1.0)
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

    # Second-swing modifier: on swings 2+ within the same AB, tilt the
    # contact distribution by eye-vs-command. High-eye batter reads the
    # pitcher; high-command pitcher disrupts the read. Competing forces.
    if swings_in_ab >= 1:
        eye_dev = (batter.eye - 0.5) * 2 * plat * b_cond
        cmd_dev = (pitcher.command - 0.5) * 2 * p_cond
        shift += (eye_dev * cfg.SECOND_SWING_EYE_SCALE
                  - cmd_dev * cfg.SECOND_SWING_COMMAND_SCALE)

    arch_delta = getattr(batter, "hard_contact_delta", 0.0)

    # Power → harder contact (collapses to 0 at power=0.5).
    power_tilt = (batter.power - 0.5) * 2 * plat * b_cond * cfg.CONTACT_POWER_TILT
    # Movement → weaker contact (collapses to 0 at movement=0.5).
    move_tilt  = (move_draw - 0.5) * 2 * cfg.CONTACT_MOVEMENT_TILT

    # Floor at 0.001 (epsilon for probability sanity only). Was 0.05, then
    # 0.01; lowered again to 0.001 to remove the last soft talent gate. An
    # elite-Stuff / elite-movement pitcher should be able to push hard-contact
    # rate vanishingly low against a replacement bat, and vice-versa.
    weak_p   = max(0.001, cfg.CONTACT_WEAK_BASE   - shift - arch_delta - power_tilt + move_tilt)
    hard_p   = max(0.001, cfg.CONTACT_HARD_BASE   + shift + arch_delta + power_tilt - move_tilt)

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


def runner_advances_for_hit(
    rng: random.Random,
    hit_type: str,
    bases: list,
    state: GameState,
) -> tuple[list, Optional[int]]:
    """Return ([adv_1B, adv_2B, adv_3B], runner_out_idx).

    runner_out_idx is the base index (0=1B, 1=2B, 2=3B) of a runner who
    was thrown out trying for the extra base, or None if all advancements
    were clean.
    """
    s1 = _get_speed(bases[0], state)
    s2 = _get_speed(bases[1], state)
    s3 = _get_speed(bases[2], state)
    br1, ag1 = _get_baserunning(bases[0], state)
    br2, ag2 = _get_baserunning(bases[1], state)
    br3, ag3 = _get_baserunning(bases[2], state)

    out_idx: Optional[int] = None

    def _resolve(idx: int, base: int, speed: float, extra: float, br: float, ag: float) -> int:
        nonlocal out_idx
        adv, thrown_out = _runner_advance(rng, base, speed, extra_chance=extra,
                                          baserunning=br, aggressiveness=ag)
        if thrown_out and out_idx is None and bases[idx] is not None:
            out_idx = idx
        return adv

    if hit_type == "single":
        adv1 = _resolve(0, 1, s1, 0.10, br1, ag1)
        adv2 = _resolve(1, 2, s2, 0.0,  br2, ag2)
        adv3 = 1   # 3B always scores on a single (90 ft, routine)
        # Runner from 2B trying to score on a single is the classic
        # close play at the plate. If the throw beats them, mark them
        # out (priority: prefer this over a 1B-runner extra-base out
        # since the play at the plate is the lead runner).
        if (bases[1] is not None and adv2 >= 2
                and _thrown_out_at_home(rng, s2, br2)):
            out_idx = 1
            adv2 = 1   # held at 3B in the log; pid is cleared by advance_runners
        return [adv1, adv2, adv3], out_idx

    elif hit_type == "double":
        # Runner on 1B: typically pulls up at 3B, but speed/baserunning/
        # aggressiveness can drive them home. Without this draw, every
        # double yielded an identical [2, 2, 1] line and runs scored
        # tracked hit count too tightly.
        adv1 = _resolve(0, 2, s1, cfg.RUNNER_EXTRA_DOUBLE_FROM_1B, br1, ag1)
        return [adv1, 2, 1], out_idx

    elif hit_type == "triple":
        # 1B runner is the close play at the plate on a triple. Almost
        # always scores, but a slow / unaware runner can be cut down by
        # a strong relay throw.
        adv1 = 3
        if bases[0] is not None and _thrown_out_at_home(rng, s1, br1):
            out_idx = 0
            adv1 = 2   # presented as held; advance_runners clears the slot
        return [adv1, 3, 3], out_idx

    elif hit_type == "hr":
        return [3, 3, 3], None

    elif hit_type in ("ground_out", "fielders_choice"):
        adv1 = 1   # 1B runner always forced to 2B on ground ball
        adv2 = _resolve(1, 0, s2, 0.25, br2, ag2)
        adv3 = _resolve(2, 0, s3, 0.35, br3, ag3)
        return [adv1, adv2, adv3], out_idx

    elif hit_type == "fly_out":
        adv1 = 0
        adv2 = 0
        # Sac fly: skill matters as much as speed (timing the tag-up).
        adv3 = _resolve(2, 0, s3, 0.55, br3, ag3)
        return [adv1, adv2, adv3], out_idx

    elif hit_type == "line_out":
        return [0, 0, 0], None   # runners freeze

    else:
        return [1, 1, 1], None   # default


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
      - The legacy archetype `hr_weight_bonus` field is folded into the
        same line_out → HR redistribution (one consistent mechanism for
        both archetype and rating).

    Park factors are applied separately as multipliers (parks really do
    create / destroy events, so they're multiplicative by design).
    """
    table = _CONTACT_TABLES.get(quality, cfg.WEAK_CONTACT)

    # Combined power axis: rating-driven (-1..+1) plus archetype legacy
    # bonus (typically tiny). Both flow through the SAME line_out → HR edge
    # via _redistribute, so the joker archetype's HR boost is now sum-
    # preserving instead of additive.
    power_dev    = (batter.power - 0.5) * 2.0
    legacy_bonus = getattr(batter, "hr_weight_bonus", 0.0)
    # Archetype bonus translated to a power_dev contribution on the HR
    # edge only. Scale: each unit of legacy_bonus ≈ 1.0 of POWER_REDIST_HR.
    archetype_dev_hr = legacy_bonus / max(0.01, cfg.POWER_REDIST_HR)

    # Apply power-axis redistribution per quality.
    if quality == "hard":
        # HR edge gets both rating and archetype contributions.
        edges = list(_hard_edges())
        # Boost the HR edge by the archetype contribution.
        edges[0] = ("line_out", "hr",
                    cfg.POWER_REDIST_HR + archetype_dev_hr * cfg.POWER_REDIST_HR)
        table = _redistribute(table, edges, power_dev)
    elif quality == "medium":
        table = _redistribute(table, _medium_edges(), power_dev)
    elif quality == "weak":
        table = _redistribute(table, _weak_edges(), power_dev)

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
    is_error = False

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
    runner_adv, br_out_idx = runner_advances_for_hit(rng, advance_type, state.bases, state)

    # For fielder's choice: throw out the lead runner. TOOTBLAN
    # (thrown-out-on-bases from the runner_advances roll) shows up via
    # br_out_idx and takes precedence on plays that wouldn't otherwise
    # produce a runner out.
    runner_out_idx = None
    if hit_type == "fielders_choice" and state.runners_on_base:
        runner_out_idx = _lead_runner_idx(state.bases)
    elif br_out_idx is not None:
        runner_out_idx = br_out_idx

    # Per-fielder play attribution. Stamps the fielder_id of the player
    # credited with this play (PO for outs, E for errors). Returns None
    # for hits — those don't get a fielder credit.
    fielder_id = _select_fielder(rng, hit_type, fielding)

    return {
        "hit_type": hit_type,
        "batter_safe": batter_safe,
        "caught_fly": caught_fly,
        "runner_advances": runner_adv,
        "runner_out_idx": runner_out_idx,
        "is_error": is_error,
        "fielder_id": fielder_id,
        "quality": quality,
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

    # Medium contact gate: only eligible to stay if RNG < contact_quality_threshold.
    if quality == "medium":
        if rng.random() > batter.contact_quality_threshold:
            return False

    # Final probabilistic gate: stay_aggressiveness.
    return rng.random() < batter.stay_aggressiveness


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
    if state.bases[0] is not None and state.count.strikes < 2:
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
        form = self.rng.gauss(cfg.TODAY_FORM_MU, cfg.TODAY_FORM_SIGMA)
        form = max(cfg.TODAY_FORM_MIN, min(cfg.TODAY_FORM_MAX, form))

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

        pitcher.today_form = max(cfg.TODAY_FORM_MIN, form)

    def __call__(self, state: GameState) -> Optional[dict]:
        # Detect new batter (new PA or batter changed by joker insertion).
        current_batter_id = state.current_batter.player_id
        if current_batter_id != self._last_batter_id:
            self._last_batter_id = current_batter_id
            self._manager_checked = False

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

        weather = getattr(state, "weather", None)

        # Select one pitch from the repertoire (if the pitcher has one).
        # The same pitch drives both the pitch-outcome model and, if contact
        # results, the contact-quality model. Legacy pitchers (no repertoire)
        # return (None, 0.5) and the aggregate Stuff/Command/Movement path fires.
        sel_pitch, sel_quality = _select_pitch(rng, pitcher, balls, strikes)

        outcome = pitch_outcome(
            rng, pitcher, batter, balls, strikes, spell, weather,
            pitch_type=sel_pitch, pitch_quality=sel_quality,
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
            return {"type": outcome}

        # --- Contact resolution ---
        quality = contact_quality(
            rng, batter, pitcher, weather,
            swings_in_ab=state.current_at_bat_swings,
            pitch_type=sel_pitch, pitch_quality=sel_quality,
        )
        is_hr     = False
        is_triple = False

        # Resolve fielding outcome.
        outcome_dict = resolve_contact(rng, quality, batter, state)

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
            eye_dev_run = (batter.eye - 0.5) * 2
            con_dev_run = (batter.contact - 0.5) * 2
            cmd_dev_run = (pitcher.command - 0.5) * 2
            talent_run  = eye_dev_run + con_dev_run - cmd_dev_run
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

        hit_type = outcome_dict["hit_type"]
        caught_fly = outcome_dict["caught_fly"]

        is_hr     = (hit_type == "hr")
        is_triple = (hit_type == "triple")

        # Stay-vs-run decision.
        if stay_mod.stay_available(state):
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
            #   weak quality   expected ≈ 0.5*(1 + talent_factor)
            #     → low-talent  (factor ≈ -1):  expected ~0   → mostly no advance
            #       neutral     (factor ≈  0):  expected ~0.5 → ~50% credit
            #       high-talent (factor ≈ +2):  expected ~1.5 → always credit, sometimes 2
            #   medium quality expected ≈ 1.0 + 0.5*talent_factor
            #     → low-talent:  expected ~0.5 → mostly 1 (credit), sometimes 0
            #       neutral:     expected ~1.0 → always 1
            #       high-talent: expected ~2.0 → 2, sometimes 3
            # Even low-talent batters can occasionally drive runners on a 2C;
            # stars are reliably better. Talent flows continuously through
            # the expected-value formula; the rng draw is purely fractional
            # resolution, not a gate on whether talent matters.
            if quality == "weak":
                expected = 0.5 * (1.0 + talent_factor)
            else:  # medium
                expected = 1.0 + 0.5 * talent_factor
            expected = max(0.0, min(3.0, expected))
            floor_v = int(expected)
            frac = expected - floor_v
            adv = floor_v + (1 if rng.random() < frac else 0)
            adv = max(0, min(3, adv))
            outcome_dict["runner_advances"] = [adv, adv, adv]

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
        if (choice == "run"
                and outcome_dict.get("hit_type") == "ground_out"
                and not outcome_dict.get("is_error")
                and any(state.bases)
                and state.outs < 2):
            dp_p = _gidp_probability(state, batter, quality)
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
                and state.outs < 2
                and outcome_dict.get("runner_out_idx") is None):
            tag_p = _gidp_probability(state, batter, quality) * cfg.GIDP_STAY_MULTIPLIER
            if rng.random() < tag_p:
                lead_idx = (0 if state.bases[0] is not None
                            else 1 if state.bases[1] is not None else 2)
                outcome_dict["runner_out_idx"] = lead_idx
                outcome_dict["hit_type"] = "fielders_choice"

        return {
            "type": "ball_in_play",
            "choice": choice,
            "outcome": outcome_dict,
        }
