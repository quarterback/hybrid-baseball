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
import random
from typing import Optional

from .state import GameState, Player
from . import stay as stay_mod
from . import manager as mgr
from o27 import config as cfg


# ---------------------------------------------------------------------------
# Pitch outcome model
# ---------------------------------------------------------------------------

_PITCH_NAMES = ("ball", "called_strike", "swinging_strike", "foul", "contact")


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
) -> tuple:
    """Return adjusted pitch-outcome probability tuple (sums to 1.0)."""
    base = list(cfg.PITCH_BASE.get((balls, strikes), cfg.PITCH_BASE[(0, 0)]))

    # Daily pitcher form — stored on Player for the duration of the spell.
    # Multiplies effective Stuff so the same SP can throw a gem one start
    # and a clunker the next. today_form == 1.0 ⇒ identity.
    form = getattr(pitcher, "today_form", 1.0)
    stuff_eff = max(0.0, min(1.0, pitcher.pitcher_skill * form))

    # Pitcher dominance: stuff_eff > 0.5 shifts probability toward strikes.
    p_dom = (stuff_eff - 0.5) * 2   # −1.0 to +1.0
    base[0] += p_dom * cfg.PITCHER_DOM_BALL
    base[1] += p_dom * cfg.PITCHER_DOM_CALLED
    base[2] += p_dom * cfg.PITCHER_DOM_SWINGING
    base[4] += p_dom * cfg.PITCHER_DOM_CONTACT

    # Batter dominance: skill > 0.5 shifts probability toward contact.
    plat = _platoon_factor(batter, pitcher)
    b_dom = (batter.skill - 0.5) * 2 * plat        # −1.0 to +1.0
    base[2] += b_dom * cfg.BATTER_DOM_SWINGING
    base[4] += b_dom * cfg.BATTER_DOM_CONTACT

    # --- Realism layer ----------------------------------------------------
    # Each contribution collapses to 0 when the rating == 0.5, preserving
    # the identity invariant against the legacy probability surface.

    # Eye: discipline → more balls taken, fewer called strikes.
    eye_dev = (batter.eye - 0.5) * 2 * plat
    base[0] += eye_dev * cfg.BATTER_EYE_BALL
    base[1] += eye_dev * cfg.BATTER_EYE_CALLED

    # Contact (batter): bat-on-ball ability → fewer whiffs, more fouls/in-play.
    con_dev = (batter.contact - 0.5) * 2 * plat
    base[2] += con_dev * cfg.BATTER_CONTACT_SWINGING
    base[3] += con_dev * cfg.BATTER_CONTACT_FOUL
    base[4] += con_dev * cfg.BATTER_CONTACT_CONTACT

    # Command (pitcher): independent of Stuff → control pitchers walk fewer.
    cmd_dev = (pitcher.command - 0.5) * 2
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
        fatigue = min(cfg.FATIGUE_MAX, (spell_count - fatigue_threshold) / cfg.FATIGUE_SCALE)
        base[0] += fatigue * cfg.FATIGUE_BALL
        base[4] += fatigue * cfg.FATIGUE_CONTACT
        base[1] += fatigue * cfg.FATIGUE_CALLED
        base[2] += fatigue * cfg.FATIGUE_SWINGING
        base[3] += fatigue * cfg.FATIGUE_FOUL

    # Normalise.
    base = [max(0.01, p) for p in base]
    total = sum(base)
    return tuple(p / total for p in base)


def pitch_outcome(
    rng: random.Random,
    pitcher: Player,
    batter: Player,
    balls: int,
    strikes: int,
    spell_count: int,
) -> str:
    """Draw one pitch outcome. Returns a string matching one of _PITCH_NAMES."""
    probs = _pitch_probs(pitcher, batter, balls, strikes, spell_count)
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

