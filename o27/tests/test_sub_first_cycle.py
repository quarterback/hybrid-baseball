"""Lineup-integrity substitution gate + in-game-injury bypass.

Symptom that motivated this: box scores showed teams churning the bench
(pinch hitters / pinch runners / defensive subs) inside the first turn
through the order, before the starting fielded lineup had even batted once.
Diagnosis: the tactical deciders (should_pinch_hit / should_pinch_run /
should_defensive_sub) had no first-cycle gate. The fix gates them on
Team.lineup_cycle_number >= 1. Joker insertion is gated the same way — the
first trip through the order belongs to the nine base batters (every fielder
and the pitcher hits once before any tactical insertion). Injuries bypass the
gate (they route through the executors, not the deciders).
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


def _joker_fires_setup(st):
    """Stack the deck so a joker insertion WOULD fire: the batter due up is far
    below the joker pool (a real upgrade) and the manager is maximally
    joker-happy. Only leverage and the cycle gate decide whether it actually
    fires."""
    team = st.batting_team
    team.mgr_joker_aggression = 1.0
    team.lineup[team.lineup_position % len(team.lineup)].skill = 0.10
    for j in team.jokers_available:
        j.skill = 0.95


def test_jokers_gated_before_first_cycle():
    st = _high_leverage_state()
    st.batting_team.lineup_cycle_number = 0
    _joker_fires_setup(st)
    # Even in a spot where the joker would otherwise fire, the first-cycle gate
    # must hold it — the nine base batters hit first.
    assert mgr.should_insert_joker(st, rng=random.Random(0)) is None


def test_jokers_fire_in_late_high_leverage():
    # Late, bases loaded, tied, joker-happy manager, big upgrade over the
    # batter due up: the leverage path should send a joker in.
    st = _high_leverage_state()
    st.batting_team.lineup_cycle_number = 1
    st.outs = 24                       # late in the half → high late_factor
    st.bases = ["V1", "V2", "V3"]      # bases loaded → max runner_factor
    _joker_fires_setup(st)
    res = mgr.should_insert_joker(st, rng=random.Random(1))
    assert isinstance(res, Player)


def test_jokers_do_not_fire_for_a_better_bat():
    # Upgrade guard: even in a max-leverage spot, no joker comes in for a
    # batter the pool can't out-hit (manager never pinch-hits his good bats).
    st = _high_leverage_state()
    st.batting_team.lineup_cycle_number = 1
    st.outs = 24
    st.bases = ["V1", "V2", "V3"]
    st.batting_team.mgr_joker_aggression = 1.0
    st.batting_team.lineup[st.batting_team.lineup_position].skill = 0.95
    for j in st.batting_team.jokers_available:
        j.skill = 0.30   # all jokers worse than the batter
    # Try many seeds; none should fire.
    assert all(
        mgr.should_insert_joker(st, rng=random.Random(s)) is None
        for s in range(50)
    )


def test_weak_hitter_is_not_benched_in_low_leverage():
    # The old override fired ~0.75-0.95 every cycle on weak bats. Now, with no
    # leverage (blowout, no runners, early), a weak hitter almost never draws a
    # joker — he gets to bat.
    fires = 0
    rng = random.Random(99)
    for _ in range(500):
        st = _high_leverage_state()
        st.batting_team.lineup_cycle_number = 1
        st.outs = 3                              # early → low late_factor
        st.bases = [None, None, None]            # no runners → low leverage
        st.score = {"visitors": 0, "home": 12}   # blowout → gap_factor 0
        _joker_fires_setup(st)
        if mgr.should_insert_joker(st, rng=rng) is not None:
            fires += 1
    # gap_factor is 0 here so leverage is 0 — effectively never fires.
    assert fires == 0, fires


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
