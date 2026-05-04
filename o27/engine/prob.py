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
    raw_stuff = float(pitcher.pitcher_skill)
    # Position-player pitching (extreme blowout fallback): blend in the
    # player's arm rating so a strong-arm bench bat throws better than a
    # noodle-arm one when forced into an emergency outing. Heavily scaled
    # down — they're still amateurs on the mound.
    if not getattr(pitcher, "is_pitcher", True):
        arm = float(getattr(pitcher, "arm", 0.5) or 0.5)
        raw_stuff = 0.55 * arm + 0.45 * raw_stuff
    stuff_eff = max(0.0, min(1.0, raw_stuff * form))

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
        adv3 = 1   # 3B always scores on a single
        return [adv1, adv2, adv3], out_idx

    elif hit_type == "double":
        return [2, 2, 1], None   # routine — 3B scores

    elif hit_type in ("triple", "hr"):
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

        outcome = pitch_outcome(rng, pitcher, batter, balls, strikes, spell)

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
