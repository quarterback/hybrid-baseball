"""In-game injuries — forced mid-game substitutions.

A participant can get hurt during a plate appearance and be removed,
forcing an immediate replacement regardless of the manager's tactical
preferences. This is the one legitimate reason a sub fires before the
starting lineup has cycled, so it bypasses the first-cycle gate in
manager.py (it routes through the executors directly, not the should_*
deciders).

Scope split (deliberate, to respect the o27 -> o27v2 dependency direction):
  * The ENGINE decides WHO gets hurt and forces the swap, recording a row
    on `state.in_game_injuries`.
  * SEVERITY (DTD / short / long IL, return date) is drawn POST-GAME by
    o27v2.injuries from that record, where the IL system already lives.

The roll runs once per PA (gated by the provider's per-PA flag), before the
manager's tactical decisions. Probabilities are per-PA and tuned for
"moderate" volume (bench/bullpen depth matters). The pitcher's risk scales
with how far he is past his Stamina fatigue threshold — the workhorse-arc
fatigue tax — mirroring the threshold used in prob.py's fatigue model.
"""
from __future__ import annotations

from typing import Optional

from .. import config as cfg
from . import manager as mgr
from .state import GameState, Player


def _fatigue_over(pitcher: Player, spell_count: int) -> int:
    """Batters faced past the pitcher's Stamina fatigue threshold (0 if
    still inside his moat). Mirrors prob.py's fatigue_threshold formula."""
    stamina = float(getattr(pitcher, "stamina", 0.5) or 0.5)
    threshold = max(
        cfg.FATIGUE_THRESHOLD_BASE,
        cfg.FATIGUE_THRESHOLD_BASE + round((stamina ** 2) * cfg.FATIGUE_THRESHOLD_SCALE),
    )
    return max(0, int(spell_count) - threshold)


def _durability_mult(player: Player) -> float:
    """Per-player injury-proneness: older and lower-grit players get hurt
    more. Identity at age <= 32 and grit == 0.5."""
    m = 1.0
    age = int(getattr(player, "age", 27) or 27)
    if age >= 33:
        m += 0.05 * (age - 32)
    grit = float(getattr(player, "grit", 0.5) or 0.5)
    m *= max(0.5, 1.0 - (grit - 0.5) * 0.6)
    return m


def _usable_position_players(team) -> list:
    """Non-pitcher, non-joker players the team can still field (not already
    subbed out)."""
    joker_ids = {j.player_id for j in team.jokers_available}
    return [
        p for p in team.roster
        if not p.is_pitcher
        and p.player_id not in joker_ids
        and team.is_available(p.player_id)
    ]


def _bench(team) -> list:
    """Usable position players not currently in the lineup."""
    lineup_ids = {p.player_id for p in team.lineup}
    return [p for p in _usable_position_players(team)
            if p.player_id not in lineup_ids]


def _best(seq: list, key) -> Optional[Player]:
    return max(seq, key=key) if seq else None


def _floor_ok(team) -> bool:
    """True if removing one more position player keeps the usable pool at or
    above the roster floor (else the hurt player plays through)."""
    return (len(_usable_position_players(team)) - 1) >= cfg.INJURY_INGAME_ROSTER_FLOOR


def _build_event(state: GameState, kind: str, victim: Player,
                 team, base_idx: int = -1) -> Optional[dict]:
    """Pick a replacement for the victim and build the injury_sub event, or
    None if no replacement is available / the roster floor would break."""
    if kind == "pitcher":
        new_p = mgr.pick_new_pitcher(state)
        if new_p is None:
            return None
        return {"type": "injury_sub", "kind": "pitcher",
                "player_out": victim, "new_pitcher": new_p}

    if not _floor_ok(team):
        return None
    bench = _bench(team)
    if not bench:
        return None
    if kind == "batter":
        repl = _best(bench, lambda p: float(getattr(p, "contact", 0.5) or 0.5)
                                       + float(getattr(p, "power", 0.5) or 0.5))
        return {"type": "injury_sub", "kind": "batter",
                "player_out": victim, "replacement": repl}
    if kind == "runner":
        repl = _best(bench, lambda p: float(getattr(p, "speed", 0.5) or 0.5))
        return {"type": "injury_sub", "kind": "runner",
                "player_out": victim, "replacement": repl, "base_idx": base_idx}
    if kind == "fielder":
        repl = _best(bench, lambda p: float(getattr(p, "defense", 0.5) or 0.5))
        return {"type": "injury_sub", "kind": "fielder",
                "player_out": victim, "replacement": repl}
    return None


def roll_injury_event(state: GameState, rng) -> Optional[dict]:
    """Roll once for an in-game injury at the PA boundary. Returns an
    injury_sub event (executed by pa.apply_event) or None. Uses the game's
    seeded rng so re-sims reproduce."""
    if not getattr(cfg, "INJURY_INGAME_ENABLED", True):
        return None

    candidates: list[tuple] = []  # (priority, kind, victim, team, base_idx)

    # 1. Pitcher — fatigue tax (risk ramps past his Stamina threshold).
    pitcher = state.get_current_pitcher()
    if pitcher is not None:
        over = _fatigue_over(pitcher, state.pitcher_spell_count)
        fat_mult = min(cfg.INJURY_INGAME_FATIGUE_MULT_MAX,
                       1.0 + cfg.INJURY_INGAME_FATIGUE_SCALE * over)
        p = cfg.INJURY_INGAME_PITCHER_BASE * fat_mult * _durability_mult(pitcher)
        if rng.random() < p:
            candidates.append((0, "pitcher", pitcher, state.fielding_team, -1))

    # 2. Batter due up.
    batter = state.batting_team.current_batter()
    if batter is not None and not batter.is_pitcher:
        p = cfg.INJURY_INGAME_BATTER_BASE * _durability_mult(batter)
        if rng.random() < p:
            candidates.append((1, "batter", batter, state.batting_team, -1))

    # 3. Baserunners (skip Walk-Back bonus runners and jokers — not swappable).
    bteam = state.batting_team
    joker_ids = {j.player_id for j in bteam.jokers_available}
    for idx, pid in enumerate(state.bases):
        if pid is None or pid in state.walk_back_runner_ids or pid in joker_ids:
            continue
        runner = bteam.get_player(pid)
        if runner is None or runner.is_pitcher:
            continue
        p = cfg.INJURY_INGAME_BASERUN_BASE * _durability_mult(runner)
        if rng.random() < p:
            candidates.append((2, "runner", runner, bteam, idx))

    # 4. One random fielder.
    fielders = [p for p in state.fielding_team.lineup
                if not p.is_pitcher and getattr(p, "position", "") != "DH"]
    if fielders:
        f = fielders[int(rng.random() * len(fielders))]
        p = cfg.INJURY_INGAME_FIELD_BASE * _durability_mult(f)
        if rng.random() < p:
            candidates.append((3, "fielder", f, state.fielding_team, -1))

    if not candidates:
        return None

    # Priority order; first with an available replacement wins.
    candidates.sort(key=lambda c: c[0])
    for _prio, kind, victim, team, base_idx in candidates:
        evt = _build_event(state, kind, victim, team, base_idx)
        if evt is not None:
            return evt
    return None
