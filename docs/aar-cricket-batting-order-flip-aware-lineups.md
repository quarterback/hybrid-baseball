# After-Action Report — Cricket Batting Order: flip-aware ("valley") lineups

**Date completed:** 2026-06-06
**Branch:** `claude/o27-cricket-batting-order-k6hhp`
**Builds on:** `docs/aar-cricket-batting-order-manager-decision.md` (the earned,
use-or-lose manager flip) and `docs/aar-cricket-batting-order.md` (the optional
rule). Living spec: `docs/feature-cricket-batting-order.md`.

---

## TL;DR

The manager-decision AAR left one follow-up explicit: a flip-minded skipper should
"build his lineup with the flip in mind." This work delivers that.

> When the Cricket Batting Order rule is on for a team AND the skipper is flip-minded
> (`mgr_flip_aggression ≥ CRICKET_FLIP_LINEUP_AGG_MIN`, default 0.60), his order is
> built as a **"valley"** — strongest bats at the ends, weakest (usually the pitcher)
> buried in the **middle** — so a flip doesn't hand the next cycle a tail-led order.
> Everyone else builds the standard best-to-worst order with the pitcher 9th.

This closes the loop the rule was designed around: the flip-minded manager both
*hoards jokers to earn flips* (manager-decision AAR) and *constructs an order that
reads well in both directions* (this AAR), while joker-happy and below-threshold
skippers are untouched.

---

## The problem with the standard order under flips

O27's standard order is best-to-worst with the pitcher hitting 9th. Reverse it for
a flip (1-9 → 9-1) and you lead the next cycle with the pitcher and the bottom of
the order — the worst possible leadoff. So a naive flip-minded manager would be
flipping into a weak cycle.

## The fix — `_valley_order` (o27v2/sim.py)

Take the nine bats sorted best-first and place them outer-in, alternating ends:
best → leadoff, 2nd → last, 3rd → 2nd, 4th → 8th, … The result puts the two best
bats at the ends and the weakest in the dead centre, e.g. talent ranks
`[0, 2, 4, 6, 8, 7, 5, 3, 1]`. Reversing that yields `[1, 3, 5, 7, 8, 6, 4, 2, 0]`
— strong ends, weak middle again. Both directions are led by quality; the pitcher
(lowest bat score) sits in the middle, off the ends the flip swaps.

`_ordered_lineup(..., flip_minded=True)` builds the valley over ALL nine by talent
(no special "pitcher bats 9th" handling, on purpose — burying him in the middle is
the whole point). `flip_minded` is computed in `_db_team_to_engine` from the team
row: rule on for the team (per-league flag OR the global default) AND
`mgr_flip_aggression` at/over the bar.

---

## Design notes

- **Why a talent threshold, not a blend.** The user framed it as a manager *type*
  distinction ("the ones who DO [flip] build their lineups with the flip in mind").
  A clean threshold on the existing persona axis reproduces that — flip-minded
  archetypes (≈0.63–0.73) qualify, the situational/joker-happy groups don't — and
  keeps construction deterministic and reproducible per seed. The bar lives in
  `cfg.CRICKET_FLIP_LINEUP_AGG_MIN` for tuning.
- **Self-contained at build time.** `_db_team_to_engine` already has the team row
  (both `cricket_order_enabled` and `mgr_flip_aggression`) plus the global config,
  so no new data threading was needed.
- **Bounded blast radius.** Only teams whose manager clears the bar AND whose league
  runs the rule build a valley; every other lineup is byte-for-byte unchanged. The
  valley trades peak leadoff PAs for the best bat in exchange for flip-symmetry — a
  deliberate strategic identity for that manager type, not a league-wide shift.

---

## Validation

- `pytest o27/tests/test_cricket_order.py` — added 3 tests (now 19 total): the
  valley puts the best at an end and the worst in the middle while preserving the
  nine; the valley's forward-vs-reverse weighted-talent disparity is < 25% of the
  standard order's (it "reads well in both directions"); and `_ordered_lineup`
  flip-minded buries the pitcher mid while standard keeps him 9th.
- `pytest o27/tests` — full engine suite **125 passed**.
- `o27v2.sim` imports cleanly (no flask dependency) and the helpers byte-compile.

---

## Not changed / possible follow-ups
- The valley is a fixed arrangement; a future refinement could vary it by handedness
  or platoon to make both directions matchup-aware, not just talent-balanced.
- No stat/telemetry surfacing of which teams built a valley — derivable from the
  manager archetype if it proves worth showing.
