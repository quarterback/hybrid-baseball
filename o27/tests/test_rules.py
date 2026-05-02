"""
Phase 1 scripted rule verification tests for O27.

Each test constructs an explicit sequence of events and asserts the resulting
GameState matches the expected values.  All outcomes are deterministic (no
probability models).

Run with:
    python -m tests.test_rules          (from o27/ directory)
    python o27/tests/test_rules.py      (from repo root)

Tests:
  1. 27-out half with full lineup cycling
  2. Multi-hit at-bat via repeated stays
  3. Stay constraints (2-strike stay = out; caught fly + stay = out; bases empty = no stay)
  4. Joker insertion mid-inning (enforcement + once-per-half constraint)
  5. Halftime transition with target announcement
  6. Super-inning trigger and resolution
"""

import sys
import os

# Ensure the workspace root is in sys.path so 'o27' resolves as a package.
_here = os.path.dirname(os.path.abspath(__file__))
_workspace_root = os.path.dirname(os.path.dirname(_here))
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

from o27.engine.state import GameState, Team, Player
from o27.engine.game import run_game, run_half, halftime, check_winner, make_script_provider
from o27.engine.pa import apply_event
from o27.engine import fielding as fld
from o27.engine import stay as stay_mod
from o27.engine.manager import insert_joker, can_insert_joker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"

_results: list[tuple[str, str, str]] = []  # (name, status, detail)


