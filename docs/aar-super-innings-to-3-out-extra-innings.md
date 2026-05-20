# After-Action Report — Super-Innings Switched to Normal 3-Out Extra Innings

**Date completed:** 2026-05-20
**Branch:** `claude/fix-super-inning-scoring-vXY0I`

---

## What was asked for

The trigger was a box score from game #53 (Missions 39, Sea Dogs 25) where San
Antonio scored **15 runs in a single super inning**:

> "you should not be able to score 15 runs in a super inning there's a bug somewhere."

The conversation then converged on a design change rather than a patch:

> "maybe instead of doing the pesapallo super inning, we should revert to 3-out
> baseball and play it normal in the super inning and the winner wins."

> "the run scoring contest is a nightmare for baseball stat keeping, it makes way
> more sense to revert to 3 out baseball and just play super innings until you
> need them to be done, walkoffs preserved and frankly the bridge between O27 and
> regular baseball. and way better for stat and record keeping."

Plus three rules clarifications that pinned down the mechanic:

> "there can be walkoffs in the super inning as soon as the 2nd batting team has
> 1 more run the game is over, no playing until they lose their outs."

> "the game gets measured still in outs not innings so it's not 3 IP, it's 30-33
> outs, etc."

> "the super innings start at out 28."

Two scoped decisions were confirmed before implementation:

1. **Manager in extras** — re-enable true normal-baseball managing (pitching
   changes, pinch hitters/runners, defensive subs, IBB). Jokers stay an
   O27-only mechanic; Declared Seconds does not apply in extras.
2. **Data/DB** — keep the schema and repurpose it. `games.super_inning` now
   means "extra-innings count," the per-phase stat bucketing stays, and the
   `super_*` naming / `SI` labels are retained. The user still calls them
   "super innings"; only the *rules* changed.

---

## Diagnosis

The old super inning was a pesäpallo-style tiebreaker. Each team picked a
5-batter "super lineup" and batted until **5 dismissals**, tracked in a
separate `super_dismissed` set instead of normal outs. Crucially:

- `GameState.is_half_over()` ended a super half only at 5 dismissals, with **no
  walk-off**. So the second-batting team kept hitting after taking the lead.
- A half could therefore string together unbounded hits before its 5th
  dismissal — that's how San Antonio posted 15 in one half.

The mechanic also had no relationship to regular baseball, which made it a
"run-scoring contest" that polluted stat and record keeping (no clean outs, no
walk-offs, separate selected lineups, no bullpen usage).

---

## What changed

The mechanic was replaced with **normal 3-out extra innings, still measured in
outs per team** (each team continues its own out count from 27 — so each team
has its own out 28, 29, 30 in the first super inning, then 31–33 in the next,
and so on), while keeping every super-inning name, label, phase bucket, and DB
column intact.

### Engine

- **`o27/engine/state.py`**
  - `is_half_over()` super branch: ends at `outs >= super_outs_target` (a normal
    3-out half) **or** `_super_walkoff()`.
  - Added `_super_walkoff()`: the bottom (second-batting/home) half ends the
    instant the home team leads — the visitors already took their top half and
    can't rebut. The visitors' top half never walks off (like the top of an
    extra inning).
  - Added `GameState.super_outs_target` (= `27 + 3*round`), since each team's
    out count continues from its regulation 27.
  - Removed the super-lineup machinery from `Team` (`super_lineup`,
    `super_dismissed`, `super_lineup_position`, `reset_super()`). Super innings
    now continue the **regular batting order** from where it left off.
    `current_batter`/`advance_lineup`/`active_lineup` simplified accordingly.
  - `SuperInningRound` slimmed to `team_name` + `runs` (a normal inning has
    nothing pesäpallo-specific to record).

- **`o27/engine/game.py`** — rewrote the super-inning loop: no super-lineup
  selection, no `setup_super_inning`, no 5-dismissal caps/asserts. Each round
  plays a 3-out top half then a 3-out (or walk-off) bottom half. Each half
  starts its team's own out counter at `out_base = 27 + 3*(r-1)` and ends at
  `out_base + 3`, so **each team's first super out is #28**: round 1 = each
  team's outs 28–30, round 2 = 31–33, etc. Deleted
  the now-unused `_default_super_lineup`/`setup_super_inning` helpers and the
  `super_selector` parameter.

