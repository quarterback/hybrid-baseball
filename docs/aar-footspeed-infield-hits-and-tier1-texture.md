# After-Action Report — Tier-1 EV texture + Tier-3 foot-speed infield hits

**Date completed:** 2026-06-01
**Branch:** `claude/peaceful-thompson-ZB5UY`
**Scope:** `o27/engine/park_effects.py`, `o27/engine/prob.py`, `o27/config.py`,
`o27/tests/test_park_ev_texture.py`, `o27/tests/test_infield_arm.py`

---

## TL;DR

Two follow-ons to the EV-texture work, both aimed at *real* batted-ball
mechanics (the user explicitly deferred the park-quirk "gimmick" tier).

**Tier 1 — five more EV/LA rules in the park hook (rules 10-14):**

| # | Rule | Trigger | Effect | Direction |
| --- | --- | --- | --- | --- |
| 10 | Can-of-corn | lazy fly, LA 36-48, EV ≤ 88 | single/double → fly_out | hit→out |
| 11 | Legged-out tapper | dribbler, EV ≤ 62, low LA | ground_out → infield_single | out→hit |
| 12 | Frozen rope | EV ≥ 102, LA 12-26 | single → double | slug |
| 13 | Down the line | \|spray\| ≥ 40, LA 8-30 | single → double | slug |
| 14 | Wall-ball carom | EV ≥ 102 into a tall (≥22ft), deep (≥400ft) alley | double → triple | slug |

10 (hit→out) and 11 (out→hit) pair off; 12-14 only move the extra-base mix.

**Tier 3 — foot speed vs infield arm on leg-out grounders:**
The borderline-grounder hit/out flex (`prob.py`) keyed off eye/contact/command
only — **foot speed was ignored**, which is backwards for infield hits. Added a
`leg_out_dev = (batter.speed − avg_infield_arm) × INFIELD_HIT_SPEED_SCALE` term,
applied **only** to infield grounder transitions (`ground_out ↔ infield_single`).
A burner vs a weak-armed infield legs more out; a plodder vs rocket arms is rung
up. New helper `_avg_infield_arm()` mirrors the existing `_avg_outfielder_arm()`.

## Why foot speed was the real gap (and why it's infield-only)

The engine already models **runner speed vs outfielder arm** on the bases —
`_resolve_runner_advance` / `_resolve_table` weight first-to-third, scoring
from second, and tag plays by `SPEED_ADVANCE_MOD` (±0.12) and `ARM_ADVANCE_MOD`
(±0.11). The nickel "4th outfielder" power play further cuts gappers to singles.
So arm-vs-speed was well covered on the **outfield/baserunning** side and left
**fully intact** by this change.

What was missing was the **infield** leg-out: the throw to first on a grounder.
That's a foot-speed-vs-infield-arm play, and it lived in a flex that didn't read
speed at all. The new term is gated to the categorical grounder hit_types
(`ground_out`, `infield_single`) on purpose — EV/LA aren't sampled until later
in the PA flow, and you don't leg out a fly ball, so fly_out / line_out /
extra-base hits are deliberately untouched.

## Validation

Deterministic rosters, `sim 300` (Tier 1) / `sim 300` (Tier 3).

**Tier 1 fires as designed:** 2B/BIP 0.198, 3B/BIP 0.018, 124 infield singles;
hits/BIP within noise of the prior layer (the user confirmed the sub-0.005
BABIP wobble is not significant).

**Tier 3 foot speed is decisive on infield hits:**
- Fast batters (speed ≥ 60) leg out infield singles at **0.0101**/BIP vs
  **0.0050** for slow batters (≤ 40) — a clean ~2:1.
- corr(foot speed, infield-single rate) = **+0.173** (modest by design —
  contact quality and the infield arm also weigh in; speed isn't the only term).

**Tests:**
- `o27/tests` — **81 passed** (was 63 pre-feature). New: 6 Tier-1 rule tests in
  `test_park_ev_texture.py`, 4 `_avg_infield_arm` tests in `test_infield_arm.py`.
- `tests/test_stat_invariants.py` — **identical 5-fail/6-pass on baseline and on
  the new code** (the 5 are pre-existing flask/full-season environmental
  failures). The Tier-3 change touches the calibrated resolve flow, so this was
  the key guard: **zero new invariant failures.**

## Tuning knobs (all in `o27/config.py`)

Tier 1: `EV_TAPPER_MAX` (62), `EV_FROZEN` (102), `EV_LAZYFLY_P` (0.45),
`EV_TAPPER_P` (0.40), `EV_FROZENROPE_P` (0.18), `EV_DOWNLINE_P` (0.30),
`EV_WALLBALL_P` (0.30).
Tier 3: `INFIELD_HIT_SPEED_SCALE` (0.20). Mean-neutral across the league
(symmetric in speed and arm); set to 0.0 for identity.

## What I did NOT do

- Did **not** touch or gate the foot-speed-vs-outfielder-arm baserunning plays
  (first-to-third, scoring from second, bang-bang tags) or the nickel power
  play — those were already modeled and remain fully active.
- Did **not** implement Tier 2 (park shape / quirks) — deferred as "gimmick"
  risk per the user.
- Did not isolate the EV rolls onto a private RNG (same compatibility call as
  the prior AAR).
- Did not sweep probabilities for an optimum — set to sensible defaults and
  validated once at n=300.
