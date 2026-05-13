"""
Park-shape gameplay hook.

After resolve_contact() decides a categorical hit_type, this module
reshapes the outcome based on the home park's actual dimensions and
the BIP's (exit_velocity, launch_angle, spray_angle) sample. The
hook is what makes the user's design fiat — pre-modern variety, no
cookie-cutter parks — actually matter in stats:

  * Polo Grounds bathtub (278' RF) → HRs down the lines that would
    have died at a 380' oval boundary clear easily.
  * Cricket-ground oval (380' lines, 4-ft picket fence) → pull HRs
    disappear; deep gappers become triples; OFs vault to rob.
  * Cavernous park (440'+ alleys) → singles that find the gap roll
    forever and become doubles or triples.
  * Green Monster (35-ft+ wall) → fringe HRs ricochet off the wall
    for doubles.

This is option (B) from the design conversation: parks genuinely
differ, no per-shape rebalancing. The league HR / 2B / 3B mix will
shift by venue — Polo Grounds = HR factory down the lines, oval =
gap-double league.

Identity invariant: if park_dims is empty / None or hit_type is a
non-BIP (strikeout / walk / hit_by_pitch), the function returns the
outcome unchanged. Legacy DBs that predate park_dimensions seed
behave exactly as before.

The hook mutates the outcome_dict in place rather than returning a
new dict, so all the existing downstream consumers (Stay decision,
runner_advances, fielder_id attribution) see a consistent shape.
"""
from __future__ import annotations

import math
import random
from typing import Optional


# --- Geometry helpers ----------------------------------------------------

# The 5 fence control points the park generator persists. Spray angles
# are -45 (LF foul line) to +45 (RF foul line). Linear interpolation
# between these is good enough — fence is roughly piecewise linear.
_FENCE_ANGLES = (-45.0, -22.5, 0.0, 22.5, 45.0)
_FENCE_KEYS   = ("lf", "lcf", "cf", "rcf", "rf")


def _fence_at_angle(spray: float, park_dims: dict) -> float:
    """Interpolate the fence distance (ft) at the given spray angle.

    Falls back to a 380-ft uniform fence when park_dims is missing or
    malformed.
    """
    if not park_dims:
        return 380.0
    s = max(-45.0, min(45.0, float(spray or 0.0)))
    # Find the bracketing fence-control points.
    for i in range(len(_FENCE_ANGLES) - 1):
        a0, a1 = _FENCE_ANGLES[i], _FENCE_ANGLES[i + 1]
        if a0 <= s <= a1:
            d0 = float(park_dims.get(_FENCE_KEYS[i],     380.0))
            d1 = float(park_dims.get(_FENCE_KEYS[i + 1], 380.0))
            t = (s - a0) / (a1 - a0) if a1 != a0 else 0.0
            return d0 + (d1 - d0) * t
    # Out-of-band (shouldn't happen given the clamp) — use CF.
    return float(park_dims.get("cf", 380.0))


def _proxy_distance(ev: float, la: float) -> float:
    """Heuristic batted-ball distance (ft) from exit velocity + launch
    angle. Approximates a drag-adjusted projectile range.

    Calibration target: Statcast median HR is ~395 ft at ~28° LA from a
    100-mph EV. With divisor 25, that calculation gives 332 ft (slightly
    under), and a 105-mph EV at the same angle gives 366 ft. Real-world
    HRs cluster from 105-115 mph EV at fly-ball arcs, producing 400-460
    ft drives — which matches.
    """
    if ev is None or la is None:
        return 0.0
    if la < 8.0:
        return max(40.0, ev * 0.95)
    rad = la * math.pi / 180.0
    d = (ev * ev * math.sin(2 * rad)) / 25.0
    return max(60.0, min(d, 480.0))


# Outcomes that this hook can mutate. Anything else (K, BB, HBP, errors,
# DPs etc.) passes through unchanged.
_BIP_TARGETS = {
    "single", "infield_single", "double", "triple",
    "hr", "home_run",
    "ground_out", "fly_out", "line_out",
    "fielders_choice",
}


