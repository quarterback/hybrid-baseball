# After-Action Report — Defense, Battery & Runs/Hits Decoupling

**Date completed:** 2026-05-30
**Branch:** `claude/baseball-hits-runs-variance-Cv0iW`
**Commits:** `6268ec2`, `3a5eece`, `0856758`, `fe5bb71`, `de90c8a`, `f3c4d7c`,
`115430c`, `ac1c35a` (+ demo-roster + this AAR)

---

## What was asked for

A multi-session arc that started as "make hits worth fewer runs" and grew into
a full **offense-vs-defense rebalance**. O27 had been engineered almost entirely
for offense; the through-line of this work is giving *defensive* attributes
(arm, glove, pitch-calling) real agency to bend a game. In order:

1. Decouple runs from hits — fewer runs per hit (target was ~1.5 H/R).
2. Add **bunting** as a visible, stat-tracked play.
3. A **rich batted-ball taxonomy** — name the contact, "more kinds of hits."
4. **Pickoffs + baserunning errors** — an aggressive, chaotic running game.
5. **Defense buildout** — aggressive shifts, fielding ratings that swing
   outcomes, and probabilistic **defensive gems**.
6. **Catching buildout** — catcher **game-calling** as a real lever, plus
   catcher **fatigue + rotation** (no catcher is effective for all 27 outs).

---

## What was built

### 1. Batted-ball texture — the "wasted hits" mechanism (`6268ec2`)

A hit now carries a **texture** — `outcome_dict["batted_ball"]` ∈ {dribbler,
grounder, liner, flyball} — rolled from contact quality + batter power
(`_roll_batted_ball`). Carried as a *separate field*, NOT a new `hit_type`, so
the ~12 places that switch on the closed `hit_type` set (stat counting, render)
are untouched.

**Key lesson (learned the hard way).** The first attempt made grounder runners
*hold*, via a negative score-roll shift. It did **nothing** — in O27's single
continuous 27-out inning there is no inning-end to strand a held runner; he
scores on a later PA (~87% of baserunners score eventually). *Holding ≠
stranding.* The only thing that lowers runs-per-hit is **erasing** a runner. So
the texture's real effect is an additive bump to the "thrown out advancing"
bucket of the advancement tables (`BATTED_BALL_OUT_SHIFT`): a grounder single
draws the throw and the trailing runner is gunned down.

**Honest ceiling finding.** Even at absurd settings (half of all grounder-single
runners thrown out, 80% of contact forced to grounders) this tops out at
**H/R ≈ 1.25**, and the game shrinks (baserunning outs burn the out-clock). The
requested **1.5 is not reachable** by batted-ball texture in the 27-out format —
that needs a structural change (real innings that strand runners). Landed a
realistic setting instead: **H/R 1.00 → 1.14**.

### 2. Bunting made real (`3a5eece`)

Bunting *existed* end-to-end (`manager.should_bunt` → prob hook →
`pa.sac_bunt`) but was invisible: it fired **0.133×/team-game** and the
`sac_bunt` event had **no branch in the stat accumulator**, so a bunt single
was never counted as a hit and a sacrifice was never recorded.

- Rate: `SAC_BUNT_BASE_PROB` 0.05 → 0.16, `SAC_BUNT_RUNGAME_SCALE` 0.20 → 0.50
  → **0.985×/team-game** (~1 bunt/team/game).
- Stats: added a `sac_bunt` branch (hit → PA+AB+H; sacrifice → PA+SH no AB;
  fail → PA+AB) that lets the existing out-reconciliation tail charge the out
  and credit runs. Added `BatterStats.sh`.
- Verified: SH recorded 322 == sacrifice count 322; run-balance exact 200/200.

### 3. Rich batted-ball taxonomy (`0856758`)

The engine already sampled (EV, LA, spray) for every ball but used it only for
spray charts. `batted_ball.classify_batted_ball(ev, la, spray, hit_type)` turns
that physics into a **descriptive name** — "swinging bunt", "seeing-eye
grounder", "frozen rope", "Texas leaguer", "no-doubter down the line", "can of
corn". Reconciled with the final `hit_type` so a name can never contradict the
result. **Purely descriptive** — never read back into mechanics. 68 distinct
names over a sample; surfaced as `outcome["batted_ball_name"]` → `disp`.

