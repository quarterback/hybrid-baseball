# Spec — Close stat invariants #2/#3 and #9

**For:** a fresh agent picking this up cold.
**Branch:** `claude/youth-teams-league-history-nYDmy`
**Prereq reading:** `docs/aar-career-leaderboard-and-stat-invariants.md` (the AAR
that root-caused both bugs). This spec is self-contained, but the AAR has the
narrative.

**Goal:** make `tests/test_stat_invariants.py` fully green. Two checks remain
red after the prior session: **#2/#3** (batter↔pitcher out reconciliation) and
**#9** (walk-back runs ≤ unearned). Invariants **#1, #4, #5, #8** are already
fixed and committed — do not regress them.

---

## 0. Reproduce (do this first, ~2 min)

The harness runs against a populated DB selected by `O27V2_DB_PATH`. Build a
fresh single-season DB and point the harness at it:

```bash
cd /home/user/hybrid-baseball
export PYTHONPATH=/home/user/hybrid-baseball

# Build a 1-season, 30-team DB (~2400 games). Takes a couple of minutes.
rm -f /tmp/inv.db
O27V2_DB_PATH=/tmp/inv.db python - <<'PY'
import os; os.environ["O27V2_DB_PATH"]="/tmp/inv.db"
from o27v2 import season_archive as sa
sa._run_history_thread(1, base_seed=21, config_id="30teams", detail="lite")
print(sa.history_status().get("current_phase"))   # -> "done"
PY

# Run the harness against it
O27V2_DB_PATH=/tmp/inv.db python -m pytest tests/test_stat_invariants.py -q
```

Expected today: invariants **1, 4, 5, 6, 7a, 7b, 8 pass**; **2, 3, 9 fail**.

**Faster inner loop** for tracing (no offseason, seconds not minutes): sim
games one at a time via `o27v2.sim._simulate_game_locked(game_id, seed=..., detail="lite")`
after seeding a league — see the AAR's trace snippets, or:

```python
from o27v2 import db, league
from o27v2.schedule import seed_schedule
from o27v2.season_archive import set_active_league_meta
db.init_db(); league.seed_league(rng_seed=21, config_id="30teams")
set_active_league_meta(21,"30teams"); seed_schedule(rng_seed=21, config_id="30teams")
from o27v2 import sim
# then loop: pick an unplayed game id, _simulate_game_locked(gid, seed=gid*5+3, detail="lite")
```

**Golden rule (learned the hard way last session): trace one game before you
sim a season.** A monkeypatched single-game trace costs seconds; a verification
sim costs minutes. Use the cheap signal to pick the hypothesis worth confirming.

---

## Mental model you must hold

The engine uses a **27-out continuous** model. Each team bats one block of up to
27 outs per regulation half (super-innings add cumulative blocks; a walk-off
ends a half early below 27). `state.outs` is the batting team's running out
count for the current phase, **reset to 0 at the second half** (`game.py` second-half
setup). There are **two independent out/run ledgers** that must both reconcile
to `state.outs`:

- **Pitcher ledger** — `o27/engine/pa.py:_record_out` bumps `state.outs` and
  `state.pitcher_outs_this_spell`; spells are flushed to `state.spell_log` and
  extracted into `game_pitcher_stats` by `o27v2/sim.py:_extract_pitcher_stats`
  (line 827). **Verified correct last session:** engine `_record_out` count ==
  `game_pitcher_stats.outs_recorded` exactly. Do not chase a pitcher-side leak.
- **Batter ledger** — `o27/render/render.py:_update_stats` (line 1254) charges
  per-batter `outs_recorded` from the play-by-play events; extracted into
  `game_batter_stats` by `o27v2/sim.py:_extract_batter_stats` (line 733).
  **This is the side that's wrong for #2/#3.**

---

## BUG A — invariants #2 / #3 (batter↔pitcher out reconciliation)

**Tests:** `tests/test_stat_invariants.py` line 170 (`test_invariant_2_…`) and
line 299 (`test_invariant_3_…`). They assert, per (game, team, phase):
`batter_outs + unattributed == phase_cap` and `batter_outs == opp_pitcher_outs`.

### Confirmed root cause (from last session)
The **renderer over-counts batter outs**. Ground-truth dump: in a half the
engine recorded as 27 outs, the renderer's raw cumulative per-team batter
`outs_recorded` (== extracted == `game_batter_stats`) is **28–30**. Pitcher
side is correct (== engine). So the renderer charges batter-outs the engine
never recorded. The gap is +1..+6, present in ~60% of team-halves.

