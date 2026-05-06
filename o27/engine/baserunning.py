"""
Runner advancement logic for O27.

Phase 1: deterministic helpers. Given a base state and an outcome dict from
fielding.py, advance runners and return (new_bases, runs_scored, log_lines).

Phase 2: will add probabilistic runner advancement (runner speed vs. arm
strength) for multi-base advances and close plays.

Base indexing throughout: bases[0]=1B, bases[1]=2B, bases[2]=3B.
"""

from __future__ import annotations
from typing import Optional


def advance_runners(
    bases: list,
    outcome: dict,
    batter_id: str,
    is_stay: bool = False,
) -> tuple[list, int, list[str]]:
    """
    Advance runners given a fielding outcome.

    Args:
        bases:     Current [1B, 2B, 3B] occupancy (player_id or None).
        outcome:   Outcome dict from fielding.py.
        batter_id: The current batter's player_id.
        is_stay:   True if this is a stay play (batter does NOT become a runner).

    Returns:
        (new_bases, runs_scored, log_lines)
        new_bases: updated [1B, 2B, 3B]
        runs_scored: number of runs that scored
        log_lines: human-readable description lines
    """
    new_bases: list = [None, None, None]
    runs_scored = 0
    log_lines: list[str] = []

    hit_type = outcome["hit_type"]
    runner_advances = outcome["runner_advances"]   # [adv_1B, adv_2B, adv_3B]
    runner_out_idx = outcome.get("runner_out_idx")  # which runner (if any) is thrown out
    extra_runner_outs = outcome.get("extra_runner_outs") or []  # additional runner outs (triple plays)
    batter_safe = outcome["batter_safe"]

    # --- Throw out a specific runner first (fielder's choice / stay runner out) ---
    bases = list(bases)
    for out_idx in ([runner_out_idx] if runner_out_idx is not None else []) + list(extra_runner_outs):
        thrown_out_id = bases[out_idx]
        if thrown_out_id is not None:
            log_lines.append(f"  Runner at {'1B 2B 3B'.split()[out_idx]} thrown out.")
            bases[out_idx] = None

    # --- Home run: everyone scores ---
    if hit_type == "hr":
        for pid in bases:
            if pid is not None:
                runs_scored += 1
                log_lines.append(f"  Runner scores.")
        new_bases = [None, None, None]
        if not is_stay:
            runs_scored += 1          # batter scores
            log_lines.append(f"  Batter scores (HR).")
        return new_bases, runs_scored, log_lines

    # --- Move existing runners ---
    # Process from 3B → 2B → 1B to avoid collisions.
    runner_order = [2, 1, 0]  # 3B, 2B, 1B
    for idx in runner_order:
        pid = bases[idx]
        if pid is None:
            continue
        advance = runner_advances[idx]
        new_pos = idx + advance   # new 0-indexed base (0=1B, 1=2B, 2=3B, 3=home)
        if new_pos >= 3:
            runs_scored += 1
            log_lines.append(f"  Runner scores from {'1B 2B 3B'.split()[idx]}.")
        else:
            # If the destination is already occupied (collision), bump the occupant home.
            # Simple resolution: the advancing runner takes the base.
            if new_bases[new_pos] is not None:
                runs_scored += 1
                log_lines.append(f"  Collision — runner scores.")
            new_bases[new_pos] = pid

    # --- Place batter on base (if safe and not a stay) ---
    if not is_stay and batter_safe:
        if hit_type in ("single", "fielders_choice", "stay_ground",
                        "stay_fly_no_catch", "error"):
            dest = 0   # 1B
        elif hit_type == "double":
            dest = 1   # 2B
        elif hit_type == "triple":
            dest = 2   # 3B
        else:
            dest = None

        if dest is not None:
            if new_bases[dest] is not None:
                runs_scored += 1
                log_lines.append("  Batter displaces runner (runner scores).")
            new_bases[dest] = batter_id

    return new_bases, runs_scored, log_lines


def stolen_base(bases: list, base_idx: int) -> tuple[list, bool]:
    """
    Attempt a stolen base. Phase 1: always succeeds (deterministic stub).

    Args:
        bases:    Current base state.
        base_idx: Index of base the runner is stealing FROM (0=1B stealing 2B, etc.)

    Returns:
        (new_bases, success)
    """
    new_bases = list(bases)
    runner = new_bases[base_idx]
    if runner is None:
        return new_bases, False
    new_bases[base_idx] = None
    if base_idx + 1 <= 2:
        new_bases[base_idx + 1] = runner
        return new_bases, True
    else:
        # Runner was on 3B stealing home
        return new_bases, True   # counts as a run (caller handles scoring)


def wild_pitch_advance(bases: list) -> tuple[list, int]:
    """
    Wild pitch or passed ball: all runners advance one base.
    Returns (new_bases, runs_scored).
    Phase 1: deterministic 1-base advance.
    """
    new_bases = [None, None, None]
    runs = 0
    for idx in [2, 1, 0]:
        pid = bases[idx]
        if pid is None:
            continue
        new_pos = idx + 1
        if new_pos >= 3:
            runs += 1
        else:
            new_bases[new_pos] = pid
    return new_bases, runs
