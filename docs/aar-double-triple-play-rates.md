# After-Action Report — Raising the Double-Play Rate and Reviving Triple Plays

**Date completed:** 2026-05-30
**Branch:** `claude/double-triple-play-rates-eVOjH`
**Commit:** `c057d15` (GIDP band bump + triple-play gate fix)

---

## What was asked for

> "We need to increase double play rates and add triple plays"

Two asks. The first is a straightforward tuning request — turn the GIDP knob up.
The second reads, on its face, like a feature request: *add* triple plays. But
triple plays already existed in the engine end-to-end (promotion logic in
`prob.py`, `extra_runner_outs` threaded through `baserunning.advance_runners`
and `pa.py`, a `gitp` counter on `BatterStats`, a `"triple play"` render string,
fielder putout/assist credit in `render.py`, and a documented `GITP` column in
`docs/stats-reference.md`). So the real question was: *why does a feature that's
fully wired never actually fire?*

---

## Diagnosis

I drove 400 games on fixed seeds through `run_game` and tallied the `gidp` /
`gitp` counters off `FastRenderer`:

```
GIDP/game = 1.970   (788 total)
GITP/game = 0.0000  (0 total)
games with >=1 TP = 0/400 (0.0%)
```

Double plays were healthy at ~2 per game. Triple plays were **dead code** — zero
in 400 games. The cause was a one-line gate. The triple-play promotion in
`_generate_pitch` was guarded by:

```python
if (state.bases[0] is not None
        and state.bases[1] is not None
        and state.outs == 0):
```

That `state.outs == 0` is the literal MLB "nobody out" rule. But O27 has **no
innings** — it's one continuous 27-out half. So `outs == 0` is true for exactly
*one* out per half: the very first one. A triple play could only ever fire if
the half's opening out happened to be a ground ball with 1B+2B occupied and the
DP-then-TP dice both came up. Across 400 games that intersection never landed
once.

This is the exact same bug — and the exact same shape — that had already been
diagnosed and fixed for *double* plays. The comment block above the DP gate
spells it out (`prob.py`):

> O27 gate: NOT MLB's per-inning "< 2 outs". There are no innings — one
> continuous 27-out half ... Gating on `outs < 2` made double plays dead code
> for 25 of every 27 outs.

The DP path had been migrated to a capacity gate (`state.outs <= out_cap() - 2`
— "is there room left in the half to record the two outs a DP turns?"). The TP
path was left behind on the old per-inning idiom. The fix writes itself: give
the TP gate the same treatment.

---

## What was built

Two surgical changes, no new machinery.

### 1. Double-play rate — band bump (`o27/config.py`)

```python
GIDP_BASE_PROB: float    = 0.26   # was 0.20
GIDP_MIN_PROB: float     = 0.09   # was 0.07
GIDP_MAX_PROB: float     = 0.50   # was 0.42
```

The force-factor and contact-quality tables, the speed/defense scales, and the
per-half form multiplier (`SEQ_FORM_GIDP_SCALE`) all stayed put — they already
shape the distribution correctly. Lifting the base and widening the clamp band
shifts the whole distribution up without changing its character. The documented
tuning targets in the config comment were updated to track the new band (low
~9% / mid ~17–18% / high ~30%, up from 6% / 13–14% / 23%).

### 2. Triple-play gate — capacity, not "nobody out" (`o27/engine/prob.py`)

```python
if (state.bases[0] is not None
        and state.bases[1] is not None
        and state.outs <= state.out_cap() - 3):   # was: state.outs == 0
```

`out_cap()` is the existing `GameState` method that returns the half's out
ceiling (27 in regulation, `super_outs_target` in a super-inning, banked outs in
a seconds round). Subtracting 3 asks the same question the DP gate asks, scaled
to the three outs a triple play records: *does the half have room to absorb all
three?* This lets triple plays fire all half long instead of only on the opening
out — and it correctly stops promoting once fewer than three outs remain, so a
TP can't push the phase past its cap.

`TRIPLE_PLAY_GIVEN_DP_PROB` was nudged `0.04 → 0.05`. The base rate is kept low
on purpose: now that it fires across the whole half instead of one out, even a
small per-eligible-DP probability accumulates into a believable season-level
frequency. The `TRIPLE_PLAY_BASERUNNING_BONUS` (poor lead-runner read inflates
the TP chance) was left untouched.

The two stale `# ... + 0 outs` / `# ... AND 0 outs` comments around the TP block
were rewritten to describe the capacity gate and to record *why* (the
dead-code finding), so the next reader doesn't re-introduce the per-inning gate.

---

## Validation data

Same 400-game fixed-seed harness, before vs. after (added to the repo as
`scripts/measure_dp_tp.py`, modeled on the existing `scripts/ab_locked_form.py`
A/B pattern — runtime config mutation, identical seeds):