- **`o27/engine/pa.py`** — removed super-inning dismissal tracking in
  `_record_out`; outs are now just normal outs.

- **`o27/engine/manager.py`** — removed the `is_super_inning` early-returns from
  the normal-baseball actions so they fire in extras: `pinch_hit`,
  `should_pinch_hit`, `should_pinch_run`, `defensive_sub`,
  `should_defensive_sub`, `should_change_pitcher`, `should_intentional_walk`,
  `should_bunt`. With each team's out count at 28–33 in extras, the manager's
  late-game out-count gates (e.g. `outs >= 18`, `outs < 6`) correctly treat
  extras as "very late game," so these tactics activate appropriately. Kept
  blocked:
  jokers (`can_insert_joker`, `should_insert_joker`, the legacy joker picker,
  `should_joker_to_field`), the exotic offense→defense bank
  (`should_swap_offensive_for_defense`), and `evaluate_declaration`.

- **`o27/engine/prob.py`** — the "rebuttal-phase offense tilt" (a higher-offense
  boost justified by the Declared-Seconds pitching break) no longer applies in
  super innings; extras are normal baseball with no declaration pause. Late-game
  pressure still applies (extras are high leverage).

### Rendering

- **`o27/render/render.py`** + **`templates/super_inning.j2`** — round header
  no longer prints a selected 5-batter lineup; the end-of-game log shows a
  simple per-round line score (`Round N: Visitors x – Home y`). Removed the
  now-orphaned `snapshot_batter_stats`/`batter_outcomes_since`/`_outcome_label`
  helpers. Updated the no-op renderer stubs in `o27v2/batch.py` and `o27/tune.py`
  to the new `render_super_inning_round_header(state, round_num)` signature.

### Tests

- **`tests/test_stat_invariants.py`** — `SI_PHASE_CAP` lowered from 5 to 3
  (a super inning is now a 3-out half).
- **`tests/test_substitution_oneway.py`** — removed
  `test_super_lineup_skips_subbed_out_players` (the 5-batter super lineup no
  longer exists; the one-way invariant is now enforced at the regular-lineup
  level).

---

## Verification

- Engine/manager/probability tests pass (`test_substitution_oneway`,
  `test_declared_seconds`, `test_weather_calibration`, `test_managers`,
  `test_risp_pressure`, `test_realism_identity`, `test_analytics_invariants`,
  etc.). The only failures in the suite are pre-existing and environmental
  (missing `flask`; `test_phase8_db_migration` `init_db` idempotency — both fail
  identically on the untouched baseline).
- **400-game sweep:** of 18 games that reached super innings, the **maximum runs
  in any single super-inning half dropped from 15 to 5** (a realistic big
  inning); zero halves exceeded 5. Every home win was by exactly +1 (walk-off
  the moment they led), while visitor wins varied (home batted its full 3 outs
  without catching up) — exactly correct.
- **Out counting:** spell records confirm each team's super innings start at its
  own out #28 — round 1 halves end at `out_when_pulled=30`, round 2 at 33.
- **Walk-off:** observed a round-2 bottom-half walk-off ending 16–15 at out 32
  (2 outs), and bullpen changes firing inside super innings (relievers pulled
  mid-half), confirming the re-enabled managing works end to end.

---

## Notes / follow-ups

- **Naming kept, semantics changed.** Internally these are still `super_*` /
  `is_super_inning` / `super_top` / `super_bottom`, and the DB column is still
  `games.super_inning`. This was deliberate (minimize blast radius; the user
  still calls them super innings). A future cosmetic pass could rename to
  "extra innings" if desired.
- **Historical games.** Not a concern — the DB is always cleared and reseeded
  on reload, so no pre-switch games persist to violate the new
  `SI_PHASE_CAP=3` invariant.
- **Small-ball tuning.** The manager's out-count gates were calibrated for the
  27-out regulation. They now behave sensibly in extras because each team's out
  count continues from 27 (28–33 reads as "late game"), but the heuristics were
  not re-tuned specifically for short 3-out extra frames — a possible future
  calibration item.
