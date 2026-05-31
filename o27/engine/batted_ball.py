"""
Batted-ball physics hybrid layer.

The O27 contact model is categorical: `contact_quality()` produces
weak / medium / hard, then `resolve_contact()` maps that plus state into
a `hit_type` like single, double, triple, hr, ground_out, fly_out, etc.
That stays the canonical engine path.

This module layers a SYNTHETIC (exit_velocity, launch_angle, spray_angle)
sample on top of each ball-in-play event. The samples are persisted on
game_pa_log so the web layer can render spray charts, EV/LA bands, and
xwOBA-style luck attribution. They do NOT drive the fielding outcome —
the engine is calibrated against the categorical model, not against a
physics surface.

The sampling distributions are shaped by:
  * contact_quality  → primary EV centre (weak ≈ 75, medium ≈ 88, hard ≈ 102)
  * hit_type         → launch-angle centre (ground_out negative, line drive
                       low positive, fly_out high positive)
  * batter.power     → EV shift (±10 mph across the 0-1 power range)
  * pitch metadata   → hard_contact_shift bleeds into EV; HR-suppressing
                       pitches (negative shift) drag down the EV centre

Distributions are clamped to MLB-plausible ranges. Identity invariant at
neutral inputs is NOT enforced — this layer is by definition non-trivial
on every BIP. Pass an `rng` for deterministic seeding.
"""
from __future__ import annotations

import random
from typing import Optional


# --- Exit velocity (mph) -------------------------------------------------

# (mu, sigma, lo, hi) per contact quality.
_EV_BY_QUALITY: dict[str, tuple[float, float, float, float]] = {
    "weak":   (74.0, 7.5, 52.0, 88.0),
    "medium": (88.0, 6.0, 76.0, 100.0),
    "hard":   (102.0, 5.5, 92.0, 119.0),
}

# Hit-type-specific EV nudges. HR-coded outcomes pull EV further up;
# infield_single / ground_out anchor it down.
_EV_HIT_TYPE_SHIFT: dict[str, float] = {
    "hr":              +4.0,
    "home_run":        +4.0,
    "triple":          +2.5,
    "double":          +1.5,
    "line_out":        +1.0,
    "fly_out":         -0.5,
    "single":          +0.0,
    "infield_single":  -3.5,
    "ground_out":      -2.5,
    "fielders_choice": -2.0,
    "error":           +0.0,
    "double_play":     -3.0,
    "triple_play":     -3.5,
}

# --- Launch angle (degrees) ----------------------------------------------

# (mu, sigma) per hit_type. Negative = grounder; ~10-25 = line drive;
# >35 = fly ball. Tuned against MLB Statcast medians.
_LA_BY_HIT_TYPE: dict[str, tuple[float, float]] = {
    "ground_out":      (-5.0, 9.0),
    "fielders_choice": (-3.0, 8.0),
    "double_play":     (-6.0, 7.0),
    "triple_play":     (-6.0, 7.0),
    "infield_single":  (+6.0, 9.0),
    "single":          (+13.0, 10.0),
    "double":          (+22.0, 8.0),
    "triple":          (+19.0, 9.0),
    "hr":              (+28.0, 5.5),
    "home_run":        (+28.0, 5.5),
    "fly_out":         (+38.0, 7.0),
    "line_out":        (+16.0, 5.0),
    "error":           (+5.0, 12.0),
}

# --- Spray angle (degrees) ----------------------------------------------
# -45 = left-field foul line, 0 = dead center, +45 = right-field foul
# line. Handedness creates a pull tendency: RHB pulls to LF (negative
# spray skew), LHB pulls to RF (positive). Switch hitters are neutral.

_PULL_SKEW: dict[str, float] = {
    "L": +12.0,   # LHB pulls to RF (positive spray)
    "R": -12.0,   # RHB pulls to LF (negative spray)
    "S":   0.0,
    "":    0.0,
}


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def sample_batted_ball(
    rng: random.Random,
    quality: str,
    hit_type: str,
    batter_power: float,
    pitch_hard_contact_shift: float,
    batter_bats: str = "",
    pitch_launch_bias: float = 0.0,
) -> tuple[float, float, float]:
    """Sample (exit_velocity_mph, launch_angle_deg, spray_angle_deg) for a
    single ball-in-play event. The triple is persisted on game_pa_log
    and consumed by visualization layers.

    Identity caveat: this layer is intentionally non-trivial — there is
    no "neutral inputs reproduce engine" check, because the engine never
    looked at these values before this layer existed.
    """
    if rng is None:
        rng = random.Random()

    # Exit velocity ------------------------------------------------------
    mu, sigma, lo, hi = _EV_BY_QUALITY.get(quality, _EV_BY_QUALITY["medium"])
    mu += _EV_HIT_TYPE_SHIFT.get(hit_type, 0.0)
    # Batter Power: ±10 mph swing across the 0-1 power range.
    mu += (float(batter_power) - 0.5) * 10.0
    # Pitch's hard_contact_shift: HR-suppressing pitches (-0.05 typical)
    # drag the EV mean down by ~1.5 mph; HR-prone (+0.05) pushes it up.
    mu += float(pitch_hard_contact_shift) * 30.0
    ev = _clamp(rng.gauss(mu, sigma), lo, hi)

    # Launch angle -------------------------------------------------------
    la_mu, la_sigma = _LA_BY_HIT_TYPE.get(hit_type, (+10.0, 12.0))
    # High-Power batters tilt a few degrees more elevation.
    la_mu += (float(batter_power) - 0.5) * 4.0
    # Pitch launch bias: grounder pitches (negative) shave degrees off, popup
    # pitches (positive) add them — keeps the synthetic LA consistent with the
    # ground_out↔fly_out tilt applied to the categorical outcome.
    la_mu += float(pitch_launch_bias) * 8.0
    la = _clamp(rng.gauss(la_mu, la_sigma), -45.0, 60.0)

    # Spray angle --------------------------------------------------------
    pull_mu = _PULL_SKEW.get(batter_bats, 0.0)
    spray = _clamp(rng.gauss(pull_mu, 16.0), -44.0, 44.0)

    return round(ev, 1), round(la, 1), round(spray, 1)


