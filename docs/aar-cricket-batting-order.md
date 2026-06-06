# After-Action Report — Cricket Batting Order (the joker-free flip)

**Date completed:** 2026-06-06
**Branch:** `claude/o27-cricket-batting-order-k6hhp`

---

## TL;DR

The user wanted O27 to take on a "cricket-style batting order." Cricket's defining
order rule (each batsman bats in fixed order, is **done once dismissed**, next man in,
innings ends when the side is all-out) conflicts head-on with O27's core 27-out half —
a ~9-12 batter side would be all-out long before 27 outs. So we clarified intent up
front and landed on a much smaller, native-feeling mechanic, shipped as an **optional
per-league rule** (the Power Play pattern):

> When the rule is on, a side's batting order **flips 1-9 → 9-1 at the end of every trip
> through the order — but only on a trip in which no joker was deployed.** Deploying a
> joker locks the order for that cycle.

That second clause was the user's own refinement, and it's the whole point: the flip
turns each joker insertion into a fork — take the high-leverage pinch-hitter now, or hold
the joker and let the order churn. A side that never uses a joker inverts its order every
single trip; a side that burns a joker every cycle keeps the order it started with.

---

## What was asked for

Two rounds of scoping:

1. Initial request was vague ("cricket-style batting order"). I surfaced the conflict
   with the 27-out core and asked two questions. The user chose **"reorder strategy
   only"** (keep the engine loop; change how the order behaves) shipped as an **optional
   per-league rule**.
2. The user then handed me the exact mechanic: *invert the order 1-9 → 9-1 at the end of
   a cycle, but only during a turn through the lineup where no joker is used.*

So this is deliberately **not** a full cricket dismissal model. It reorders the existing
cycle and changes nothing else — outs, stays, jokers, walk-back, Declared Seconds, super
innings all behave exactly as before.

---

## Why it hooks in cleanly

The existing cycle machinery already had everything needed:

- `Team.advance_lineup()` already detects the wrap to the top of the order (`new_pos == 0`),
  increments `lineup_cycle_number`, and resets `jokers_used_this_cycle`.
- `jokers_used_this_cycle` (populated in `manager.py` when a joker is inserted, reset at
  the wrap) is *exactly* the "was a joker deployed during this trip?" signal — read at the
  wrap **before** it's cleared.

So the rule is a single check at the wrap: if the rule is on and that set is empty,
`lineup.reverse()`. Reversing the list is robust to the real lineup length (8 fielders +
SP), and because `lineup_position` resets to 0 at the wrap, the new `lineup[0]` (the old
#9, usually the pitcher) correctly leads off the next cycle.

---

## What changed

Mirrors Power Play end-to-end (so the two optional rules are structurally identical):

- **`o27/engine/cricket_order.py`** (new) — `cricket_order_on(team)` (per-team override OR
  global config) and `maybe_invert_on_cycle(team)` (reverse on a joker-free wrap, return a
  PBP line). Imports only `config`, never `state`, so `state.py` can import it with no
  circular dependency; operates on the Team via `getattr` duck typing.
- **`o27/engine/state.py`** — `Team.cricket_order_enabled` field; `advance_lineup` calls
  the helper at the wrap and now returns `Optional[str]` (the flip line) instead of `None`;
  `GameState.cricket_flip_msg` transient field for the renderer.
- **`o27/engine/pa.py`** — both `advance_lineup()` call sites append the flip line to the
  raw log (no-renderer path) and stash it on `state.cricket_flip_msg` (renderer path).
- **`o27/render/render.py`** — `render_event` emits `state.cricket_flip_msg` once and
  clears it (the raw log is discarded when a Renderer is present, so the flip needed a
  state-stash hook to reach the live play-by-play — see "What bit me").
- **`o27/config.py`** — `CRICKET_BATTING_ORDER_ENABLED: bool = False`.
- **`o27v2/engine_config.py`** — dashboard toggle under "Optional rules".
- **`o27v2/db.py`** — `teams.cricket_order_enabled` column + idempotent ALTER migration.
- **`o27v2/sim.py`** — stamps both teams from the league flag (home row authoritative).
- **Web UI** — checkbox (`new_league.html`), per-league select (`universe_new.html` +
  its JS clone/preset wiring), and an existing-league toggle (`league_edit.html`), with
  the matching `app.py` handlers.

---

## What bit me

My first pass appended the flip line in `pa.py` and assumed it would show up. It didn't,
in the live app. `run_half` discards the raw `apply_event` log whenever a `Renderer` is
present (the production path) and rebuilds the play-by-play from the renderer instead —
the raw log only survives in the no-renderer/test path. The fix was the
`state.cricket_flip_msg` stash that the renderer reads on the same event and clears, the
same `getattr(state_after, …)` pattern the Power Play short-handed flag already uses.
The mechanic itself (the lineup reversal) was working the whole time — I caught this only
because I instrumented `maybe_invert_on_cycle` and saw 8 flips with 0 rendered lines.

---

## Validation

- `pytest o27/tests/test_cricket_order.py` — 9 targeted tests (see the feature doc for the
  list). All pass.
- `pytest o27/tests` — **114 passed.** The `advance_lineup` signature change (None →
  Optional[str]) and the pa/render edits regress nothing; both `advance_lineup` callers
  are in `pa.py` and now consume the return.
- Full random games (`ProbabilisticProvider`, two joker-less demo sides, rule on): 8 flips
  per game, **pitcher who hit 9th leads off** the next cycle (confirms 1-9 → 9-1). Rule
  off: zero flip lines, identical to today.
- DB: fresh schema has the column (default 0); re-init idempotent; ALTER adds it to a
  legacy `teams` table. All changed `.py` byte-compile; all three edited templates parse.

---

## What I did NOT change / follow-ups

- **No new stats and no box-score family.** The flip only reorders PAs the stat machinery
  already records; a "flips per game" telemetry line could come later.
- **No manager-AI awareness.** `manager.should_insert_joker` does not yet treat "this joker
  forfeits my flip" as a cost — the rule is mechanically correct but the AI plays it
  blind. Folding the flip's value into the joker decision is the obvious next step.
- **No dismissal/all-out model.** Explicitly out of scope (it breaks the 27-out half); the
  flip is the agreed minimal interpretation.
