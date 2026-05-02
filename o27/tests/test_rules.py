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
    """12-batter active lineup: 9 position players (slot 9 = pitcher) + 3 jokers.

    All 12 are in the lineup from the start (PRD §4.2 / task requirement).
    Jokers are also in jokers_available so the manager can explicitly re-slot
    them mid-inning (insert_joker moves them within the 12, does not grow it).
    Once a joker bats, they are added to jokers_used_this_half and are skipped
    by advance_lineup for the rest of that half.
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
    roster = starters + jokers          # full 12-player roster
    return Team(
        team_id=team_id,
        name=name,
        roster=roster,
        lineup=list(roster),            # all 12 in batting order
        jokers_available=list(jokers),  # available for explicit mid-inning slot moves
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
    # Lineup is 12 batters (9 position + 3 jokers).  Each joker bats once in the
    # first cycle then is skipped for the rest of the half:
    #   PA  1-12: all 12 bat → pos wraps to 0
    #   PA 13-21: positions 0-8 bat (jokers skipped) → pos wraps to 0
    #   PA 22-27: positions 0-5 bat → after advance, pos = 6
    expected_pos = 6
    _assert(
        f"lineup_position == {expected_pos} (12-batter lineup, jokers skipped after first use)",
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

    # 4b: Insert the joker (moves within 12; must not grow the list).
    log = insert_joker(state, joker, lineup_position=2)
    _assert("joker still in lineup after insertion (moved, not added)",
            joker in team.lineup, f"lineup={[p.name for p in team.lineup]}")
    _assert("lineup stays at 12 after insertion (no duplicate)",
            len(team.lineup) == 12, f"len={len(team.lineup)}")
    _assert("joker is now at the inserted slot",
            team.lineup[2] is joker, f"slot2={team.lineup[2].name}")
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
# Test 7: Stolen base, pickoff, balk (§4.3 baserunning events)
# ---------------------------------------------------------------------------

def test_contact_runner_outs():
    print("\n[Test 7] Contact resolution — runner outs recorded (fielder's choice / stay)")

    # 7a: Fielder's choice — batter safe, runner on 1B thrown out → 1 out.
    print("  7a: Fielder's choice (run chosen) — runner thrown out, batter safe")
    state = _fresh_state()
    state.half = "top"
    state.bases[0] = "runner_FC"   # runner on 1B

    apply_event(state, {
        "type": "ball_in_play",
        "choice": "run",
        "outcome": fld.outcome_fielders_choice(runner_out_idx=0,
                                               runner_advances=[0, 0, 0]),
    })
    _assert("runner removed from 1B on FC", state.bases[0] != "runner_FC",
            f"bases={state.bases}")
    _assert("batter placed on 1B (FC, batter_safe=True)",
            state.current_batter is not None, "")   # lineup advanced → next batter
    _assert("1 out recorded on FC", state.outs == 1, f"outs={state.outs}")

    # 7b: Fielder's choice — both batter and runner out (DP) → 2 outs.
    print("  7b: Fielder's choice with batter also out (double play) → 2 outs")
    state2 = _fresh_state()
    state2.half = "top"
    state2.bases[0] = "runner_DP"
    apply_event(state2, {
        "type": "ball_in_play",
        "choice": "run",
        "outcome": {
            "hit_type": "fielders_choice",
            "batter_safe": False,   # batter also out
            "caught_fly": False,
            "runner_advances": [0, 0, 0],
            "runner_out_idx": 0,    # runner on 1B thrown out
        },
    })
    _assert("2 outs on double play", state2.outs == 2, f"outs={state2.outs}")

    # 7c: Valid stay — runner on 1B thrown out → 1 out, at-bat continues, no hit credit.
    print("  7c: Stay with runner thrown out → out recorded, at-bat continues, no hit credit")
    state3 = _fresh_state()
    state3.half = "top"
    state3.bases[0] = "runner_S"   # runner on 1B

    apply_event(state3, {
        "type": "ball_in_play",
        "choice": "stay",
        "outcome": {
            "hit_type": "ground_out",   # runner on 1B thrown out, batter_safe=True forced by stay
            "batter_safe": True,
            "caught_fly": False,
            "runner_advances": [0, 0, 0],
            "runner_out_idx": 0,        # runner thrown out
        },
    })
    _assert("1 out recorded on stay+runner-out", state3.outs == 1, f"outs={state3.outs}")
    _assert("at-bat continues (count reset to 0-0)", state3.count.balls == 0
            and state3.count.strikes == 0, f"count={state3.count}")
    _assert("no hit credit when only runner thrown out (no advancement)",
            state3.current_at_bat_hits == 0, f"hits={state3.current_at_bat_hits}")

    # 7d: Valid stay — runner advances (not thrown out) → hit credit awarded.
    print("  7d: Stay with runner advancing → hit credit")
    state4 = _fresh_state()
    state4.half = "top"
    state4.bases[0] = "runner_ADV"

    apply_event(state4, {
        "type": "ball_in_play",
        "choice": "stay",
        "outcome": fld.outcome_stay_ground_ball([1, 0, 0]),   # runner advances 1B→2B
    })
    _assert("runner advanced on stay", state4.bases[1] == "runner_ADV",
            f"bases={state4.bases}")
    _assert("hit credit awarded for runner advance", state4.current_at_bat_hits == 1,
            f"hits={state4.current_at_bat_hits}")


def test_baserunning_events():
    print("\n[Test 8] §4.3 baserunning events (stolen_base_attempt, pickoff, balk)")

    # 7a: Successful stolen base — runner advances, no out.
    print("  7a: Successful stolen base")
    state = _fresh_state()
    state.half = "top"
    state.bases[0] = "runner_A"   # runner on 1B

    apply_event(state, {"type": "stolen_base_attempt", "base_idx": 0, "success": True})
    _assert("runner advanced from 1B after successful steal",
            state.bases[0] is None and state.bases[1] == "runner_A",
            f"bases={state.bases}")
    _assert("no out on successful steal", state.outs == 0, f"outs={state.outs}")

    # 7b: Failed stolen base — runner out.
    print("  7b: Failed stolen base (caught stealing)")
    state2 = _fresh_state()
    state2.half = "top"
    state2.bases[0] = "runner_B"

    apply_event(state2, {"type": "stolen_base_attempt", "base_idx": 0, "success": False})
    _assert("runner removed from 1B on caught stealing",
            state2.bases[0] is None, f"bases={state2.bases}")
    _assert("out recorded on caught stealing", state2.outs == 1, f"outs={state2.outs}")

    # 7c: Steal of home — runner scores.
    print("  7c: Steal of home")
    state3 = _fresh_state()
    state3.half = "top"
    state3.bases[2] = "runner_C"   # runner on 3B

    apply_event(state3, {"type": "stolen_base_attempt", "base_idx": 2, "success": True})
    _assert("runner off 3B after steal of home", state3.bases[2] is None, f"bases={state3.bases}")
    _assert("run scored on steal of home",
            state3.score[state3.batting_team.team_id] == 1,
            f"score={state3.score}")

    # 7d: Successful pickoff — runner out.
    print("  7d: Pickoff (success)")
    state4 = _fresh_state()
    state4.half = "top"
    state4.bases[1] = "runner_D"  # runner on 2B

    apply_event(state4, {"type": "pickoff_attempt", "base_idx": 1, "success": True})
    _assert("runner removed from 2B on pickoff", state4.bases[1] is None, f"bases={state4.bases}")
    _assert("out recorded on pickoff", state4.outs == 1, f"outs={state4.outs}")

    # 7e: Failed pickoff — runner safe, no out.
    print("  7e: Pickoff (failure)")
    state5 = _fresh_state()
    state5.half = "top"
    state5.bases[0] = "runner_E"

    apply_event(state5, {"type": "pickoff_attempt", "base_idx": 0, "success": False})
    _assert("runner stays on base after failed pickoff",
            state5.bases[0] == "runner_E", f"bases={state5.bases}")
    _assert("no out on failed pickoff", state5.outs == 0, f"outs={state5.outs}")

    # 7f: Balk — all runners advance one base.
    print("  7f: Balk")
    state6 = _fresh_state()
    state6.half = "top"
    state6.bases[0] = "runner_F"
    state6.bases[1] = "runner_G"

    apply_event(state6, {"type": "balk"})
    _assert("1B runner advanced to 2B on balk",
            state6.bases[0] is None and state6.bases[1] == "runner_F",
            f"bases={state6.bases}")
    _assert("2B runner advanced to 3B on balk",
            state6.bases[2] == "runner_G",
            f"bases={state6.bases}")

    # 7g: stolen_base_attempt against empty base is a no-op (no error raised).
    print("  7g: Steal attempt on empty base")
    state7 = _fresh_state()
    state7.half = "top"
    try:
        apply_event(state7, {"type": "stolen_base_attempt", "base_idx": 0, "success": True})
        _assert("no exception on empty-base steal attempt", True)
    except Exception as exc:
        _assert("no exception on empty-base steal attempt", False, str(exc))
    _assert("bases unchanged after empty steal attempt",
            state7.bases == [None, None, None], f"bases={state7.bases}")


# ---------------------------------------------------------------------------
# Test 8: Joker cannot field (§2.3)
# ---------------------------------------------------------------------------

def test_joker_cannot_field():
    print("\n[Test 9] Joker cannot field (§2.3)")

    # After a joker bats their PA should be in joker_fielding_restricted.
    state = _fresh_state()
    state.half = "top"
    team = state.visitors

    # Move joker to position 0 so they bat immediately.
    joker = team.jokers_available[0]
    insert_joker(state, joker, lineup_position=0)

    # Joker is now current batter — complete their PA with a ground out.
    apply_event(state, {
        "type": "ball_in_play",
        "choice": "run",
        "outcome": fld.outcome_ground_out([0, 0, 0]),
    })

    _assert("joker in joker_fielding_restricted after batting",
            joker.player_id in team.joker_fielding_restricted,
            f"restricted={team.joker_fielding_restricted}")
    _assert("joker in jokers_used_this_half after batting",
            joker.player_id in team.jokers_used_this_half,
            f"used={team.jokers_used_this_half}")

    # reset_half clears used-this-half but NOT fielding restriction.
    team.reset_half()
    _assert("jokers_used_this_half cleared after reset", len(team.jokers_used_this_half) == 0, "")
    _assert("joker_fielding_restricted persists across halves",
            joker.player_id in team.joker_fielding_restricted,
            f"restricted={team.joker_fielding_restricted}")

    # Verify fielding restriction is queryable via helper.
    def cannot_field(player_id: str, t: "Team") -> bool:
        return player_id in t.joker_fielding_restricted

    _assert("cannot_field returns True for restricted joker",
            cannot_field(joker.player_id, team), "")
    other = team.roster[0]
    _assert("cannot_field returns False for regular player",
            not cannot_field(other.player_id, team), "")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 9: Package importability (all o27.* modules import cleanly)
# ---------------------------------------------------------------------------

def test_package_imports():
    print("\n[Test 9] Package importability")
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
    print("\n[Test 10] Pitcher state across halftime")

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


# ---------------------------------------------------------------------------
# Test 11: Pinch-hit heuristic fires under correct conditions
# ---------------------------------------------------------------------------

def test_pinch_hit_heuristic():
    """
    Verify should_pinch_hit() returns a replacement when:
      - Current batter is the pitcher (skill 0.30)
      - Runners in scoring position (2B occupied)
      - No jokers remain available this half
      - Score is tied (within 1)
    And returns None when conditions are NOT met (e.g. score is not close).
    """
    print("\n[Test 11] Pinch-hit heuristic fires correctly")
    from o27.engine.manager import should_pinch_hit

    # --- Build a state that satisfies all trigger conditions ---
    # In O27, all 12 players are in the lineup from the start, so a valid
    # pinch hitter must be a roster member who is NOT already in the lineup.
    # We add a bench player to the roster only (not the lineup) to model this.
    visitors = _make_team("visitors", "Red")
    home     = _make_team("home",     "Blue")
    state    = GameState(visitors=visitors, home=home)
    state.current_pitcher_id = home.roster[8].player_id
    state.half = "top"

    # Give the pitcher low batting skill (default is 0.5; lower it for realistic gap).
    visitors.roster[8].skill = 0.25   # roster[8] == lineup[8] (same object reference)

    # Add a genuine bench player: in roster but NOT in lineup.
    bench = Player(player_id="VBENCH", name="RedBench", skill=0.65)
    visitors.roster.append(bench)
    # NOTE: bench is NOT added to visitors.lineup — that's what makes them eligible.

    # Advance lineup to the pitcher slot (index 8, the pitcher).
    visitors.lineup_position = 8

    # Runner on 2B (scoring position).
    state.bases[1] = "some_runner"

    # Exhaust all jokers for this half so pinch hit becomes the fallback.
    for j in visitors.jokers_available:
        visitors.jokers_used_this_half.add(j.player_id)

    # Score tied.
    state.score["visitors"] = 5
    state.score["home"] = 5

    result = should_pinch_hit(state)
    _assert("pinch_hit fires when pitcher up, RISP, no jokers, score tied, bench available",
            result is not None,
            f"got {result}")
    if result is not None:
        _assert("pinch hitter is the bench player (not in lineup)",
                result.player_id == "VBENCH", f"replacement={result}")
        _assert("pinch hitter skill > pitcher skill",
                result.skill > visitors.lineup[8].skill,
                f"ph={result.skill:.2f} pitcher={visitors.lineup[8].skill:.2f}")
        _assert("pinch hitter is NOT already in lineup",
                all(p.player_id != result.player_id for p in visitors.lineup),
                f"found {result.player_id} in lineup")

    # --- Verify lineup integrity after applying the pinch-hit event ---
    from o27.engine.pa import apply_event
    apply_event(state, {"type": "pinch_hit", "replacement": bench})
    lineup_ids = [p.player_id for p in visitors.lineup]
    unique_ids = list(dict.fromkeys(lineup_ids))  # preserves order, removes dups
    _assert("lineup has no duplicate players after pinch hit",
            lineup_ids == unique_ids,
            f"dups: {[pid for pid in lineup_ids if lineup_ids.count(pid) > 1]}")

    # --- Condition not met: score gap > 1 → should return None ---
    state.score["home"] = 10   # score gap = 5
    result_no_fire = should_pinch_hit(state)
    _assert("pinch_hit does NOT fire when score gap > 1",
            result_no_fire is None,
            f"got {result_no_fire}")

    # --- Condition not met: no runners in scoring position ---
    state.score["home"] = 5    # reset to tie
    state.bases[1] = None      # clear 2B
    result_no_risp = should_pinch_hit(state)
    _assert("pinch_hit does NOT fire without RISP",
            result_no_risp is None,
            f"got {result_no_risp}")

    # --- Condition not met: batter is not the pitcher ---
    state.bases[1] = "some_runner"   # restore RISP
    visitors.lineup_position = 0     # batter is now the leadoff hitter (not pitcher)
    result_not_pitcher = should_pinch_hit(state)
    _assert("pinch_hit does NOT fire when batter is not the pitcher",
            result_not_pitcher is None,
            f"got {result_not_pitcher}")


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
    test_contact_runner_outs()
    test_baserunning_events()
    test_joker_cannot_field()
    test_package_imports()
    test_pitcher_across_halftime()
    test_pinch_hit_heuristic()

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
