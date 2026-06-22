# After-Action Report — jokers are a leverage tool, not a weak-hitter bench

**Date:** 2026-06-22
**Branch:** `claude/vigilant-davinci-hn34xy`
**Status:** Shipped. 137 engine tests pass (incl. the rewritten
`test_sub_first_cycle.py` joker cases).

This is the cleanup of two related mistakes in how the manager AI deployed
jokers. It follows directly from `aar-sub-gate-and-in-game-injuries.md`; read
that first for the substitution-gate context.

---

## 1. Symptom

Box scores (e.g. Foundrymen in game #1953) showed teams **fielding only six
or seven of their nine before jokers appeared in the order**, and the weakest
bats — the catcher and first baseman, hitting at the bottom — barely batted at
all. The owner's read was exact: *"all 9 batters means every position player
and the pitcher should hit once before introducing jokers and pinch types."*
And on the mechanism: *"it was meant as a late stage tool, not something meant
to ensure hitters never hit."*

## 2. Diagnosis — two mistakes

**Mistake 1 — jokers were left ungated through the first cycle.** The
lineup-integrity pass gated `should_pinch_hit` / `should_pinch_run` /
`should_defensive_sub` behind `lineup_cycle_number >= 1` but **deliberately
exempted `should_insert_joker`** as an "intended per-cycle mechanic." That was
wrong. A joker insertion bats *in place of* the scheduled batter via
`state.batter_override` and does **not** advance the lineup (`_end_at_bat`
clears the override without calling `advance_lineup`). So a joker that fires
when a weak batter is due simply skips that batter for the PA — and the same
batter is still due next.

**Mistake 2 — the weak-hitter override fired at 0.75–0.95, leverage-blind.**
`should_insert_joker` had a "Path 1" that, whenever the batter due up was
below `JOKER_WEAK_BATTER_THRESHOLD` and any joker out-hit him, inserted at
`0.75 + 0.20·agg`. Combined with Mistake 1, the bottom-of-order weak bats got
jokered nearly every time they came up — across the whole game, not just late.
They were effectively benched. That is the opposite of a tactical tool: a
joker is supposed to be a *strategic, late-game weapon*, which is the only
reason to have them at all.

## 3. Fix

### A — gate jokers like every other tactical sub
Added the same first-cycle gate to `should_insert_joker`:
`if team.lineup_cycle_number < 1: return None`. The first trip through the
order is now always the nine base batters (eight fielders + the pitcher), each
hitting once before any joker. Forced injury subs still bypass via the
executor path. Side effect: cycle 0 is always joker-free, so a Cricket-Order
flip is reliably armed entering cycle 1 — consistent with that rule.

### B — delete the weak-hitter override; jokers fire on leverage only
Removed Path 1 entirely (and its config constants
`JOKER_WEAK_BATTER_THRESHOLD` / `JOKER_WEAK_INSERT_BASE` /
`JOKER_WEAK_INSERT_AGG_SCALE`). `should_insert_joker` is now a single
leverage-aware path with one guard:

- **Upgrade guard:** the best eligible joker must out-hit the batter due up
  (`best_joker_skill > batter_skill`), else `None`. This stops the manager
  pinch-hitting his own good bats, and it makes the leverage roll naturally
  favor the weak end of the order.
- **Leverage roll:** `leverage = gap_factor × late_factor × runner_factor`
  (tight game × late in the half × runners on), same curve as before. The
  per-PA probability is
  `min(0.35, leverage · (0.25 + 0.5·joker_agg) · (1 + upgrade)) · pool_mult`.
  The `(1 + upgrade)` term tilts spend toward the weak end of the order; the
  0.35 cap keeps even a max spot bounded; `pool_mult` folds in joker fatigue
  and the Cricket-Order flip opportunity cost.

Net effect: a weak hitter bats freely early and mid-game, and only draws a
joker in a genuinely high-leverage late spot — which is the point of a joker.

## 4. Validation

- `pytest o27/tests` — **137 passed**. The joker cases in
  `test_sub_first_cycle.py` now assert: gated at cycle 0; fires in a late,
  bases-loaded, tied spot at cycle ≥ 1; **never** fires for a batter the pool
  can't out-hit even at max leverage; and effectively never fires in a
  no-leverage blowout (the old override's home turf), so weak hitters aren't
  benched.
- Determinism unchanged — the single leverage roll is the only RNG draw and is
  seeded from the game RNG as before.

## 5. Not done / follow-ups

- The 0.35 cap and the `(1 + upgrade)` weighting are first-pass; if jokers feel
  too rare or too frequent over a full season, tune the cap / the
  `0.25 + 0.5·joker_agg` slope (both live in `should_insert_joker`).
- `JOKER_SCORE_DIFF_MAX` / `JOKER_OUTS_CEILING` remain in `config.py` — they
  belong to the dormant `_legacy_should_insert_joker` and were left untouched.
- No box-score change: the fix is purely in when the manager reaches for a
  joker, not in how the insertion is rendered.
