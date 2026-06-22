# After-Action Report — lineup-integrity subs + in-game injuries

**Date:** 2026-06-22
**Branch:** `claude/vigilant-davinci-hn34xy`
**Status:** Shipped. 134 engine tests pass; new gate/injury regression
suite passes; 224-game DB sim persists in-game injuries cleanly.

---

## 1. Symptom

Real box scores (games #1862, #1863) showed teams **churning their bench
inside the first turn through the order** — a cascade of pinch hitters,
pinch runners, and defensive entries stamped "in the 1st/2nd/3rd."
Examples: Montréal pinch-running for McLaughlin "in the 1st" and burning
three pinch hitters by the 4th; Aberdeen swapping CF/C/SS/LF in the 3rd–4th.
The owner's read: *"you wouldn't have gotten to 27 outs this fast … teams
should at least bat with their initial fielded lineup before making weird
swaps."* The lineup looked broken — many starters were gone before they'd
hit even once.

## 2. Diagnosis

The manager AI's tactical-substitution **deciders had no early-game gate**.
In `o27/engine/manager.py`:

- `should_pinch_hit` and `should_pinch_run` fire whenever the leverage
  score clears the persona threshold — **from out 0**. A close game with a
  strong bench (a weak starter vs a good reserve) clears the bar in the
  first inning-equivalent, so the manager "correctly" upgrades but does it
  absurdly early and empties the bench.
- `should_defensive_sub` gated only on `state.outs >= 6`, which can still
  be **before the order turns over** (6 outs ≈ half the 12-spot order with
  no baserunners).

Meanwhile the engine had **no in-game injury path at all** — injuries were
purely off-field, between-games roster management (`o27v2/injuries.py`). So
there was no legitimate reason for an early forced sub, and no model for one.

The deciders are invoked only from the event provider
(`o27/engine/prob.py._try_manager_action`); `pa.py` just executes the
returned event dicts. So a gate inside the deciders is sufficient.

## 3. Fix

### Part A — first-cycle gate
`Team.lineup_cycle_number` (increments when the order wraps in
`advance_lineup`) is the exact "the whole lineup has batted once" signal —
already used by `should_swap_offensive_for_defense`. Added
`if <team>.lineup_cycle_number < 1: return None` to `should_pinch_hit`,
`should_pinch_run`, and `should_defensive_sub` (the last keyed on the
*batting* team, since the fielding side holds its defense until the
opposing order turns over once). **Jokers stay ungated** — they are O27's
intended per-cycle tactical mechanic. Monotonic counter, never reset
per-half, so extras stay allowed.

### Part B — in-game injuries (forced mid-game subs)
New engine module `o27/engine/injury.py`, rolled once per PA in the
provider before tactical decisions:

- **Pitcher fatigue tax** (owner choice): per-batter arm-injury risk ramps
  with how far the pitcher is past his Stamina fatigue threshold (mirrors
  `prob.py`'s `fatigue_threshold`), dampened by grit. Overusing a tired ace
  on the 27-out arc is now genuinely risky — giving the workhorse trait
  teeth.
- **Acute position-player risk**: small per-PA chances for the batter, each
  baserunner, and a random fielder, all scaled by an age/grit durability
  multiplier. "Moderate" frequency (owner choice) — ~0.25 forced removals
  per game in an 8-team DB sim; bench/bullpen depth matters.
- On fire it routes through the existing executors (`pitching_change` /
  `pinch_hit` / `pinch_run` / `defensive_sub`), so it **bypasses the Part-A
  gate by construction** (it never touches the should_* deciders). Records
  `{player_id, team_id, kind, outs, replaced_by}` on `state.in_game_injuries`.
- **Fallbacks**: no eligible replacement or below the roster floor → the
  player plays through (no forced sub, no IL), keeping games playable.

Severity stays in the o27v2 layer (engine must not import o27v2):
`o27v2/injuries.apply_in_game_injuries` draws tier/duration with the
existing `_draw_tier`/`_tier_duration`, sets `injured_until`/`il_tier`,
logs the transaction + depth-chart cover. It runs in
`_post_game_roster_processing` **before** the ambient roll, so the
already-hurt players are naturally excluded (their `injured_until` filters
them out) — no double-injury.

Config constants live in `o27/config.py` (`INJURY_INGAME_*`), tunable via
the engine-tunables dashboard; `INJURY_INGAME_ENABLED=False` disables it.

## 4. Validation

- `pytest o27/tests` — 134 passed (incl. new
  `test_sub_first_cycle.py`: deciders return None at cycle 0, fire at
  cycle ≥ 1, jokers ungated, injury bypasses the gate, injury roll
  reproduces under a fixed seed).
- **Determinism**: same seed + same roster state → identical injuries
  (CLAUDE.md invariant). An earlier "flip" was a test artifact from two
  *identical* dummy teams (home-bats-first coin flip resolving by object
  id); with distinct rosters it's deterministic.
- **End-to-end**: 8-team DB, 224 games → 55 in-game injuries persisted with
  correct IL tiers/return dates and transactions; pitcher injuries fire
  deep in the arc (the fatigue tax).

## 5. Follow-ups / not done

- Box-score footnotes still show injury replacements as ordinary
  PH/DEF/pitching entries; the injury itself surfaces via the play-by-play
  line, the IL stint, and the transaction log. A footnote special-case
  ("left the game (injury)") is a nice-to-have not yet wired.
- Injury triggers are PA-boundary rolls, not attributed to the specific
  HBP / diving catch / slide. Good enough for "moderate" volume + the
  fatigue tax; finer event attribution is a future refinement.
- The in-game injury rate constants are first-pass estimates — tune via the
  dashboard against a full-season sim if the volume feels off.
