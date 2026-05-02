"""
Pitcher stat accumulator stub for Phase 1.
Full implementation in Phase 4.
"""
from dataclasses import dataclass, field


@dataclass
class PitcherStats:
    player_id: str
    name: str
    batters_faced: int = 0
    outs_recorded: int = 0
    hits_allowed: int = 0
    runs_allowed: int = 0
    bb: int = 0
    k: int = 0
    hbp: int = 0
    max_spell: int = 0     # Longest single spell (consecutive BF)
