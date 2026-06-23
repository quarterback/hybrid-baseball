"""Fatigue-reactive pull + the death of the forced complete game.

O27 used to ride a workhorse toward a complete game: the manager pulled on
batters-faced only, and a guard (RELIEVER_ENTRY_OUTS_MIN) actively *forced* a
tiring starter to stay in until late in the half. Pitching is now a cascade —
no one goes the distance:

  - a gassed arm (consecutive-pitch fatigue past its stamina-derived budget)
    is pulled even when its batters-faced count is still low;
  - a fresh arm under its BF threshold is left in;
  - a workhorse past his (now much shorter) BF threshold is pulled immediately,
    not extended toward a complete game, regardless of how few outs are in.

The fatigue signal is emergent from ratings (stamina sets the budget), not a
flat pitch cap — see prob.pitch_fatigue_level.
"""
from o27.engine.state import Team, GameState, Player
from o27.engine import manager as mgr
from o27.engine import prob
from o27 import config as cfg

_POS = ["CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"]


def _p(pid, name, pitcher=False, pos="", **kw):
    return Player(player_id=pid, name=name, is_pitcher=pitcher, position=pos, **kw)


def _team(tid, name, sp_stamina=0.5, sp_skill=0.5):
    pre = tid[0].upper()
    starters = [_p(f"{pre}{i}", f"{name[:3]}-{i}", pos=_POS[i - 1], skill=0.5)
                for i in range(1, 9)]
    sp = _p(f"{pre}SP", f"{name[:3]}-SP", pitcher=True, pos="P",
            stamina=sp_stamina, pitcher_skill=sp_skill)
    pen = [_p(f"{pre}P{p}", f"{name[:3]}-P{p}", pitcher=True, pos="P",
              stamina=0.55, pitcher_skill=0.55) for p in range(1, 6)]
    roster = starters + [sp] + pen
    return Team(team_id=tid, name=name, roster=roster, lineup=starters + [sp])


def _state(sp_stamina=0.5, sp_skill=0.5):
    st = GameState(visitors=_team("visitors", "Visitors"),
                   home=_team("home", "Home", sp_stamina, sp_skill))
    st.half = "top"                      # visitors bat, home (HSP) fields
    st.bases = [None, None, None]
    st.current_pitcher_id = "HSP"
    st.score = {"visitors": 3, "home": 3}   # close — no blowout pull
    st.pitcher_runs_this_spell = 0          # under the manager hook
    st.pitcher_h_this_spell = 0
    st.pitcher_spell_count = 1              # barely any batters faced
    st.pitcher_pitches_this_spell = 0
    st.outs = 6
    return st


def _budget(stamina):
    return cfg.PITCH_FATIGUE_BUDGET_BASE + stamina * cfg.PITCH_FATIGUE_BUDGET_SCALE


def test_gassed_arm_pulled_even_with_low_batters_faced():
    # Only 1 BF (well under any threshold), but the pitch count is far past the
    # stamina budget — the fatigue hook pulls him.
    st = _state(sp_stamina=0.5, sp_skill=0.5)
    st.pitcher_spell_count = 1
    st.pitcher_pitches_this_spell = int(_budget(0.5)) + 60
    assert mgr.should_change_pitcher(st) is True


def test_fresh_arm_not_pulled():
    # Under budget and under the BF threshold — stay in.
    st = _state(sp_stamina=0.5, sp_skill=0.5)
    st.pitcher_spell_count = 1
    st.pitcher_pitches_this_spell = 5
    assert mgr.should_change_pitcher(st) is False


def test_high_stamina_arm_outlasts_a_low_stamina_arm():
    # Same pitch count: the low-stamina arm is gassed and pulled; the
    # high-stamina arm still has budget left and is not (length is emergent).
    pc = int(_budget(0.35)) + 25
    weak = _state(sp_stamina=0.35, sp_skill=0.5)
    weak.pitcher_pitches_this_spell = pc
    strong = _state(sp_stamina=0.95, sp_skill=0.5)
    strong.pitcher_pitches_this_spell = pc
    assert mgr.should_change_pitcher(weak) is True
    assert mgr.should_change_pitcher(strong) is False


def test_workhorse_not_extended_toward_a_complete_game():
    # A workhorse past his BF threshold early in the half (few outs in) used to
    # be FORCED to stay until RELIEVER_ENTRY_OUTS_MIN. Now he is pulled.
    st = _state(sp_stamina=0.8, sp_skill=0.5)   # workhorse tier
    st.outs = 4                                  # early — old guard would extend
    st.pitcher_pitches_this_spell = 10           # not yet gassed
    base, scale = cfg.WORKHORSE_CHANGE_BASE, cfg.WORKHORSE_CHANGE_SCALE
    threshold = max(base, base + round(0.5 * scale))
    st.pitcher_spell_count = threshold           # exactly at threshold
    assert mgr.should_change_pitcher(st) is True


def test_pitch_fatigue_level_is_emergent_and_monotonic():
    p = _p("X", "X", pitcher=True, stamina=0.5)
    budget = _budget(0.5)
    assert prob.pitch_fatigue_level(p, int(budget) - 5) == 0.0   # under budget
    assert prob.pitch_fatigue_level(p, None) == 0.0              # unknown count
    lo = prob.pitch_fatigue_level(p, int(budget) + 5)
    hi = prob.pitch_fatigue_level(p, int(budget) + 12)
    assert 0.0 < lo < hi <= cfg.PITCH_FATIGUE_MAX
    # deep overextension saturates at the cap
    assert prob.pitch_fatigue_level(p, int(budget) + 200) == cfg.PITCH_FATIGUE_MAX