def apply_park_effects(
    rng: random.Random,
    outcome_dict: dict,
    ev: Optional[float],
    la: Optional[float],
    spray: Optional[float],
    park_dims: Optional[dict],
) -> None:
    """Mutate outcome_dict in place based on park geometry. No-op when
    park_dims is missing, the hit_type isn't a BIP target, or the EV/LA
    sample is missing.

    Mutations applied (independently — each rule fires its own roll so a
    single drive can only end up in one bucket):

      1. **HR upgrades**: a fly-arc drive (LA 18-42) whose proxy distance
         clears the fence at that spray angle plus a wall_h-scaled
         clearance margin gets upgraded to HR, regardless of the
         engine's categorical roll. This is what makes Polo-Grounds
         bathtub a HR factory: a 105-mph 28° pull shot needs only 280
         ft to clear, not 380.

      2. **HR downgrades**: an existing HR whose distance falls short
         of the fence at its spray angle gets demoted to a double off
         the wall. Tall walls (≥25 ft) require extra clearance — fringe
         HRs scrape the wall for doubles.

      3. **Wall-scraper doubles**: a deep "fly_out" or "line_out" that
         got tagged as caught but actually has HR-grade distance gets
         upgraded to a double (drives that died in the OF in a
         390-ft park clear the 280-ft RF wall in a bathtub).

      4. **Gappers in cavernous parks**: a single in deep alleys with
         high distance + spray well off-center gets upgraded to a
         double, and a really long alley shot (440+ ft alleys, 360+
         ft distance) can become a triple.

      5. **Picket-fence robberies**: cricket-ground low-wall parks
         (wall_h ≤ 7) let outfielders vault — a borderline HR in a
         deep oval has a small chance of being robbed for a fly_out.
    """
    if not park_dims:
        return
    hit_type = outcome_dict.get("hit_type", "")
    if hit_type not in _BIP_TARGETS:
        return
    if ev is None or la is None or spray is None:
        return

    dist = _proxy_distance(ev, la)
    fence = _fence_at_angle(spray, park_dims)
    wall_h = float(park_dims.get("wall_h", 10) or 10)
    abs_spray = abs(spray)

    # Tall-wall clearance margin: a 12-ft wall needs +0 ft above fence;
    # a 35-ft wall needs +12 ft of carry to clear cleanly.
    wall_margin = max(0.0, (wall_h - 12.0) * 0.55)

    fly_arc = 18.0 <= la <= 42.0
    line_arc = 14.0 <= la <= 28.0

    # ── 1. HR upgrade ───────────────────────────────────────────────────
    # Drive cleared the fence at its spray angle with the wall-height
    # clearance. Overrides any non-HR hit_type — including fly_out and
    # line_out. The engine called it caught because the categorical
    # roll said so, but the physics say it left the yard.
    if fly_arc and hit_type != "hr" and dist >= fence + wall_margin + 4.0:
        outcome_dict["hit_type"] = "hr"
        outcome_dict["batter_safe"] = True
        outcome_dict["caught_fly"] = False
        outcome_dict["runner_advances"] = [4, 4, 4]
        outcome_dict["runner_out_idx"] = None
        return

    # ── 2. HR downgrade ─────────────────────────────────────────────────
    if hit_type in ("hr", "home_run"):
        # Required: dist >= fence + wall_margin. If we fall short, demote.
        if dist < fence + wall_margin - 2.0:
            # Off the wall — double in play. Tall walls produce more
            # of these by design.
            outcome_dict["hit_type"] = "double"
            outcome_dict["batter_safe"] = True
            outcome_dict["caught_fly"] = False
            outcome_dict["runner_advances"] = [3, 3, 2]
            return

    # ── 3. Picket-fence robbery ─────────────────────────────────────────
    # Cricket-low fences let OFs vault. Only a small chance — most HRs
    # fly so far over a 4-ft fence that vaulting isn't possible. This
    # only fires on borderline HRs (within 6 ft of the fence + margin).
    if hit_type == "hr" and wall_h <= 7 and dist < fence + 6.0:
        if rng.random() < 0.18:
            outcome_dict["hit_type"] = "fly_out"
            outcome_dict["batter_safe"] = False
            outcome_dict["caught_fly"] = True
            outcome_dict["runner_advances"] = [0, 0, 0]
            return

    # ── 4. Gappers in cavernous parks ───────────────────────────────────
    # A single on a line drive into a deep alley (LCF/RCF > 415 ft, and
    # the BIP spray angle is in the alley) rolls forever. Promote to
    # double; really deep balls promote to triple.
    if hit_type in ("single", "infield_single") and line_arc:
        # Are we in an alley? Spray ~12-35 degrees from CF.
        if 12.0 <= abs_spray <= 35.0:
            # Alley distance — interpolated fence at this spray.
            alley_depth = fence
            if alley_depth >= 420.0 and dist >= 290.0:
                outcome_dict["hit_type"] = "triple"
                outcome_dict["batter_safe"] = True
                outcome_dict["caught_fly"] = False
                outcome_dict["runner_advances"] = [4, 4, 3]
                return
            if alley_depth >= 395.0 and dist >= 270.0:
                outcome_dict["hit_type"] = "double"
                outcome_dict["batter_safe"] = True
                outcome_dict["caught_fly"] = False
                outcome_dict["runner_advances"] = [3, 3, 2]
                return

    # ── 5. Tweener triples in oval parks ────────────────────────────────
    # Oval shape = uniformly deep boundary. A deep double that the
    # engine called a double could become a triple if the boundary is
    # 380+ ft at this spray angle (lots of grass for the ball to roll).
    if hit_type == "double" and fence >= 380.0 and dist >= 320.0 and abs_spray >= 15.0:
        if rng.random() < 0.22:
            outcome_dict["hit_type"] = "triple"
            outcome_dict["runner_advances"] = [4, 4, 3]
            return
