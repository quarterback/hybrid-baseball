"""
GameState and supporting data structures for the O27 simulator.

Half naming convention (state.half):
  "top"          — visitors batting (first half of regulation)
  "bottom"       — home batting (second half of regulation)
  "super_top"    — visitors batting in super-inning tiebreaker
  "super_bottom" — home batting in super-inning tiebreaker

Public state contract (convenience entry points):
  state.batting_team          → Team currently at bat
  state.fielding_team         → Team currently in the field
  state.current_batter        → Player now at the plate (property)
  state.get_current_pitcher() → Player now pitching (method)
  state.active_lineup         → list[Player] from the appropriate Team lineup
  state.runners_on_base       → bool
  state.runner_count          → int
  state.is_super_inning       → bool

Active-lineup model:
  Each Team stores an active lineup (9 position players + jokers) in Team.lineup.
  The v2 roster carries 9 jokers (3 per archetype); the v1 baseline uses 3 jokers.
  Jokers are part of the lineup from the start; once a joker bats they are
  added to Team.jokers_used_this_half and skipped by Team.advance_lineup().
  In super-innings Team.super_lineup (5 players) is used instead.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from o27 import config as _cfg


# ---------------------------------------------------------------------------
# Count
# ---------------------------------------------------------------------------

@dataclass
class Count:
    """Ball-strike count for the current plate appearance.

    O27 also tracks total fouls per AB: 3 fouls = foul-out (FO).
    Distinct from `strikes` because once strikes==2 a foul no longer
    advances the count, but the foul tally keeps climbing toward 3.
    """
    balls: int = 0
    strikes: int = 0
    fouls: int = 0

    def reset(self) -> None:
        self.balls = 0
        self.strikes = 0
        self.fouls = 0

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

    # Skill attributes — used by Phase 2 probability models.
    # Defaults come from o27.config so all tunables are in one place.
    skill: float = _cfg.PLAYER_DEFAULT_SKILL
    speed: float = _cfg.PLAYER_DEFAULT_SPEED
    stay_aggressiveness: float = _cfg.PLAYER_DEFAULT_STAY_AGGRESSIVENESS
    contact_quality_threshold: float = _cfg.PLAYER_DEFAULT_CONTACT_QUALITY_THRESHOLD
    pitcher_skill: float = _cfg.PLAYER_DEFAULT_PITCHER_SKILL

    # Task #65: Stamina is rolled independently of Stuff (`pitcher_skill`)
    # so the manager AI can derive today's role at game time without any
    # persisted role tag. High-Stamina arms are preferred for starts and
    # long relief; high-Stuff arms are preferred for late-inning leverage.
    stamina: float = _cfg.PLAYER_DEFAULT_PITCHER_SKILL

    # Pitcher usage role — legacy "workhorse" | "committee" | "starter" |
    # "reliever" | "". Task #65 retired role-based usage; the field is
    # left in place for back-compat with old DB rows but the manager AI
    # no longer reads it.
    pitcher_role: str = ""

    # Legacy Phase-8 fields (kept zeroed for backward compatibility with the
    # probability code that still references them; jokers/archetypes are gone).
    archetype: str = ""
    hard_contact_delta: float = 0.0
    hr_weight_bonus:    float = 0.0

    # Realism layer — multi-dimensional ratings (0.0–1.0).
    # All default to 0.5 so legacy callers that don't set them produce
    # numerically identical output to the pre-realism engine (identity
    # invariant: every (x - 0.5) * 2 term collapses to 0).
    contact:  float = 0.5   # batter: lower whiff rate, more fouls/in-play
    power:    float = 0.5   # batter: shifts contact toward hard, boosts HR weight
    eye:      float = 0.5   # batter: more balls taken, fewer called strikes
    command:  float = 0.5   # pitcher: lower P(ball)
    movement: float = 0.5   # pitcher: bias contact toward weak/ground_out
    # Defense layer — fielding ability + throwing arm.
    # `defense` is the player's general glove rating. The three position-
    # group sub-ratings let a player be elite at INF but weak at OF, etc.
    # — utility players have decent ratings across groups, specialists
    # have one elite group and replacement-level elsewhere. Identity at
    # all = 0.5 means no defensive contribution.
    defense:           float = 0.5   # general glove / surehandedness
    arm:               float = 0.5   # throwing strength (C / OF / SS most)
    defense_infield:   float = 0.5   # 1B / 2B / 3B / SS specific glove
    defense_outfield:  float = 0.5   # LF / CF / RF specific glove
    defense_catcher:   float = 0.5   # catcher-specific framing / blocking

    # Handedness — drives platoon split. Default '' means "unknown handedness"
    # and bypasses the platoon adjustment, preserving the identity invariant
    # for legacy callers that don't set these fields. League-generated rosters
    # always populate explicit 'L' / 'R' / 'S'.
    bats:   str = ""   # '' | 'L' | 'R' | 'S'
    throws: str = ""   # '' | 'L' | 'R'

    # Per-spell daily form multiplier on effective Stuff. Re-rolled by the
    # game loop on every `_set_fielding_pitcher` so the same SP can pitch
    # a gem one start and a clunker the next. 1.0 = legacy parity.
    today_form: float = 1.0

    # Workload-model state — populated per-game by sim.py from the live DB
    # game_pitcher_stats history. Defaults preserve identity for legacy
    # callers / fresh Players that don't have rest data yet.
    days_rest: int = 99      # days since last appearance (99 = fully rested)
    pitch_debt: int = 0      # rolling 5-day pitch count (recovery-decayed)

    def __hash__(self) -> int:
        return hash(self.player_id)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Player) and self.player_id == other.player_id

    def __repr__(self) -> str:
        tags = []
        if self.is_pitcher:
            tags.append("P")
        if self.pitcher_role:
            tags.append(self.pitcher_role[:3].upper())
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
    unearned_runs: int = 0      # subset of runs_allowed scored on a passed_ball
    hits_allowed: int = 0
    bb: int = 0
    k: int = 0
    hbp: int = 0
    hr_allowed: int = 0
    pitches_thrown: int = 0
    out_when_pulled: int = 0    # team's out count at the moment this spell ended
    start_batter_num: int = 0   # ordinal PA number when this spell began
    half: str = "top"
    super_inning_number: int = 0
    # Persisted counting stats for advanced rate-stat aggregation.
    sb_allowed: int = 0   # successful stolen bases against this pitcher
    cs_caught:  int = 0   # caught-stealing outs while this pitcher was on
    fo_induced: int = 0   # foul-outs (3-foul rule) ending an AB on this pitcher


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
    selected_batter_names: list = field(default_factory=list)
    runs: int = 0
    dismissals: int = 0          # outs recorded (max 5)
    batter_outcomes: list = field(default_factory=list)  # brief per-batter outcome strings


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

    # Realism layer — ballpark factors applied when this team is at home.
    # 1.0 = neutral (legacy parity). Bounded by config.PARK_*_MIN/MAX at seed.
    park_hr:   float = 1.0   # multiplier on HR weight in HARD_CONTACT
    park_hits: float = 1.0   # multiplier on hit-vs-out balance

    # Aggregate team defense rating (positional-value-weighted). Stamped
    # at game start by sim.py:_db_team_to_engine. 0.5 = neutral; higher =
    # better collective defense → fewer hits, fewer errors.
    defense_rating: float = 0.5
    # Catcher's arm rating, stamped at game start. Drives SB-success
    # suppression. 0.5 = neutral.
    catcher_arm:    float = 0.5

    # Joker pool — 3 tactical pinch-hitters available per game. They are
    # NOT in the base lineup; the manager AI inserts them per-rotation
    # subject to "each joker can only be inserted once per cycle through
    # the order." Insertions add an extra PA to the rotation; the joker
    # bats then returns to the bench without taking a roster slot or a
    # field position.
    jokers_available: list = field(default_factory=list)
    jokers_used_this_cycle: set = field(default_factory=set)
    jokers_used_this_half: set = field(default_factory=set)   # legacy alias
    lineup_cycle_number: int = 0   # increments when lineup_position wraps

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
        """Advance the lineup position (wraps around).

        In super-inning mode: skip over any batters already dismissed so that
        dismissed batters are never sent back to the plate.  The caller must
        check is_half_over() before calling current_batter() again — once all
        5 are dismissed the skip loop will cycle without finding anyone, but
        is_half_over() will return True first, ending the half.
        """
        if self.super_lineup:
            n = len(self.super_lineup)
            pos = (self.super_lineup_position + 1) % n
            for _ in range(n):
                if self.super_lineup[pos].player_id not in self.super_dismissed:
                    break
                pos = (pos + 1) % n
            self.super_lineup_position = pos
        else:
            n = len(self.lineup)
            new_pos = (self.lineup_position + 1) % n
            if new_pos == 0 and n > 0:
                # Lineup wrapped to top of order — start of a new cycle.
                # Each joker is once-per-cycle, so clear the used set.
                self.lineup_cycle_number += 1
                self.jokers_used_this_cycle = set()
            self.lineup_position = new_pos

    def reset_half(self) -> None:
        """Reset intra-half tracking at the start of a new half.

        Jokers were removed in Task #47 — DH-only roster has no per-half
        eligibility tracking, so this is now a no-op kept for call-site
        compatibility with the engine.
        """
        return

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
    pitcher_outs_this_spell: int = 0   # outs recorded in current spell
    pitcher_runs_this_spell: int = 0   # runs allowed in current spell
    pitcher_unearned_runs_this_spell: int = 0  # unearned subset (passed-ball runs)
    pitcher_h_this_spell: int = 0      # hits allowed in current spell
    pitcher_bb_this_spell: int = 0     # walks issued in current spell
    pitcher_k_this_spell: int = 0      # strikeouts in current spell
    pitcher_hbp_this_spell: int = 0    # hit batters in current spell
    pitcher_hr_this_spell: int = 0     # HR allowed in current spell
    pitcher_pitches_this_spell: int = 0  # pitches thrown in current spell
    pitcher_sb_allowed_this_spell: int = 0  # stolen bases against current spell
    pitcher_cs_caught_this_spell: int = 0   # CS outs while current spell on mound
    pitcher_fo_induced_this_spell: int = 0  # foul-outs in current spell
    pitcher_errors_this_spell: int = 0      # defensive errors during current spell
                                            # (post-error runs in the spell charge UER)
    pitcher_start_pa: int = 0          # total_pa_this_half when spell began
    total_pa_this_half: int = 0        # cumulative PA count this half (incremented on PA end)
    current_pitcher_id: Optional[str] = None
    spell_log: list = field(default_factory=list)

    # --- Multi-hit tracking (within one at-bat) ---
    current_at_bat_hits: int = 0

    # --- Joker insertion override ---
    # When the manager inserts a joker, this field holds the joker Player
    # for one PA. The current_batter property checks this first, so the
    # joker bats instead of the base-lineup batter. After the joker AB
    # ends, _end_at_bat clears this and does NOT call advance_lineup
    # (the joker insertion is EXTRA — base lineup position is unchanged).
    batter_override: Optional[Player] = None

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
        # Joker insertion override takes precedence — when the manager has
        # called in a joker for the next PA, that joker bats instead of
        # the base lineup batter.
        if self.batter_override is not None:
            return self.batter_override
        return self.batting_team.current_batter()

    @property
    def active_lineup(self) -> list:
        """Return the list that is currently active for the batting team.

        In super-innings this is the 5-player super_lineup; in regulation it
        is the 12-batter Team.lineup (jokers already included, used ones
        skipped by advance_lineup).
        """
        team = self.batting_team
        if team.super_lineup:
            return list(team.super_lineup)
        return list(team.lineup)

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
