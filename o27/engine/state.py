"""
GameState and supporting data structures for the O27 simulator.

Half naming convention:
  "top"          — visitors batting (first half of regulation)
  "bottom"       — home batting (second half of regulation)
  "super_top"    — visitors batting in super-inning tiebreaker
  "super_bottom" — home batting in super-inning tiebreaker
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Count
# ---------------------------------------------------------------------------

@dataclass
class Count:
    """Ball-strike count for the current plate appearance."""
    balls: int = 0
    strikes: int = 0

    def reset(self) -> None:
        self.balls = 0
        self.strikes = 0

    def __str__(self) -> str:
        return f"{self.balls}-{self.strikes}"


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

@dataclass
class Player:
    """A player on a roster."""
    player_id: str
    name: str
    is_pitcher: bool = False
    is_joker: bool = False

    # Skill attributes — stubs for Phase 1, used by probability models in Phase 2.
    skill: float = 0.5              # 0.0–1.0 general batting / pitching quality
    speed: float = 0.5              # 0.0–1.0 baserunning speed
    stay_aggressiveness: float = 0.5        # 0.0–1.0 tendency to choose stay
    contact_quality_threshold: float = 0.4  # threshold for stay heuristic

    def __hash__(self) -> int:
        return hash(self.player_id)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Player) and self.player_id == other.player_id

    def __repr__(self) -> str:
        tags = []
        if self.is_pitcher:
            tags.append("P")
        if self.is_joker:
            tags.append("JKR")
        tag_str = f"[{','.join(tags)}]" if tags else ""
        return f"Player({self.name}{tag_str})"


# ---------------------------------------------------------------------------
# Log records (populated by game loop; rendered in Phases 3–4)
# ---------------------------------------------------------------------------

@dataclass
class SpellRecord:
    """A pitcher's consecutive spell."""
    pitcher_id: str
    pitcher_name: str
    batters_faced: int = 0
    outs_recorded: int = 0
    runs_allowed: int = 0
    half: str = "top"
    super_inning_number: int = 0


@dataclass
class PartnershipRecord:
    """Runs scored between two consecutive outs."""
    batter1_id: str
    batter1_name: str
    batter2_id: Optional[str]   # None if game ends before second out
    batter2_name: Optional[str]
    runs: int = 0
    half: str = "top"
    super_inning_number: int = 0


@dataclass
class SuperInningRound:
    """One team's turn in a super-inning round."""
    team_name: str
    selected_batter_ids: list = field(default_factory=list)
    runs: int = 0
    dismissals: int = 0  # outs recorded (max 5)


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------

@dataclass
class Team:
    """One side in an O27 game."""
    team_id: str    # "visitors" | "home"
    name: str
    roster: list = field(default_factory=list)          # All Player objects (12)
    lineup: list = field(default_factory=list)          # Active batting order (12)
    lineup_position: int = 0
    jokers_available: list = field(default_factory=list)    # joker Player objects not yet inserted
    jokers_used_this_half: set = field(default_factory=set) # player_ids used this half

    # Super-inning
    super_lineup: list = field(default_factory=list)        # 5 selected Player objects
    super_dismissed: set = field(default_factory=set)       # player_ids dismissed in current super round
    super_lineup_position: int = 0

    def current_batter(self) -> Player:
        """Get the current batter from the appropriate active lineup."""
        if self.super_lineup:
            pos = self.super_lineup_position % len(self.super_lineup)
            return self.super_lineup[pos]
        if not self.lineup:
            raise ValueError(f"Team {self.name} has no active lineup.")
        return self.lineup[self.lineup_position % len(self.lineup)]

    def advance_lineup(self) -> None:
        """Advance the lineup position (wraps around)."""
        if self.super_lineup:
            self.super_lineup_position = (self.super_lineup_position + 1) % len(self.super_lineup)
        else:
            self.lineup_position = (self.lineup_position + 1) % len(self.lineup)

    def reset_half(self) -> None:
        """Reset intra-half tracking at the start of a new half."""
        self.jokers_used_this_half = set()

    def reset_super(self) -> None:
        """Reset super-inning tracking for a new round."""
        self.super_lineup = []
        self.super_dismissed = set()
        self.super_lineup_position = 0

    def get_player(self, player_id: str) -> Optional[Player]:
        """Look up a player by ID anywhere in the roster."""
        for p in self.roster:
            if p.player_id == player_id:
                return p
        return None