def contact_quality(rng: random.Random, batter: Player, pitcher: Player) -> str:
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
    stuff_eff = max(0.0, min(1.0, pitcher.pitcher_skill * form))

    matchup = (batter.skill * plat) - stuff_eff   # +ve → batter advantage
    shift = matchup * cfg.CONTACT_MATCHUP_SHIFT    # up to ±0.125 swing

    arch_delta = getattr(batter, "hard_contact_delta", 0.0)

    # Power → harder contact (collapses to 0 at power=0.5).
    power_tilt = (batter.power - 0.5) * 2 * plat * cfg.CONTACT_POWER_TILT
    # Movement → weaker contact (collapses to 0 at movement=0.5).
    move_tilt  = (pitcher.movement - 0.5) * 2 * cfg.CONTACT_MOVEMENT_TILT

    # Floors at 0.01 (epsilon for probability sanity), NOT 0.05. The old
    # 0.05 floor was a soft lever pushing the engine toward the middle —
    # it artificially capped how much an elite pitcher could suppress hard
    # contact, or how much an elite hitter could suppress weak contact.
    # Removing it lets the .01% transcendent talents really transcend.
    weak_p   = max(0.01, cfg.CONTACT_WEAK_BASE   - shift - arch_delta - power_tilt + move_tilt)
    hard_p   = max(0.01, cfg.CONTACT_HARD_BASE   + shift + arch_delta + power_tilt - move_tilt)
    medium_p = max(0.01, 1.0 - weak_p - hard_p)

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
) -> int:
    """Compute bases advanced by one runner; may take an extra base if fast."""
    advance = base_advance
    if rng.random() < extra_chance + max(0.0, (speed - 0.5) * cfg.RUNNER_EXTRA_SPEED_SCALE):
        advance += 1
    return advance


def _get_speed(pid: Optional[str], state: GameState) -> float:
    if pid is None:
        return 0.5
    p = state.batting_team.get_player(pid) or state.fielding_team.get_player(pid)
    return p.speed if p else 0.5


def runner_advances_for_hit(
    rng: random.Random,
    hit_type: str,
    bases: list,
    state: GameState,
) -> list:
    """Return [adv_1B, adv_2B, adv_3B] for each occupied base (0 = no runner)."""
    s1 = _get_speed(bases[0], state)
    s2 = _get_speed(bases[1], state)
    s3 = _get_speed(bases[2], state)   # noqa: F841  (3B runner always scores on single+)

    if hit_type == "single":
        adv1 = _runner_advance(rng, 1, s1, extra_chance=0.10)
        adv2 = _runner_advance(rng, 2, s2, extra_chance=0.0)   # usually scores
        adv3 = 1   # 3B always scores on a single
        return [adv1, adv2, adv3]

    elif hit_type == "double":
        return [2, 2, 1]   # runners advance 2; 3B scores

    elif hit_type in ("triple", "hr"):
        return [3, 3, 3]   # everyone scores

    elif hit_type in ("ground_out", "fielders_choice"):
        adv1 = 1   # 1B runner always forced to 2B on ground ball
        adv2 = _runner_advance(rng, 0, s2, extra_chance=0.25)
        adv3 = _runner_advance(rng, 0, s3, extra_chance=0.35)
        return [adv1, adv2, adv3]

    elif hit_type == "fly_out":
        adv1 = 0
        adv2 = 0
        adv3 = _runner_advance(rng, 0, s3, extra_chance=0.55)  # sac fly
        return [adv1, adv2, adv3]

    elif hit_type == "line_out":
        return [0, 0, 0]   # runners freeze

    else:
        return [1, 1, 1]   # default


# ---------------------------------------------------------------------------
# Contact outcome (fielding resolution) model
# ---------------------------------------------------------------------------

_CONTACT_TABLES = {
    "weak":   cfg.WEAK_CONTACT,
    "medium": cfg.MEDIUM_CONTACT,
    "hard":   cfg.HARD_CONTACT,
}


