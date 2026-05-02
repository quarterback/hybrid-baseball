"""
Batter stat accumulator stub for Phase 1.
Full implementation in Phase 4.
"""
from dataclasses import dataclass, field


@dataclass
class BatterStats:
    player_id: str
    name: str
    pa: int = 0      # Plate appearances
    ab: int = 0      # At-bats
    runs: int = 0
    hits: int = 0
    doubles: int = 0
    triples: int = 0
    hr: int = 0
    rbi: int = 0
    bb: int = 0
    k: int = 0
    hbp: int = 0
    sty: int = 0           # Stays
    stay_rbi: int = 0
    multi_hit_abs: int = 0  # At-bats with 2+ credited hits
