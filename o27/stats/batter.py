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
    sty: int = 0           # Stays (internal only — not displayed in UI)
    outs_recorded: int = 0  # OR — times this batter was retired
    stay_rbi: int = 0
    stay_hits: int = 0      # Hits credited on a 2C event (subset of `hits`)
    multi_hit_abs: int = 0  # At-bats with 2+ credited hits

    # 2C moved-runner stats (Apollo-style "did this 2C swing actually
    # move runners?"). Per-base opportunities and successes — a runner
    # is "moved" if their post-stay position is a higher base than their
    # pre-stay position OR they scored cleanly. A runner thrown out on
    # the play counts as an opportunity but NOT a successful move.
    c2_op_1b: int = 0       # 2C events with a runner on 1B
    c2_adv_1b: int = 0      # ...where the 1B runner advanced
    c2_op_2b: int = 0
    c2_adv_2b: int = 0
    c2_op_3b: int = 0
    c2_adv_3b: int = 0      # 3B "advance" = scored

    # Box-score entry classification. Set by the render pipeline:
    #   "starter" — batted at the start of the game (default)
    #   "PH"       — entered as a pinch hitter for `replaced_player_id`
    #   "PR"       — entered as a pinch runner
    #   "DEF"      — defensive substitution (mid-game)
    #   "joker"    — tactical joker insertion (one-PA appearance)
    #   "joker_field" — joker pulled in to play the field (rare)
    # `replaced_player_id` lets the box score indent this row under the
    # player they replaced so the lineup ordering reads naturally.
    entry_type: str = "starter"
    replaced_player_id: str = ""

    # Grounded into double / triple play — for box-score annotations.
    # Incremented in the run path of contact resolution when the engine
    # produces a double_play / triple_play outcome attributed to this
    # batter. Pitcher-side DPs/TPs induced are not separately tracked
    # here (they show up implicitly through outs_recorded on the GP).
    gidp: int = 0
    gitp: int = 0
    # Counting stats persisted post-realism layer.
    sb: int = 0     # Successful steals charged to this runner
    cs: int = 0     # Caught-stealing outs charged to this runner
    fo: int = 0     # Foul-outs (3-foul rule) — subset of outs_recorded
    roe: int = 0    # Reached on error (NOT a hit; AB credited; defensive miscue)
    # Per-fielder defense events (credited to the player who fielded the
    # play, NOT to the batter at the plate). Stored on BatterStats since
    # every fielder is also a batter.
    po: int = 0     # Putouts — outs recorded as the primary fielder on a play
    a:  int = 0     # Assists — credited on throwing outs and DP / TP chains
    e:  int = 0     # Errors committed
