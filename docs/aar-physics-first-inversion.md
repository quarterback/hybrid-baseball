# After-Action Report — physics-first contact resolution (the inversion)

**Date completed:** 2026-06-01
**Branch:** `claude/peaceful-thompson-ZB5UY`
**Scope:** `o27/engine/batted_ball.py`, `o27/engine/prob.py`, `o27/config.py`,
`o27/tests/test_physics_resolver.py`. Plan: `docs/design-physics-first-resolution.md`.

---

## TL;DR

The contact engine is inverted. The dependency used to run **outcome →
physics**: `resolve_contact()` drew a `hit_type` from the WEAK/MEDIUM/HARD
contact tables, then `sample_batted_ball()` sampled `(EV, LA, spray)` *to match*
the already-decided result. Now it runs **talent → physics → outcome**:

1. `generate_batted_ball()` samples `(EV, LA, spray, texture)` from talent —
   contact quality, power, batted-ball texture, pitch metadata, handedness —
   with **no knowledge of the outcome**.
2. `resolve_batted_ball()` derives the base `hit_type` from that trajectory vs
   the park fence geometry (grounder / liner / fly / popup bands).

The categorical contact-table draw is gone from the live path. Exit velocity
now **causally drives** the result.

## What changed, precisely

Only the **table-draw core** of `resolve_contact()` was replaced (the
redistribution + `_apply_park` + `_pick_from_table` block). The **defense layer**
(range shift, gems, shifts, errors, GIDP, fielder attribution), the caller's
talent flex (incl. the Tier-3 foot-speed/infield-arm work), all stat counting,
baserunning, render, persistence and the full `outcome_dict` shape are
**unchanged** — they now operate on a physics-derived `hit_type` instead of a
table-derived one and can't tell the difference.

Re-homed levers (were sum-preserving table redistributions):
- **power, pitch hard-contact/launch bias** → feed the EV/LA generator directly.
- **per-half form** (the H~R decoupler) → EV lift (`RES_FORM_EV_SCALE`): a hot
  half carries the ball farther → more XBH/HR at the same hit count.
- **RISP XBH suppression** → EV trim at RISP (`RES_RISP_EV_TRIM`).
- **park HR / hits + fence geometry** → the resolver (absorbs the old
  `park_effects` geometry).
- Joker decay, platoon, catcher, RISP contact penalty → untouched; they were
  always upstream in `contact_quality()`, which we keep (it also drives EV and
  still supplies `quality` to the stay rule / GIDP / gems / render).

The `(EV, LA, spray)` that drove the outcome are threaded out of
`resolve_contact()` and persisted as-is — physics and result never disagree
(the caller no longer re-samples).

## Validation

**The headline property — physics drives the outcome** (200-game sim):

| EV band | n | hit% | HR% | XBH% |
| --- | --- | --- | --- | --- |
| <80 | 2694 | 32.0 | 0.0 | 0.6 |
| 80–90 | 2009 | 46.3 | 0.0 | 9.0 |
| 90–100 | 2645 | 58.9 | 1.3 | 31.3 |
| 100–110 | 1970 | 73.2 | 9.2 | 53.8 |
| 110+ | 1237 | 85.4 | 29.7 | 68.6 |

Event-level **corr(EV, hit) = 0.364** — causal. In the old engine EV was
sampled *from* the outcome, so any correlation was an echo; now it's the driver.
This also resolves the Savant feasibility caveat: xwOBA−wOBA is now a real
measurement, not a tautology.

**Run environment (final, singles+doubles-heavy by design):** R/G **26.37**;
per-BIP single 26.2%, double 20.2%, ground_out 14.2%, fly_out 13.9%, line_out
9.1%, hr 6.0%, FC 3.2%, error 2.7%, triple 1.8%, DP 1.6%, infield_single 1.0%.
Per the owner's call, R/G was **not** pinned to the old 24.2 — "there is no such
thing as R/G too hot"; the offense-rich, doubles-heavy profile is the intent.

**Tests:**
- `o27/tests` — **90 passed** (was 81). New: 9 in `test_physics_resolver.py`
  (generator plausibility, power→EV, outcome-blind generation, each resolver
  band, EV→hit monotonicity).
- `tests/test_stat_invariants.py` — **same 5-fail/6-pass as baseline** (the 5
  are pre-existing flask/full-season-context env failures). The inversion
  reconciles cleanly: **zero new invariant failures** — PA identity, out counts
  (27/half), hit and OR reconciliation all hold.
- `python -m o27.main --seed 1` runs a full game end-to-end.

## Tuning knobs (all in `o27/config.py`, `RES_*`)

Texture mix (`RES_TEXTURE_WEIGHTS`), band cut points (`RES_POPUP_LA`,
`RES_FLY_LA`, `RES_LINER_LA`), per-band hit/XBH rates, `RES_HR_MARGIN`, and the
re-homed-lever scales (`RES_FORM_EV_SCALE`, `RES_RISP_EV_TRIM`). The mix is
shaped entirely by these — no categorical tables.

## What I did NOT do / follow-ups

- **Dead code retained, not deleted.** The old `WEAK/MEDIUM/HARD_CONTACT`
  tables, `_redistribute`/`_*_edges`, `_pick_from_table`, `sample_batted_ball`,
  and `park_effects.apply_park_effects` are no longer called by the engine but
  are still **defined and unit-tested** (`test_power_redistribute.py`,
  `test_park_ev_texture.py`). Deleting them means removing those test modules
  too — a deliberate cleanup left as a follow-up so the cutover commit stays
  reviewable.
- Did not re-pin R/G to the pre-inversion value (owner's directive).
- Calibration knobs were set by a handful of full-sim iterations, not an
  exhaustive sweep; they're all exposed for future tuning.