### Where the renderer charges batter outs
`o27/render/render.py:_update_stats` — structured per-event charges at lines
**1300** (foul-tip K), **1308** (foul-out), **1326** (CS → runner), **1345**
(strikeout), **1495** (stay-out), **1515** (ball-in-play batter retired),
**1580** (TOA → runner), plus a "leftover" top-up at **1598–1602**:

```python
engine_outs_delta = (state_after.outs or 0) - (ctx.get("outs") or 0)
already_charged = s.outs_recorded - _or_before + toa_credited
leftover = engine_outs_delta - already_charged
if leftover > 0:
    s.outs_recorded += leftover
```

The leftover **only ever adds** (tops the batter up to the engine delta); it
never rolls back when structured charges already exceeded the engine's outs.

### Disproven last session — don't repeat
- **Two-way reconciliation** (also roll back when `delta_charge < 0` at line
  1598) — implemented, re-simmed, **gap unchanged**. So the over-charge is NOT
  isolated to the leftover block; it's in the structured branches and/or the
  baseline `ctx["outs"]` is not the true pre-event out count for some events.
- **Pitcher-side spell drop** (`_close_spell` dropping `BF==0` spells) — real
  but irrelevant here; pitcher side is already exact.

### Attack plan
1. **Instrument with an INDEPENDENT engine-out counter, not `ctx["outs"]`.**
   Last session's per-event trace used `ctx.get("outs")` as the baseline and
   gave false "matches" — the production reconciliation uses the same suspect
   baseline. Wrap `o27/engine/pa.py:_record_out` to maintain a ground-truth
   running `state.outs` per batting team, and wrap
   `o27/render/render.py:_update_stats` to record, per event, the batter-out
   delta. Compare batter-delta vs the TRUE engine `state.outs` delta for that
   event. Find the event class where batter-delta > engine-delta.
2. **Verify whether `ctx["outs"]` is the true pre-event out count.**
   `render_event` (line 176) receives `ctx` from `game.py:run_half` (line ~304),
   built by `capture_context(state)` (render.py line 144) **before**
   `apply_event`. Confirm `ctx["outs"]` equals `state.outs` immediately before
   the event and is not stale across multi-out plays / phase resets. If it's
   wrong, the production `engine_outs_delta` is wrong → fix the baseline.