### 4. Pickoffs + baserunning errors (`fe5bb71`)

Leaned on the existing-but-near-zero baserunning-out machinery (pickoffs,
TOOTBLAN). Raised `PICKOFF_ATTEMPT_BASE` 0.004 → 0.035, `PICKOFF_SUCCESS_BASE`
0.10 → 0.38, `TOOTBLAN_SAFE_BASE` 0.62 → 0.46. **Failed pickoffs are now silent
in the play-by-play** (not even the bare header line) — only an actual pickout
earns a line. Net **H/R 1.14 → 1.23**.

**Deliberately above real baseball** (~0.94 successful pickoffs/team-game vs
MLB ~0.06). O27's second-chance at-bats pressure the running game into
aggressive secondary leads, so an elevated, chaotic running game is a design
choice, per the user.

### 5. Defense buildout (`de90c8a`)

- **Aggressive shifts.** Decision gets a floor (`SHIFT_BASE_PROB` 0.35) +
  bigger scale (`SHIFT_DECISION_SCALE` 1.0 → 1.8, cap 0.95) so even
  neutral-spray batters draw a shift and pull hitters get shifted nearly every
  AB. Bite raised (`SHIFT_PULL_OUT_PROB` 0.30 → 0.42, `SHIFT_OF_XBH_HELD_PROB`
  0.30 → 0.40). ~0.60 shift outs added/team-game.
- **Fielding swings outcomes.** `DEFENSE_RANGE_SHIFT_SCALE` 0.10 → 0.15.
- **Defensive gems.** A fielder turns a would-be hit into an out, rendered
  "ROBBED! {fielder} lays out for the diving grab…". **Per-fielder +
  probabilistic**: a base rate lets anyone in the pool with a decent glove
  flash one; the individual fielder's defense/arm scales it up (elite) or
  toward zero (poor) — never a fixed "only this guy" trait. The fielder is
  drawn from the position pool **weighted by glove rating** (positions aren't
  plumbed onto Players in O27, so glove rating IS the selector). ~0.71
  gems/team-game. Config: `GEM_BASE_XBH/SINGLE`, `GEM_HARD_MULT`,
  `GEM_FIELDER_SCALE`, `GEM_ARM_SCALE`, `GEM_MAX`. League BA .470 → .425.

### 6. Catcher game-calling (`f3c4d7c`)

New `Player.game_calling`. `_fielding_catcher(state)` resolves the catcher as
the lineup's best `defense_catcher` non-pitcher (so a catcher sub automatically
re-points "the catcher"). `_catcher_gc_shift` turns their game_calling into a
contact-quality shift away from hard contact, folded into `contact_quality` via
a new `catcher_shift` param. **NOT framing** (O27 skips framing by design) —
this is pitch-calling/sequencing. Demo rosters given a real catching corps so
the lever is live. Verified: an elite caller (gc 0.85) suppresses opponent
offense by **3.6 runs** vs a poor one (gc 0.20).

### 7. Catcher fatigue + rotation (`115430c`, `ac1c35a`)

- **Fatigue (always live).** `Team.catcher_outs_caught` accumulates per out in
  `pa._record_out` (reset on a swap). Past `CATCHER_FATIGUE_THRESHOLD` it ramps
  a fatigue fraction that decays the catcher's effective **game_calling** AND
  **arm** (`_catcher_arm_eff`, used at all SB/stay read sites). A tired catcher
  calls a worse game and throws weaker — +0.58 R/game late-game lift; arm
  0.66 → 0.53 at 27 outs caught.
- **Rotation.** `manager.should_swap_catcher` pulls a gassed catcher for the
  best reserve, reusing the `defensive_sub` event/handler and resetting the
  counter. **Situational**: protecting a lead → defensive specialist (glove +
  arm + game-calling); chasing → spark-plug bat. Excludes jokers (they can't be
  subbed once fielded). Draws from the reserve pool (roster − lineup), matching
  the real ~47-man roster model (`aar-task-65-expanded-rosters.md`).

### 8. Demo rosters with a catching corps

