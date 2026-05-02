"""
Fielding outcome resolution for O27.

Phase 1: deterministic stubs — callers pass in an explicit `outcome` dict.
Phase 2: will add probability-weighted resolution based on contact quality
         and fielder positioning.

A fielding outcome describes what happens after bat-on-ball contact:

  outcome = {
      "hit_type":  "single" | "double" | "triple" | "hr" |
                   "ground_out" | "fly_out" | "line_out" |
                   "fly_caught" | "fielders_choice" | "error",
      "batter_safe": True | False,   # whether the batter reaches base
      "caught_fly":  True | False,   # True if a fly ball was caught in the air
      "runner_advances": [0, 1, 2],  # bases advanced per runner (indexed 0=1B, 1=2B, 2=3B)
      "runner_out_idx":  None | int, # index of runner thrown out (0-2), or None
  }
"""

from __future__ import annotations
from typing import Optional


# ---------------------------------------------------------------------------
# Deterministic outcome builders (used by Phase 1 scripted tests)
# ---------------------------------------------------------------------------

def outcome_single(runner_advances: Optional[list] = None) -> dict:
    """Batter reaches 1B; runners advance by default 1 base."""
    return {
        "hit_type": "single",
        "batter_safe": True,
        "caught_fly": False,
        "runner_advances": runner_advances if runner_advances is not None else [1, 1, 1],
        "runner_out_idx": None,
    }


def outcome_double(runner_advances: Optional[list] = None) -> dict:
    """Batter reaches 2B; runners advance 2 bases by default."""
    return {
        "hit_type": "double",
        "batter_safe": True,
        "caught_fly": False,
        "runner_advances": runner_advances if runner_advances is not None else [2, 2, 2],
        "runner_out_idx": None,
    }


def outcome_triple(runner_advances: Optional[list] = None) -> dict:
    """Batter reaches 3B; all runners score by default."""
    return {
        "hit_type": "triple",
        "batter_safe": True,
        "caught_fly": False,
        "runner_advances": runner_advances if runner_advances is not None else [3, 3, 3],
        "runner_out_idx": None,
    }


def outcome_home_run() -> dict:
    """Batter and all runners score."""
    return {
        "hit_type": "hr",
        "batter_safe": True,
        "caught_fly": False,
        "runner_advances": [3, 3, 3],
        "runner_out_idx": None,
    }


def outcome_ground_out(runner_advances: Optional[list] = None) -> dict:
    """Batter is out; runners advance 1 base by default."""
    return {
        "hit_type": "ground_out",
        "batter_safe": False,
        "caught_fly": False,
        "runner_advances": runner_advances if runner_advances is not None else [1, 1, 1],
        "runner_out_idx": None,
    }


def outcome_fly_out(runner_advances: Optional[list] = None) -> dict:
    """Fly ball caught; batter out. Runners may tag (advance 0 by default)."""
    return {
        "hit_type": "fly_out",
        "batter_safe": False,
        "caught_fly": True,
        "runner_advances": runner_advances if runner_advances is not None else [0, 0, 0],
        "runner_out_idx": None,
    }


def outcome_fly_out_runner_scores(runner_out_idx: Optional[int] = None,
                                   runner_advances: Optional[list] = None) -> dict:
    """Fly ball caught; runner tags and scores (sacrifice fly)."""
    return {
        "hit_type": "fly_out",
        "batter_safe": False,
        "caught_fly": True,
        "runner_advances": runner_advances if runner_advances is not None else [0, 0, 1],
        "runner_out_idx": runner_out_idx,
    }


def outcome_line_out() -> dict:
    """Line drive caught; batter out, runners frozen."""
    return {
        "hit_type": "line_out",
        "batter_safe": False,
        "caught_fly": False,
        "runner_advances": [0, 0, 0],
        "runner_out_idx": None,
    }


def outcome_fielders_choice(runner_out_idx: int = 0,
                             runner_advances: Optional[list] = None) -> dict:
    """Batter safe; a specific runner is thrown out."""
    return {
        "hit_type": "fielders_choice",
        "batter_safe": True,
        "caught_fly": False,
        "runner_advances": runner_advances if runner_advances is not None else [1, 1, 1],
        "runner_out_idx": runner_out_idx,
    }


def outcome_stay_ground_ball(runner_advances: Optional[list] = None,
                              runner_out_idx: Optional[int] = None) -> dict:
    """
    Stay play — ground ball.
    Batter stays; no force at 1B; no DP through 1B.
    Runners advance per fielding play.
    """
    return {
        "hit_type": "stay_ground",
        "batter_safe": True,    # batter cannot be put out on a stay play
        "caught_fly": False,
        "runner_advances": runner_advances if runner_advances is not None else [1, 1, 1],
        "runner_out_idx": runner_out_idx,
    }


def outcome_stay_fly_not_caught(runner_advances: Optional[list] = None) -> dict:
    """Stay play — fly ball not caught. Runners advance."""
    return {
        "hit_type": "stay_fly_no_catch",
        "batter_safe": True,
        "caught_fly": False,
        "runner_advances": runner_advances if runner_advances is not None else [1, 1, 1],
        "runner_out_idx": None,
    }


def outcome_stay_fly_caught() -> dict:
    """
    Stay play — fly ball caught.
    PRD §2.6: batter is OUT. Runners may tag.
    """
    return {
        "hit_type": "stay_fly_caught",
        "batter_safe": False,   # batter is out on a caught fly with stay
        "caught_fly": True,
        "runner_advances": [0, 0, 0],  # runners may tag (advance 1 in Phase 2)
        "runner_out_idx": None,
    }
