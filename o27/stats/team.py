"""
Team-level stat accumulator stub for Phase 1.
Full implementation in Phase 4.
"""
from dataclasses import dataclass


@dataclass
class TeamStats:
    team_name: str
    runs: int = 0
    outs: int = 0
    hits: int = 0
    stays: int = 0

    @property
    def run_rate(self) -> float:
        """R/out — primary O27 scoring efficiency stat."""
        if self.outs == 0:
            return 0.0
        return self.runs / self.outs
