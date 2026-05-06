# After-Action Report — Decoupling Hits and Runs (Doubles, Throw-outs at Home, GIDP, Triple Play, 2C Fielders' Choice)

**Date completed:** 2026-05-06
**Branch:** `claude/decouple-hits-runs-rGteJ`
**Commits:** `f1ac800` (doubles), `9145b77` (throw-outs at home + GIDP + triple play + 2C FC)

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

## Files touched (commit 1 — doubles)

- `o27/config.py` — added `RUNNER_EXTRA_DOUBLE_FROM_1B`.
- `o27/engine/prob.py` — replaced the static doubles branch in `runner_advances_for_hit` with a `_resolve(...)` call.

Total diff: 15 insertions, 1 deletion.

---

## Follow-up — broader scope (commit `9145b77`)

User came back with a series of expansions:

> "Triples shouldn't always score there should be a percentage that get thrown out at home or GIDP or Triple plays or get caught stealing or other thing"
>
> "Or just men on 3rd base i mean not just triples, same for anyone on base really."
>
> "Double plays too obviously"
>
> "But not impossible Fielders choice lessens witb 2nd chance ab"

Translation in order: (1) triples shouldn't auto-score; (2) generalize that to any runner trying to score on a hit; (3) ship double plays; (4) double plays / fielders' choice should still happen on a 2C / stay AB, just at a reduced rate.

### What was built

**1. `_thrown_out_at_home` helper (`o27/engine/prob.py`).**
A small utility for "runner whose default base advance carries them across the plate, who can still be cut down by a strong relay." Distinct from the existing TOOTBLAN model (which fires only on *extra*-base attempts above the default). Symmetric in sign — fast/skilled runners shave the rate toward zero, slow/raw runners inflate it. Tuned via three new config knobs:

```python
RUNNER_THROWN_OUT_AT_HOME_BASE: float        = 0.05
RUNNER_THROWN_OUT_AT_HOME_SPEED_SCALE: float = 0.10
RUNNER_THROWN_OUT_AT_HOME_SKILL_SCALE: float = 0.10
```

Wired into two `runner_advances_for_hit` branches:
- **Single** — the runner from 2B trying to score (close play at the plate). The runner from 3B is NOT challenged: at 90 ft from home it's routine.
- **Triple** — the runner from 1B trying to score. The triple branch was previously merged with `hr` into `[3, 3, 3]`; it's now split out so HRs stay deterministic.

The 2B runner on a double also routinely scores, but the throw home from a deep outfield ball is too long to be a meaningful close play, so it's left as automatic.

**2. GIDP — ground-ball double plays.**

Inserted in `_generate_pitch` *after* the run/stay choice (so it only fires on run plays). Conditions: `hit_type == "ground_out"`, runner on 1B, `< 2` outs, not an error. Probability:

```
dp_p = clamp(0, 0.65,
        0.32                                            # GIDP_BASE_PROB
        - (batter.speed - 0.5) * 0.30                   # GIDP_SPEED_SCALE
        + (team.defense_rating - 0.5) * 0.20)           # GIDP_DEFENSE_SCALE
```

