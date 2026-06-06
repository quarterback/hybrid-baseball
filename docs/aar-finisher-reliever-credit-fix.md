# After-Action Report — the finish is a reliever credit (not the starter/winner)

**Date:** 2026-06-06
**Branch:** `claude/war-oaa-reconciliation-koalas-7t0pG`
**Commit:** `be0e323`
**Scope:** `o27v2/sim.py` (terminal-out / quality-finish accrual),
`o27v2/web/box_score.py` (`pick_finisher`), `o27v2/web/app.py` (the two
finisher call sites).

---

## 1. The bug

The box-score **F:** line (O27's save-equivalent "finisher") was credited to
whichever pitcher on the *winning team* had the most `terminal_outs` — with **no
starter filter, no win-pitcher filter, and no minimum-outs floor.** The code even
documented it: *"a finisher can also be the winning pitcher."*

`terminal_outs` accrue for any spell that entered with a lead, never let it slip,
and finished — which a **complete-game starter** does for ~27 outs. So the
starter led the winning team in terminal outs and got tagged the finisher,
producing the nonsensical line the owner caught: a single game scored as a
**complete game + win + finish**. The same flaw tagged non-CG starters who led
and finished, and let the *winning pitcher* double as the finisher.

## 2. The intended rule (owner spec)

A finish is a **reliever** credit. It goes to the pitcher who:

- **finished** the game (recorded the last out),
- with **4 or more** terminal outs,
- **entered with a lead and never let it be tied or lost** (lead maintained),
- and is **neither the starter NOR the decision-taking pitcher** (the win /
  majority-relief arm).

If nobody qualifies — a complete game, or a game where the closer also took the
win — **no finish is credited.** A complete game / a win is its own credit and is
never *also* a finish.

## 3. Root cause

| Layer | What it did | Why it was wrong |
| --- | --- | --- |
| `sim.py` terminal-out accrual | credited `terminal_outs` (and `quality_finish`) to **any** finishing lead-held spell, starters included | a starter finishing his own start isn't a "finish" |
| `box_score.pick_finisher` | returned the winning team's `max(terminal_outs)` pitcher, full stop | no starter / win-pitcher / 4-out filter |
| `app.py` finisher call sites | called `pick_finisher(win_rows)` with no win-pitcher context | couldn't exclude the decision-taker |

## 4. The fix (three layers — data *and* display)

- **`sim.py`** — `terminal_outs` and `quality_finish` no longer accrue to the
  starter (`... and not is_starter`). A starter's complete game / lead-held
  finish is not a finish. (`lead_entries` / `lead_held`, which drive LR%, are
  unchanged — lead retention legitimately includes starters.)
- **`box_score.pick_finisher(win_rows, win_pid=None, min_outs=FINISH_MIN_OUTS=4)`**
  — skips any `is_starter` row and the `win_pid`, and requires `terminal_outs >=
  4`; returns `None` when no reliever qualifies.
- **`app.py`** — both finisher call sites (`_game_finisher` and the batch
  box-score decorator) now select `is_starter` in their queries and compute the
  win pitcher via `credit_win`, passing `win_pid` to `pick_finisher`. This
  corrects the F: line on **already-simmed** DBs without a re-sim, since the
  exclusions are applied at display time.

Why both layers: the `sim.py` change fixes the persisted counting stat going
forward; the `app.py`/`box_score` change fixes the *designation* (the F: line)
everywhere immediately, including historical games whose stored `terminal_outs`
still include the starter.

## 5. Validation

- After a fresh sim, **starters have 0 `terminal_outs` and 0 `quality_finish`**;
  relievers accrue normally.
- A **complete-game win shows no finisher** (the headline bug — `finisher=None`).
- Across 30 played games: 7 finishers credited, **0 violations** — none is the
  starter, none is the winning pitcher, every one has 4+ terminal outs.
- Stat-invariant suite + `test_rotation` pass; `o27v2/tests` 129 passed; the box
  score renders 200.

## 6. Follow-up / known limitation

The persisted `terminal_outs` / `quality_finish` **counting totals** for games
simmed *before* this fix still include starters, so the season-to-date
parenthetical on the F: line (e.g. "F: Brockman (33)") can carry old inflation
for historical games — even though the per-game *designation* is now correct
everywhere. New sims are clean. A one-off historical recompute of those two
columns (re-derive per spell with the `not is_starter` rule) would retroactively
correct the résumé totals if desired; it's not required for correctness going
forward.
