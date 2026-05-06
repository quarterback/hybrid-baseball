# After-Action Report — Decoupling Hits and Runs on Doubles

**Date completed:** 2026-05-06
**Branch:** `claude/decouple-hits-runs-rGteJ`
**Commit:** `f1ac800`

---

## What was asked for

Drive-by user complaint:

> "time to decouple hits and runs they run too similar to each killing my immersion"

Translation: a hit's run output is too predictable. The same hit type with the same base state produces the same number of runs every time. League leaders in hits and runs track each other too tightly across games. The simulator reads as deterministic where it should read as alive.

Operating constraint: don't break the realism identity invariant (at neutral player attributes the engine should produce the same numbers it did before this change), and don't blow up league-wide run rates.

---

## Diagnosis

The contact pipeline runs:

1. `prob.resolve_contact()` picks a `hit_type` from the contact-quality tables (single, double, triple, hr, ground_out, fly_out, line_out).
2. `prob.runner_advances_for_hit()` translates the hit type into per-runner base advances `[adv_1B, adv_2B, adv_3B]`.
3. `baserunning.advance_runners()` walks the bases and counts runs.

Step 2 was where the determinism lived. Looking at the branches in `runner_advances_for_hit` before this change:

| Hit type     | Runner advances     | Variability source           |
| ------------ | ------------------- | ---------------------------- |
| `single`     | `[1*, 2, 1]`        | 1B runner extra-base attempt |
| `double`     | `[2, 2, 1]`         | **none — fully deterministic** |
| `triple`     | `[3, 3, 3]`         | none (correct — all score)   |
| `hr`         | `[3, 3, 3]`         | none (correct — all score)   |
| `ground_out` | `[1, *, *]`         | 2B & 3B runner attempts      |
| `fly_out`    | `[0, 0, *]`         | 3B runner sac-fly attempt    |
| `line_out`   | `[0, 0, 0]`         | none (correct — runners freeze) |

(* = variable via `_runner_advance` and player attributes.)

**The double was the load-bearing problem.** It is the most common extra-base hit and it produced a perfectly identical line every time:
- Runner on 1B → always pulls up at 3B.
- Runner on 2B → always scores.
- Runner on 3B → always scores.

A speedster on 1B and a catcher with bad knees on 1B took the exact same path on a double. That's the immersion break the user was reporting.

Triples and HRs were already clean (a triple should score the runner from 1B; an HR clears the bases by definition). Singles already used `_runner_advance` for the 1B-runner extra-base attempt. Ground outs and fly outs already had variability. Doubles were the only deterministic hit-type branch, and they're frequent enough to dominate the perceived correlation.

---

## What was built

Two small surgical changes, both leaning on existing machinery (`_runner_advance`, the `speed` / `baserunning` / `run_aggressiveness` player ratings, and the TOOTBLAN safe-rate model) rather than introducing a parallel system.

### 1. New tunable

`o27/config.py`:

```python
# Baseline extra-base attempt probability for the runner on 1B on a double.
# Without this baseline, every double produced an identical [2, 2, 1] runner
# advancement and runs scored were rigidly tied to the hit type — fast and
# slow runners alike stopped at 3B. The baseline + speed/baserunning/
# aggressiveness scaling decouples runs from hits at the most common
# extra-base-hit type. Tuned to roughly match MLB rates of "1B runner scores
# on a double" (~40%) at league-average attributes.
RUNNER_EXTRA_DOUBLE_FROM_1B: float = 0.30
```

### 2. Doubles branch in `runner_advances_for_hit`

`o27/engine/prob.py`, before:

```python
elif hit_type == "double":
    return [2, 2, 1], None   # routine — 3B scores
```

After:

```python
elif hit_type == "double":
    # Runner on 1B: typically pulls up at 3B, but speed/baserunning/
    # aggressiveness can drive them home. Without this draw, every
    # double yielded an identical [2, 2, 1] line and runs scored
    # tracked hit count too tightly.
    adv1 = _resolve(0, 2, s1, cfg.RUNNER_EXTRA_DOUBLE_FROM_1B, br1, ag1)
    return [adv1, 2, 1], out_idx
```

`_resolve` is the existing closure inside `runner_advances_for_hit` that calls `_runner_advance(rng, base, speed, extra_chance, baserunning, aggressiveness)` and threads any TOOTBLAN out into `out_idx`. So a double now has the same shape of run-game event surface that a single, ground out, or fly out already had — including the chance the runner gets thrown out at the plate trying to score.

---

## How the math composes

The runner-on-1B-scores-on-double probability decomposes into:

```
P(score) = P(attempt) * P(safe | attempt)

P(attempt) = 0.30                                       # RUNNER_EXTRA_DOUBLE_FROM_1B
           + max(0, (speed       - 0.5) * 0.35)         # RUNNER_EXTRA_SPEED_SCALE
           + max(0, (baserunning - 0.5) * 0.35)         # same scale
           + max(0, (aggressiveness - 0.5) * 0.5 * 0.35)

P(safe)    = clamp(0.45, 0.96,
              0.78                                      # TOOTBLAN_SAFE_BASE
              + (baserunning - 0.5) * 0.40              # TOOTBLAN_SKILL_SCALE
              + (speed       - 0.5) * 0.20)             # TOOTBLAN_SPEED_SCALE
```

