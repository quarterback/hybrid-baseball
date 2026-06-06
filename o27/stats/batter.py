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
    ibb: int = 0     # Intentional walks (subset of bb).
    k: int = 0
    hbp: int = 0
    sh: int = 0            # Sacrifice bunts (successful — advances a runner, not an AB)
    # Expanded bunting (multi-type). bunt_att counts every bunt PA; bunt_hits
    # are bunt singles (a subset of hits); sqz / sqz_rbi track squeeze plays
    # and the runs they drove home from third.
    bunt_att: int = 0
    bunt_hits: int = 0
    sqz: int = 0
    sqz_rbi: int = 0
    sty: int = 0           # Stays (internal only — not displayed in UI)
    outs_recorded: int = 0  # OR — times this batter was retired
    stay_rbi: int = 0
    stay_hits: int = 0      # Hits credited on a 2C event (subset of `hits`)
    c2_strand_out: int = 0  # AB ended in a batter-out AFTER ≥1 credited 2C this
                            # AB — the batter advanced runners then made an out,
                            # valued in wOBA like a runner being put out.
    multi_hit_abs: int = 0  # At-bats with 2+ credited hits
    walkback_runs: int = 0  # Runs scored as a Walk-Back bonus runner (this
                            # hitter's homer set him back on base and he was
                            # driven in again). Per-hitter mirror of the
                            # pitcher's wb_runs; subset of `runs`.

    # RISP (runners in scoring position — a runner on 2B and/or 3B at the
    # start of the PA). Each is the subset of the matching counter that was
    # accrued in a RISP situation, so a full RISP slash line (AVG/OBP/SLG/OPS)
    # plus RISP RBI can be computed downstream with the usual O27 formulas.
    # Unlike the engine's RISP-pressure probability model, these are recorded
    # outcomes — the "how good is this bat at cashing in RISP" stat.
    risp_pa: int = 0
    risp_ab: int = 0
    risp_h: int = 0
    risp_2b: int = 0
    risp_3b: int = 0
    risp_hr: int = 0
    risp_bb: int = 0
    risp_hbp: int = 0
    risp_rbi: int = 0

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

    # Per-base PA advancement stats. Where c2_* tracks advancement during
    # 2C events only, adv_* tracks advancement across the WHOLE plate
    # appearance — runs from 2C events PLUS run-chosen contact PLUS
    # BB-force PLUS sac-bunt all roll up here. Displayed as 1B%/2B%/3B%
    # conversion rates on the player page (note: 1B here means "runner
    # who started on first base," not the hit type).
    #   adv_op_Xb  — runner was on Xb at PA start
    #   adv_adv_Xb — that runner moved to a higher base OR scored (binary)
    # A runner thrown out during the PA counts as an opportunity but not
    # a successful advance. Conversion% = adv / op displayed per-batter.
    adv_op_1b: int = 0
    adv_adv_1b: int = 0
    adv_op_2b: int = 0
    adv_adv_2b: int = 0
    adv_op_3b: int = 0
    adv_adv_3b: int = 0
    # Runners Advanced (RAD) — graded advancement, counting the BASES
    # each runner gained instead of binary success/fail. 1B → 2B = +1,
    # 1B → 3B = +2, 1B → home = +3, etc. Mirrors MLB's Total Bases
    # concept but applied to RUNNER movement rather than the batter's
    # own movement. Total RAD = rad_1b + rad_2b + rad_3b — the
    # headline "runners advanced" metric for the batter.
    rad_1b: int = 0
    rad_2b: int = 0
    rad_3b: int = 0

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
    # Inning (1-indexed: outs // 3 + 1) at which this row entered the game.
    # 0 = starter. Footnote rendering reads this to emit "in the 5th."
    entered_inning: int = 0

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
    toa: int = 0    # Thrown out advancing — runner cut down on a batted ball
                    # (distinct from CS; happens to baserunners when the
                    # advancement-table roll lands on the out outcome).
    # Per-fielder defense events (credited to the player who fielded the
    # play, NOT to the batter at the plate). Stored on BatterStats since
    # every fielder is also a batter.
    po: int = 0     # Putouts — outs recorded as the primary fielder on a play
    a:  int = 0     # Assists — credited on throwing outs and DP / TP chains
    e:  int = 0     # Errors committed

    # Short-handed offense (Power Play optional rule). Charged to the BATTER
    # for plate appearances taken while the opposing defense had its nickel
    # fielder deployed (state.power_play_sh_active snapshotted at PA start).
    # These are subsets of the player's overall pa/ab/hits, sliced to the
    # short-handed condition so "who hits through a loaded defense" is
    # measurable. Only ever non-zero in leagues where the rule is on.
    sh_pa:  int = 0   # short-handed plate appearances
    sh_ab:  int = 0   # short-handed at-bats
    sh_hits: int = 0  # short-handed hits
