# After-Action Report — O27-native baserunning value (BSR) + into WAR

**Date:** 2026-06-06
**Branch:** `claude/war-oaa-reconciliation-koalas-7t0pG`
**Scope:** `o27v2/analytics/expanded.py` (BSR run values), `o27v2/web/app.py`
(WAR baserunning term + baselines), `tests/test_stat_invariants.py`.
**Predecessor:** `docs/aar-war-oaa-fix-and-fielding-regression.md` (the fielding
half of the same "surfaced ⇒ summed" pass).

---

## TL;DR

Baserunning (BSR) was the last WAR-eligible component still **surfaced but not
summed**, and it was computed with **imported MLB linear weights**
(`+0.25`/extra base, `+0.20`/SB, `−0.42`/CS). Those constants mis-state O27 so
badly that raw BSR was *noise-dominated and mildly perverse* — it correlated
−0.01 with runner speed and penalised fast runners. The fix was **not** to patch
the engine's running game, but to make BSR **O27-native**: derive its run values
from O27's own run-expectancy surface, the same way wOBA weights, the FIP
constant and `_RUN_PER_OUT` already are. That single change flips BSR from noise
to real signal, after which it goes into WAR cleanly.

## Why MLB constants broke BSR in O27

Verified on a fresh 396-game sim DB. BSR decomposes into two parts:

| Component | sd (MLB consts) | corr(speed) | Why |
| --- | ---: | ---: | --- |
| Extra bases taken (XBT) | 3.43 runs | +0.05 | dominant, but near-zero signal once MLB-overvalued — see correction below |
| Steals (SB/CS) | 1.11 runs | −0.18 | **perverse** — league steal success is 51.9%, below MLB's 68% break-even, so running loses runs; speed gates attempts, so fast runners lose more |

So the *bigger* half of BSR was inflated by MLB's `+0.25`/base, and the smaller
half was anti-speed.

> **Correction (2026-06-06, follow-up audit).** An earlier draft of this AAR
> claimed the XBT signal was ~0 because "runner advancement is set by hit type,
> not runner speed — a Phase-2 TODO never built." **That is wrong.** Runner
> advancement *is* speed-aware: `prob.py:runner_advances_for_hit()` (the live
> producer, called from the contact-resolution path) resolves every runner's
> advance count through `_resolve_table()` with a per-runner `speed_dev` and
> `_runner_advance()`'s extra-base/TOOTBLAN model. The misleading claim came from
> reading the `advance_runners` *consumer* docstring in `baserunning.py` (since
> corrected) without tracing upstream. The XBT term's weak aggregate
> speed-correlation is real but is **dilution**, not absence of the feature:
> discretionary extra-bases are a small slice of total advancement (forced/
> contextual moves dominate), and — per the native run values below — an extra
> base is worth ~0 in O27 anyway, so XBT contributes ~0 to BSR regardless. The
> native re-derivation's conclusion (XBT term self-zeroes; BSR becomes
> steals-driven with positive speed signal) is unaffected by this correction.

## The O27-native run values

`_baserunning_run_values()` builds a fine (per-exact-out) RE surface by a
backwards tail-sum per half, then takes the frequency-weighted RE delta for each
event over every base/out state the league reached:

| run value | O27-native | MLB constant |
| --- | ---: | ---: |
| per extra base | **+0.001** | +0.25 |
| per stolen base | **+0.325** | +0.20 |
| per caught stealing | **−0.408** | −0.42 |

The headline: **an extra base is worth ~0 in O27.** In a 27-out, ~27 R/G half a
runner usually scores regardless of whether he's on 2B or 3B, so advancing barely
moves run expectancy. MLB's `+0.25` overvalued it ~250×. Valuing it natively at
~0 **zeroes the noisy XBT term at the source** — no regression needed (contrast
the fielding fix, which needed shrinkage because OAA was irreducibly noisy).
Native break-even SB success is **55.7%** (not MLB's 68%); at 51.9% actual,
steals are only *mildly* net-negative — a believable O27 truth, not a catastrophe.

## Result

| BSR | sd | range | corr(speed) |
| --- | ---: | --- | ---: |
| MLB constants | 3.33 | −10.4 … +9.8 | −0.01 |
| **O27-native** | **1.09** | **−4.8 … +4.3** | **+0.16** |

The sign **flips from perverse to correct**: native BSR rises with runner speed.
The scale tightens to a believable ±4.5 runs (≈ ±0.22 WAR at this DB's rpw 20).

## Into WAR

- `_league_baselines_compute` publishes a `bsr_runs` per-player map (same
  `build_baserunning_value` the surface shows).
- The WAR block adds `bwar_base = bsr_runs / rpw`; `WAR = war_off + dwar +
  bwar_base`. BSR enters **un-regressed** — it's already a centered, small,
  correctly-signed run value (this is how real WAR treats BsR), and the native
  re-derivation already removed the noise at its source. Low-opportunity runners
  net ~0 by construction.
- **Invariant 11** extended: `bsr_runs` must equal the displayed BSR for the same
  player, and the component identity is now `WAR == war_off + dwar + bwar_base`.

With this, the "surfaced ⇒ summed" principle holds for **both** fielding and
baserunning: every defensive/baserunning number shown in the UI is the exact
number flowing into WAR.

## Validation

- Native values in use: (xb +0.001, sb +0.325, cs −0.408).
- WAR reconciles: `bsr_runs` == displayed BSR (e.g. 4.30 == 4.30); component
  identity holds to 1e-6.
- `tests/test_stat_invariants.py` → **12 passed**; `o27/tests o27v2/tests` →
  full suite green. Pages `/player/<id>/o27i`, `/o27i/advanced`, `/leaders`,
  `/players` render 200.
- Fresh `initdb 12teams` + 396-game sim, `O27V2_DB_PATH=/tmp/recon.db`.

## What I did NOT do / follow-ups

- **Did not touch the engine's running game.** Considered ("fix the 51.9% steal
  success rate"), but the right immediate fix was the metric, not the sim. The
  sub-break-even running game is a real O27 property the native metric now
  reports honestly rather than papers over.
- **Speed-based runner advancement already exists** (see correction above) — it
  is *not* a gap. Its aggregate effect is modest because discretionary
  extra-bases are diluted by forced advances and, in O27, an extra base is worth
  ~0 runs. If that effect is ever strengthened (tuning `SPEED_ADVANCE_MOD` /
  `RUNNER_EXTRA_SPEED_SCALE`), the native extra-base run value should be
  re-checked — but it will likely stay near 0 for the run-environment reason.
- **Run values are derived per render** from live data (like the linear weights).
  They will drift with the run environment; that's intended. `_BSR_MLB_FALLBACK`
  covers empty/no-data DBs.
- **BSR enters WAR un-regressed** — defensible given its small, centered,
  positive-signal shape, but the same reliability pattern used for fielding is
  available if more damping is ever wanted.
- This is the broader payoff of the owner's call that BSR was "a bad metric for
  O27": baserunning is now O27-native like the rest of the stat suite. Other
  early metrics still on imported constants are worth the same audit.
