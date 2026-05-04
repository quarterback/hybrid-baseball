"""
Manager personas.

Each team is assigned a manager when the league is seeded. Managers are
re-rolled on every reseed (a different rng_seed produces a different set of
managers — they are NOT bound to the franchise).

Three overarching archetypes, each producing a tendency vector that biases
in-game decisions. Inside an archetype, individual managers carry per-axis
noise so two "Old School" skippers still feel different from each other.

Tendencies are floats in [0.0, 1.0]:
  quick_hook         — propensity to pull a pitcher who's getting tagged
                       (vs. riding the SP for fatigue thresholds only).
  bullpen_aggression — willingness to burn multiple relievers, including
                       earlier in the half.
  leverage_aware     — pull/insert decisions weighted by score margin
                       and runners-on (high-leverage situations).
  joker_aggression   — willingness to spend joker pinch hitters early.

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
    # Centre values for each tendency axis. Per-manager rolls add ±0.15 noise
    # on top, so a "modern" manager could land anywhere in 0.65–0.95 on
    # quick_hook depending on the seed.
    quick_hook: float
    bullpen_aggression: float
    leverage_aware: float
    joker_aggression: float


ARCHETYPES: dict[str, Archetype] = {
    "old_school": Archetype(
        key="old_school", label="Old-School Skipper",
        quick_hook=0.25, bullpen_aggression=0.30,
        leverage_aware=0.40, joker_aggression=0.30,
    ),
    "modern": Archetype(
        key="modern", label="Modern Tactician",
        quick_hook=0.80, bullpen_aggression=0.80,
        leverage_aware=0.85, joker_aggression=0.65,
    ),
    "fiery": Archetype(
        key="fiery", label="Fiery Competitor",
        quick_hook=0.70, bullpen_aggression=0.60,
        leverage_aware=0.55, joker_aggression=0.80,
    ),
}

ARCHETYPE_KEYS = tuple(ARCHETYPES.keys())


# ---------------------------------------------------------------------------
# Roll
# ---------------------------------------------------------------------------

_NOISE = 0.15


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def roll_manager(rng: random.Random) -> dict:
    """Roll a manager: pick an archetype, then add per-axis noise.

    Returns a dict of columns ready to insert into `teams`:
        manager_archetype, mgr_quick_hook, mgr_bullpen_aggression,
        mgr_leverage_aware, mgr_joker_aggression.
    """
    arch_key = rng.choice(ARCHETYPE_KEYS)
    arch = ARCHETYPES[arch_key]
    return {
        "manager_archetype":      arch.key,
        "mgr_quick_hook":         _clamp(arch.quick_hook         + rng.uniform(-_NOISE, _NOISE)),
        "mgr_bullpen_aggression": _clamp(arch.bullpen_aggression + rng.uniform(-_NOISE, _NOISE)),
        "mgr_leverage_aware":     _clamp(arch.leverage_aware     + rng.uniform(-_NOISE, _NOISE)),
        "mgr_joker_aggression":   _clamp(arch.joker_aggression   + rng.uniform(-_NOISE, _NOISE)),
    }


def archetype_label(key: str | None) -> str:
    if not key:
        return "Unassigned"
    arch = ARCHETYPES.get(key)
    return arch.label if arch else key
