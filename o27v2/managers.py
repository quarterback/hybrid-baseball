"""
Manager personas.

Each team is assigned a manager when the league is seeded. Managers are
re-rolled on every reseed (a different rng_seed produces a different set of
managers — they are NOT bound to the franchise).

A baker's-dozen archetypes span eras and styles, from 1900s dead-ball
purists who'd let a starter throw 180 pitches, through 1970s iron managers,
mid-2000s LaRussa-style bullpen specialists, and modern Rays-coded
analytics shops that open with a reliever and never platoon the same way
twice. The intra-archetype noise band is intentionally wide (0.22 by
default, 0.30+ for the unorthodox personas) so two managers nominally
sharing the same key still routinely diverge enough that the league
produces visibly weird seasons -- a "set-and-forget" who happens to roll
high on run_game, an old-school skipper with surprising bullpen aggression,
and so on.

Tendencies are floats in [0.0, 1.0]:
  quick_hook            propensity to pull a pitcher who's getting tagged
                        (vs. riding the SP for fatigue thresholds only).
  bullpen_aggression    willingness to burn multiple relievers, including
                        earlier in the half.
  leverage_aware        pull/insert decisions weighted by score margin
                        and runners-on (high-leverage situations).
  joker_aggression      willingness to spend joker pinch hitters early.
  pinch_hit_aggression  willingness to permanently pinch-hit for a weak
                        bat in a leverage spot (separate mechanic from
                        the per-cycle joker insertion).
  platoon_aggression    bias toward LHB-vs-RHP / RHB-vs-LHP matchups via
                        late-game pinch hitters (consumes pinch_hit budget
                        but with a platoon target instead of a skill upgrade).
  run_game              SB attempt rate, hit-and-run aggression. High
                        managers will run with average speed; low managers
                        only run with elite speed.
  bench_usage           propensity to rest a regular and start a UT bench
                        bat. Old-school skippers run the same 8 every day;
                        modern / sabermetric ones rotate aggressively
                        (catcher every 4-5 days, vets vs tough lefties,
                        etc.). High here means starters miss more games.

The first four ship live as decision biases today. pinch_hit_aggression,
platoon_aggression, and run_game have schema/seed/stamp wired and are
consumed where engine hooks already exist (pinch hits, SB attempt rate);
deeper platoon-aware substitution is the next layer.

Stored on the `teams` table (re-rolled per seed) and stamped onto the
engine's Team object at game time so the engine's manager.py can read them
without any DB calls.
"""
from __future__ import annotations
from dataclasses import dataclass
import random


# ---------------------------------------------------------------------------
# Archetypes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Archetype:
    key: str
    label: str
    quick_hook: float
    bullpen_aggression: float
    leverage_aware: float
    joker_aggression: float
    pinch_hit_aggression: float
    platoon_aggression: float
    run_game: float
    bench_usage: float
    # Shift aggression — how often the skipper calls a defensive shift
    # against an extreme-spray batter. Old-school archetypes shift less,
    # modern / saber archetypes shift more. Defaulted on the dataclass
    # so legacy archetype defs don't all need updating.
    shift_aggression: float = 0.50
    # Per-archetype noise band. Wide by design — we WANT visible
    # within-archetype variation so two old-school skippers don't feel
    # like the same guy. Default 0.22 covers a ~0.45-wide window per
    # axis; unorthodox personas push wider so mad scientists and
    # gamblers regularly produce truly weird seasons.
    noise: float = 0.22


