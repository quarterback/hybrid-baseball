# After-Action Report — Walk-Back Becomes a Live, Persistent 3B Runner

**Date completed:** 2026-05-20
**Branch:** `claude/add-foul-outs-stat-OXRYr`

---

## What was asked for

The Walk-Back rule places the HR-hitter back on third for a bonus run. The old
implementation resolved it in a one-PA window: a phantom flag (`walk_back_pending`)
that the *very next* plate appearance either cashed in (batted home) or
"evaporated." The user flagged that as wrong:

> "I think there might be a mistake with how the walk back runner is treated…
> the walk back runner [is] no different than the runner on second base in extra
> innings and normal baseball. They can stay there as long as they need to until
> the inning is over to be scored home or put out or whatever. It shouldn't just
> be a conditional next-ball play — it can happen whenever, their fate is
> determined at any point like any other runner on third base."

Three rules clarifications pinned the mechanic:

1. > "Once back on third they are a live baserunner indifferent from any other
   >  runner at 3rd."
2. > "The walkback run scored is unearned."
3. > "A subsequent homer puts a new runner at third and the runner at third
   >  scores the walkback (bonus) run."

The user also described the cleanest implementation, which matched the approach
taken:

> "Resolve the home run first and then immediately… create a new runner who is
> the same batter back on third base in a separate instance… and then everything
> else should continue like normal."

And one simplifying rule, decided after a pinch-runner edge case surfaced:

> "It would be easier not to replace the walk back runner with a pinch runner…
> it takes that bat out of the lineup which is not necessarily what you want…
> I would not allow you in this simulation to replace a walk back runner with a
> pinch runner."

---

## Diagnosis

The one-PA model had two structural problems beyond "it ends too early":

- **It contradicted the precedent it cited.** The code tagged the bonus run a
  "Manfred-runner" run, but the MLB ghost runner is a *persistent* real runner,
  not a next-pitch coin flip.
- **It special-cased every PA terminus.** `_resolve_walk_back_at_pa_end` was
  called from seven places (K, foul-out, HBP, BB, contact-run, stay-out, K-tip)
  with bespoke "did this drive a runner home from 3B" logic
  (`_walk_back_should_fire`), duplicating what ordinary baserunning already does.

The fix is to stop modelling the bonus runner as a flag and start modelling him
as what he is: a runner on third.

---

## What changed

### Engine

- **State.** Replaced `walk_back_pending: Optional[str]` with
  `walk_back_runner_ids: set` (`o27/engine/state.py`) — the player_ids of the
  Walk-Back runners currently on base.
- **Placement.** After a HR resolves MLB-exactly (and clears the bases), the
  HR-hitter is placed on `bases[2]` and added to `walk_back_runner_ids`
  (`o27/engine/pa.py`, run-chosen contact path). From there he is an ordinary
  baserunner — no other code path knows or cares that he is "special."
- **Resolution is centralized, not per-PA.** Deleted `_walk_back_should_fire`,
  `_WALK_BACK_BAT_DRIVES`, `_resolve_walk_back_at_pa_end` and all seven call
  sites. A runner can only leave the bases by scoring (a `_score_run` run) or by
  being retired (a `_record_out` call), so the bonus runner is settled at exactly
  those two choke points plus end-of-half:
  - `_reconcile_walk_back(state)` runs after every event (thin `apply_event`
    wrapper). Any tracked runner no longer on the bases must have scored — tick
    `wb_faced` + `wb_runs` and move one run from earned to unearned (demote one
    `er_arc` bucket) so the Walk-Back run lands in `unearned_runs`.
  - `_record_out` drops a retired Walk-Back runner from the set and ticks
    `wb_faced` (a stop, no run).
  - `resolve_stranded_walk_backs(state)` is called at the end of `run_half`
    (`o27/engine/game.py`): a runner stranded when the half ends is a stop —
    tick `wb_faced`, then clear the set.
- **Counter attribution.** `wb_faced` is charged at *resolution* (score / out /
  strand) to the pitcher on the mound — the same inherited-runner simplification
  the engine already makes for earned runs. This keeps `wb_runs ≤ wb_faced`
  *per pitcher* even across a mid-inning pitching change.
- **Walk-off.** Ported `_walkoff_blocks_walk_back` into the new model: a walk-off
  HR ends the game, so no bonus runner is placed at all (per "on a walk-off there
  is no Walk-Back, the game ends").
- **Pinch-running disallowed.** A Walk-Back bonus runner can't be pinch-run for:
  `should_pinch_run` skips a base occupied by one, and `pinch_run` refuses with a
  log line if called directly (`o27/engine/manager.py`).

### Rendering

- Walk-Back arming caption now keys off `walk_back_runner_ids` instead of the
  removed `walk_back_pending` flag (`o27/render/render.py`). The bonus run itself
  now renders as an ordinary "scores from 3B" event, and the scoring-log
  reconcile (diff-based on bases-before/after) captures it with no phantom
  padding.

### Tests

- Rewrote `o27/tests/test_walk_back.py` for the persistent model: runner placed
  on 3B; scores on single/double/sac-fly/productive-out/subsequent-HR; **persists**
  through an intervening K/BB/line-out/foul-out (does not evaporate); resolves as
  a stop on pickoff / caught-stealing-home / strand; run is unearned and excluded
  from ER while `er_arc` stays consistent; pinch-running a Walk-Back runner is
  refused. 26 engine tests pass.

---

## Verification

- **Engine suite:** `o27/tests/` — 26 passed.
- **Repo suites:** `o27v2/tests/` + `tests/` — 171 passed, 7 skipped. The only
  failures are two pre-existing `test_phase8_db_migration` cases (init_db
  idempotency) that fail identically on the base commit, and the known-flaky
  20-game `test_weather_calibration` envelope.
- **80-game integration sim (8-team config, seed 7):** 0 reconcile anomalies;
  aggregate `wb_runs (362) ≤ wb_faced (440)` and `wb_runs ≤ unearned (636)`.
  `test_invariant_9_walk_back_runs_le_faced` passes against the simmed DB (it
  failed under an intermediate version — see below).

---

## Notes / follow-ups

- **The bug that the invariant caught.** An intermediate version leaked: a
  **pinch-runner** swapped onto third for a Walk-Back runner left the original id
  in `walk_back_runner_ids` while a different id sat on the bag, so
  `_reconcile_walk_back` later fired a false-positive "scored" with no run behind
  it (`wb_runs > unearned`). The user's rule — Walk-Back runners can't be
  pinch-run for — removes the case entirely, which is cleaner than transferring
  the designation (it also keeps the HR-hitter's bat in the lineup).
- **Merged `main` mid-task.** `main` had concurrently landed the super-innings →
  3-out-extra-innings refactor plus scoring-log fixes; this branch merged it in.
  The strand sweep and walk-off block were re-fitted to the new inning structure.
- **Suggested deeper check:** re-run the 800-game scoring-log reconcile sweep on
  this branch to confirm the diff-based log still balances now that Walk-Back runs
  are real 3B scores rather than phantom bonus runs (expected: it does, by
  construction).