| Metric            | Before | After  | Δ        |
| ----------------- | ------ | ------ | -------- |
| GIDP / game       | 1.970  | 2.513  | +27%     |
| GITP / game       | 0.0000 | 0.0700 | (0 → 28) |
| GIDP per 100 PA   | 2.223  | 2.867  | +29%     |
| GITP per 100 PA   | 0.0000 | 0.0799 | (0 → +)  |
| Games with ≥1 TP  | 0/400  | 27/400 | 0% → 6.8% |

Reading the table:

- **Double plays up ~27%.** A meaningful, felt increase without running away —
  ~2.5 twin-killings a game keeps the DP as O27's structural runner-erasing
  event, which is what the widened band is for.
- **Triple plays revived from literally never to ~1 every 14 games** (28 across
  400, in 6.8% of games). Rare enough to stay a highlight-reel event, common
  enough that a player will actually see one over a season — which is the whole
  point of "add triple plays."

---

## Identity / regression check

The repo's test suite has a number of pre-existing failures in this environment
(missing `flask` → `ModuleNotFoundError` on the template/stat-render tests, a
missing sim DB → `sqlite3.OperationalError` on the stat-invariant suite, plus a
contact-table identity assert and a roster-shape assert that both fail on a
clean checkout of `main`). I confirmed every one of these by stashing the change
and re-running: they reproduce identically with zero diff applied.

**This change introduces no new failures.** It touches only the GIDP/TP tuning
constants and one comparison operator in the TP gate — none of the
contact-quality / pitch-probability / power axes that `test_realism_identity.py`
pins, and no schema or stat-counter shape.

---

## What this does NOT change

- **The DP path's structure.** Force-factor table, contact-quality multipliers,
  speed/defense scales, and the per-half form multiplier are all untouched — only
  the base rate and clamp band moved.
- **The TP promotion logic.** Still gated on 1B+2B both occupied (bases loaded
  extends it), still uses the lead-runner baserunning bonus, still zeroes
  `runner_advances` so no runner scores before the third out. Only the *out*
  condition changed.
- **Stay (2C) fielders' choice.** The reduced-rate lead-runner tag-out on stays
  (`GIDP_STAY_MULTIPLIER`) is unaffected — it reads the same `_gidp_probability`
  composition, which scales with the new base automatically, but its own gate
  and multiplier are unchanged.
- **Everything downstream of the outcome dict.** Triple plays were already wired
  through baserunning, PA out-recording, stats (`gitp`), and rendering. No
  consumer needed a change — the feature was complete; it just couldn't reach.

---

## Why this was the right knob

The double-play ask had one obvious knob and I turned it. The triple-play ask
*looked* like it needed new code, and the temptation was to build a standalone
triple-play mechanic. That would have been wrong on two counts: it would have
duplicated logic that already existed and worked, and it would have left the
actual bug — the inning-shaped gate — in place to bite the DP-adjacent code
again later.

The correct read was that "add triple plays" meant "make the triple plays you
already have actually happen." The fix is a single operator change that brings
the TP gate into line with the DP gate's already-established O27 convention.
That's the same lesson the DP gate fix recorded in its own comment, applied one
layer over. One-line gate, +1 config nudge, +1 band bump — and a feature that
fired zero times in 400 games now lands at a believable clip.

---

## Files touched

- `o27/config.py` — `GIDP_BASE_PROB` / `GIDP_MIN_PROB` / `GIDP_MAX_PROB` bumped;
  `TRIPLE_PLAY_GIVEN_DP_PROB` 0.04 → 0.05; tuning-target and TP comments updated.
- `o27/engine/prob.py` — TP gate `state.outs == 0` → `state.outs <= out_cap() - 3`;
  surrounding comments rewritten to document the capacity gate and the dead-code
  finding.
- `scripts/measure_dp_tp.py` — new fixed-seed DP/TP rate harness for before/after
  validation.

Total engine/config diff: 30 insertions, 16 deletions.

---

## Follow-up — worth a glance later

- **Net run-rate drift.** More DPs erase more baserunners, so league run rates
  will tick *down* slightly. Triple plays are rare enough to be negligible. Worth
  re-checking against the stat-invariant suite once a fresh-sim DB is available;
  if the offense gets too suppressed, ease `GIDP_BASE_PROB` back toward 0.22–0.24.
- **TP frequency tuning.** 6.8% of games with a triple play is a defensible
  starting point, but it's a taste call. If it reads as too frequent in actual
  play, `TRIPLE_PLAY_GIVEN_DP_PROB` is the dial — and now that the gate is fixed,
  it's the *only* dial needed; no more inning-shaped surprises.