# ---------------------------------------------------------------------------
# Descriptive batted-ball taxonomy
# ---------------------------------------------------------------------------
# Turn the (EV, LA, spray) the engine already samples into a rich, human name
# for the box score / play-by-play — "swinging bunt", "frozen rope", "Texas
# leaguer", "no-doubter". PURELY DESCRIPTIVE: it is derived from physics that
# are themselves derived from the categorical outcome, so it NEVER feeds back
# into mechanics. The name is reconciled with the final hit_type so it can't
# contradict the result (a ball that carried out is named in the HR family; a
# caught fly is a "flyout", not a "double").

_GB_LA = 10.0    # launch angle below this  → on the ground
_LD_LA = 26.0    # [_GB_LA, _LD_LA)         → line drive
_FB_LA = 50.0    # [_LD_LA, _FB_LA)         → fly ball;  >= _FB_LA → popup

_HIT_TYPES = frozenset(("single", "infield_single", "double", "triple"))
_OUT_TYPES = frozenset(("ground_out", "fly_out", "line_out",
                        "fielders_choice", "double_play", "triple_play"))


def _ev_tier(ev: float) -> int:
    """0 weak · 1 medium · 2 hard · 3 crushed."""
    if ev < 80.0:
        return 0
    if ev < 95.0:
        return 1
    if ev < 103.0:
        return 2
    return 3


def _zone(spray) -> str:
    a = abs(float(spray or 0.0))
    if a >= 32.0:
        return "down the line"
    if a <= 12.0:
        return "up the middle"
    return "in the gap"


def classify_batted_ball(ev, la, spray, hit_type: str = "") -> str:
    """Return a rich descriptive name for a batted ball from its EV/LA/spray
    and the final categorical hit_type. Descriptive only — for display.

    Examples: "swinging bunt", "seeing-eye grounder up the middle",
    "scorched one-hopper down the line", "frozen rope", "bloop into the gap",
    "can of corn", "warning-track flyout", "no-doubter down the line".
    """
    ht = (hit_type or "").lower()
    ev = float(ev if ev is not None else 88.0)
    la = float(la if la is not None else 12.0)
    tier = _ev_tier(ev)
    zone = _zone(spray)
    is_hit = ht in _HIT_TYPES

    # Home runs name themselves by how far they were hit (EV proxy).
    if ht in ("hr", "home_run"):
        if ev >= 106.0:
            base = "no-doubter"
        elif ev >= 100.0:
            base = "deep drive"
        elif ev >= 95.0:
            base = "home run"
        else:
            base = "wall-scraper"
        return f"{base} {zone}"

    # Popups (very high launch angle).
    if la >= _FB_LA:
        if is_hit:
            return f"bloop {zone}"          # a popup that found grass
        return "infield popup" if tier <= 1 else "towering popup"

    # Fly balls.
    if la >= _LD_LA:
        if is_hit:
            if ht == "triple":
                return f"deep drive {zone}"
            if ht == "double":
                return f"{'scorched' if tier >= 2 else 'flared'} fly {zone}"
            return f"{'Texas leaguer' if tier <= 1 else 'looping fly'} {zone}"
        # Fly OUT.
        if tier >= 3:
            return f"warning-track flyout {zone}"
        if tier <= 0:
            return "can of corn"
        return f"routine flyout {zone}"

    # Line drives.
    if la >= _GB_LA:
        if is_hit:
            if ht in ("double", "triple"):
                return f"line-drive gapper {zone}" if zone == "in the gap" \
                    else f"{'scorched' if tier >= 2 else 'sharp'} liner {zone}"
            if tier >= 3:
                return f"frozen rope {zone}"
            if tier >= 2:
                return f"line-drive single {zone}"
            return f"soft liner {zone}"
        # Line OUT — caught on a line.
        return f"{'scorched' if tier >= 2 else 'sharp'} lineout {zone}"

    # Ground balls (low launch angle).
    if is_hit:
        if ht == "infield_single":
            if tier <= 0:
                return "swinging bunt" if abs(float(spray or 0)) >= 28 else "dribbler"
            return f"infield single {zone}"
        if tier <= 0:
            return f"seeing-eye grounder {zone}"
        if tier >= 3:
            return f"scorched one-hopper {zone}"
        return f"ground-ball single {zone}"
    # Ground OUT / FC / DP.
    if tier <= 0:
        return f"slow roller {zone}"
    if tier >= 3:
        return f"scorched grounder {zone}"
    return f"ground ball {zone}"