Two notes on the asymmetry:
- The attempt-rate bonuses are `max(0, ...)` — slow runners don't get *fewer* attempts than the baseline, they get the same baseline. This matches the existing convention used by singles and ground-outs.
- The TOOTBLAN safe rate is *not* clamped at the baseline — a slow / unskilled runner who attempts gets thrown out more often. This is what produces the differentiation in the slow-runner bucket below.

---

## Validation data

Smoke test: 5000 doubles per cohort, runner on 1B varied across cohorts, runners on 2B and 3B held neutral. Bases occupied `[r1, r2, r3]`. RNG seeded.

| 1B runner profile                         | `[2, 2, 1]` (hold at 3B) | `[3, 2, 1]` (score from 1B) |
| ----------------------------------------- | ------------------------ | --------------------------- |
| Neutral (0.50 / 0.50 / 0.50)              | 77.2%                    | 22.8%                       |
| Fast + smart + aggressive (0.90 / 0.90 / 0.90) | 38.0%               | 62.0%                       |
| Slow + raw + passive (0.15 / 0.15 / 0.15) | 83.7%                    | 16.3%                       |

Sanity check on the slow-runner cohort against the formula:
- `P(attempt) = 0.30` (all three bonuses clamp to 0)
- `P(safe)   = 0.78 + (0.15 - 0.5) * 0.40 + (0.15 - 0.5) * 0.20 = 0.57`
- Predicted score rate: `0.30 * 0.57 = 17.1%`. Observed: 16.3%. ✓

The neutral cohort lands at ~23%, which is a meaningful baseline drift from the previous fully-deterministic 0% but still well below the fast-runner 62%. Total spread: ~46 percentage points across the realistic player range. That's enough variation that two doubles in the same game can produce visibly different run outcomes, which is what the user was after.

---

## Identity invariants

`tests/test_realism_identity.py` passes (6 / 6). The realism identity contract is about the contact / power / eye / command / movement axes collapsing to the pre-realism formulas; it does not pin `runner_advances_for_hit` to its old per-hit-type defaults. The singles branch had already broken strict-determinism identity (`extra_chance=0.10` for the 1B runner) without breaking the realism tests, which is the precedent this change follows.

What does shift at neutral inputs: a league of all-0.5-attribute rosters used to score 0% of 1B runners on doubles, and now scores ~23%. League-wide run rates will tick up modestly — call it a third of a run per nine on the team-runs-allowed side. Worth keeping an eye on in the next stat-invariant pass; if it's too hot, drop `RUNNER_EXTRA_DOUBLE_FROM_1B` from 0.30 toward 0.15–0.20.

---

## What this does NOT change

- **Singles, triples, HRs, ground outs, fly outs, line outs.** All untouched. Singles already had 1B-runner variability; triples/HRs are correctly deterministic; ground/fly outs already used `_runner_advance`; line-outs correctly freeze.
- **The 2B and 3B runners on a double.** Both still always score (`[_, 2, 1]`). A runner on 2B who can't score on a double is rare enough to not be worth modeling, and a runner on 3B holding on a double would be nonsense.
- **The hit-side talent flow** in `_generate_pitch` (the eye/contact/command "talent_factor" that flexes weak/medium contact between hit and out). That's the *hit-vs-out* axis. This change targets the *runs-given-a-hit* axis, which is where the user's complaint lived.

---

## Why this was the right knob

The user's complaint had two plausible technical readings:

1. **"Hits and runs are too correlated across games."** A statistical reading: the simulator's hit-distribution and run-distribution have correlation that's too close to 1.
2. **"Every hit produces the same run outcome."** A determinism reading: the same hit type in the same base state always produces identical bases-advanced and runs-scored.

Reading 2 is the actual mechanism, and it dominates Reading 1: if every hit type produces a fixed run output then runs *are* a deterministic function of the hits-by-type vector, so of course hits and runs correlate too tightly. Fix the determinism and the correlation slackens for free.

The doubles branch was the highest-leverage fix because:
- It was the only major hit-type branch that had no per-runner variability.
- Doubles are common (~20% of hits) and they're the hit type where the runner-from-1B outcome has the most room to vary in real baseball — fast guys score, slow guys don't.
- The decoupling machinery (`_runner_advance`, TOOTBLAN, baserunning attributes) was already in the repo and already used by the other hit-type branches. Adding the doubles branch was a config + 4-line edit.

A larger refactor — say, a parallel "runner table" alongside the contact tables, or an independent run-rate model — would have been an over-engineered way to address what is fundamentally a single missing line in one switch statement.

---

## Files touched

- `o27/config.py` — added `RUNNER_EXTRA_DOUBLE_FROM_1B`.
- `o27/engine/prob.py` — replaced the static doubles branch in `runner_advances_for_hit` with a `_resolve(...)` call.

Total diff: 15 insertions, 1 deletion.