# Archetype catalogue. Centre values were calibrated so the four-axis vector
# space stays roughly uniform — i.e., the league should produce a plausible
# spread of skipper styles instead of clustering on the median.
ARCHETYPES: dict[str, Archetype] = {
    # ----- conservative / old-school end of the spectrum -----
    "dead_ball": Archetype(
        key="dead_ball", label="Dead-Ball Traditionalist",
        quick_hook=0.08, bullpen_aggression=0.10, leverage_aware=0.20,
        joker_aggression=0.10, pinch_hit_aggression=0.08,
        platoon_aggression=0.05, run_game=0.55, bench_usage=0.05,
        shift_aggression=0.10,
    ),
    "iron_manager": Archetype(
        key="iron_manager", label="Iron Manager",
        quick_hook=0.18, bullpen_aggression=0.20, leverage_aware=0.30,
        joker_aggression=0.20, pinch_hit_aggression=0.18,
        platoon_aggression=0.15, run_game=0.50, bench_usage=0.12,
        shift_aggression=0.20,
    ),
    "old_school": Archetype(
        key="old_school", label="Old-School Skipper",
        quick_hook=0.28, bullpen_aggression=0.32, leverage_aware=0.42,
        joker_aggression=0.30, pinch_hit_aggression=0.30,
        platoon_aggression=0.25, run_game=0.45, bench_usage=0.22,
        shift_aggression=0.30,
    ),
    "small_ball": Archetype(
        key="small_ball", label="Small-Ball Tactician",
        quick_hook=0.40, bullpen_aggression=0.45, leverage_aware=0.55,
        joker_aggression=0.55, pinch_hit_aggression=0.55,
        platoon_aggression=0.50, run_game=0.85, bench_usage=0.45,
    ),
    "players_manager": Archetype(
        key="players_manager", label="Players' Manager",
        quick_hook=0.32, bullpen_aggression=0.40, leverage_aware=0.50,
        joker_aggression=0.42, pinch_hit_aggression=0.35,
        platoon_aggression=0.35, run_game=0.45, bench_usage=0.40,
    ),
    "set_and_forget": Archetype(
        key="set_and_forget", label="Set-It-and-Forget-It",
        quick_hook=0.30, bullpen_aggression=0.25, leverage_aware=0.20,
        joker_aggression=0.18, pinch_hit_aggression=0.20,
        platoon_aggression=0.20, run_game=0.40, bench_usage=0.10,
    ),

    # ----- balanced middle -----
    "balanced": Archetype(
        key="balanced", label="Balanced Skipper",
        quick_hook=0.50, bullpen_aggression=0.50, leverage_aware=0.55,
        joker_aggression=0.50, pinch_hit_aggression=0.50,
        platoon_aggression=0.50, run_game=0.50, bench_usage=0.50,
    ),
    "fiery": Archetype(
        key="fiery", label="Fiery Competitor",
        quick_hook=0.70, bullpen_aggression=0.60, leverage_aware=0.55,
        joker_aggression=0.80, pinch_hit_aggression=0.70,
        platoon_aggression=0.45, run_game=0.65, bench_usage=0.55,
    ),

    # ----- modern / aggressive end -----
    "hot_hand": Archetype(
        key="hot_hand", label="Hot-Hand Hunter",
        quick_hook=0.85, bullpen_aggression=0.55, leverage_aware=0.45,
        joker_aggression=0.60, pinch_hit_aggression=0.65,
        platoon_aggression=0.40, run_game=0.55, bench_usage=0.65,
    ),
    "bullpen_innovator": Archetype(
        key="bullpen_innovator", label="Bullpen Innovator",
        quick_hook=0.78, bullpen_aggression=0.88, leverage_aware=0.92,
        joker_aggression=0.55, pinch_hit_aggression=0.65,
        platoon_aggression=0.78, run_game=0.45, bench_usage=0.65,
    ),
    "modern": Archetype(
        key="modern", label="Modern Tactician",
        quick_hook=0.80, bullpen_aggression=0.80, leverage_aware=0.85,
        joker_aggression=0.65, pinch_hit_aggression=0.70,
        platoon_aggression=0.70, run_game=0.50, bench_usage=0.70,
        shift_aggression=0.80,
    ),

    # ----- unorthodox / high-variance personas -----
    "sabermetric_max": Archetype(
        key="sabermetric_max", label="Sabermetric Maximalist",
        quick_hook=0.92, bullpen_aggression=0.95, leverage_aware=0.95,
        joker_aggression=0.75, pinch_hit_aggression=0.85,
        platoon_aggression=0.90, run_game=0.40, bench_usage=0.85,
        shift_aggression=0.95,
    ),
    "mad_scientist": Archetype(
        # Maddon-coded / Rays-coded chaos. Even wider noise so two mad
        # scientists routinely diverge enough to read as different teams.
        key="mad_scientist", label="Mad Scientist",
        quick_hook=0.65, bullpen_aggression=0.72, leverage_aware=0.70,
        joker_aggression=0.92, pinch_hit_aggression=0.85,
        platoon_aggression=0.82, run_game=0.65, bench_usage=0.80, noise=0.32,
    ),
    "gambler": Archetype(
        # Roll-the-dice aggressive on every axis — extremely high variance.
        key="gambler", label="Gambler",
        quick_hook=0.80, bullpen_aggression=0.78, leverage_aware=0.65,
        joker_aggression=0.88, pinch_hit_aggression=0.80,
        platoon_aggression=0.55, run_game=0.85, bench_usage=0.60, noise=0.30,
    ),
}

ARCHETYPE_KEYS = tuple(ARCHETYPES.keys())


# ---------------------------------------------------------------------------
# Roll
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def roll_manager(rng: random.Random) -> dict:
    """Roll a manager: pick an archetype uniformly, then add per-axis noise
    sized by that archetype's `noise` band.

    Returns a dict of columns ready to insert into `teams`.
    """
    arch_key = rng.choice(ARCHETYPE_KEYS)
    arch = ARCHETYPES[arch_key]
    n = arch.noise
    return {
        "manager_archetype":      arch.key,
        "mgr_quick_hook":         _clamp(arch.quick_hook           + rng.uniform(-n, n)),
        "mgr_bullpen_aggression": _clamp(arch.bullpen_aggression   + rng.uniform(-n, n)),
        "mgr_leverage_aware":     _clamp(arch.leverage_aware       + rng.uniform(-n, n)),
        "mgr_joker_aggression":   _clamp(arch.joker_aggression     + rng.uniform(-n, n)),
        "mgr_pinch_hit_aggression": _clamp(arch.pinch_hit_aggression + rng.uniform(-n, n)),
        "mgr_platoon_aggression": _clamp(arch.platoon_aggression   + rng.uniform(-n, n)),
        "mgr_run_game":           _clamp(arch.run_game             + rng.uniform(-n, n)),
        "mgr_bench_usage":        _clamp(arch.bench_usage          + rng.uniform(-n, n)),
        "mgr_shift_aggression":   _clamp(arch.shift_aggression     + rng.uniform(-n, n)),
    }


def archetype_label(key: str | None) -> str:
    if not key:
        return "Unassigned"
    arch = ARCHETYPES.get(key)
    return arch.label if arch else key