3. **Likely culprit classes** (verify, don't assume): a structured branch that
   charges a batter-out on a play the engine scored as safe; a multi-out play
   (DP/TP) whose nominal out count exceeds what the engine recorded under the
   `out_cap()` truncation (`pa.py:_record_out` refuses outs past the phase cap —
   the renderer must not charge those either); or double-charging when a runner
   out is credited both structurally (1326/1580) and via leftover.
4. **Fix** so the per-team batter `outs_recorded` for each phase equals the
   engine's `state.outs` for that phase (== the pitcher ledger). The renderer
   already intends this ("OR column sums to the engine's outs"); make it hold
   in both directions.

### Acceptance
`test_invariant_2_or_reconciliation` and `test_invariant_3_pitcher_batter_cross_check`
pass on a freshly-built `/tmp/inv.db`. Spot-check: for every (game, team, phase),
`SUM(game_batter_stats.outs_recorded) == SUM(opp game_pitcher_stats.outs_recorded)`
(walk-off halves may legitimately be < 27 — the test already allows that).

### Caveat
This is a **sim-time** change (the renderer writes stats during simulation), so
every fix requires a re-sim to verify against the DB. Build the trace first;
only re-sim once you have a specific event class and a fix in hand.

---

## BUG B — invariant #9 (walk-back runs ≤ unearned)

**Test:** `tests/test_stat_invariants.py` line 635
(`test_invariant_9_walk_back_runs_le_faced`), part (b): per (game, team),
`SUM(wb_runs) ≤ SUM(unearned_runs)`. Fails on ~6–7 (game, team) groups/season.

### Confirmed root cause (from last session)
A Walk-Back bonus run is unearned **by rule**, but `wb_runs` is credited to a
**different pitcher** than the one charged the run in `runs_allowed`. Concrete
case (game 1472, team 21): pid 991 has `wb_runs=1, runs_allowed=0`, while the
team's pitcher `runs_allowed` sums to exactly the opponent's score — so the run
IS counted, just on another same-team spell. The `wb_runs` tick and the
`_score_run` charge land on different spells.

### Where it lives
- `o27/engine/pa.py:_reconcile_walk_back` (line 200): when a tracked bonus
  runner is no longer on base and wasn't put out, it ticks
  `pitcher_wb_runs_this_spell += 1` (line 218) and demotes one earned→unearned
  **only if** `unearned < runs` (line ~223). It assumes `_score_run` already
  booked the run **into the current spell** — that assumption breaks here.
- `o27/engine/pa.py:_score_run` (line 177) charges `pitcher_runs_this_spell`
  (line 189) to whoever is current when the run crosses.
- Persistence: `o27v2/sim.py:_extract_pitcher_stats` line 901 computes
  `er = runs_allowed - unearned_runs`.

### Disproven last session — don't repeat
- **Per-spell clamp at extraction** (`unearned = min(runs, max(unearned, wb_runs))`,
  plus trimming `er_arc` to keep it summing to ER) — took violations 7→1 but
  **can't** cover the case where a spell has `wb_runs > runs_allowed` (bounded
  by that pitcher's own runs). Reverted.

### Attack plan
1. **Fix at the engine, not at extraction.** Trace one game with a Walk-Back
   bonus run that scores (instrument `_reconcile_walk_back` and `_score_run`).
   Determine why the bonus run's `_score_run` charge and the `wb_runs` tick land
   on different spells — e.g. the run booked to a prior pitcher, or the bonus
   runner detected as "scored" in an event where `_score_run` charged 0 to the
   current pitcher. Walk `o27/engine/baserunning.py:advance_runners` for how the
   bonus runner at `bases[2]` is counted when he crosses.
2. **Make the tick and the charge agree:** the pitcher credited `wb_runs` must
   be the pitcher whose `runs_allowed`/`er_arc` carries (and demotes) that run.
   Then the per-spell relation `wb_runs ≤ unearned ≤ runs` holds and the
   aggregate invariant follows.
3. **Keep ER/er_arc consistent.** Whatever demotes a run earned→unearned must
   also decrement the matching `er_arc` bucket (latest-arc first, matching the
   existing convention) so `er_arc1+er_arc2+er_arc3 == er` per row — invariant 8
   depends on this anchor. Verify with:
   `SELECT COUNT(*) FROM game_pitcher_stats WHERE (er_arc1+er_arc2+er_arc3) != er`
   → must be 0.
4. **Rejected fallback** (only if the engine fix proves too deep): an
   extraction-level **team-aggregate** reconciliation — after building a team's
   pitcher rows, if `SUM(wb_runs) > SUM(unearned)`, bump `unearned` (and trim
   `er_arc`) on same-team rows that have `runs > unearned` headroom. This makes
   the invariant green but only approximates per-pitcher attribution; prefer the
   engine fix.

### Acceptance
`test_invariant_9_walk_back_runs_le_faced` passes on a freshly-built
`/tmp/inv.db`. Verify `er_arc` sum == `er` (query above) and no
`er < 0 OR unearned_runs > runs_allowed` rows.

---

## Guardrails / definition of done

1. **All of `tests/test_stat_invariants.py` passes** on a fresh `/tmp/inv.db`
   (all 10 checks). Do not narrow scope or weaken assertions to pass — fix the
   data.
2. **No regressions:** `python -m pytest o27/ -q` (engine/render unit tests, 54
   passing) stays green. `python -m pytest o27v2/ -q` and the almanac tests stay
   green. Note `tests/test_weather_calibration.py::test_extreme_weather_within_calibration_envelope`
   is **pre-existing flaky** (statistical envelope, fails independent of these
   changes) — ignore it, don't "fix" it here.
3. **Don't regress #1/#4/#5/#8.** In particular `pa.py:_record_out`'s
   `out_cap()` guard and `GameState.out_cap()` must stay.
4. **Trace before you sim.** Each renderer/engine change needs a re-sim to
   verify; minimize cycles by confirming the mechanism on one game first.
5. Commit each bug fix separately with a clear message; push to the branch.
   Update `docs/aar-career-leaderboard-and-stat-invariants.md` (flip #2/#3 and
   #9 from OPEN to FIXED with the actual root cause) when done.
