"""Sim-batch validator for the substitution-economy subsystem.

Runs N games end-to-end with the new 42-player rosters, then reports:
  - Roster shape (counts per roster_slot)
  - Substitution volume per game (position-player + pitching)
  - One-way invariant: no replaced player ever re-enters

Used as part of the Item 1-3 AAR to confirm the mechanic actually fires
in live games rather than being a dead code path.
"""
from __future__ import annotations

import random
import sys
from collections import Counter

from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.engine.state import GameState, Player, Team
from o27 import config as ecfg
from o27.render.render import Renderer
from o27v2 import scout as _scout
from o27v2.league import generate_players


def _hydrate(p_dict: dict, role: str) -> Player:
    pid = f"{role}_{p_dict['name'].replace(' ', '_')}_{id(p_dict)}"
    player = Player(
        player_id=pid,
        name=p_dict["name"],
        is_pitcher=bool(p_dict["is_pitcher"]),
        skill=_scout.to_unit(p_dict["skill"]),
        speed=_scout.to_unit(p_dict["speed"]),
        pitcher_skill=_scout.to_unit(p_dict["pitcher_skill"]),
        stamina=_scout.to_unit(p_dict.get("stamina") or p_dict["pitcher_skill"]),
        stay_aggressiveness=float(p_dict["stay_aggressiveness"]),
        contact_quality_threshold=float(p_dict["contact_quality_threshold"]),
        archetype=str(p_dict.get("archetype") or ""),
        contact=_scout.to_unit(p_dict.get("contact") or 50),
        power=_scout.to_unit(p_dict.get("power") or 50),
        eye=_scout.to_unit(p_dict.get("eye") or 50),
        command=_scout.to_unit(p_dict.get("command") or 50),
        movement=_scout.to_unit(p_dict.get("movement") or 50),
        bats=str(p_dict.get("bats") or "R"),
        throws=str(p_dict.get("throws") or "R"),
        defense=_scout.to_unit(p_dict.get("defense") or 50),
        arm=_scout.to_unit(p_dict.get("arm") or 50),
        defense_infield=_scout.to_unit(p_dict.get("defense_infield") or 50),
        defense_outfield=_scout.to_unit(p_dict.get("defense_outfield") or 50),
        defense_catcher=_scout.to_unit(p_dict.get("defense_catcher") or 50),
        baserunning=_scout.to_unit(p_dict.get("baserunning") or 50),
        position=str(p_dict.get("position") or ""),
        roster_slot=str(p_dict.get("roster_slot") or ""),
        role_hit=bool(p_dict.get("role_hit", 1)),
        role_run=bool(p_dict.get("role_run", 0)),
        role_two_way=bool(p_dict.get("role_two_way", 1)),
        role_field_pos=str(p_dict.get("role_field_pos") or ""),
    )
    return player


def _make_team(role: str, name: str, players: list[dict],
               manager_platoon_aggression: float = 0.5) -> Team:
    actives = [p for p in players if p.get("is_active", 1)]
    hydrated = [_hydrate(p, role) for p in actives]
    # Starting nine: 8 canonical fielders + the SP. Heuristic — pick the
    # first non-pitcher at each position + the first pitcher.
    by_pos: dict[str, Player] = {}
    starting: list[Player] = []
    for player in hydrated:
        pos = player.position
        if not player.is_pitcher and pos in (
            "CF", "SS", "2B", "3B", "RF", "LF", "1B", "C"
        ) and pos not in by_pos:
            by_pos[pos] = player
            starting.append(player)
    # Pick the first pitcher as SP and put in lineup slot 9.
    sp = next((p for p in hydrated if p.is_pitcher), None)
    if sp:
        starting.append(sp)
    team = Team(
        team_id=role, name=name,
        roster=hydrated,
        lineup=starting,
    )
    team.mgr_platoon_aggression = manager_platoon_aggression
    team.mgr_bench_usage = 0.6
    team.mgr_pinch_hit_aggression = 0.6
    team.mgr_quick_hook = 0.5
    team.mgr_bullpen_aggression = 0.5
    team.mgr_run_game = 0.5
    team.mgr_leverage_aware = 0.5
    team.mgr_joker_aggression = 0.5
    # Bench: all active non-pitcher non-lineup.
    starting_ids = {p.player_id for p in starting}
    team.bench = [
        p for p in hydrated
        if not p.is_pitcher and p.player_id not in starting_ids
    ]
    # Jokers: best 3 bench bats by skill.
    bench_sorted = sorted(team.bench, key=lambda p: -p.skill)
    team.jokers_available = bench_sorted[:3]
    return team


