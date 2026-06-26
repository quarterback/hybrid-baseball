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

from o27 import config as cfg


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


def _wall_at_angle(spray: float, park_dims: dict) -> float:
    """Interpolate the fence HEIGHT (ft) at the given spray angle.

    Real parks carry a per-zone `walls` map (lf/lcf/cf/rcf/rf) so a tall wall
    that only stands down one line — Fenway's 37-ft Monster, Tropicana's tall
    RF — actually rides the spray angle instead of being smeared into a single
    scalar. Falls back to the scalar `wall_h` when no `walls` map is present,
    so procedurally-generated parks (which predate the map) behave exactly as
    before.
    """
    walls = park_dims.get("walls") if park_dims else None
    scalar = float(park_dims.get("wall_h", 10) or 10) if park_dims else 10.0
    if not walls:
        return scalar
    s = max(-45.0, min(45.0, float(spray or 0.0)))
    for i in range(len(_FENCE_ANGLES) - 1):
        a0, a1 = _FENCE_ANGLES[i], _FENCE_ANGLES[i + 1]
        if a0 <= s <= a1:
            h0 = float(walls.get(_FENCE_KEYS[i],     scalar))
            h1 = float(walls.get(_FENCE_KEYS[i + 1], scalar))
            t = (s - a0) / (a1 - a0) if a1 != a0 else 0.0
            return h0 + (h1 - h0) * t
    return float(walls.get("cf", scalar))


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

    Park-independent EV texture (rules 6-9, reached only when no geometry
    rule above fired). These let exit velocity re-decide a thin slice of
    *marginal* balls in play. They are paired so league offense stays ~flat:

      6. **Seeing-eye single**: a scorched grounder (EV ≥ EV_SCORCHED, low
         LA) handcuffs the infield and sneaks through — ground_out → single.
      7. **At-'em ball**: a scorched line drive (EV ≥ EV_SCORCHED, line arc)
         is hit right at a fielder — single/double → caught line_out.
      8. **Bloop single**: a softly-struck fly/liner (EV ≤ EV_SOFT) dies in
         front of the outfielders — fly_out/line_out → single.
      9. **Routine roller**: a weak grounder (EV ≤ EV_SOFT, low LA) that the
         categorical roll called a hit is an easy play — single → ground_out.

    Rules 6+8 (out→hit) are balanced against 7+9 (hit→out): the net is BABIP
    variance driven by contact quality, not a shift in run scoring.

    Tier-1 extensions (rules 10-14):

      10. **Can-of-corn**: a lazy fly (LA 36-48, EV ≤ 88) the categorical
          roll called a hit is run down — single/double → caught fly_out.
      11. **Legged-out tapper**: a dribbler (EV ≤ EV_TAPPER_MAX, low LA) the
          batter beats out — ground_out → infield_single. Offsets rule 10.
      12. **Frozen rope**: a line-drive one-hopper (EV ≥ EV_FROZEN, LA 12-26)
          one-hops the wall — single → double.
      13. **Down the line**: a single skipped into the corner (|spray| ≥ 40)
          rolls for a double — single → double.
      14. **Wall-ball carom**: a deep drive (EV ≥ EV_FROZEN) into a tall, deep
          alley caroms wild — double → triple.

    Rules 12-14 change only the extra-base mix, not hits/BIP.
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
    # Fence height AT this spray angle — honors a real park's per-zone walls
    # map (e.g. the Green Monster down the LF line) and falls back to the
    # scalar wall_h for generated parks. Every downstream rule reads wall_h.
    wall_h = _wall_at_angle(spray, park_dims)
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

    # ── EV texture (rules 6-9) ──────────────────────────────────────────
    # Park-independent: exit velocity re-decides a thin slice of marginal
    # BIP. Reached only because no geometry rule above fired, so these stay
    # mutually exclusive with the park mutations. A caught conversion mirrors
    # rule 3's shape; a fallen-hit conversion mirrors the gapper double's.
    grounder = la < 10.0

    # ── 6. Seeing-eye single: scorched grounder sneaks through ──────────
    if hit_type == "ground_out" and ev >= cfg.EV_SCORCHED and grounder:
        if rng.random() < cfg.EV_SCORCH_THRU_P:
            outcome_dict["hit_type"] = "single"
            outcome_dict["batter_safe"] = True
            outcome_dict["caught_fly"] = False
            outcome_dict["runner_out_idx"] = None
            outcome_dict["runner_advances"] = [1, 2, 2]
            return

    # ── 7. At-'em ball: scorched liner snared ───────────────────────────
    if hit_type in ("single", "double") and line_arc and ev >= cfg.EV_SCORCHED:
        if rng.random() < cfg.EV_ATEM_P:
            outcome_dict["hit_type"] = "line_out"
            outcome_dict["batter_safe"] = False
            outcome_dict["caught_fly"] = True
            outcome_dict["runner_out_idx"] = None
            outcome_dict["runner_advances"] = [0, 0, 0]
            return

    # ── 8. Bloop single: softly-struck fly/liner dies in front ──────────
    if hit_type in ("fly_out", "line_out") and ev <= cfg.EV_SOFT and 12.0 <= la <= 40.0:
        if rng.random() < cfg.EV_BLOOP_P:
            outcome_dict["hit_type"] = "single"
            outcome_dict["batter_safe"] = True
            outcome_dict["caught_fly"] = False
            outcome_dict["runner_out_idx"] = None
            outcome_dict["runner_advances"] = [1, 2, 2]
            return

    # ── 9. Routine roller: weak grounder hit is an easy play ────────────
    if hit_type in ("single", "infield_single") and ev <= cfg.EV_SOFT and grounder:
        if rng.random() < cfg.EV_ROLLER_P:
            outcome_dict["hit_type"] = "ground_out"
            outcome_dict["batter_safe"] = False
            outcome_dict["caught_fly"] = False
            outcome_dict["runner_out_idx"] = None
            outcome_dict["runner_advances"] = [0, 0, 0]
            return

    # ── 10. Can-of-corn: lazy fly the engine called a hit is run down ───
    if hit_type in ("single", "double") and 36.0 <= la <= 48.0 and ev <= 88.0:
        if rng.random() < cfg.EV_LAZYFLY_P:
            outcome_dict["hit_type"] = "fly_out"
            outcome_dict["batter_safe"] = False
            outcome_dict["caught_fly"] = True
            outcome_dict["runner_out_idx"] = None
            outcome_dict["runner_advances"] = [0, 0, 0]
            return

    # ── 11. Legged-out tapper: dribbler the batter beats out ────────────
    if hit_type == "ground_out" and ev <= cfg.EV_TAPPER_MAX and grounder:
        if rng.random() < cfg.EV_TAPPER_P:
            outcome_dict["hit_type"] = "infield_single"
            outcome_dict["batter_safe"] = True
            outcome_dict["caught_fly"] = False
            outcome_dict["runner_out_idx"] = None
            outcome_dict["runner_advances"] = [1, 1, 1]
            return

    # ── 12. Frozen rope: line-drive one-hopper to the wall ──────────────
    if hit_type == "single" and ev >= cfg.EV_FROZEN and 12.0 <= la <= 26.0:
        if rng.random() < cfg.EV_FROZENROPE_P:
            outcome_dict["hit_type"] = "double"
            outcome_dict["batter_safe"] = True
            outcome_dict["caught_fly"] = False
            outcome_dict["runner_advances"] = [3, 3, 2]
            return

    # ── 13. Down the line: single skipped into the corner ───────────────
    if hit_type == "single" and abs_spray >= 40.0 and 8.0 <= la <= 30.0:
        if rng.random() < cfg.EV_DOWNLINE_P:
            outcome_dict["hit_type"] = "double"
            outcome_dict["batter_safe"] = True
            outcome_dict["caught_fly"] = False
            outcome_dict["runner_advances"] = [3, 3, 2]
            return

    # ── 14. Wall-ball carom: deep drive off a tall, deep alley ──────────
    if (hit_type == "double" and ev >= cfg.EV_FROZEN and 18.0 <= la <= 35.0
            and wall_h >= 22.0 and 12.0 <= abs_spray <= 35.0 and fence >= 400.0):
        if rng.random() < cfg.EV_WALLBALL_P:
            outcome_dict["hit_type"] = "triple"
            outcome_dict["batter_safe"] = True
            outcome_dict["caught_fly"] = False
            outcome_dict["runner_advances"] = [4, 4, 3]
            return
