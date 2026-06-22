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
  Each Team stores an active lineup (9 position players) in Team.lineup.
  Jokers are NOT in the base lineup — they live in Team.jokers_available
  (3 per game) and are inserted tactically by the manager AI per PA.
  Any joker can be inserted any number of times per game (no per-cycle
  or per-game cap). Super-innings are normal 3-out innings that continue
  the regular batting order (no separate selected lineup).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from o27 import config as _cfg
from o27.engine import cricket_order as _cricket_order


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
class PitchEntry:
    """One pitch in a pitcher's repertoire.

    pitch_type   — key into config.PITCH_CATALOG
    quality      — this pitcher's mastery of the pitch (0.0–1.0)
    usage_weight — relative frequency in neutral situations (un-normalised)
    """
    pitch_type:    str
    quality:       float = 0.5
    usage_weight:  float = 1.0


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
    # Baserunning skill — separate from foot speed. Reads off the bat,
    # turn radius, slide technique, picking up the third-base coach. A
    # smart-but-slow runner can still take the extra base; a fast-but-
    # raw runner runs into outs. 0.5 = neutral (identity).
    baserunning: float = 0.5
    # Run aggressiveness — willingness to risk the extra base. High
    # aggressiveness boosts attempt rate but pushes more close plays
    # toward the cutoff/throw outcome. 0.5 = neutral.
    run_aggressiveness: float = 0.5
    stay_aggressiveness: float = _cfg.PLAYER_DEFAULT_STAY_AGGRESSIVENESS
    contact_quality_threshold: float = _cfg.PLAYER_DEFAULT_CONTACT_QUALITY_THRESHOLD
    pitcher_skill: float = _cfg.PLAYER_DEFAULT_PITCHER_SKILL

    # Task #65: Stamina is rolled independently of Stuff (`pitcher_skill`)
    # so the manager AI can derive today's role at game time without any
    # persisted role tag. High-Stamina arms are preferred for starts and
    # long relief; high-Stuff arms are preferred for late-inning leverage.
    stamina: float = _cfg.PLAYER_DEFAULT_PITCHER_SKILL

    # Canonical crew role — "" | "HM" | "1C" | "2C" | "BO" | "SK" | "AN" |
    # "PI" (see o27v2/rotation.py: Helms / First & Second Change / Bosun /
    # Skidder / Anchor / Pilot). Drives the steering pick (sim.py) and the
    # relief-role preference in pick_new_pitcher (manager.py). "" means no
    # crew role (legacy rows) → live-derivation fallback. Any unrecognized
    # legacy value reads as "no crew role" too.
    pitcher_role: str = ""

    # Usage rank within the crew role (1 = primary; the two Helms alternate
    # as slot 1 / slot 2 so the steering arm can go ~every other day).
    # 0 for relievers / non-pitchers.
    rotation_slot: int = 0

    # Legacy Phase-8 archetype fields. None are read by the probability
    # code anymore: the `hr_weight_bonus` (HR boost) and
    # `hard_contact_delta` (hard-contact boost) modifiers were removed
    # because they inflated joker contact/power outcomes in ways the
    # modern `power` rating already models — and as unscaled additive
    # boosts they bypassed the per-game joker decay. Fields retained as
    # zeroed stubs so v2 DB rows / seed code keep loading without schema
    # churn. `archetype` / `pitcher_archetype` strings are likewise inert.
    archetype: str = ""
    pitcher_archetype: str = ""
    hard_contact_delta: float = 0.0   # unused; see comment above
    hr_weight_bonus:    float = 0.0   # unused; see comment above

    # Realism layer — multi-dimensional ratings (0.0–1.0).
    # All default to 0.5 so legacy callers that don't set them produce
    # numerically identical output to the pre-realism engine (identity
    # invariant: every (x - 0.5) * 2 term collapses to 0).
    contact:  float = 0.5   # batter: lower whiff rate, more fouls/in-play
    power:    float = 0.5   # batter: shifts contact toward hard, boosts HR weight
    eye:      float = 0.5   # batter: more balls taken, fewer called strikes
    # batter: bunting technique / bat control. Drives whether the manager
    # asks for a bunt and whether it's executed cleanly (good bunt down vs
    # popup / lead runner thrown out). Distinct from raw foot speed, so a
    # slow contact specialist can still be an elite bunter. 0.5 = neutral.
    bunt:     float = 0.5
    command:  float = 0.5   # pitcher: lower P(ball)
    movement: float = 0.5   # pitcher: bias contact toward weak/ground_out

    # Pitch-quality range — each pitcher has a STATIC half-width around their
    # central Stuff/Command/Movement ratings. Each pitch samples uniformly in
    # [rating - pitch_variance, rating + pitch_variance], so a "consistent"
    # arm (low variance) repeats his stuff every pitch while a "max-effort,
    # frayed mechanics" arm (high variance) lives on the edges. Identity at
    # pitch_variance = 0.0 (every pitch == central rating).
    pitch_variance: float = 0.0

    # Grit — pitcher fatigue resistance. Bounded 0.25–0.75 in roster
    # generation; at 0.50 the fatigue ramp is unaffected (identity). High
    # grit lets stuff/movement/command keep playing even when the arm is
    # past its Stamina threshold; low grit means a pitcher's repertoire
    # falls apart the moment they tire. This is what the user calls "the
    # gutty veteran who finds another gear" vs "the kid who unravels."
    grit: float = 0.5
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
    game_calling:      float = 0.5   # catcher pitch-calling — a good caller
                                     # suppresses contact when behind the plate
                                     # (only applies to whoever is catching)

    # Spray tendency — 0.0 = pure opposite-field, 0.5 = neutral spray,
    # 1.0 = pure pull. Drives the shift decision (extreme values invite
    # the defensive shift) and the per-event contact-direction roll
    # when shifted. Default 0.5 keeps legacy rosters shift-immune.
    pull_pct: float = 0.5
    # Adaptability — how quickly the batter reads a sustained defensive
    # alignment and finds the holes. When a manager keeps the SAME shift
    # call across consecutive ABs against this batter (streak), the
    # batter's adaptability erodes the shift's effectiveness. 0.5 =
    # neutral (no erosion); 1.0 = elite shift-reader.
    adaptability: float = 0.5
    # Leadership — batter-side mental attribute, independent of hard
    # skills (eye, contact). Stacks with `grit` to lift the RISP-pressure
    # bonus in prob._resolve_risp_pressure: a high-leadership AND
    # high-grit batter gets both lifts at once, so a low-eye/contact
    # bench guy can still tip a big AB (the joker archetype). 0.5 = neutral.
    leadership: float = 0.5

    # Transient per-game shift memory — reset to defaults each new
    # game (Player rebuilt from DB at game start). NOT persisted.
    last_shift_alignment: str = "none"  # "none" | "infield" | "outfield"
    shift_streak: int = 0               # consecutive ABs with same alignment

    # Handedness — drives platoon split. Default '' means "unknown handedness"
    # and bypasses the platoon adjustment, preserving the identity invariant
    # for legacy callers that don't set these fields. League-generated rosters
    # always populate explicit 'L' / 'R' / 'S'.
    bats:   str = ""   # '' | 'L' | 'R' | 'S'
    throws: str = ""   # '' | 'L' | 'R'

    # Canonical defensive position (CF / SS / 2B / 3B / RF / LF / 1B / C /
    # P / J / UT). Used by per-fielder play attribution to credit PO/A/E
    # to the right player. This is the player's PRIMARY position; for the
    # actual position they played in a given game, see `game_position`.
    position: str = ""

    # Per-game fielding position. Assigned at game start by the lineup
    # builder so that even utility players (`position == "UT"`) get pinned
    # to a concrete defensive spot for that day's box score. Mid-game
    # defensive moves can extend the string (e.g., "SS-2B" for a player
    # who started at SS and moved to 2B). Defaults to "" — the box-score
    # renderer falls back to `position` when this is empty.
    game_position: str = ""

    # Per-spell daily form multiplier on effective Stuff. Re-rolled by the
    # game loop on every `_set_fielding_pitcher` so the same SP can pitch
    # a gem one start and a clunker the next. 1.0 = legacy parity.
    today_form: float = 1.0

    # Per-game wellness multiplier. Rolled once per player per game in
    # o27v2/sim.py:_db_team_to_engine (driven by weather + slight talent
    # bias). Stacks multiplicatively on top of today_form for pitchers
    # and scales batter ratings in the dominance terms in prob.py so a
    # great hitter can have a 0-fer day and a replacement bat can carry
    # a game. 1.0 = identity (no condition effect).
    today_condition: float = 1.0

    # Release-point position within the sidearm/submarine spectrum.
    # O27 is a sidearm/submarine sport (lore-level structural fact).
    #   0.0 = submarine       (extreme downward angle, strongest platoon effect, least arm stress)
    #   0.5 = sidearm         (default; league centre-mass; identity for all multipliers)
    #   1.0 = three-quarter   (highest slot in O27; slightly reduced platoon effect)
    release_angle: float = 0.5

    # Pitch repertoire. Empty list = legacy pitcher without typed repertoire;
    # the engine falls back to aggregate Stuff/Movement/Command. Populated by
    # roster generation or player creation helpers.
    repertoire: list = field(default_factory=list)  # list[PitchEntry]

    # Workload-model state — populated per-game by sim.py from the live DB
    # game_pitcher_stats history. Defaults preserve identity for legacy
    # callers / fresh Players that don't have rest data yet.
    days_rest: int = 99      # days since last appearance (99 = fully rested)
    pitch_debt: int = 0      # rolling 5-day pitch count (recovery-decayed)

    # Substitution-economy role tags (derived; see o27v2/archetypes.py).
    # `roster_slot` is one of "bat_first" / "glove_first" / "two_way" /
    # "pitcher" / "joker" / "pr_specialist" / "ph_specialist". Empty on
    # legacy DB rows (treated as "two_way" for the safest fallback in
    # deployment logic). Role flags are bools — True if the player clears
    # the deployment threshold for that role. `role_field_pos` is the
    # comma-joined list of positions they can defend at (e.g., "2B,SS,3B").
    roster_slot:    str  = ""
    role_hit:       bool = True   # default True keeps legacy bench bats deployable
    role_run:       bool = False
    role_two_way:   bool = True   # default True keeps legacy players viable in both halves
    role_field_pos: str  = ""     # comma-joined positions

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
class Substitution:
    """One position-player substitution event.

    Stored in GameState.substitution_log in the order they fired so the
    one-way invariant can be walked (no out_player_id should later appear
    as an in_player_id) and so the AAR / box score can render
    substitution-volume stats per game.

    `kind` is one of: "pinch_hit" / "pinch_run" / "pinch_field" / "joker"
    / "pitching". `trigger_score` is the score_substitution() output that
    cleared the manager's threshold (0.0 for legacy paths that bypass the
    unified trigger).
    """
    half: str
    outs_at_sub: int
    kind: str
    team_id: str
    in_player_id: str
    out_player_id: str
    lineup_index: Optional[int] = None
    score_for: int = 0
    score_against: int = 0
    trigger_score: float = 0.0
    reason: str = ""


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
    balks:          int = 0   # pitcher's balks called this spell
    catchers_balks: int = 0   # catcher's balks called this spell
    ci_allowed:     int = 0   # catcher's interference calls this spell
    # Walk-Back: PAs where this pitcher faced a hitter with a Walk-Back
    # runner pending on 3B (Manfred-runner analog). wb_runs is the subset
    # of those PAs where the Walk-Back runner scored. Used by the
    # Walk-Back Stop% pitcher rate stat and by the ERA-exclusion logic
    # (the Walk-Back run is always unearned).
    wb_faced: int = 0
    wb_runs:  int = 0
    # Inherited runners: how many were on base when this reliever entered
    # (ir_inherited) and how many of them scored against him (ir_scored).
    # Powers IR-Stop% — a structure-agnostic relief skill (cleaning up a
    # rally you walked into). The starter inherits nobody.
    ir_inherited: int = 0
    ir_scored:    int = 0
    # Finisher context: the fielding-team lead (fielding − batting score) at the
    # moment this pitcher entered (entry_lead) and the minimum that lead reached
    # during the spell (min_lead). finished = he was on the mound at the end of
    # his defensive half. Together these drive Terminal Outs / Quality Finish /
    # Lead-Retention — the structure-agnostic "who seals games" stats.
    entry_lead: int = 0
    min_lead:   int = 0
    finished:   int = 0
    # Arc-bucketed counters for wERA / xFIP / Decay. Indices 0/1/2 cover
    # outs 1-9 / 10-18 / 19-27 of the defending team's running 27-out half.
    # Super-innings outs roll into arc 3 (treat as continuation).
    er_arc:  list = field(default_factory=lambda: [0, 0, 0])
    k_arc:   list = field(default_factory=lambda: [0, 0, 0])
    fo_arc:  list = field(default_factory=lambda: [0, 0, 0])
    bf_arc:  list = field(default_factory=lambda: [0, 0, 0])
    # Times-through-the-order counters. Indices 0/1/2 = the 1st / 2nd / 3rd+
    # time each batter faced this pitcher this game (the look number), so
    # K%/FO%/contact can be split by familiarity. Distinct from the arc
    # buckets (which split by out position / fatigue): a pitcher can be on
    # his 1st look deep in the arc (fresh reliever) or his 4th look early
    # (top of the order in a marathon). Powers the Deception decay stat.
    k_tto:   list = field(default_factory=lambda: [0, 0, 0])
    fo_tto:  list = field(default_factory=lambda: [0, 0, 0])
    bf_tto:  list = field(default_factory=lambda: [0, 0, 0])


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
    """One team's half in a super-inning round (a normal 3-out inning)."""
    team_name: str
    runs: int = 0


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
    # Components of defense_rating (the positional-value-weighted mean):
    # rating = defense_weighted_sum / defense_weight_sum. Stashed at game start
    # so an in-game defensive sub can move the rating by the exact marginal
    # value of swapping one glove for another at one position, then re-derive
    # the ratio. Zero weight_sum (simple sims that hard-set defense_rating)
    # disables the live update — the rating just stays put.
    defense_weighted_sum: float = 0.0
    defense_weight_sum:   float = 0.0
    # Catcher's arm rating, stamped at game start. Drives SB-success
    # suppression. 0.5 = neutral.
    catcher_arm:    float = 0.5
    # Outs caught by the current catcher this game (fatigue accumulator). Resets
    # to 0 when the manager rotates a fresh catcher in. Drives game-calling decay.
    catcher_outs_caught: int = 0

    # Manager persona — stamped at game start from the team row. 0.5 = neutral.
    # Re-rolled per league seed (see o27v2/managers.py). The engine's
    # manager.py reads these to bias situational decisions; prob.py reads
    # mgr_run_game to scale stolen-base attempt rates.
    manager_archetype:        str   = ""
    mgr_quick_hook:           float = 0.5
    mgr_bullpen_aggression:   float = 0.5
    mgr_leverage_aware:       float = 0.5
    mgr_joker_aggression:     float = 0.5
    # Willingness to issue an intentional walk to a hot or elite batter.
    # 0.0 = never; 1.0 = walks anyone hot at the first available chance.
    # Read by manager.should_intentional_walk; varied per archetype in
    # o27v2/managers.py so different skippers have different IBB policies.
    mgr_ibb_aggression:       float = 0.5
    mgr_pinch_hit_aggression: float = 0.5
    mgr_platoon_aggression:   float = 0.5
    mgr_run_game:             float = 0.5
    mgr_bench_usage:          float = 0.5
    mgr_shift_aggression:     float = 0.5
    # Declared Seconds — two new persona axes
    mgr_declare_aggression:   float = 0.5
    mgr_bat_first_pref:       float = 0.5

    # Declared Seconds — per-game state (reset between games via dataclass defaults)
    outs_banked:       int = 0       # 27 - declared_at_out if declared, else 0
    declared_at_out:   Optional[int] = None
    declare_score_for:     int = 0   # team's own score at moment of declaration
    declare_score_against: int = 0   # opp's score at moment of declaration
    seconds_used:      bool = False
    seconds_outs_used: int = 0

    # Left-on-base counter — runners stranded when a half ends (out 27)
    # or when this team declares Seconds with runners still on the bags.
    # Resets to 0 at game start via dataclass defaults; accumulated by the
    # half-end / declaration hooks. Surfaced in the box score so a 15 H /
    # 12 R / 6 LOB line reads differently from 12 H / 15 R / 1 LOB.
    lob: int = 0

    # Shift telemetry (per-game, accumulates over the game). Stamped on
    # the FIELDING team for the play that produced the effect.
    shift_outs_added:  int = 0   # outs the shift converted from singles
    shift_hits_lost:   int = 0   # hits the shift gave up (oppo through the gap)

    # Power Play (optional rule) — per-game manager-behavior rolls and
    # telemetry. The skip / mistime flags are rolled lazily once per game
    # (None = not yet rolled) so they vary game-to-game, not per manager.
    power_play_skip:    Optional[bool] = None   # never deploy this game
    power_play_mistime: Optional[bool] = None   # deploy too early / too late
    power_play_mistime_late: bool = False       # mistime flavor: True=late, False=early
    pp_xbh_held:        int = 0   # XBH the nickel cut down to singles
    pp_hits_converted:  int = 0   # outfield singles the nickel turned into outs

    # Cricket Batting Order (optional rule) — per-game opt-in stamped by
    # sim.py from the per-league flag. None = not opted in (fall back to the
    # global cfg.CRICKET_BATTING_ORDER_ENABLED default). See
    # o27/engine/cricket_order.py.
    cricket_order_enabled: Optional[bool] = None
    # Manager flip-aggression persona (0.5 neutral) — how readily this skipper
    # SPENDS an earned cricket flip, and (inversely) how reluctant he is to
    # burn a joker that would forfeit the flip. Stamped by sim.py from the
    # teams row; read by manager.should_use_flip / should_insert_joker.
    mgr_flip_aggression: float = 0.5
    # Earned-but-unspent cricket flip. Armed by advance_lineup at a joker-free
    # cycle boundary (only when the rule is on); consumed — used or lost — by
    # the manager decision at the top of the new cycle. Never banks: at most
    # one is pending at a time, and reset_half clears it.
    pending_flip: bool = False

    # Joker pool — 3 tactical pinch-hitters available per game. They are
    # NOT in the base lineup; the manager AI inserts them per-PA based on
    # leverage. Cooldown is per-turnover: each joker may be deployed at
    # most once per time through the order, then becomes eligible again
    # when the base lineup cycles (jokers_used_this_cycle is cleared in
    # advance_lineup). There is no overall per-game cap, so across a long
    # half a joker can be brought back cycle after cycle — but never more
    # than once within a single cycle. Insertions add an extra PA to the
    # rotation; the joker bats then returns to the bench without taking a
    # roster slot or a field position.
    jokers_available: list = field(default_factory=list)
    jokers_used_this_cycle: set = field(default_factory=set)   # reset on lineup wrap
    jokers_used_this_half: set = field(default_factory=set)    # legacy alias
    lineup_cycle_number: int = 0   # increments when lineup_position wraps

    # Substitution economy — bench pool + one-way exit set. `bench` is the
    # active roster MINUS the starting nine (8 fielders + starting DH/SP)
    # and the joker pool. Populated by sim.py at game start. Substitution
    # candidate-pickers filter on `bench` to avoid pulling the current
    # starters; the one-way invariant is enforced by `substituted_out` —
    # any player ID in this set is gone for the rest of the game, including
    # super-innings and Declared Seconds.
    bench: list = field(default_factory=list)
    substituted_out: set = field(default_factory=set)
    # Field-only defensive replacements. Maps {out_player_id: in_player} for
    # tactical (non-injury) defensive subs. In O27 the batting order is a
    # fixed lineup card: a defensive sub swaps the GLOVE in the field but the
    # displaced starter is "not out" and keeps his slot in the batting order
    # for the whole game (the substitute fields without taking a bat). This
    # set lets the manager avoid re-replacing an already-covered starter and
    # avoid re-using a glove that's already in the field — without ever
    # touching `lineup` (the batting order) or `substituted_out` (true exits).
    field_replacements: dict = field(default_factory=dict)
    # Phase-transition swap — fires at most once per game. The first-batting
    # team swaps in a defensive unit late in their offensive phase so the
    # better gloves are in place when they take the field. Reset per game
    # via dataclass defaults.
    phase_swap_done: bool = False

    def current_batter(self) -> Player:
        """Get the current batter from the active lineup."""
        if not self.lineup:
            raise ValueError(f"Team {self.name} has no active lineup.")
        return self.lineup[self.lineup_position % len(self.lineup)]

    def advance_lineup(self) -> None:
        """Advance the lineup position (wraps around).

        Super-innings continue the regular batting order from wherever it
        left off — they are normal innings, not a separate selected lineup.
        """
        n = len(self.lineup)
        new_pos = (self.lineup_position + 1) % n
        if new_pos == 0 and n > 0:
            # Lineup wrapped to top of order — start of a new cycle.
            # Cricket Batting Order (optional rule): arm a flip opportunity if
            # this trip was joker-free (checked BEFORE clearing the cooldown
            # set, which is the record of whether a joker was deployed). The
            # manager decides at the top of the new cycle whether to spend it
            # (prob.py / manager.should_use_flip). Inert when the rule is off.
            if not self.jokers_used_this_cycle and _cricket_order.cricket_order_on(self):
                self.pending_flip = True
            self.lineup_cycle_number += 1
            # Per-cycle joker cooldown resets here. A joker may be deployed
            # at most once per time through the order; once every base
            # hitter has batted (joker PAs do NOT advance the lineup, so
            # they never count toward a cycle) the whole pool is eligible
            # again. See manager.can_insert_joker / should_insert_joker.
            self.jokers_used_this_cycle = set()
        self.lineup_position = new_pos

    def reset_half(self) -> None:
        """Reset intra-half tracking at the start of a new half.

        Jokers were removed in Task #47 — DH-only roster has no per-half
        eligibility tracking, so this is now a no-op kept for call-site
        compatibility with the engine.
        """
        return

    def get_player(self, player_id: str) -> Optional[Player]:
        """Look up a player by ID anywhere in the roster."""
        for p in self.roster:
            if p.player_id == player_id:
                return p
        return None

    def is_available(self, player_id: str) -> bool:
        """True if the player has not been substituted out this game.

        The one-way invariant: once a position player exits, they're done
        — they don't come back for super-innings or Declared Seconds.
        Every substitution candidate-pick must call this. Pitchers are
        also included so a pulled pitcher can't return to the mound.
        """
        return player_id not in self.substituted_out


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
    half: str = "top"              # "top" | "bottom" | "super_top" | "super_bottom" | "seconds_first" | "seconds_second"
    super_inning_number: int = 0   # 0 = regulation; increments each super tiebreaker
    super_outs_target: int = 30    # cumulative out count that ends the current super half (27 + 3*round)

    # --- Declared Seconds ---
    home_bats_first:     Optional[bool] = None   # None until the home manager picks pre-game
    in_seconds_phase:    bool = False            # mutually exclusive with is_super_inning
    seconds_phase_number: int = 0                # 0 = not in seconds; 1+ = which seconds round
    first_batting_team:  Optional["Team"] = None # set at game start (top/bottom of 1st)
    second_batting_team: Optional["Team"] = None

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
    pitcher_balks_this_spell:         int = 0
    pitcher_catchers_balk_this_spell: int = 0
    pitcher_ci_this_spell:            int = 0
    pitcher_errors_this_spell: int = 0      # defensive errors during current spell
                                            # (post-error runs in the spell charge UER)
    # Walk-Back: rule-placed-runner PAs this pitcher has faced this spell,
    # and the subset where the Walk-Back runner scored. Flushed to
    # SpellRecord at spell end (manager.pick_new_pitcher / game._close_spell).
    pitcher_wb_faced_this_spell: int = 0
    pitcher_wb_runs_this_spell:  int = 0
    # Inherited-runner per-spell counters (flushed to SpellRecord at spell end).
    pitcher_ir_inherited_this_spell: int = 0
    pitcher_ir_scored_this_spell:    int = 0
    # Finisher tracking. entry/min lead are lazily initialized on the spell's
    # first event (so entry = the lead when he took the mound); lead_init gates
    # that. See pa.apply_event + game._close_spell / manager.pitching_change.
    pitcher_entry_lead_this_spell: int = 0
    pitcher_min_lead_this_spell:   int = 0
    pitcher_lead_init_this_spell:  bool = False
    # Arc-bucketed per-spell counters (indices 0/1/2 → arc 1/2/3 of the
    # defending team's 27-out running half). Reset at spell start.
    pitcher_er_arc_this_spell: list = field(default_factory=lambda: [0, 0, 0])
    pitcher_k_arc_this_spell:  list = field(default_factory=lambda: [0, 0, 0])
    pitcher_fo_arc_this_spell: list = field(default_factory=lambda: [0, 0, 0])
    pitcher_bf_arc_this_spell: list = field(default_factory=lambda: [0, 0, 0])
    # Live times-through-the-order counters for the current spell (look
    # buckets: 1st / 2nd / 3rd+ time the batter has faced this pitcher this
    # game). Persisted onto SpellRecord.{k,fo,bf}_tto at spell end.
    pitcher_k_tto_this_spell:  list = field(default_factory=lambda: [0, 0, 0])
    pitcher_fo_tto_this_spell: list = field(default_factory=lambda: [0, 0, 0])
    pitcher_bf_tto_this_spell: list = field(default_factory=lambda: [0, 0, 0])
    # Outs at the start of the current PA — used to bucket BF/K/BB/FO so
    # an out-producing AB charges its event to the arc the AB began in,
    # not the arc the resulting out crossed into.
    pa_start_outs: int = 0
    pitcher_start_pa: int = 0          # total_pa_this_half when spell began
    total_pa_this_half: int = 0        # cumulative PA count this half (incremented on PA end)
    current_pitcher_id: Optional[str] = None
    # Hit-and-run flag — set True when an h&r SB attempt succeeds; the
    # next pitch resolution applies a multiplicative K-weight reduction
    # (batter is swinging to protect). Cleared at PA boundaries.
    hit_and_run_active: bool = False
    spell_log: list = field(default_factory=list)

    # --- Walk-Back (post-HR rule-placed runner) ---
    # After an HR resolves MLB-exactly, the HR-hitter "walks back" to 3B as
    # a live bonus runner and is added to this set. From there he is a normal
    # baserunner with no special handling: he advances, scores, is put out,
    # or is stranded at the half's end exactly like any other runner on 3B.
    # His fate is NOT a one-PA window — he persists across PAs until resolved
    # (mirrors the extra-innings ghost runner). Whenever he scores it is an
    # unearned Walk-Back run; the pitcher's wb_faced ticks once at the moment
    # his fate resolves (score / out / strand) and wb_runs ticks when he
    # scores. Holds player_ids of the Walk-Back runners currently on base.
    walk_back_runner_ids: set = field(default_factory=set)

    # Player_ids of Walk-Back bonus runners who SCORED on the event just
    # applied — reset at the top of every apply_event and populated by
    # _reconcile_walk_back. The renderer reads this to credit each batter's
    # `walkback_runs` (the per-hitter mirror of the pitcher's wb_runs).
    walk_back_scored_ids: list = field(default_factory=list)

    # Player_ids of the runners the CURRENT pitcher inherited at his pitching
    # change. Shrinks as each resolves (scores / out / strand); _reconcile_inherited
    # tallies how many scored into pitcher_ir_scored_this_spell.
    inherited_runner_ids: set = field(default_factory=set)

    # --- In-game injuries (forced mid-game removals) ---
    # One dict per player hurt mid-game and removed:
    #   {player_id, team_id, kind, outs, replaced_by}
    # The engine forces the substitution and records here; o27v2's post-game
    # layer draws severity (DTD/short/long), sets injured_until/il_tier, and
    # logs the transaction. See o27/engine/injury.py.
    in_game_injuries: list = field(default_factory=list)

    # --- Multi-hit tracking (within one at-bat) ---
    current_at_bat_hits: int = 0
    # Count of contact events (swings that put ball in play) so far in
    # the current AB. Increments at the top of _resolve_contact, resets
    # at AB end. Read by contact_quality on subsequent swings to apply
    # the eye/command second-swing modifier.
    current_at_bat_swings: int = 0

    # Shift state — set once at AB start by the defense's shift decision
    # (prob.py), consumed during contact resolution. Reset at new-batter
    # detection in ProbabilisticProvider.__call__.
    current_ab_shift_type: str = "none"     # "none" | "infield" | "outfield"
    current_ab_shift_decided: bool = False  # have we rolled this AB?

    # --- Power Play (optional rule) ---
    # When None, the engine falls back to cfg.POWER_PLAY_ENABLED (the league
    # toggle). Tests set it explicitly to force the rule on/off per game.
    power_play_enabled: Optional[bool] = None
    # Active nickel window. `open_out` is state.outs at the moment of
    # deployment (the window covers the next POWER_PLAY_WINDOW_OUTS outs);
    # cleared at every half start in run_half so it never carries over.
    power_play_open_out: Optional[int] = None
    power_play_deploy_team_id: Optional[str] = None   # which fielding side deployed
    power_play_nickel_id: Optional[str] = None        # the chosen nickel player_id
    # Per-(phase, fielding team) "already used this half" keys — use-or-lose.
    power_play_used: set = field(default_factory=set)
    # Box-score record, one dict per deployment:
    #   {team_id, team_name, start_out, end_out, phase}
    power_play_deployments: list = field(default_factory=list)
    # Set once per AB so the deploy decision is considered at most once per AB.
    power_play_checked_this_ab: bool = False
    # Presence lift — banded multiplicative boost to fielding defense_rating and
    # the active pitcher's effectiveness while the window is open. `presence` is
    # the fraction rolled at window open (scaled by the nickel's glove); the
    # originals/active pair mirror the leadership-flare stash so the lift is
    # restored every PA boundary and leaks nothing.
    power_play_presence: float = 0.0
    pp_presence_originals: list = field(default_factory=list)
    pp_presence_active: bool = False
    # Snapshotted once per AB (at PA start, after the deploy check): True when
    # the batting team is facing an active nickel window. Read by the renderer
    # to charge the batter's short-handed offense counters for the whole PA.
    power_play_sh_active: bool = False
    # Nickel saves attributed to the FIELDING pitcher on the mound (the one
    # with the nickel behind him): {pitcher_id: {"xbh_saved", "hits_saved"}}.
    # Folded into the Power Play pitcher rows by sim.py.
    pp_pitcher_support: dict = field(default_factory=dict)

    # --- Joker insertion override ---
    # When the manager inserts a joker, this field holds the joker Player
    # for one PA. The current_batter property checks this first, so the
    # joker bats instead of the base-lineup batter. After the joker AB
    # ends, _end_at_bat clears this and does NOT call advance_lineup
    # (the joker insertion is EXTRA — base lineup position is unchanged).
    batter_override: Optional[Player] = None

    # --- Halftime target ---
    target_score: Optional[int] = None         # visitors' score; set at halftime

    # --- Per-PA leadership flare ---
    # When the per-PA flare fires for the batter and/or pitcher, the
    # engine MUTATES their rating fields in place at PA start (so every
    # downstream read — pitch_probs, contact_quality, fielding rolls,
    # talent gate — sees the lifted value uniformly, no plumbing needed).
    # `flare_originals` holds (object, attr_name, original_value) tuples
    # so `_end_at_bat` can restore everything via try/finally semantics
    # at PA boundary. `flare_lift_active` is True between PA start and
    # PA end while a flare is in effect.
    flare_originals: list = field(default_factory=list)
    flare_lift_active: bool = False

    # --- Super-inning rounds ---
    super_inning_rounds: list = field(default_factory=list)

    # --- Per-game batter running stats ---
    # Keyed by player_id. Used by:
    #   - The joker decay system in prob.py (joker_pa count drives the
    #     rating-decay multiplier so a joker's effective ratings sag with
    #     each successive AB this game).
    #   - The intentional-walk decision in manager.py (hot-streak factor
    #     reads pa / h to decide whether to give a free pass).
    # Resets naturally each new GameState (fresh dict per game).
    batter_game_stats: dict = field(default_factory=dict)

    # --- Per-game batter-vs-pitcher matchup counts ---
    # Keyed by (pitcher_id, batter_id) → number of COMPLETED PAs this batter
    # has had against this pitcher this game. Read by the times-through-the-
    # order familiarity model in prob.py: the more times a hitter has faced
    # an arm, the more he's timed it up. Keying on the pitcher means a fresh
    # reliever resets familiarity to zero against everyone — bringing in a
    # new look is itself a lever. Incremented in pa._end_at_bat at PA close.
    matchup_pa: dict = field(default_factory=dict)

    # --- Raw event log ---
    events: list = field(default_factory=list)

    # --- Structured substitution log ---
    # One Substitution record per position-player swap (PH / PR / PF /
    # joker / pitching change). Walked by tests to assert the one-way
    # invariant and by the AAR / box-score render to surface substitution
    # volume per game. Distinct from `events` (renderer-shaped dicts) so
    # the substitution invariant doesn't depend on the renderer being
    # plumbed in.
    substitution_log: list = field(default_factory=list)

    # --- Winner ---
    winner: Optional[str] = None    # "visitors" | "home" | None

    # --- Weather (per-game game-conditions context). None = neutral. Read
    # only by prob.py; everything else passes it through opaquely.
    weather: Optional[object] = None  # o27.engine.weather.Weather

    # --- Park dimensions (home park, the only one that matters for the
    # park-shape gameplay hook). Dict shaped like {lf, lcf, cf, rcf,
    # rf, wall_h}. None / empty = neutral 380-ft fence, no hook
    # mutations. See o27.engine.park_effects.apply_park_effects.
    park_dimensions: Optional[dict] = None

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def batting_team(self) -> Team:
        # Seconds halves dispatch via first/second_batting_team because the
        # comeback team isn't necessarily home or visitors — it's whichever
        # team batted first/second in regulation.
        if self.half == "seconds_first":
            return self.first_batting_team or self.visitors
        if self.half == "seconds_second":
            return self.second_batting_team or self.home
        if self.half in ("top", "super_top"):
            return self.visitors
        return self.home

    @property
    def fielding_team(self) -> Team:
        if self.half == "seconds_first":
            return self.second_batting_team or self.home
        if self.half == "seconds_second":
            return self.first_batting_team or self.visitors
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
        """Return the lineup currently active for the batting team.

        Always the regular Team.lineup — super-innings continue the same
        batting order rather than using a separate selected lineup.
        """
        return list(self.batting_team.lineup)

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

    @property
    def phase_number(self) -> int:
        """Unified phase index. 0 = regulation. Positive = the round number
        of either an SI tiebreaker or a Declared Seconds round (which are
        mutually exclusive within a single game). The renderer uses this as
        the `phase` stamp on per-event rows so stats split correctly into
        game_*_stats.phase buckets.
        """
        if self.is_super_inning:
            return int(self.super_inning_number or 0)
        if self.in_seconds_phase:
            return int(self.seconds_phase_number or 0)
        return 0

    def bgs(self, pid: str) -> dict:
        """Get-or-create the per-game running stat row for a player.

        Returns a dict with keys: pa, h, bb, joker_pa. Mutated in place by
        pa.py at event boundaries; read by prob.py (joker decay) and
        manager.py (intentional walk hot-streak factor).
        """
        return self.batter_game_stats.setdefault(
            pid, {"pa": 0, "h": 0, "bb": 0, "joker_pa": 0}
        )

    def matchup_count(self, pitcher_id: str, batter_id: str) -> int:
        """Prior completed PAs of this batter vs this pitcher this game.

        0 the first time they meet (familiarity model collapses to identity),
        1 the second time, etc. Drives the times-through-the-order penalty.
        """
        return self.matchup_pa.get((pitcher_id, batter_id), 0)

    def out_cap(self) -> int:
        """Numeric out ceiling for the current phase, ignoring walk-offs.

        The half ends at this many cumulative outs and no PA may push the
        phase past it. Mirrors the thresholds used by is_half_over():

          - regulation        → 27
          - super-inning      → super_outs_target (cumulative: 27 + 3*round)
          - seconds round     → the batting team's banked outs
        """
        if self.is_super_inning:
            return int(self.super_outs_target or 0)
        if self.in_seconds_phase:
            return max(0, int(self.batting_team.outs_banked or 0))
        return 27

    def is_half_over(self) -> bool:
        """True when the current half has ended."""
        if self.is_super_inning:
            # Super-innings are normal 3-out innings, measured in cumulative
            # outs (the first super out is #28, so each round's half ends at
            # super_outs_target = 27 + 3*round). The bottom half also ends
            # early on a walk-off — the moment the second-batting team leads.
            return self.outs >= self.out_cap() or self._super_walkoff()
        if self.in_seconds_phase:
            # Seconds round: ends when the team has used its banked outs OR
            # the comeback walk-off fires (lead change with opp out of options).
            return self.outs >= self.out_cap() or self._seconds_walkoff()
        return self.outs >= self.out_cap() or self._regulation_walkoff()

    def _regulation_walkoff(self) -> bool:
        """Walk-off in regulation: only valid in the SECOND half (the half
        belonging to second_batting_team), when the currently batting team
        has taken the lead AND the first-batting team has no banked outs
        to come back with. Walk-off PAs are normal PAs — this check fires
        *after* the PA completes, so the half just ends at the current out
        count rather than being truncated mid-PA.
        """
        first  = self.first_batting_team
        second = self.second_batting_team
        if first is None or second is None:
            return False
        # Must be in the half where the second-batting team is hitting.
        if self.batting_team is not second:
            return False
        # Lead must belong to the team batting now (second).
        if self.score.get(second.team_id, 0) <= self.score.get(first.team_id, 0):
            return False
        # And the first-batting team must have no banked outs to use.
        return int(first.outs_banked or 0) <= 0

    def _seconds_walkoff(self) -> bool:
        """Walk-off in a seconds half.

        - `seconds_first` (first-batting team): never walks off. They bat
          their full banked-outs allotment, analogous to the top of the
          9th where visitors finish even when leading.
        - `seconds_second` (second-batting team): walks off the moment
          they retake the lead. The first-batting team has already used
          their seconds, so they cannot rebut — analogous to a
          bottom-of-9th walk-off.
        """
        if not self.in_seconds_phase:
            return False
        if self.half == "seconds_first":
            return False
        bat = self.batting_team
        fld = self.fielding_team
        if self.score.get(bat.team_id, 0) <= self.score.get(fld.team_id, 0):
            return False
        return True

    def _super_walkoff(self) -> bool:
        """Walk-off in a super-inning round.

        Mirrors the seconds-phase rule. The visitors bat the top of the
        round to their full 5-dismissal allotment (no walk-off, like the
        top of the 9th). The home team, batting the bottom, walks off the
        instant they take the lead — the visitors have already used their
        round and cannot rebut. Fires after the PA completes, so the half
        ends at the current dismissal count rather than mid-PA.
        """
        if self.half != "super_bottom":
            return False
        bat = self.batting_team
        fld = self.fielding_team
        return self.score.get(bat.team_id, 0) > self.score.get(fld.team_id, 0)

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
