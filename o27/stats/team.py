"""
Team-level stat accumulator for O27 (Phase 4).

TeamStats computes run rate (R/out) per team at the end of each half,
required run rate for the team batting second, and net run rate for
multi-game tiebreakers (cricket-style).
"""
from dataclasses import dataclass
from typing import Optional


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
        Minimum R/out the home side must sustain to win.
        Only meaningful during / after the bottom half when target_runs is set.
        Uses remaining outs (27 total) at the point this is called.
        Returns None when target is not yet known.
        """
        if self.target_runs is None:
            return None
        remaining_outs = max(1, 27 - self.outs)
        return self.target_runs / remaining_outs

    @property
    def required_run_rate_full(self) -> Optional[float]:
        """Required run rate computed over all 27 outs (pre-game projection)."""
        if self.target_runs is None:
            return None
        return self.target_runs / 27

    def net_run_rate(self, opponent: "TeamStats") -> float:
        """
        Net run rate for multi-game tiebreakers (cricket NRR style):
          NRR = (runs_for / outs_faced) - (runs_against / outs_bowled)
        Returns 0.0 when outs data is absent.
        """
        own_rr = self.run_rate
        opp_rr = opponent.run_rate
        return round(own_rr - opp_rr, 4)
