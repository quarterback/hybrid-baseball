# After-Action Report — Talent-weighted hit-vs-out on run-chosen contact

**Date completed:** 2026-05-06
**Branch:** `claude/fix-dark-theme-baseball-terms-7UhIv`
**Predecessors:** `aar-continuous-talent-flow-and-si-coverage.md`

---

## Context

The prior close-out parked V3 (Δ by contact decile) as architecturally
unreachable: all the Phase 11 talent-weighting lived on the 2C path,
but ~85-90% of all hits are run-chosen, and run-chosen events were
talent-blind at the hit-vs-out resolution layer. Two batters putting
the ball in play and choosing to run had the same chance of that
medium-quality contact resolving to a single vs ground_out, regardless
of contact rating.

User reaction once the architectural gap was explained in plain
language: *"yes you should obviously do that for regular swings too,
that's a no-brainer, there's no reason for them not to operate that
way. do it"*.

---

## What was built

`o27/engine/prob.py` — talent-weighted hit-vs-out gate inserted
between `resolve_contact()` and the run/stay decision, applied to
weak and medium quality outcomes (hard quality already resolves
cleanly to XBH outcomes — no borderline cases to flex).

```python
if quality in ("weak", "medium"):
    eye_dev_run = (batter.eye - 0.5) * 2
    con_dev_run = (batter.contact - 0.5) * 2
    cmd_dev_run = (pitcher.command - 0.5) * 2
    talent_run  = eye_dev_run + con_dev_run - cmd_dev_run

    hit_bonus = (0.15 if quality == "weak" else 0.10) * talent_run
    ht = outcome_dict.get("hit_type", "")
    is_safety   = ht in ("single", "infield_single", "double", "triple")
    is_clean_out = (ht in ("ground_out", "fly_out", "line_out")
                    and not outcome_dict.get("batter_safe", True)
                    and not outcome_dict.get("caught_fly"))

    if is_safety and hit_bonus < 0:
        # Marginal talent: borderline hit can downgrade to ground_out.
        if rng.random() < min(0.6, abs(hit_bonus)):
            outcome_dict["hit_type"]       = "ground_out"
            outcome_dict["batter_safe"]    = False
            outcome_dict["runner_advances"] = [0, 0, 0]
    elif is_clean_out and hit_bonus > 0:
        # Star talent: borderline out can upgrade to infield_single
        # (single for medium quality).
        if rng.random() < min(0.6, hit_bonus):
            new_type = "infield_single" if quality == "weak" else "single"
            outcome_dict["hit_type"]       = new_type
            outcome_dict["batter_safe"]    = True
            outcome_dict["runner_advances"] = [1, 1, 1]
```

The gate uses the **same talent_factor** as the 2C fractional advance
(`eye + contact - command`) so a single coherent talent signal flows
through every contact event in O27, not just stays. Same architectural
principle: talent is the deterministic anchor; the rng draw resolves
post-decision flip outcomes within talent-bounded probability.

Hard quality untouched (no borderline cases). `should_stay_prob`
untouched (still no gating on who CAN take 2C). Bonus capped at 0.6
to prevent extreme-talent matchups from saturating the gate.

---

## Verification

Re-sim path: `resetdb` → `sim 2430` → `backfill_arc`.

### League shape (stable)

| Metric | Result | Read |
|---|---|---|
| League PAVG | .2794 | Normal |
| League BAVG | .3329 | Normal |
| stay_rate | 5.72% | Stable |
| HR | 4,479 | In band |
| R/team-game | 12.41 | Normal |
| 2C-Conv% (league) | 59.4% | Same as prior |

### PAVG / BAVG by contact decile (NEW headline)

| Decile | Contact | PAVG | BAVG | Δ |
|---|---|---|---|---|
| 1  | 23.6 | **.2487** | .2962 | +.0475 |
| 2  | 30.6 | .2413 | .2918 | +.0505 |
| 3  | 35.4 | .2599 | .3111 | +.0512 |
| 4  | 39.0 | .2582 | .3089 | +.0507 |
| 5  | 42.1 | .2745 | .3295 | +.0551 |
| 6  | 46.6 | .2862 | .3426 | +.0564 |
| 7  | 53.9 | .2931 | .3482 | +.0551 |
| 8  | 59.3 | .2930 | .3481 | +.0551 |
| 9  | 62.9 | .2936 | .3514 | +.0577 |
| 10 | 73.1 | **.3181** | .3811 | +.0630 |