# ---------------------------------------------------------------------------
# GameState
# ---------------------------------------------------------------------------

@dataclass
class GameState:
    """Complete mutable state of one O27 game."""

    # --- Teams ---
    visitors: Team = field(default_factory=lambda: Team("visitors", "Visitors"))
    home: Team = field(default_factory=lambda: Team("home", "Home"))

    # --- Game structure ---
    half: str = "top"              # "top" | "bottom" | "super_top" | "super_bottom"
    super_inning_number: int = 0   # 0 = regulation; increments each super tiebreaker

    # --- Current half state ---
    outs: int = 0                  # 0–27 in regulation, 0–5 in super
    bases: list = field(default_factory=lambda: [None, None, None])
    # bases[0]=1B, bases[1]=2B, bases[2]=3B; each entry is a player_id str or None

    # --- Current plate appearance ---
    count: Count = field(default_factory=Count)

    # --- Score ---
    score: dict = field(default_factory=lambda: {"visitors": 0, "home": 0})

    # --- Partnership tracking ---
    partnership_runs: int = 0
    partnership_first_batter_id: Optional[str] = None
    partnership_log: list = field(default_factory=list)

    # --- Pitcher / spell tracking ---
    pitcher_spell_count: int = 0       # batters faced in current spell
    current_pitcher_id: Optional[str] = None
    spell_log: list = field(default_factory=list)

    # --- Multi-hit tracking (within one at-bat) ---
    current_at_bat_hits: int = 0

    # --- Halftime target ---
    target_score: Optional[int] = None         # visitors' score; set at halftime

    # --- Super-inning rounds ---
    super_inning_rounds: list = field(default_factory=list)

    # --- Raw event log ---
    events: list = field(default_factory=list)

    # --- Winner ---
    winner: Optional[str] = None    # "visitors" | "home" | None

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def batting_team(self) -> Team:
        if self.half in ("top", "super_top"):
            return self.visitors
        return self.home

    @property
    def fielding_team(self) -> Team:
        if self.half in ("top", "super_top"):
            return self.home
        return self.visitors

    @property
    def current_batter(self) -> Player:
        return self.batting_team.current_batter()

    @property
    def runners_on_base(self) -> bool:
        return any(b is not None for b in self.bases)

    @property
    def runner_count(self) -> int:
        return sum(1 for b in self.bases if b is not None)

    @property
    def runners_in_scoring_position(self) -> bool:
        """True if any runner is on 2B or 3B."""
        return self.bases[1] is not None or self.bases[2] is not None

    @property
    def is_super_inning(self) -> bool:
        return self.half in ("super_top", "super_bottom")

    def is_half_over(self) -> bool:
        """True when the current half has ended."""
        if self.is_super_inning:
            # Super half: ends when 5 batters from the selected lineup are dismissed
            return len(self.batting_team.super_dismissed) >= 5
        return self.outs >= 27

    def is_game_over(self) -> bool:
        return self.winner is not None

    def get_current_pitcher(self) -> Optional[Player]:
        if self.current_pitcher_id is None:
            return None
        return self.fielding_team.get_player(self.current_pitcher_id)

    def bases_summary(self) -> str:
        """Human-readable base occupancy string."""
        labels = ["1B", "2B", "3B"]
        occupied = [labels[i] for i, pid in enumerate(self.bases) if pid is not None]
        return ", ".join(occupied) if occupied else "empty"

    def score_summary(self) -> str:
        v = self.score["visitors"]
        h = self.score["home"]
        return f"{self.visitors.name} {v}, {self.home.name} {h}"