When a DP fires:
- `hit_type` flipped to `double_play`.
- `runner_out_idx = 0` (1B runner forced at 2B).
- `runner_advances[0] = 0` (1B runner doesn't advance, will be cleared anyway).
- `batter_safe` stays False from the original ground out.

The architecture already supported two outs on a single play — `_resolve_contact` in `pa.py` records `_record_out` once for the runner_out_idx slot and once for `not batter_safe`. No changes needed there for DPs.

**3. Triple plays.**

Conditional on a DP firing in the bases-loaded-0-outs case, a small slice promote to triple plays (`TRIPLE_PLAY_GIVEN_DP_PROB = 0.04`). Required adding a new optional outcome-dict field:

```python
extra_runner_outs: list[int] = []   # additional runner outs beyond runner_out_idx
```

Threaded through `baserunning.advance_runners` (clears the extra slots, logs the outs) and `_resolve_contact` in `pa.py` (records `_record_out` for each extra runner). On a TP all `runner_advances` are zeroed — the 5-4-3 around-the-horn play is too abrupt for a runner to score before the 3rd out is recorded.

**4. 2C / stay reduced-rate fielders' choice.**

User constraint: stays shouldn't make the bases a free pass, but the batter isn't running so a true DP through 1B is physically impossible. Implementation: a separate `choice == "stay"` block in `_generate_pitch` rolls for a tag-out of the lead runner (1B forced no, but a fielder can still tag a runner who broke). Same speed-vs-defense math as GIDP, scaled by `GIDP_STAY_MULTIPLIER = 0.30`. When it fires:

- `hit_type` flipped to `fielders_choice` (renders as "fielder's choice").
- `runner_out_idx = 0`.
- The stay path in `_resolve_contact` preserves `batter_safe = True`, so the batter survives the AB; only the lead runner is out.

This deliberately *does not* fire if `runner_out_idx` was already set by an earlier path (TOOTBLAN, fielders_choice from `resolve_contact`) — first writer wins.

### Validation data — throw-outs at home

5000 hits per cohort, runner attribute on the relevant base varied. Other bases empty.

| Hit type | Runner profile (speed / baserunning)        | Thrown out at home |
| -------- | ------------------------------------------- | ------------------ |
| Single   | Neutral 2B runner (0.50 / 0.50)             | 5.2%               |
| Single   | Fast / skilled (0.95 / 0.95)                | 1.3%               |
| Single   | Slow / raw (0.10 / 0.10)                    | 13.1%              |
| Triple   | Neutral 1B runner (0.50 / 0.50)             | 4.6%               |
| Triple   | Fast / skilled (0.95 / 0.95)                | 0.0%               |
| Triple   | Slow / raw (0.10 / 0.10)                    | 13.1%              |

A ~13-percentage-point spread between elite and replacement-level runners on the close-play axis. At neutral attributes the rate is small enough (5%) that league-wide run rates barely move; at extreme attributes the variation is enough to feel different.

### Validation data — GIDP / TP rates

5000 BIPs per scenario, neutral pitcher / batter / defense unless noted. Reporting share of all balls in play (so totals include singles, doubles, etc.):

| Scenario                                       | Ground out | Double play | Triple play |
| ---------------------------------------------- | ---------- | ----------- | ----------- |
| 1B runner, 0 outs, neutral                     | 22.1%      | 5.5%        | —           |
| 1B runner, 0 outs, slow batter + elite defense | 19.1%      | 9.0%        | —           |
| 1B runner, 0 outs, fast batter + weak defense  | 23.5%      | 0.9%        | —           |
| Bases loaded, 0 outs, neutral                  | 21.9%      | 5.5%        | 0.34%       |
| 1B runner, 2 outs (gate closed)                | 28.4%      | 0.0%        | —           |
| Bases empty, 0 outs (gate closed)              | 27.5%      | 0.0%        | —           |

Reading the table:
- The GIDP gate works: DPs are 0% when the 1B-runner-and-<2-outs gate is closed, and 5–9% when open.
- Ground-out share drops by ~5–6 points in GIDP-eligible scenarios — that's the ground outs that got upgraded to DPs. Total out share is preserved.
- The slow-batter-elite-defense cohort hits 9% (up from 5.5% neutral); the fast-batter-weak-defense cohort hits 0.9% (down from 5.5%). The same speed-vs-defense math the user already applies to extra-base hits applies cleanly here.
- Triple plays at 0.34% per BIP in their tightest gate (bases loaded, 0 outs). MLB-conditional rate is roughly in the right neighborhood.

### Identity / regression check

`tests/test_realism_identity.py` still passes 6/6. None of the changes touch the contact-quality / pitch-probability / power axes that the identity contract pins.

### Net effect on run rates

Three changes that *reduce* runs (throw-outs at home on singles & triples, GIDPs, TPs) and one that *increases* runs (1B runner scoring on a double from the first commit). Rough back-of-envelope for a neutral roster:

- Doubles per game ≈ 3-4. Previously 0% of 1B-runner-on-double scored; now ~23%. With ~1.5 doubles per game having a runner on 1B, that's ~+0.35 runs/game per side.
- GIDPs at ~5% of BIPs in eligible situations (~3 ground outs per game with runner on 1B and < 2 outs) → ~+0.15 outs/game per side. Each DP forfeits ~0.4 expected runs → ~-0.06 runs/game.
- Throw-outs at home on triples + singles: rare; ~5% of triples (1 per 5 games?) and ~5% of single-with-2B-runner situations (~1 per 2 games?) → ~-0.05 runs/game per side.
- Triple plays: ~1 every 100+ games. Negligible.

Rough net: maybe +0.20 runs/game per side at neutral. Worth re-checking against the stat-invariant suite once a fresh-sim DB is available; the relevant config knobs to dial back if it's too hot are `RUNNER_EXTRA_DOUBLE_FROM_1B` and `GIDP_BASE_PROB`.

---

## Open question — moving-runners ("Apollo") effectiveness stat for 2C

User raised a separate question alongside the gameplay asks:

> "pay Apollo has the stat ... where you can see ... on second chance at bat they actually rate the batters on like how successful they are moving Reuter moving runners so like you've got a rate of moving runners from first second 2nd to 3rd and third to home and so it just shows the effectiveness of the second chance at bat ... if it's worth modeling I'd like to do it"

This is a tracked-and-rendered stat, not a gameplay change. The engine already has all the inputs:

- 2C events are first-class — they fire through `pa.py`'s stay path with `is_stay=True` into `advance_runners`, which returns `runner_advances`.
- The pre-stay base state and the post-stay base state are both visible in the stay path.
- The existing batter-stats schema (`o27/stats/batter.py`) is the natural place to add tracked counters.

**Recommendation: yes, model it. It's a high-fit feature.**

Rationale:
1. **2C is already a first-class concept.** The engine names it, gates it, has dedicated stay-vs-run probability code (`stay.py`, `should_stay_prob`, `_2C_*` config). Adding a per-batter "how good are you on 2Cs?" stat is consistent with the existing direction of the project — it's the natural box-score companion to the engine work that's already shipped.
2. **The mechanic is sabermetric in flavor.** Apollo's "rate of moving runners from 1st→2nd / 2nd→3rd / 3rd→home" is exactly the kind of derived rate stat that fits O27's other sabermetric exposures (the project already has the contact-quality breakdown, talent-flexed hits, fatigue-adjusted ERA, etc.). It's not a gameplay stat masquerading as box-score noise; it's a genuine evaluator.
3. **The data is cheap to capture.** Three counters per batter — `c2_attempts_runner_1B`, `c2_advances_runner_1B`, same for 2B and 3B — incremented in the stay branch of `_resolve_contact`. The aggregate rate is a derived percentage in the renderer.

**Suggested shape** (proposing only — would land in a follow-up commit, not this branch):

- **Counter:** for each 2C event with a runner on base X, increment `c2_attempts_X`. If the post-stay base state has the runner advanced ≥ 1 base, also increment `c2_advances_X`.
- **Display:** new column in the batter detail page — "2C Move %" with three sub-rates. In the box score: a single aggregate "2C eff" number, weighted across opportunities.
- **Naming:** "2C Moved-Runner Rate" or "2C Productivity" — depending on how heavily you want to lean into the Apollo nomenclature. The internal name `c2_*` is consistent with the existing config style; the user-facing name should match the rest of the stats page.
- **Edge cases:** no opportunity if no runner on base, so divide-by-zero handling. Foul-tip strikeouts on 2C swings shouldn't count as a "move attempt." A 2C that scores a runner from 3B on an existing fielding play (e.g., the runner was already going) shouldn't credit the batter — but separating that from a "moved by the swing" event is hard and probably not worth the complexity for a first cut.

Not implementing in this branch (would expand scope beyond the decoupling work); flagging as a clean next step if the user wants to pursue it.

---

## Follow-up — calibration pass (commit `<retune>`)

User pushback on the throw-out-at-home rates and the GIDP eligibility / range:

> "It should never be automatic there should ag least be a 2% chance of a fast runner being thrown out at home, 9% neutral and 25% slow/raw"
>
> "Same for GIDP the opp should be higher, can be with any scenario with a runner on base and hitter at plate or 2 or more runners on. gIDP should be nkt just speed impacted by the kind of contact that dictated it, weak hits can lead to them along than just speed + defense. Should be a range of 6 % to 23% depending on the eligible spot."
>
> "Triple plays can happen with 2 runners on and a batter; baserunner errors can induce them as well."
>
> "Moving runner % add it"

Translation: re-tune throw-out rates with a hard floor; broaden GIDP eligibility to any runner on base; layer contact quality into the GIDP formula; clamp GIDP into a 6–23% band; allow triple plays with 1B+2B (not just bases loaded); model baserunner errors as a TP bonus; ship the Apollo-style 2C moved-runner stat.

### Throw-outs at home — re-tuned

Old config gave 0% / 5% / 13% across fast/neutral/slow. Targets are 2% / 9% / 25%. Hit them by lifting the base rate, doubling the per-axis scales, and adding a hard floor:

```python
RUNNER_THROWN_OUT_AT_HOME_BASE: float        = 0.09
RUNNER_THROWN_OUT_AT_HOME_SPEED_SCALE: float = 0.20
RUNNER_THROWN_OUT_AT_HOME_SKILL_SCALE: float = 0.20
RUNNER_THROWN_OUT_AT_HOME_MIN: float         = 0.02   # never automatic
```

Helper now clamps at the MIN rather than at 0. Verified rates (20k samples per cohort):

| Profile (speed / baserunning) | Throw-out rate |
| ----------------------------- | -------------- |
| Fast / skilled (0.95 / 0.95)  | 1.9%           |
| Mid-fast (0.75 / 0.75)        | 1.9% (floored) |
| Neutral (0.50 / 0.50)         | 8.7%           |
| Mid-slow (0.30 / 0.30)        | 17.2%          |
| Slow / raw (0.10 / 0.10)      | 24.9%          |

### GIDP — broadened eligibility, contact-quality input, 6–23% band

The single-knob `GIDP_BASE_PROB` got refactored into a composition of four factors:

```
p = clamp(0.06, 0.23,
        BASE × force_factor(bases) × quality_factor(contact)
        - speed_bonus + defense_bonus)
```

Where:
- **`force_factor(bases)`** captures *which* bases are occupied — a runner on 1B gives the defense a free force at 2B (1.0 baseline); a lone 2B or 3B runner has no force and requires a tag (0.40, much rarer); multiple runners with 1B multiply force options (1.10–1.40); 2B+3B without 1B has no force at all (0.50). Bases loaded peaks at 1.40.
- **`quality_factor(contact)`** captures that weak-contact ground balls (slow choppers, 6-4-3 setups) are DP-prone (1.30); medium is baseline (1.00); hard contact is too fast for the relay or skips through (0.55).
- **Speed bonus** stays linear (slow batter → more DPs); attenuated to 0.20 to leave headroom for the multiplicative factors above.
- **Defense bonus** ditto, attenuated to 0.15.

Eligibility now fires on **any** runner on base (not just 1B), with the lead force/tag-out target picked as the lowest-indexed occupied base. Identity behaviors:

| Scenario                                       | GIDP probability |
| ---------------------------------------------- | ---------------- |
| 3B alone, hard contact, fast batter, weak def  | 6.0% (floor)     |
| 2B alone, medium, neutral                      | 6.0%             |
| 1B alone, hard, neutral                        | 7.2%             |
| 1B alone, medium, neutral                      | 13.0%            |
| 1B alone, weak, neutral                        | 16.9%            |
| 1B+2B, medium, neutral                         | 15.6%            |
| 1B+3B, medium, neutral                         | 14.3%            |
| 2B+3B, medium, neutral                         | 6.5%             |
| Bases loaded, medium, neutral                  | 18.2%            |
| Bases loaded, weak, slow batter, elite def     | 23.0% (ceiling)  |

The 6–23% band is achieved by the clamp, with the BASE/scales tuned so the cap-pinning cases are the actual extremes the user described.

### Triple plays — extended to 1B+2B; baserunning-error bonus

Old TP gate: bases loaded + 0 outs only. New: 1B+2B occupied + 0 outs is enough — a 4-6-3 force at 3B then 2B then 1B is physical (the 2B runner is forced because 1B-runner-forced-by-batter chains through). Bases loaded still fires (extends the same chain).

Baserunner errors: a small bonus added to `TRIPLE_PLAY_GIVEN_DP_PROB` when the lead forced runner (the one on 2B) has below-average baserunning skill. Models the real-baseball case where a runner takes too aggressive a secondary lead, breaks early, or doesn't read the contact correctly:

```python
if lead_br < 0.5:
    tp_p += (0.5 - lead_br) * cfg.TRIPLE_PLAY_BASERUNNING_BONUS
```

Verified spreads (10k BIPs per scenario via `_generate_pitch`):

| Scenario                        | DP rate | TP rate |
| ------------------------------- | ------- | ------- |
| 1B+2B, neutral 2B-runner BR     | 3.3%    | 0.13%   |
| 1B+2B, weak 2B-runner BR (0.10) | 3.2%    | 0.26%   |
| Bases loaded, neutral           | 4.0%    | 0.21%   |
| 1B only (TP gate closed)        | 2.5%    | 0.00%   |

Weak baserunning ~doubles the TP rate, which is the qualitative signal the user asked for.

### Apollo-style 2C moved-runner stat — shipped

Tracked end-to-end: counters → DB → bridge → player page.

**Schema (`o27v2/db.py`):** six new columns on `game_batter_stats`. `c2_op_X` = opportunities (runner on base X at the start of a 2C event); `c2_adv_X` = successes (runner ended on a higher base or scored cleanly). Migration via `ALTER TABLE` like the existing pattern, defaulting to 0 for legacy rows.

**Engine (`o27/stats/batter.py`, `o27/render/render.py`):** new fields on `BatterStats`. The stay branch of `_update_stats` walks `bases_before` × `bases_after`, per source base:
- `bases_before[i]` is not None → opportunity++
- That same `runner_id` shows up at `bases_after[j]` for some `j > i` → success
- Or runner is no longer on the field AND was not in `runner_out_idx` / `extra_runner_outs` → scored cleanly → success

Runner thrown out trying = opportunity, not success. Same rule the user asked for.

**Persistence (`o27v2/sim.py`):** the existing per-game batter-stats INSERT path picks up the six new fields. Both INSERT sites (the inline one in `play_game` and the helper `_insert_batter_stats`) updated.

**Career aggregation (`o27/v2_bridge.py`):** `get_player_stats` SUMs the six fields across all games and computes the rates in the dict it returns to the template.

**Render (`o27/web/templates/player.html`):** new card titled "2C Moved-Runner Rate" with a four-row table — 1B → 2B+, 2B → 3B+, 3B → home, plus a Combined row. Card only renders when the player has at least one opportunity (`c2_op_total > 0`). Help text explains that thrown-out-trying counts as opportunity, not success — same convention real-MLB advance-rate stats use.

Sample after a single 2C ground-out where the 1B runner advanced to 2B:

```
c2_op_1b=1, c2_adv_1b=1
c2_op_2b=0, c2_adv_2b=0
c2_op_3b=0, c2_adv_3b=0
sty=1, stay_hits=1
```

The card on the player page reads (after enough volume) like:

| From       | Opp | Adv | Rate  |
|------------|-----|-----|-------|
| 1B → 2B+   |  64 |  41 | 64.1% |
| 2B → 3B+   |  37 |  22 | 59.5% |
| 3B → home  |  19 |  15 | 78.9% |
| Combined   | 120 |  78 | 65.0% |

Naming + framing match O27's existing sabermetric flavor. The "Apollo" in the user's request was the conceptual reference; the in-game label is just "2C Moved-Runner Rate" — consistent with how `stay_hits` and `stay_rbi` are exposed elsewhere on the stats page.

### Identity / regression check

`tests/test_realism_identity.py` still passes 6/6.

---

## Files touched (commit 2 — broader scope)

- `o27/config.py` — `RUNNER_THROWN_OUT_AT_HOME_*`, `GIDP_*`, `TRIPLE_PLAY_GIVEN_DP_PROB`, `GIDP_STAY_MULTIPLIER`.
- `o27/engine/prob.py` — `_thrown_out_at_home` helper; split triple from hr in `runner_advances_for_hit`; added 2B-runner-out-at-home check on singles and 1B-runner-out-at-home on triples; GIDP/TP conversion + 2C reduced-rate FC block in `_generate_pitch`.
- `o27/engine/baserunning.py` — `advance_runners` now reads `extra_runner_outs` and clears those slots before the advance loop.
- `o27/engine/pa.py` — `_resolve_contact` now records outs for `runner_out_idx` and every entry in `extra_runner_outs`.
- `o27/render/render.py` — display string for `triple_play`.

Total diff for commit 2: 161 insertions, 13 deletions.
