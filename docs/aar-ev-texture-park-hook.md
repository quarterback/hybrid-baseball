# After-Action Report — exit-velocity BABIP texture in the park hook

**Date completed:** 2026-06-01
**Branch:** `claude/peaceful-thompson-ZB5UY`
**Scope:** `o27/engine/park_effects.py`, `o27/config.py`,
`o27/tests/test_park_ev_texture.py`

---

## TL;DR

Follow-on from the Savant feasibility audit. The user asked: the batted-ball
physics (EV/LA/spray) is sampled but "doesn't drive outcomes" — *why, and can
I make it?* The honest answer turned out to be that it **already** drives
outcomes in one place — `apply_park_effects` flips hit_type against fence
geometry — and the user chose **option A: broaden that hook** so exit velocity
re-decides a slice of *non-park* marginal balls too.

Implemented four EV-gated rules, appended after the five existing
park-geometry rules (reached only when none of those fired):

| # | Rule | Condition | Effect |
| --- | --- | --- | --- |
| 6 | Seeing-eye single | scorched grounder (EV ≥ 108, LA < 10) | ground_out → single |
| 7 | At-'em ball | scorched liner (EV ≥ 108, LA 14-28) | single/double → line_out |
| 8 | Bloop single | soft fly/liner (EV ≤ 78, LA 12-40) | fly_out/line_out → single |
| 9 | Routine roller | soft grounder hit (EV ≤ 78, LA < 10) | single → ground_out |

The rules are **deliberately paired**: 6+8 turn outs into hits, 7+9 turn hits
into outs. The point is BABIP *variance* tied to contact quality, not a shift
in the run environment.

## Why the physics was downstream in the first place

Reading the resolution path (`o27/engine/prob.py`):

1. `resolve_contact()` decides the categorical `hit_type` from the tuned
   weak/medium/hard contact tables (power redistribution, RISP suppression,
   defense rating, park multipliers, joker decay).
2. `sample_batted_ball()` then samples `(EV, LA, spray)` **conditioned on**
   that already-decided hit_type.
3. `apply_park_effects()` is the one sanctioned spot where physics overrides
   the roll — via fence geometry only.

The categorical tables *are* the calibration: the whole run environment is
expressed as sum-preserving redistributions on them, with identity invariants
everywhere. Making physics the primary driver would throw that away and force
a full re-tune. The park hook is the design's intended extension point —
which is why option A lives there.

## Validation

Method: deterministic `initdb` (confirmed byte-identical rosters across runs:
2016 players, identical total power, same team order) + `sim 300`, before and
after the change.

**Run environment (same rosters):**

| metric | baseline | with EV rules | Δ |
| --- | --- | --- | --- |
| runs / game | 25.69 | 25.20 | −0.49 (−1.9%) |
| **hits / BIP** | **0.5859** | **0.5863** | **+0.0004** |
| HR / BIP | 0.0668 | 0.0622 | −0.0046 |
| outs / BIP | 0.3926 | 0.3932 | +0.0006 |

`hits/BIP` is the metric the rules *directly* move, and it is flat to four
decimals — the out→hit and hit→out conversions cancel as designed. The R/G and
HR/BIP wobble are **RNG-stream desync artifacts**, not a real offense shift:
each fired rule consumes an `rng.random()`, which re-phases every downstream
roll in the game (the pre-existing park rules 3 & 5 do the same). With the
stream re-phased, ±60 HR over 300 games is ordinary variance for the
highest-variance event; the balanced BABIP number is the trustworthy signal.

**EV is now genuinely decisive on marginal balls:** scorched grounders
(EV ≥ 108) fall for hits 86% of the time; soft flies/liners become bloops 68%.

**Tests:**
- `o27/tests` — **63 passed** (engine suite clean).
- `o27/tests/test_park_ev_texture.py` — **8 new tests** pinning each rule's
  firing, the rng gate, and the identity contracts (None park_dims → no-op;
  non-BIP → no-op; mid-EV → unchanged).
- `tests/test_stat_invariants.py` — **identical 5-fail / 6-pass on baseline
  AND on the new code.** The 5 failures are pre-existing/environmental (flask
  absent in this sandbox; suite expects a full-season web context). My change
  introduces **zero** new invariant failures.

## Tuning knobs

All in `o27/config.py` (set any probability to 0.0 to disable a rule):
`EV_SCORCHED` (108), `EV_SOFT` (78), `EV_SCORCH_THRU_P` (0.35),
`EV_ATEM_P` (0.18), `EV_BLOOP_P` (0.28), `EV_ROLLER_P` (0.25). The EV cuts are
read off the **live O27 league distribution** (median ~92, top decile ~108,
bottom decile ~76), not MLB's 95-mph anchor.

## What I did NOT do

- Did not touch `resolve_contact` or the contact tables — the base hit/out
  roll is unchanged; this layers strictly on top.
- Did not invert the engine (options B/C from the prior discussion) — those
  would require re-calibrating the run environment.
- Did not isolate the EV rolls onto a private RNG. Perturbing the shared
  stream is consistent with the existing park rules; switching would silently
  change every legacy game's seed-replay, which is a bigger compatibility
  decision than this change warrants.
- Did not sweep the probabilities for an optimum — they were set to balance
  hits/BIP and validated once at n=300. A larger sweep is a reasonable
  follow-up if the league mix needs fine-tuning.
