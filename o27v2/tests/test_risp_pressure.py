"""
Tests for the talent-driven RISP pressure model
(o27.engine.prob._resolve_risp_pressure).

The pressure roll is two-stage and reads from EXISTING player
attributes — no new schema, no new rolled rating. Batter clutch is
derived from (eye + contact) / 2; pitcher composure is derived from
(command + grit) / 2.

  1. Stage 1 — does the moment manifest? Probability composes from
     situational pressure (RISP / RISP+3rd / loaded), pitcher composure,
     and batter clutch.
  2. Stage 2 — which manifestation? Talent-weighted draw between
     {hit, error, leave_up}, mutually exclusive.

These tests pin the directional invariants — no exact rate assertions
that would brittle-fail under tuning. The intent is "fires more often
in the situations the design says it should" and "the three pathways
are mutually exclusive (never both hit AND error on the same PA)."
"""
from __future__ import annotations

import random

from o27.engine.state import Player, Team
from o27.engine.prob import _resolve_risp_pressure


class _FakeState:
    """Minimum surface area for the pressure roll — just .bases and
    .fielding_team. The function never touches anything else on state."""
    def __init__(self, bases, fielding_team):
        self.bases = list(bases)
        self.fielding_team = fielding_team


def _new_batter(eye: float = 0.5, contact: float = 0.5) -> Player:
    return Player(player_id="b", name="B", is_pitcher=False,
                  eye=eye, contact=contact)


def _new_pitcher(command: float = 0.5, grit: float = 0.5) -> Player:
    return Player(player_id="p", name="P", is_pitcher=True,
                  command=command, grit=grit)


def _new_fielding(defense_rating: float = 0.5) -> Team:
    t = Team(team_id="F", name="F", roster=[])
    t.defense_rating = defense_rating
    return t


def _fire_rate(state, batter, pitcher, n=10000, seed=42) -> float:
    rng = random.Random(seed)
    fires = sum(1 for _ in range(n)
                if _resolve_risp_pressure(rng, state, batter, pitcher) is not None)
    return fires / n


def test_no_risp_never_fires():
    """Bases empty or runner on 1B only — no RISP, pressure never
    manifests (returns None on every roll)."""
    fielding = _new_fielding()
    batter = _new_batter(eye=0.85, contact=0.85)         # max-clutch
    pitcher = _new_pitcher(command=0.15, grit=0.15)      # min-composure
    for bases in ([None, None, None], ["r1", None, None]):
        state = _FakeState(bases, fielding)
        rate = _fire_rate(state, batter, pitcher, n=5000)
        assert rate == 0.0, f"non-RISP state {bases} should never fire (got {rate:.4f})"


def test_loaded_pressure_strictly_above_risp_only():
    """At any fixed personnel matchup, bases-loaded fires strictly more
    often than a runner on 2nd or 3rd alone. This is the O27 design point
    — bases loaded is the highest situational tier because the 2C stay
    mechanic compounds the payoff."""
    fielding = _new_fielding()
    batter = _new_batter(eye=0.65, contact=0.65)
    pitcher = _new_pitcher(command=0.40, grit=0.40)
    risp_alone = _fire_rate(_FakeState([None, "r2", None], fielding), batter, pitcher)
    loaded     = _fire_rate(_FakeState(["r1", "r2", "r3"], fielding), batter, pitcher)
    assert loaded > risp_alone, (
        f"bases-loaded rate {loaded:.3f} should exceed RISP-only rate {risp_alone:.3f}"
    )
    # Sanity: the bump should be meaningful (>40% relative lift), not noise.
    assert loaded > 1.4 * risp_alone


def test_clutch_batter_fires_more_than_anti_clutch():
    """At identical situation + pitcher, a high-clutch batter (good
    eye + contact) manifests pressure more often than a flat one. This
    is the derived-clutch channel — a star hitter's plate discipline
    makes the pressure event fire more often, no separate attribute
    needed."""
    fielding = _new_fielding()
    pitcher = _new_pitcher(command=0.40, grit=0.40)
    bases = ["r1", "r2", "r3"]
    clutch_rate = _fire_rate(_FakeState(bases, fielding),
                             _new_batter(eye=0.85, contact=0.85), pitcher)
    flat_rate   = _fire_rate(_FakeState(bases, fielding),
                             _new_batter(eye=0.15, contact=0.15), pitcher)
    assert clutch_rate > flat_rate, (
        f"clutch (eye/con=0.85) fire rate {clutch_rate:.3f} should exceed "
        f"non-clutch (eye/con=0.15) rate {flat_rate:.3f}"
    )


def test_uncomposed_pitcher_fires_more_than_veteran():
    """At identical situation + batter, a low-composure pitcher
    (poor command + low grit) yields more pressure events than a
    high-composure veteran. The talent gate cuts both ways."""
    fielding = _new_fielding()
    batter = _new_batter(eye=0.60, contact=0.60)
    bases = [None, "r2", None]
    weak_pitcher   = _new_pitcher(command=0.20, grit=0.20)
    sharp_pitcher  = _new_pitcher(command=0.80, grit=0.80)
    weak_rate  = _fire_rate(_FakeState(bases, fielding), batter, weak_pitcher)
    sharp_rate = _fire_rate(_FakeState(bases, fielding), batter, sharp_pitcher)
    assert weak_rate > sharp_rate


def test_manifestation_set_is_constrained():
    """Returned manifestation, when not None, is one of the three
    canonical pathways. This is the mutual-exclusion guarantee — at
    most one of {hit, error, leave_up} per PA, never a stacked combo."""
    fielding = _new_fielding(defense_rating=0.4)
    batter = _new_batter(eye=0.85, contact=0.85)
    pitcher = _new_pitcher(command=0.30, grit=0.30)
    state = _FakeState(["r1", "r2", "r3"], fielding)
    rng = random.Random(7)
    seen = set()
    for _ in range(5000):
        m = _resolve_risp_pressure(rng, state, batter, pitcher)
        if m is not None:
            seen.add(m)
            assert m in {"hit", "error", "leave_up"}, f"unknown manifestation: {m}"
    # All three paths should be reachable under a balanced matchup.
    assert seen == {"hit", "error", "leave_up"}, (
        f"expected all 3 manifestations to appear; saw {seen}"
    )


def test_weak_defense_skews_to_error_path():
    """Holding pitcher + batter fixed, a weaker defense yields a higher
    share of `error` manifestations among the events that fire. This is
    the talent-driven Stage-2 weighting — the team's defense_rating
    pulls the manifestation mix, not a flat constant."""
    batter = _new_batter(eye=0.65, contact=0.65)
    pitcher = _new_pitcher(command=0.50, grit=0.50)
    bases = ["r1", "r2", "r3"]

    def _error_share(defense_rating, n=10000, seed=1):
        rng = random.Random(seed)
        events = [_resolve_risp_pressure(
            rng, _FakeState(bases, _new_fielding(defense_rating=defense_rating)),
            batter, pitcher) for _ in range(n)]
        non_none = [e for e in events if e is not None]
        if not non_none:
            return 0.0
        return sum(1 for e in non_none if e == "error") / len(non_none)

    weak_d_share   = _error_share(defense_rating=0.20)
    strong_d_share = _error_share(defense_rating=0.80)
    assert weak_d_share > strong_d_share, (
        f"weak defense should bobble more under pressure: "
        f"weak={weak_d_share:.3f} strong={strong_d_share:.3f}"
    )