def _assert(name: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    _results.append((name, status, detail))
    marker = "✓" if condition else "✗"
    print(f"  {marker} {name}" + (f": {detail}" if detail else ""))


def _make_player(pid: str, name: str, is_pitcher=False, is_joker=False) -> Player:
    return Player(player_id=pid, name=name, is_pitcher=is_pitcher, is_joker=is_joker)


def _make_team(team_id: str, name: str) -> Team:
    """9-batter starting lineup + 3 jokers in the reserve pool (not pre-seeded in lineup).

    Jokers are available for mid-inning insertion but are NOT placed in the
    starting lineup — inserting one at lineup position P adds them to the order
    without duplication, keeping the lineup at 9+N where N jokers have been used.
    """
    prefix = team_id[0].upper()
    starters = []
    for i in range(1, 10):
        starters.append(_make_player(f"{prefix}{i}", f"{name[:3]}{i}",
                                     is_pitcher=(i == 9)))
    jokers = []
    for j in range(1, 4):
        jk = _make_player(f"{prefix}J{j}", f"{name[:3]}J{j}", is_joker=True)
        jokers.append(jk)
    roster = starters + jokers   # full 12-player roster
    return Team(
        team_id=team_id,
        name=name,
        roster=roster,
        lineup=list(starters),   # starting batting order: 9 players (no jokers yet)
        jokers_available=list(jokers),
    )


def _fresh_state() -> GameState:
    visitors = _make_team("visitors", "Red")
    home = _make_team("home", "Blue")
    state = GameState(visitors=visitors, home=home)
    state.current_pitcher_id = home.roster[8].player_id  # home pitcher starts
    return state


def _ground_out_event() -> dict:
    return {"type": "ball_in_play", "choice": "run",
            "outcome": fld.outcome_ground_out([0, 0, 0])}


def _strikeout_sequence() -> list:
    return [
        {"type": "swinging_strike"},
        {"type": "swinging_strike"},
        {"type": "swinging_strike"},
    ]


# ---------------------------------------------------------------------------
# Test 1: 27-out half with full lineup cycling
# ---------------------------------------------------------------------------

def test_27_out_half():
    print("\n[Test 1] 27-out half with full lineup cycling")
    state = _fresh_state()
    state.half = "top"

    # 27 ground outs — each ends the plate appearance immediately.
    events = [_ground_out_event() for _ in range(27)]
    provider = make_script_provider(events)
    run_half(state, provider)

    _assert("outs == 27", state.outs == 27, f"got {state.outs}")
    _assert("half is over", state.is_half_over(), "")
    # Lineup cycles: 27 outs / 9 starters = exactly 3 full cycles → back to position 0
    expected_pos = 27 % 9   # = 0
    _assert(
        f"lineup_position == {expected_pos} (3 full cycles of 9)",
        state.visitors.lineup_position == expected_pos,
        f"got {state.visitors.lineup_position}",
    )
    _assert("bases empty at end", state.bases == [None, None, None], f"got {state.bases}")


# ---------------------------------------------------------------------------
# Test 2: Multi-hit at-bat via repeated stays
# ---------------------------------------------------------------------------

def test_multi_hit_stay():
    print("\n[Test 2] Multi-hit at-bat via repeated stays")
    state = _fresh_state()
    state.half = "top"

    # Place a runner on 1B manually.
    state.bases[0] = "runner_A"

    events = [
        # Stay 1: ground ball, runner_A advances 1B→2B, batter stays (fresh count).
        {"type": "ball_in_play", "choice": "stay",
         "outcome": fld.outcome_stay_ground_ball([1, 0, 0])},
        # Stay 2: ground ball, runner_A advances 2B→3B ([0,1,0] advances the 2B runner).
        {"type": "ball_in_play", "choice": "stay",
         "outcome": fld.outcome_stay_ground_ball([0, 1, 0])},
        # Run: batter finally runs — single; runner_A on 3B advances 1 and scores.
        {"type": "ball_in_play", "choice": "run",
         "outcome": fld.outcome_single([2, 1, 1])},
    ]
    for ev in events:
        apply_event(state, ev)

    _assert("batter on 1B after running",
            state.bases[0] is not None, f"bases={state.bases}")
    # The at-bat ended (batter ran); at-bat hits were reset.
    # But we can check the runner advanced far.
    _assert("run scored (runner went from 1B → 2B → 3B → home)",
            state.score["visitors"] >= 1,
            f"score={state.score}")


def test_multi_hit_credits():
    """Verify that stay_hit credits accumulate within one at-bat."""
    print("\n[Test 2b] Stay-hit credit accumulation")
    state = _fresh_state()
    state.half = "top"
    state.bases[0] = "runner_X"
    state.bases[1] = "runner_Y"

    # Two stays in one at-bat.
    ev1 = {"type": "ball_in_play", "choice": "stay",
            "outcome": fld.outcome_stay_ground_ball([1, 1, 0])}
    apply_event(state, ev1)
    hits_after_first_stay = state.current_at_bat_hits
    _assert("1 hit credited after first stay",
            hits_after_first_stay >= 1,
            f"got {hits_after_first_stay}")

    ev2 = {"type": "ball_in_play", "choice": "stay",
            "outcome": fld.outcome_stay_ground_ball([1, 1, 0])}
    apply_event(state, ev2)
    hits_after_second_stay = state.current_at_bat_hits
    _assert("2 hits credited after second stay",
            hits_after_second_stay >= 2,
            f"got {hits_after_second_stay}")


# ---------------------------------------------------------------------------
# Test 3: Stay constraints
# ---------------------------------------------------------------------------

def test_stay_constraints():
    print("\n[Test 3] Stay constraints")

    # 3a: Two-strike contact + stay → batter out.
    print("  3a: Two-strike stay = batter out")
    state = _fresh_state()
    state.half = "top"
    state.bases[0] = "runner_A"
    state.count.strikes = 2

    outs_before = state.outs
    apply_event(state, {
        "type": "ball_in_play", "choice": "stay",
        "outcome": fld.outcome_stay_ground_ball([1, 0, 0]),
    })
    _assert("batter out on 2-strike stay",
            state.outs == outs_before + 1,
            f"outs before={outs_before}, after={state.outs}")
    _assert("count reset after 2-strike stay out",
            state.count.balls == 0 and state.count.strikes == 0,
            f"count={state.count}")

    # 3b: Caught fly ball + stay → batter out.
    print("  3b: Caught fly + stay = batter out")
    state2 = _fresh_state()
    state2.half = "top"
    state2.bases[1] = "runner_B"  # runner on 2B

    outs_before2 = state2.outs
    apply_event(state2, {
        "type": "ball_in_play", "choice": "stay",
        "outcome": fld.outcome_stay_fly_caught(),
    })
    _assert("batter out on caught fly + stay",
            state2.outs == outs_before2 + 1,
            f"outs before={outs_before2}, after={state2.outs}")

    # 3c: Bases empty → stay unavailable (treated as run).
    print("  3c: Bases empty → stay unavailable")
    state3 = _fresh_state()
    state3.half = "top"
    # Bases are empty by default.

    _assert("stay_available returns False with empty bases",
            not stay_mod.stay_available(state3), "")

    # 3d: Normal valid stay with runners on base → at-bat continues, count fresh.
    print("  3d: Valid stay → fresh count, at-bat continues")
    state4 = _fresh_state()
    state4.half = "top"
    state4.bases[0] = "runner_C"
    state4.count.balls = 2
    state4.count.strikes = 1

    batter_before = state4.current_batter.player_id
    apply_event(state4, {
        "type": "ball_in_play", "choice": "stay",
        "outcome": fld.outcome_stay_ground_ball([1, 0, 0]),
    })
    _assert("count reset to 0-0 after stay",
            state4.count.balls == 0 and state4.count.strikes == 0,
            f"count={state4.count}")
    _assert("same batter still up after stay",
            state4.current_batter.player_id == batter_before,
            f"batter changed to {state4.current_batter.player_id}")
    _assert("no out recorded on valid stay",
            state4.outs == 0, f"outs={state4.outs}")

    # 3e: Home run with stay chosen → resolver forces run (stay doesn't apply).
    #     Batter AND runner score; at-bat ends; no stay continuation.
    print("  3e: Home run + stay → forced run, both batter and runner score")
    state5 = _fresh_state()
    state5.half = "top"
    state5.bases[0] = "runner_D"   # 1 runner on 1B
    outs_before5 = state5.outs

    apply_event(state5, {
        "type": "ball_in_play", "choice": "stay",
        "outcome": fld.outcome_home_run(),
    })
    _assert("no out on HR-stay (batter scores)",
            state5.outs == outs_before5,
            f"outs before={outs_before5}, after={state5.outs}")
    _assert("batter + runner both score on HR-stay (2 runs)",
            state5.score["visitors"] == 2,
            f"score={state5.score}")
    _assert("bases clear after HR-stay",
            state5.bases == [None, None, None],
            f"bases={state5.bases}")
    _assert("should_stay heuristic returns False for home run",
            not stay_mod.should_stay(
                state5, state5.current_batter, "hard", is_hr=True),
            "")


# ---------------------------------------------------------------------------
# Test 4: Joker insertion
# ---------------------------------------------------------------------------

def test_joker_insertion():
    print("\n[Test 4] Joker insertion")
    state = _fresh_state()
    state.half = "top"
    team = state.visitors
    joker = team.jokers_available[0]   # first joker

    # 4a: Can insert joker (basic check).
    ok, reason = can_insert_joker(state, joker)
    _assert("can insert joker (first time)", ok, reason)

    # 4b: Insert the joker.
    log = insert_joker(state, joker, lineup_position=2)
    _assert("joker inserted into lineup",
            joker in team.lineup, f"lineup={[p.name for p in team.lineup]}")
    _assert("joker marked used this half",
            joker.player_id in team.jokers_used_this_half,
            f"used={team.jokers_used_this_half}")
    _assert("joker removed from available pool",
            joker not in team.jokers_available,
            f"available={[j.name for j in team.jokers_available]}")

    # 4c: Cannot insert same joker again this half.
    ok2, reason2 = can_insert_joker(state, joker)
    _assert("cannot insert same joker twice in same half",
            not ok2, reason2)

    # 4d: Cannot insert joker during super-inning.
    state_super = _fresh_state()
    state_super.half = "super_top"
    joker2 = state_super.visitors.jokers_available[0]
    ok3, reason3 = can_insert_joker(state_super, joker2)
    _assert("cannot insert joker in super-inning", not ok3, reason3)

    # 4e: Different joker can still be inserted.
    state2 = _fresh_state()
    state2.half = "top"
    team2 = state2.visitors
    j1 = team2.jokers_available[0]
    j2 = team2.jokers_available[1]
    insert_joker(state2, j1, lineup_position=0)
    ok4, reason4 = can_insert_joker(state2, j2)
    _assert("different joker can still be inserted", ok4, reason4)

    # 4f: Joker used in top half is available again for bottom half (per-half-inning rule).
    print("  4f: Joker resets after half-inning reset")
    state3 = _fresh_state()
    state3.half = "top"
    team3 = state3.visitors
    j3 = team3.jokers_available[0]
    insert_joker(state3, j3, lineup_position=0)
    _assert("joker not available mid-half after use",
            j3 not in team3.jokers_available,
            f"available={[x.name for x in team3.jokers_available]}")
    # Simulate end of half → reset for bottom half.
    team3.reset_half()
    _assert("joker available again after half reset",
            any(p.player_id == j3.player_id for p in team3.jokers_available),
            f"available={[x.name for x in team3.jokers_available]}")
    _assert("used-this-half set cleared after reset",
            len(team3.jokers_used_this_half) == 0, "")


# ---------------------------------------------------------------------------
# Test 5: Halftime transition
# ---------------------------------------------------------------------------

def test_halftime():
    print("\n[Test 5] Halftime transition with target announcement")
    state = _fresh_state()
    state.half = "top"

    # Produce some runs in top half.
    events = [
        # Walk to load 1B.
        {"type": "ball"}, {"type": "ball"}, {"type": "ball"}, {"type": "ball"},
        # Home run — 2 runs (batter + runner).
        {"type": "ball_in_play", "choice": "run",
         "outcome": fld.outcome_home_run()},
        # Fill remaining 27 outs.
    ] + [_ground_out_event() for _ in range(27)]

    provider = make_script_provider(events)
    run_half(state, provider)

    top_score = state.score["visitors"]
    _assert("visitors scored runs in top half", top_score >= 2, f"score={top_score}")

    # Run halftime.
    ht_log = halftime(state)
    _assert("target_score set to visitors' score",
            state.target_score == top_score,
            f"target={state.target_score}, score={top_score}")
    _assert("halftime log references target",
            any(str(top_score) in line for line in ht_log),
            f"log={ht_log}")

    # Verify required run rate appears in log.
    required_rr = (top_score + 1) / 27
    _assert("halftime log mentions required run rate",
            any("required run rate" in line.lower() for line in ht_log),
            "")


# ---------------------------------------------------------------------------
# Test 6: Super-inning trigger and resolution
# ---------------------------------------------------------------------------

def test_super_inning():
    print("\n[Test 6] Super-inning trigger and resolution")

    # 6a: Tied game → no winner yet.
    state = _fresh_state()
    state.score["visitors"] = 5
    state.score["home"] = 5
    winner = check_winner(state)
    _assert("tied game has no winner", winner is None, f"got {winner}")

    # 6b: Visitors ahead → visitors win.
    state2 = _fresh_state()
    state2.score["visitors"] = 7
    state2.score["home"] = 5
    _assert("visitors win if ahead", check_winner(state2) == "visitors", "")

    # 6c: Home ahead → home wins.
    state3 = _fresh_state()
    state3.score["visitors"] = 3
    state3.score["home"] = 9
    _assert("home wins if ahead", check_winner(state3) == "home", "")

    # 6d: Full game tied after 27 outs each, resolved by super-inning.
    # Super-top (visitors): 5 strikeouts → 5 dismissals, 0 runs.
    # Super-bottom (home):  1 solo HR (batter scores, not dismissed) then 5 strikeouts.
    #   Lineup cycles through h2,h3,h4,h5 (4 SO), then h1 returns and also SO → 5 dismissals.
    #   Total: 1 HR + 5 SOs = home wins 1-0 in the super-inning.
    print("  6d: Full game with super-inning resolution")
    state4 = _fresh_state()

    top_events    = [_ground_out_event() for _ in range(27)]
    bottom_events = [_ground_out_event() for _ in range(27)]

    # Super-top: 5 strikeouts gives exactly 5 dismissals → half over.
    super_top_events = [ev for _ in range(5) for ev in _strikeout_sequence()]

    # Super-bottom: HR by batter-0 (scores, not dismissed), then batters 1-4 SO,
    # then batter-0 cycles back and SO (5th dismissal) → half over with 1 run for home.
    super_bottom_events = (
        [{"type": "ball_in_play", "choice": "run",
          "outcome": fld.outcome_home_run()}]          # batter-0 scores solo HR
        + [ev for _ in range(5) for ev in _strikeout_sequence()]  # batters 1-4, then batter-0
    )

    all_events = top_events + bottom_events + super_top_events + super_bottom_events
    provider = make_script_provider(all_events)

    def super_selector(st, team_id):
        team = st.visitors if team_id == "visitors" else st.home
        return team.roster[:5]

    final_state, log = run_game(state4, provider, super_selector=super_selector)

    _assert("game has a winner after super-inning",
            final_state.winner is not None,
            f"winner={final_state.winner}")
    _assert("home wins super-inning (1 > 0 runs)",
            final_state.winner == "home",
            f"winner={final_state.winner}, score={final_state.score}")
    _assert("super_inning_number >= 1",
            final_state.super_inning_number >= 1,
            f"super_inning_number={final_state.super_inning_number}")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 7: Package importability
# ---------------------------------------------------------------------------

def test_package_imports():
    print("\n[Test 7] Package importability")
    import importlib
    for mod_path in [
        "o27",
        "o27.engine",
        "o27.engine.state",
        "o27.engine.game",
        "o27.engine.pa",
        "o27.engine.stay",
        "o27.engine.manager",
        "o27.engine.fielding",
        "o27.engine.baserunning",
    ]:
        try:
            importlib.import_module(mod_path)
            _assert(f"import {mod_path}", True)
        except ImportError as exc:
            _assert(f"import {mod_path}", False, str(exc))


# ---------------------------------------------------------------------------
# Test 8: Pitcher state across halftime
# ---------------------------------------------------------------------------

def test_pitcher_across_halftime():
    print("\n[Test 8] Pitcher state across halftime")

    # Build a state manually (like run_game does) with both halves.
    from o27.engine.game import _set_fielding_pitcher

    state = _fresh_state()

    # Top half: home team pitches.
    state.half = "top"
    _assert("pitcher valid in top half (home pitches)",
            state.get_current_pitcher() is not None,
            f"pitcher={state.get_current_pitcher()}")
    _assert("top-half pitcher belongs to home roster",
            any(p.player_id == state.current_pitcher_id
                for p in state.home.roster),
            f"pitcher_id={state.current_pitcher_id}")

    # Transition to bottom half (visitors pitch).
    state.half = "bottom"
    _set_fielding_pitcher(state)
    _assert("pitcher valid in bottom half (visitors pitch)",
            state.get_current_pitcher() is not None,
            f"pitcher={state.get_current_pitcher()}")
    _assert("bottom-half pitcher belongs to visitors roster",
            any(p.player_id == state.current_pitcher_id
                for p in state.visitors.roster),
            f"pitcher_id={state.current_pitcher_id}")

    # Verify pitcher is the is_pitcher=True player.
    bottom_pitcher = state.get_current_pitcher()
    _assert("bottom-half pitcher has is_pitcher=True",
            bottom_pitcher.is_pitcher,
            f"is_pitcher={bottom_pitcher.is_pitcher}")


def run_all():
    print("=" * 60)
    print("O27 Phase 1 Rule Verification Tests")
    print("=" * 60)

    test_27_out_half()
    test_multi_hit_stay()
    test_multi_hit_credits()
    test_stay_constraints()
    test_joker_insertion()
    test_halftime()
    test_super_inning()
    test_package_imports()
    test_pitcher_across_halftime()

    print("\n" + "=" * 60)
    passes = sum(1 for _, s, _ in _results if s == PASS)
    fails = sum(1 for _, s, _ in _results if s == FAIL)
    total = len(_results)
    print(f"Results: {passes}/{total} passed, {fails} failed.")
    if fails:
        print("\nFailed tests:")
        for name, status, detail in _results:
            if status == FAIL:
                print(f"  ✗ {name}: {detail}")
    print("=" * 60)
    return fails == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
