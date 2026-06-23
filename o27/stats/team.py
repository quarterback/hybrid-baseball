"""
Team-level stat accumulator for O27 (Phase 4).

TeamStats computes run rate (R/out) per team at the end of each half,
required run rate for the team batting second, and net run rate for
multi-game tiebreakers (cricket-style).
"""
from dataclasses import dataclass
from typing import Optional


def required_run_rate_3o(
    target_runs: Optional[int],
    runs: int,
    outs: int,
    envelope: int = 27,
) -> Optional[float]:
    """Cricket-style Required Run Rate normalized to 3 outs (RRR/3O).

    The runs-per-out the chasing side must still average to reach ``target_runs``,
    scaled by 3 so the figure reads like a cricket "over" (3 outs ≈ one over).
    O27's natural scoring unit is R/out (league average ~0.43, i.e. ~1.3 per 3
    outs), so RRR/3O ≈ required_run_rate × 3.

        RRR/3O = (max(0, target_runs - runs) / outs_remaining) * 3

    Returns None when there is no target yet (``target_runs is None``) or the
    out envelope is exhausted (``outs_remaining <= 0``) — both cases where the
    rate is undefined. A chase that has already reached the target yields 0.0.
    """
    if target_runs is None:
        return None
    outs_remaining = envelope - outs
    if outs_remaining <= 0:
        return None
    runs_needed = max(0, target_runs - runs)
    return (runs_needed / outs_remaining) * 3.0


@dataclass
class TeamStats:
    team_name: str
    runs: int = 0
    outs: int = 0
    hits: int = 0
    stays: int = 0
    target_runs: Optional[int] = None      # set for home side once halftime is announced

    @property
    def run_rate(self) -> float:
        """R/out — primary O27 scoring efficiency stat. Average ~0.43."""
        if self.outs == 0:
            return 0.0
        return self.runs / self.outs

    @property
    def required_run_rate(self) -> Optional[float]:
        """
        Minimum R/out the home side must sustain to win from the current point.
        Uses remaining runs needed (target - scored so far) / remaining outs.
        Only meaningful during the bottom half when target_runs is set.
        Returns None when target is not yet known.
        """
        if self.target_runs is None:
            return None
        remaining_outs = max(1, 27 - self.outs)
        runs_needed = max(0, self.target_runs - self.runs)
        return runs_needed / remaining_outs

    @property
    def required_run_rate_full(self) -> Optional[float]:
        """Required run rate computed over all 27 outs (pre-game projection)."""
        if self.target_runs is None:
            return None
        return self.target_runs / 27

    @property
    def required_run_rate_3o(self) -> Optional[float]:
        """Required Run Rate normalized to 3 outs (cricket-over analog).

        Delegates to the module-level :func:`required_run_rate_3o`; the live
        chase value from the current point (remaining runs / remaining outs × 3).
        None until ``target_runs`` is set.
        """
        return required_run_rate_3o(self.target_runs, self.runs, self.outs)

    def net_run_rate(self, opponent: "TeamStats") -> float:
        """
        Net run rate for multi-game tiebreakers (cricket NRR style):
          NRR = (runs_for / outs_faced) - (runs_against / outs_bowled)
        Returns 0.0 when outs data is absent.
        """
        own_rr = self.run_rate
        opp_rr = opponent.run_rate
        return round(own_rr - opp_rr, 4)
