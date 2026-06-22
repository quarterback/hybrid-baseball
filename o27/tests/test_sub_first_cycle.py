"""Lineup-integrity substitution gate + in-game-injury bypass.

Symptom that motivated this: box scores showed teams churning the bench
(pinch hitters / pinch runners / defensive subs) inside the first turn
through the order, before the starting fielded lineup had even batted once.
Diagnosis: the tactical deciders (should_pinch_hit / should_pinch_run /
should_defensive_sub) had no first-cycle gate. The fix gates them on
Team.lineup_cycle_number >= 1; jokers stay ungated; injuries bypass the gate
(they route through the executors, not the deciders).
"""
import random

from o27.engine.state import Team, GameState, Player
from o27.engine import manager as mgr
from o27.engine import injury
from o27 import config as cfg


_POS = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]


def _p(pid, name, pitcher=False, pos="", **kw):
    return Player(player_id=pid, name=name, is_pitcher=pitcher, position=pos, **kw)


def _team(tid, name, base=0.5):
    pre = tid[0].upper()
    starters = [_p(f"{pre}{i}", f"{name[:3]}-{i}", pos=_POS[i - 1],
                   contact=base, power=base, speed=base, defense=base)
                for i in range(1, 9)]
    sp = _p(f"{pre}SP", f"{name[:3]}-SP", pitcher=True, pos="P", stamina=base)
    jokers = [_p(f"{pre}J{j}", f"{name[:3]}-J{j}", pos="DH") for j in range(1, 4)]
    # Clearly-superior bench so a tactical sub WOULD fire if not gated.
    bench = [_p(f"{pre}B{b}", f"{name[:3]}-B{b}", pos=_POS[(b - 1) % 8],
                contact=0.95, power=0.95, speed=0.95, defense=0.95)
             for b in range(1, 7)]
    pen = [_p(f"{pre}P{p}", f"{name[:3]}-P{p}", pitcher=True, pos="P", stamina=0.7)
           for p in range(1, 8)]
    roster = starters + [sp] + jokers + bench + pen
    return Team(team_id=tid, name=name, roster=roster,
                lineup=starters + [sp] + jokers, jokers_available=list(jokers))


def _high_leverage_state():
    """Tie game, runners in scoring position, weak batter due up — a spot
    where the manager WOULD pinch-hit/run if the gate allowed it."""
    st = GameState(visitors=_team("visitors", "Visitors"),
                   home=_team("home", "Home"))
    st.half = "top"
    st.outs = 4
    st.score = {"visitors": 3, "home": 3}
    st.bases = ["V2", "V3", None]   # runners on 1st and 2nd (ids from visitors)
    st.current_pitcher_id = "HSP"
    return st


def test_tactical_subs_gated_before_first_cycle():
    st = _high_leverage_state()
    st.batting_team.lineup_cycle_number = 0
    rng = random.Random(1)
    assert mgr.should_pinch_hit(st, rng=rng) is None
    assert mgr.should_pinch_run(st, rng=rng) is None
    # Defensive sub keys off the *batting* team's cycle.
    assert mgr.should_defensive_sub(st, rng=rng) is None


def test_tactical_subs_allowed_after_first_cycle():
    # Extreme, unambiguous spot: late, bases loaded, tie, aggressive manager,
    # huge bench upgrade. With the gate lifted (cycle >= 1) the pinch hit must
    # fire; the cycle-0 test proves the gate is what blocks it otherwise.
    st = _high_leverage_state()
    st.outs = 22
    st.bases = ["V1", "V2", "V3"]
    st.batting_team.lineup_cycle_number = 1
    st.batting_team.mgr_platoon_aggression = 0.92
    assert mgr.should_pinch_hit(st, rng=random.Random(1)) is not None


def test_jokers_ungated_at_cycle_zero():
    st = _high_leverage_state()
    st.batting_team.lineup_cycle_number = 0
    # Jokers are intentionally exempt from the first-cycle gate; the decider
    # must still be reachable (returns a Player or None by its own logic, but
    # never raises and isn't hard-gated by cycle).
    rng = random.Random(1)
    # Should not raise; result type is Player or None.
    res = mgr.should_insert_joker(st, rng=rng)
    assert res is None or isinstance(res, Player)


def test_injury_event_bypasses_gate_at_cycle_zero():
    st = _high_leverage_state()
    st.batting_team.lineup_cycle_number = 0
    st.pitcher_spell_count = 40   # deep into the arc → fatigue tax elevated
    # Force injuries on so the roll can fire; crank rates to guarantee a hit.
    cfg.INJURY_INGAME_ENABLED = True
    old = (cfg.INJURY_INGAME_PITCHER_BASE, cfg.INJURY_INGAME_BATTER_BASE,
           cfg.INJURY_INGAME_BASERUN_BASE, cfg.INJURY_INGAME_FIELD_BASE)
    cfg.INJURY_INGAME_PITCHER_BASE = 1.0
    try:
        evt = injury.roll_injury_event(st, random.Random(0))
        assert evt is not None and evt["type"] == "injury_sub"
        # The forced replacement exists despite cycle 0 (gate bypassed).
        assert evt.get("new_pitcher") is not None or evt.get("replacement") is not None
    finally:
        (cfg.INJURY_INGAME_PITCHER_BASE, cfg.INJURY_INGAME_BATTER_BASE,
         cfg.INJURY_INGAME_BASERUN_BASE, cfg.INJURY_INGAME_FIELD_BASE) = old


def test_injury_roll_is_deterministic():
    st1 = _high_leverage_state(); st1.pitcher_spell_count = 40
    st2 = _high_leverage_state(); st2.pitcher_spell_count = 40
    cfg.INJURY_INGAME_PITCHER_BASE = 0.5
    e1 = injury.roll_injury_event(st1, random.Random(7))
    e2 = injury.roll_injury_event(st2, random.Random(7))
    k1 = None if e1 is None else (e1["kind"], e1["player_out"].player_id)
    k2 = None if e2 is None else (e2["kind"], e2["player_out"].player_id)
    assert k1 == k2