The 12-player demo teams carried no reserve catcher, so rotation only fired in
constructed tests. Added a reserve catcher to each (in `roster`, not `lineup`):
Foxes' **Q. Bauer** (defensive specialist), Bears' **L. Russo** (spark-plug
bat). Rotation now fires in the demo sim — FR1 266/300, BR1 207/300 games.

---

## Final state (all levers on)

| Metric | Baseline | Final |
|---|---|---|
| H/R | 1.00 | **1.23** |
| League BA | .470 | ~.42 |
| Bunts/team-game | 0.13 | ~0.99 |
| Pickoff outs/team-game | ~0.06 | ~0.94 |
| Defensive gems/team-game | 0 | ~0.71 |
| Shift outs/team-game | (mixed in) | ~0.60 |

48 engine + RISP tests pass; sanity green (R/G ~21, super-inning < 8%);
run-balance invariant (Σ batter R == final score) exact across every batch.

---

## Key decisions & trade-offs

- **Texture as a data field, not a new hit_type** — avoids breaking ~12
  `hit_type` string comparisons across stat/render code.
- **Erase, don't hold** — the load-bearing insight for the whole runs/hits
  arc; verified empirically (holding shifts were a measured no-op).
- **Gems & game-calling key on ratings, not fixed positions** — positions
  aren't stamped on Players, and "anyone with a good glove can flash one" was
  the explicit design intent, so glove/arm ratings *are* the selector.
- **Aggressive, not realistic, running game & shifts** — explicit user choice;
  O27's second-chance structure justifies a chaotic, defense-weaponized game.

---

## Process notes (honest)

- **Commit `576fe1d` is a no-op with a fabricated commit message.** Its config
  constants never landed (a silently-failed edit), and its hold-based mechanism
  didn't move runs anyway. `6268ec2` corrects the record with real numbers and
  the working (erasure-based) mechanism. `576fe1d` is left in history per the
  user's call; flagged here so it isn't trusted.
- **A mid-session environment glitch reset local HEAD** back to the plan commit
  (`9310744`) and discarded uncommitted edits. No pushed work was lost — the
  remote tip and all commit objects were intact; local was restored via
  `git reset --hard` to the remote tip. The demo-roster edits were redone.

---

## Known issues / follow-up candidates

- **1.5 H/R needs a structural change** (real innings / rally-ending
  boundaries), out of scope here; ~1.23 is the practical ceiling for the
  27-out format with these levers.
- **Catcher fatigue is in-game only.** It complements (does not replace) the
  season-level catcher rest mechanic in `aar-roster-utilization-and-tactical-subs.md`.
- **Pre-existing stale test** `test_realism_identity.py::test_resolve_contact_
  table_unchanged_…` fails independent of this work (neutral 'hard' single rate
  is .253 vs its .185 expectation even with all defense layers off).
- **Media-digest narration** (Frontier Scout / Ledger-Post flavor for pickoffs,
  TOOTBLANs, gems) is specced in chat but not built — a natural next feature
  now that these events carry rich fielder/texture metadata.

---

## Files changed

| File | Change |
|------|--------|
| `o27/config.py` | Batted-ball, gem, shift, pickoff/TOOTBLAN, catcher game-calling + fatigue constants |
| `o27/engine/prob.py` | `_roll_batted_ball`, out-shift erase channel, gems, `_fielding_catcher`/`_catcher_gc_shift`/`_catcher_arm_eff`, shift-decision floor |
| `o27/engine/pa.py` | `sac_bunt` already handled; `catcher_outs_caught` increment in `_record_out` |
| `o27/engine/manager.py` | `should_swap_catcher` (situational catcher rotation) |
| `o27/engine/state.py` | `Player.game_calling`, `Team.catcher_outs_caught` |
| `o27/engine/batted_ball.py` | `classify_batted_ball` taxonomy |
| `o27/render/render.py` | bunt stats, `batted_ball_name` + gem surfacing, silent failed pickoffs |
| `o27/render/templates/play_by_play.j2` | gem line, silent failed pickoff |
| `o27/stats/batter.py` | `BatterStats.sh` |
| `o27/main.py` | demo catching corps + reserve catchers |
| `scripts/ab_batted_ball.py` | tuning harness |
| `o27/tests/test_batted_ball_taxonomy.py` | taxonomy tests |