def run_batch(n_games: int = 10, base_seed: int = 1000) -> dict:
    sub_volumes_per_team = []
    pitching_changes_per_team = []
    oneway_violations = 0
    roster_slot_counts: Counter = Counter()
    total_players_seeded = 0

    for game_i in range(n_games):
        seed = base_seed + game_i
        rng = random.Random(seed)
        v_players = generate_players(0, random.Random(seed))
        h_players = generate_players(1, random.Random(seed + 7))

        # Tally roster slots from the freshly-generated rosters.
        for plist in (v_players, h_players):
            for p in plist:
                if p.get("is_active"):
                    roster_slot_counts[p.get("roster_slot", "")] += 1
                    total_players_seeded += 1

        visitors = _make_team("visitors", "Visitors", v_players,
                              manager_platoon_aggression=0.7)
        home     = _make_team("home",     "Home",     h_players,
                              manager_platoon_aggression=0.7)

        state = GameState(visitors=visitors, home=home)
        # Stamp the SP as the starting pitcher.
        for p in home.roster:
            if p.is_pitcher:
                state.current_pitcher_id = p.player_id
                break

        renderer = Renderer()
        provider = ProbabilisticProvider(rng)

        final_state, _log = run_game(state, provider, renderer)

        # Tally substitutions per team.
        v_subs = [r for r in final_state.substitution_log
                  if r.team_id == "visitors" and r.kind != "pitching"]
        h_subs = [r for r in final_state.substitution_log
                  if r.team_id == "home" and r.kind != "pitching"]
        sub_volumes_per_team.extend([len(v_subs), len(h_subs)])

        v_pc = [r for r in final_state.substitution_log
                if r.team_id == "visitors" and r.kind == "pitching"]
        h_pc = [r for r in final_state.substitution_log
                if r.team_id == "home" and r.kind == "pitching"]
        pitching_changes_per_team.extend([len(v_pc), len(h_pc)])

        # One-way invariant: walk the log, look for re-entry.
        seen_out: dict[str, set] = {"visitors": set(), "home": set()}
        for rec in final_state.substitution_log:
            team_set = seen_out[rec.team_id]
            if rec.in_player_id in team_set:
                oneway_violations += 1
            team_set.add(rec.out_player_id)

    return {
        "n_games": n_games,
        "roster_slot_counts": dict(roster_slot_counts),
        "active_players_per_team_avg":
            total_players_seeded / (n_games * 2) if n_games else 0,
        "sub_volume_per_team_avg":
            sum(sub_volumes_per_team) / len(sub_volumes_per_team) if sub_volumes_per_team else 0,
        "sub_volume_per_team_max": max(sub_volumes_per_team) if sub_volumes_per_team else 0,
        "pitching_changes_per_team_avg":
            sum(pitching_changes_per_team) / len(pitching_changes_per_team) if pitching_changes_per_team else 0,
        "oneway_violations": oneway_violations,
    }


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    out = run_batch(n_games=n)
    print(f"Sub-economy validation batch — {out['n_games']} games:")
    print(f"  Avg active players / team:    {out['active_players_per_team_avg']:.1f}")
    print(f"  Roster slot mix per league (active only):")
    for slot, count in sorted(out['roster_slot_counts'].items()):
        per_team = count / (out['n_games'] * 2)
        print(f"    {slot:18}  total={count:4}   per_team={per_team:.1f}")
    print(f"  Sub volume (position) / team / game:")
    print(f"    avg = {out['sub_volume_per_team_avg']:.2f}")
    print(f"    max = {out['sub_volume_per_team_max']}")
    print(f"  Pitching changes / team / game:")
    print(f"    avg = {out['pitching_changes_per_team_avg']:.2f}")
    print(f"  One-way violations:           {out['oneway_violations']}")