def _scale_hard_row(
    row: tuple,
    hr_bonus: float,
    park_hr: float,
    park_hits: float,
) -> tuple:
    """Apply HR weight bonus + park factors to one HARD_CONTACT row.

    `hr_bonus` is additive (legacy archetype + new power-derived bump).
    `park_hr` multiplies the HR row only.
    `park_hits` multiplies single / double rows. Other rows pass through.

    Identity: hr_bonus=0, park_hr=1.0, park_hits=1.0 ⇒ row unchanged.
    """
    name, batter_safe, caught_fly, weight = row
    if name == "hr":
        weight = (weight + hr_bonus) * park_hr
    elif name in ("single", "double"):
        weight *= park_hits
    return (name, batter_safe, caught_fly, max(0.01, weight))


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
    Phase 8: for hard-contact events, batter.hr_weight_bonus adjusts the HR
    row weight in HARD_CONTACT (positive → more HR, negative → fewer HR /
    more line drives / doubles).  Sourced from ARCHETYPE_PA_MODIFIERS.

    Realism layer:
      - batter.power adds an extra HR-weight bump on top of hr_weight_bonus
        (collapses to 0 at power=0.5).
      - The home team's park_hr multiplies the HR row weight (1.0 = identity);
        park_hits multiplies single/double weights for hit-vs-out feel.
    """
    table = _CONTACT_TABLES.get(quality, cfg.WEAK_CONTACT)

    # Power-driven HR weight bonus, additive with the legacy archetype field.
    legacy_bonus = getattr(batter, "hr_weight_bonus", 0.0)
    power_bonus  = (batter.power - 0.5) * 2 * cfg.POWER_HR_WEIGHT_SCALE
    hr_bonus = legacy_bonus + power_bonus

    # Park factors from the home team (applied symmetrically — both lineups
    # play in the home park). state.home is the host regardless of half.
    park_hr   = getattr(state.home, "park_hr", 1.0) if state.home else 1.0
    park_hits = getattr(state.home, "park_hits", 1.0) if state.home else 1.0

    if quality == "hard" and (hr_bonus != 0.0 or park_hr != 1.0 or park_hits != 1.0):
        table = [
            _scale_hard_row(r, hr_bonus, park_hr, park_hits)
            for r in table
        ]
    elif quality == "medium" and park_hits != 1.0:
        table = [
            (r[0], r[1], r[2],
             max(0.01, r[3] * (park_hits if r[0] in ("single", "double") else 1.0)))
            for r in table
        ]

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
        err_p = max(cfg.DEFENSE_ERROR_MIN, min(cfg.DEFENSE_ERROR_MAX, err_p))
        if rng.random() < err_p:
            is_error = True
            hit_type = "error"      # synthetic outcome — pa.py + render handle
            batter_safe = True
            caught_fly = False

    # Compute runner advances based on (possibly flipped) hit type.
    # An "error" advances runners like a single — same conservative shape.
    advance_type = "single" if hit_type == "error" else hit_type
    runner_adv = runner_advances_for_hit(rng, advance_type, state.bases, state)

    # For fielder's choice: throw out the lead runner.
    runner_out_idx = None
    if hit_type == "fielders_choice" and state.runners_on_base:
        runner_out_idx = _lead_runner_idx(state.bases)

    return {
        "hit_type": hit_type,
        "batter_safe": batter_safe,
        "caught_fly": caught_fly,
        "runner_advances": runner_adv,
        "runner_out_idx": runner_out_idx,
        "is_error": is_error,
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
    # Hard rule: home run → always run.
    if is_hr:
        return False
    # Hard rule: triple → run (too valuable to forfeit).
    if is_triple:
        return False
    # Hard rule: hard contact → run.
    if quality == "hard":
        return False
    # Hard rule: 2 outs → run.
    if state.outs == 2:
        return False
    # Hard rule: 2-strike count → batter out if stays; heuristic avoids.
    if state.count.strikes == 2:
        return False
    # Hard rule: caught fly → batter out if stays; heuristic avoids.
    if caught_fly:
        return False

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
    Optionally return a between-pitch event (stolen base attempt or wild pitch).

    Called before each pitch draw; returns None if no event fires.
    """
    # Wild pitch: small chance per pitch with runners on base.
    if state.runners_on_base and rng.random() < cfg.WILD_PITCH_PROB:
        return {"type": "wild_pitch"}

    # Stolen base attempt: check 1B and 2B runners.
    for base_idx in (0, 1):
        pid = state.bases[base_idx]
        if pid is None:
            continue
        speed = _get_speed(pid, state)
        if speed < cfg.SB_ATTEMPT_SPEED_THRESHOLD:
            continue
        if rng.random() < cfg.SB_ATTEMPT_PROB_PER_PITCH:
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

        # Joker insertion was removed in Task #47.

        # Pinch hit check (fallback when jokers are exhausted).
        replacement = mgr.should_pinch_hit(state)
        if replacement is not None:
            return {"type": "pinch_hit", "replacement": replacement}

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

        outcome = pitch_outcome(rng, pitcher, batter, balls, strikes, spell)

        if outcome != "contact":
            return {"type": outcome}

        # --- Contact resolution ---
        quality = contact_quality(rng, batter, pitcher)
        is_hr     = False
        is_triple = False

        # Resolve fielding outcome.
        outcome_dict = resolve_contact(rng, quality, batter, state)
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

        return {
            "type": "ball_in_play",
            "choice": choice,
            "outcome": outcome_dict,
        }