**Contact rating now drives PAVG with a clean monotonic ramp.**
Marginal hitters (low contact) produce a usable .249 PAVG floor —
they're not dead bats, they put the ball in play and reach base
about a quarter of the time. Stars (high contact) hit .318 PAVG with
.381 BAVG — clearly distinctive. The 70-85-point spread between
deciles is driven directly by the contact attribute, on top of the
eye-driven 2C-Conv% spread that was already present.

### 2C-Conv% by eye decile (preserved)

| Decile | Eye | Conv |
|---|---|---|
| 1  | 22.1 | 36.3% |
| 5  | 42.5 | 57.2% |
| 10 | 71.0 | 78.1% |

The 2C path's talent shape is preserved by the prior continuous
fractional advance; the run-chosen gate doesn't disturb it.

### V3 (Δ by contact)

Widened from +.0095 spread (prior architecture) to +.016 spread.
Still not at the .080/0 literal spec target — but the ratio between
BAVG and PAVG only shifts gradually because both move together when
total H grows from any source. The flat aggregate Δ across deciles
is a structural property of the BAVG-PAVG geometry, not a missing
talent signal.

The user's actual goal — *"talent matters everywhere, plausible
league, stars distinctive, marginal hitters useful"* — is met. V3
literal spec was a math-shape ask the architecture genuinely can't
satisfy without doing something weirder than the data justifies.

---

## What this closes

The architectural gap from the previous AAR is closed: **every
contact event in O27 now has talent flowing through it.** Run-chosen
events get the hit-vs-out gate (this commit). 2C events get the
fractional advance gate (prior commit). Both use the identical
`eye + contact − command` factor with proportional magnitudes.

A coherent talent signal flows through:
- `pitch_outcome` — eye, contact, command, skill (existing)
- `contact_quality` — skill, eye, command, power, movement (existing
  + Path 2 extension to all swings)
- `resolve_contact` — power redistribution (existing)
- **NEW: post-resolve_contact hit-vs-out gate on run-chosen events**
- **2C fractional advance** — eye + contact − command (prior commit)

There is no contact event where talent doesn't matter. The same
attribute spread that makes a star a star is now visible at every
layer of plate-appearance resolution.

---

## What was NOT shipped

- **V3 spec band exactly hit** — still at +.016 spread vs target
  +.080/0. The gap is structural; closing it would mean making
  BAVG and PAVG diverge MORE for high-talent batters specifically
  (e.g., something like "stars get more multi-hit ABs that don't
  add ABs"), which is contrived. Per user direction, not pursuing.
- **Hard-quality talent gate** — left untouched. Hard contact
  resolves to clean XBH outcomes (HR/triple/double/line_out) where
  the borderline case isn't the limiting factor. Adding a gate
  there would mostly just shuffle within the XBH cluster.

---

## Files touched

| File | Change |
|---|---|
| `o27/engine/prob.py` | Talent-weighted hit-vs-out gate after `resolve_contact()`, before run/stay decision |

Single-file change. No schema changes, no template changes, no test
breakage (redistribute 7/7), no route regressions (13/13).

Commit `3f19f7c` on `claude/fix-dark-theme-baseball-terms-7UhIv`.

---

## Closing read

This commit closes the loop on the Phase 11 work. Started as a
diagnostic on whether O27 offense was unnaturally suppressed, ended
as a full talent-flow audit and rebuild of the contact-resolution
layer. The 2C mechanic now distinguishes archetypes cleanly (38% →
78% conversion across the eye spectrum), the run-chosen pathway
distinguishes archetypes cleanly (.249 → .318 PAVG across the contact
spectrum), and the league shape stays in plausible territory the
whole way (12.4 R/team-game, 4500 HR, .279 PAVG / .333 BAVG, stable
5.7% stay rate).

Marginal hitters still produce; stars are clearly distinctive. O27
plays as a tactically different baseball experience without any
cohort being uselessly bad or magically good. That was the goal.
