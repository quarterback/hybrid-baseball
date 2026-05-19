"""Sim-batch validator for the substitution-economy subsystem.

Runs N games end-to-end with the new 42-player rosters, then reports:
  - Roster shape (counts per roster_slot)
  - Substitution volume per game (position-player + pitching)
  - One-way invariant: no replaced player ever re-enters

The point is to show *variety* across manager personas — substitutions
are situational, not goal-driven. A platoon_manager should cycle the
bench every PA worth swapping into; a dead_ball traditionalist should
basically never substitute. There is no "target subs/team/game" — what
matters is the spread, not the league average.

Run with `python scripts/validate_substitution_economy.py 30` for the
default 30-game per-manager-aggression batch (covers passive through
aggressive personas).
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
    # Starting fielders: 8 canonical positions, prefer non-jokers /
    # non-specialists. Pick the first eligible at each position.
    by_pos: dict[str, Player] = {}
    fielders: list[Player] = []
    for player in hydrated:
        if player.is_pitcher:
            continue
        if player.roster_slot in ("joker", "pr_specialist", "ph_specialist"):
            continue
        pos = player.position
        if pos in ("CF", "SS", "2B", "3B", "RF", "LF", "1B", "C") and pos not in by_pos:
            by_pos[pos] = player
            fielders.append(player)
    # SP bats 9th — pitcher is an explicit part of O27's batting order.
    sp = next((p for p in hydrated if p.is_pitcher), None)
    starting = list(fielders)
    if sp:
        starting.append(sp)
    # Jokers: 3 players with roster_slot=="joker" — a FIXED tactical
    # pool, NOT in the batting order. Can be inserted via
    # batter_override (existing joker mechanic) but cannot be subbed
    # out of the pool.
    jokers = [p for p in hydrated if p.roster_slot == "joker"][:3]
    for j in jokers:
        j.game_position = "J"
    team = Team(
        team_id=role, name=name,
        roster=hydrated,
        lineup=starting,
        jokers_available=list(jokers),
    )
    team.mgr_platoon_aggression = manager_platoon_aggression
    team.mgr_bench_usage = 0.6
    team.mgr_pinch_hit_aggression = 0.6
    team.mgr_quick_hook = 0.5
    team.mgr_bullpen_aggression = 0.5
    team.mgr_run_game = 0.5
    team.mgr_leverage_aware = 0.5
    team.mgr_joker_aggression = 0.5
    # Bench: all active non-pitcher non-lineup non-joker.
    lineup_ids = {p.player_id for p in starting}
    joker_ids  = {j.player_id for j in jokers}
    team.bench = [
        p for p in hydrated
        if not p.is_pitcher
        and p.player_id not in lineup_ids
        and p.player_id not in joker_ids
    ]
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

        # Sample realistic per-team aggression — drawn from the same
        # distribution that league seeding uses (managers.py archetypes
        # span 0.05–0.92 across the league). The "neutral baseline" of
        # 0.5 produces the brief's ≥3 subs/team/game target.
        v_agg = random.Random(seed + 100).uniform(0.30, 0.75)
        h_agg = random.Random(seed + 200).uniform(0.30, 0.75)
        visitors = _make_team("visitors", "Visitors", v_players,
                              manager_platoon_aggression=v_agg)
        home     = _make_team("home",     "Home",     h_players,
                              manager_platoon_aggression=h_agg)

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
                # Dump enough to diagnose.
                import sys
                print(f"  [violation] game seed={seed} team={rec.team_id} "
                      f"kind={rec.kind} in={rec.in_player_id} "
                      f"prior_outs={team_set}", file=sys.stderr)
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


def per_archetype_batch(n_games: int = 20) -> dict:
    """Run a fixed batch at each of five aggression levels spanning the
    persona ladder. Returns a dict mapping aggression → avg subs.

    This is the *variety* report: substitutions are situational, not
    goal-driven, so the right success measure is the spread across
    managers — not any single league average.
    """
    import random as _r
    from o27.engine.state import GameState as _GameState
    from o27.engine.game import run_game as _run_game
    from o27.engine.prob import ProbabilisticProvider as _Provider
    from o27.render.render import Renderer as _Renderer
    from o27v2.league import generate_players as _gen

    results: dict = {}
    for agg in (0.05, 0.25, 0.50, 0.70, 0.92):
        volumes = []
        for game_i in range(n_games):
            seed = 7000 + game_i
            rng = _r.Random(seed)
            v_players = _gen(0, _r.Random(seed))
            h_players = _gen(1, _r.Random(seed + 7))
            visitors = _make_team("visitors", "V", v_players,
                                  manager_platoon_aggression=agg)
            home     = _make_team("home",     "H", h_players,
                                  manager_platoon_aggression=agg)
            state = _GameState(visitors=visitors, home=home)
            for p in home.roster:
                if p.is_pitcher:
                    state.current_pitcher_id = p.player_id
                    break
            renderer = _Renderer()
            provider = _Provider(rng)
            final_state, _ = _run_game(state, provider, renderer)
            v_subs = sum(1 for r in final_state.substitution_log
                         if r.team_id == "visitors" and r.kind != "pitching")
            h_subs = sum(1 for r in final_state.substitution_log
                         if r.team_id == "home" and r.kind != "pitching")
            volumes.extend([v_subs, h_subs])
        results[agg] = sum(volumes) / len(volumes) if volumes else 0
    return results


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    out = run_batch(n_games=n)
    print(f"Sub-economy validation batch — {out['n_games']} games:")
    print(f"  Avg active players / team:    {out['active_players_per_team_avg']:.1f}")
    print(f"  Roster slot mix per league (active only):")
    for slot, count in sorted(out['roster_slot_counts'].items()):
        per_team = count / (out['n_games'] * 2)
        print(f"    {slot:18}  total={count:4}   per_team={per_team:.1f}")
    print(f"  Sub volume (position) / team / game (mixed-archetype league):")
    print(f"    avg = {out['sub_volume_per_team_avg']:.2f}")
    print(f"    max = {out['sub_volume_per_team_max']}")
    print(f"  Pitching changes / team / game:")
    print(f"    avg = {out['pitching_changes_per_team_avg']:.2f}")
    print(f"  One-way violations:           {out['oneway_violations']}")
    print()
    print(f"Per-archetype variety report ({n}-game batches at fixed aggression):")
    print(f"  Substitutions / team / game by manager personality —")
    per_arch = per_archetype_batch(n_games=max(n, 20))
    for agg, avg_subs in sorted(per_arch.items()):
        label = {
            0.05: "dead_ball / iron",
            0.25: "small_ball",
            0.50: "balanced",
            0.70: "modern / bullpen_innovator",
            0.92: "platoon_manager",
        }.get(agg, "")
        print(f"    platoon_aggression={agg:.2f}  ({label:30}) avg_subs={avg_subs:5.2f}")
