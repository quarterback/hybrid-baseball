# After-Action Report — native-stat audit + the runner-advancement correction

**Date:** 2026-06-06
**Branch:** `claude/war-oaa-reconciliation-koalas-7t0pG`
**Scope:** audit of the analytics/stats layer for imported MLB constants, two
doc/comment corrections, and — on the owner's call — a coordinated wOBAScale
recalibration (MLB 1.20 → O27-native 0.86) in wRC+/VORP, with runs-per-win
empirically validated and replacement level kept. Spans two commits (audit +
corrections first, then the wOBAScale switch).
**Context:** follow-up to the BSR native-ification
(`docs/aar-bsr-o27-native-and-into-war.md`). The brief was "do the native stat
audit and fix speed-based runner advancement."

---

## TL;DR

Two headline results, both of which narrow the work rather than expand it:

1. **Speed-based runner advancement is already implemented and live.** My earlier
   claim that it was an unbuilt "Phase-2 gap" was **wrong** — I'd read the
   `advance_runners` *consumer* docstring and never traced upstream to the
   speed-aware producer. There is nothing to build. (Docstring + BSR AAR
   corrected.)

2. **The rest of the stat suite is already overwhelmingly O27-native.** The
   remaining MLB constants are either *by design* (Crossover/XO) or *calibration
   choices that aren't demonstrably wrong* (FIP coefficients, positional DRS
   ranges). **None** is *perverse* the way BSR was. The one with real leverage —
   the **wOBA scale** — was, on the owner's call, switched to the O27-native
   value (0.86) as a **coordinated recalibration** (§3b); runs-per-win was
   empirically validated as already-correct and replacement level was kept. The
   rest are documented with measured tradeoffs, not blind-fixed.

A process note up front: during the audit I briefly mis-concluded that wRC+/WAR
were badly broken (wRC+ centering at −10). That was a **bug in my throwaway test
harness** (it aliased SQL columns as `hits`/`doubles` where the aggregator reads
`h`/`d2`). Re-run with the real column names, **wRC+ centers at 98 and WAR tops
out at a reasonable 6.2** — production is fine. Flagging it because it nearly
became a false "bug report."

---

## Finding 1 — runner advancement IS speed-aware (correction)

- **Producer:** `o27/engine/prob.py:runner_advances_for_hit()` (lines ~1507-1691),
  the live generator called from the contact-resolution path
  (`prob.py:2103`, `:2873`). For every runner on every hit type it resolves the
  advance via `_resolve_table(..., _spd_dev(idx), arm_dev, ...)` — i.e. the
  runner's speed deviation and the fielders' arm deviation shift the
  score/advance/out probabilities. `_runner_advance()` (lines ~1022-1067) adds a
  speed/baserunning-driven extra-base attempt with a TOOTBLAN (thrown-out) check.
- **Config knobs** already exist: `cfg.SPEED_ADVANCE_MOD` (0.12), `ARM_ADVANCE_MOD`
  (0.11), `RUNNER_EXTRA_SPEED_SCALE` (0.35), `TOOTBLAN_*`.
- **Speed reaches the engine correctly:** the DB stores ratings on a 20-80 scale,
  but `o27v2/sim.py:432` normalises via `_scout.to_unit()` to 0-1 before the
  engine sees them, so `(speed - 0.5)` is correct — no scale-mismatch bug.
- **Not overridden:** `apply_park_effects` is *not* called in `prob.py`/`pa.py`;
  the 14 fixed `runner_advances` tuples in `park_effects.py` are not in the live
  probabilistic path, so they don't shadow the speed-aware result.

**Why the aggregate signal looked flat.** Bases-gained-per-opportunity correlates
only ~+0.05 with the speed rating. That is **dilution, not absence**: most
"advancement opportunities" resolve by force/context (a single forces the runner
on 1B to 2B regardless of speed); the genuinely discretionary plays (first-to-
third, scoring from second) are a minority, and the per-decision speed effect is
modest by design. And per the BSR work, an extra base is worth ~0 runs in O27, so
even a stronger effect wouldn't move WAR/BSR.

**Recommendation:** no fix needed. *Optional* flavour tuning — raise
`SPEED_ADVANCE_MOD` / `RUNNER_EXTRA_SPEED_SCALE` so speed visibly matters more on
the bases — is available, but it has ~0 WAR/BSR impact and risks disturbing the
existing calibration/tests. Left to the owner's call.

---

## Finding 2 — what's already O27-native (do not touch)

Derived per-render from live data, the same philosophy as the BSR fix:

| Metric | Where | How it's native |
| --- | --- | --- |
| wOBA linear weights | `linear_weights.py:derive_linear_weights` | RE-fitted, OBP-scaled so league wOBA = league OBP |
| FIP / DIPS constant | `app.py:_league_fip_constant` / `_league_dips_constant` | fitted so league FIP/DIPS = league ERA |
| Runs-per-win | `app.py:_league_baselines_compute` | Pythagorean, scales with run env |
| Run expectancy (RE24) | `expanded.py:_re_lookup`, `run_expectancy.py` | tail-sum over `game_pa_log` |
| Baserunning run values (BSR) | `expanded.py:_baserunning_run_values` | RE-derived (this branch) |
| Pythagorean exponent | `pythag.py:refit_pythag_exponent` | grid + ternary fit |
| BaseRuns coefficients | `base_runs.py:_refit_coeffs` | coordinate-descent refit |
| Game Score coeffs | `linear_weights.py` | tuned so league-mean GSc = 50 |
| Fielding run value | `expanded.py:build_fielding_value` | OAA × reliability, `_RUN_PER_OUT` |
| WPA / Leverage | `wpa.py`, `expanded.py:build_win_probability` | empirical win-prob table |

This is the important context: O27 had *already* done the hard native-ification
work everywhere except baserunning (now fixed). The audit's job was mostly to
confirm there were no more BSR-shaped landmines — and there aren't.

---

## Finding 3 — remaining MLB constants, each a judgement call (NOT blind-fixed)

### 3a. Crossover (XO) anchors — **by design, leave**
`crossover.py:MLB_ANCHOR_MEAN/SD` are MLB 2018-23 composites. This is **not** a
bug: XO's entire purpose is a *reading layer* that maps an O27 player's z-score
within the O27 distribution onto an MLB-readable mean±sd (`xo = MLB_mean + z_O27
· MLB_sd`). The O27 side (mean, sd) is already data-derived; the MLB anchors are
the intended target scale (there's even a "LOCKED DECISION" comment). Replacing
them would defeat the metric. **No change.**

### 3b. wOBA scale `1.20` → native **0.86** — **SWITCHED (coordinated recalibration)**
`app.py` hardcoded wOBAScale = 1.20 (FanGraphs convention) for wRC+ and VORP. The
O27-native value is the OBP-scale factor `league_obp / raw-wRAA-per-PA` that
`derive_linear_weights` already fits; on the recon DB it's **0.8589**, not 1.20
(O27's league wOBA is 0.489 vs MLB's ~0.32 — a different run environment, where
dividing by 1.20 mis-converts wOBA points to runs). Now exposed as
`derive_linear_weights()["woba_scale"]` and used in both wRC+ and VORP.

The owner chose to go native **with coordinated re-checks of runs-per-win and
replacement level** (the worry being that 1.20→0.86 multiplies VORP ~1.40× and so
inflates WAR). The coordination came back cleaner than expected:

- **Runs-per-win is already correct.** Fitting RPW empirically from actual team
  W–L vs run differential (run_diff / wins-above-.500, through origin) gives
  **19.89**, essentially identical to the derived **20.05**. No change.
- **The "inflation" is actually a correction.** By the proper WAR reconciliation
  (Σ net position WAR ≈ league wins-above-replacement ≈ **94**), the old 1.20
  netted only **49** — it was *under*-crediting hitters. Native nets **72**,
  *closer* to target. So native is more correct, not over-hot.
- **High top-end is structurally plausible.** Team wins span **17–52 of 66
  games** — O27 has far larger talent gaps than MLB, so a stacked team carries
  ~32 WAR of talent and an ~8.7-WAR star on it is believable.
- **Replacement stays at 0.85×league.** Net (72) lands slightly under the fuzzy
  ~94 target; lowering replacement to close the gap would only push tops higher,
  so 0.85 is the conservative keep.

**Net effect:** wRC+ centering is unchanged (the scale only affects *spread*,
not the center — regulars 98→97); WAR widens (top 6.2→8.7) toward better
reconciliation. wRC+/VORP now share one O27-native scale.

### 3c. League-baseline wOBA weights (`app.py:2121`, MLB) — **follow-up**
The league-baseline wOBA (the wRC+ centering point) still uses MLB weights
(0.5136) while players use native weights (PA-weighted ~0.505, ≈ league OBP
0.489). That ~5% gap is why wRC+ now centers at ~91 full-pop / 97 for regulars
instead of exactly 100. Fixing it (baseline wOBA via native weights, i.e. ≡
league OBP by construction) would re-center wRC+ — but it also lowers
`replacement_woba = 0.85×league_woba`, which *further* raises WAR (compounding
with 3b). Left as a separate, measured decision rather than bundled into this
recalibration. Low urgency: regulars already read a sane ~97.

### 3d. FIP/DIPS coefficients `13 / 3 / −2` (`app.py:1931-1933, 1964-1966`)
Standard Tango weights. Because the FIP **constant** is refit so league FIP =
league ERA, coefficient miscalibration is largely absorbed into the constant
(league mean stays right); only the *spread* between pitchers could shift. A
proper refit (à la BaseRuns) is a contained future project, not demonstrably
broken today. **Documented, not fixed.**

### 3e. Positional DRS ranges, wERA arc weights, replacement levels (0.85/1.20)
MLB-flavoured calibration constants (`app.py:678-691`, `:1858`, `:2126`, `:2138`).
Each could be empirically fit from O27 distributions; none is demonstrably wrong.
Lower priority; listed for completeness.

---

## Changes in this commit

- `o27/engine/baserunning.py` — module docstring corrected: the stale
  "Phase 2: will add probabilistic runner advancement" note now points to the
  real speed-aware producer in `prob.py`. (This stale note is what misled the
  earlier BSR AAR.)
- `docs/aar-bsr-o27-native-and-into-war.md` — added a correction box and fixed
  the follow-up that called speed-based advancement an unbuilt gap.
- `o27v2/analytics/linear_weights.py` — `derive_linear_weights()` now returns
  `woba_scale` (the native OBP-scale factor, ≈0.86).
- `o27v2/web/app.py` — wRC+ and bVORP now use the native `woba_scale` instead of
  the hardcoded MLB 1.20 (§3b). This is the one metric-output change: WAR widens
  toward better reconciliation (top 6.2→8.7), wRC+ centering unchanged.

The wOBAScale switch lands in a follow-up commit (the audit + corrections came
first); rpw and replacement were re-checked and left as-is (§3b).

## Validation

- `tests/test_stat_invariants.py` and `o27/tests o27v2/tests` — green.
- Re-verified with correct column aliases: PA-weighted player wOBA 0.5054 ≈
  league OBP 0.4882; wRC+ centers at 98; WAR top-5 [6.2, 4.7, 4.5, 4.4, 4.3].
- `runner_advances_for_hit` confirmed as the live producer (call sites
  `prob.py:2103`, `:2873`); `_resolve_table` receives a real per-runner
  `speed_dev` at every single/double/triple call site.

## Honest notes

- I was **wrong** earlier that runner advancement ignored speed. Root cause:
  trusting a consumer-side docstring. Lesson re-applied: trace the producer.
- I nearly shipped a false "wRC+ is broken" alarm off a harness-only bug; caught
  it by sanity-checking against the real route's SQL. Absolute-value surprises
  get re-derived through the real code path before they become claims.
- The net: the suite needed far less "fixing" than the brief assumed, which is a
  good sign about the prior native-ification work. The high-leverage remaining
  item (wOBA scale → WAR magnitude) is surfaced with numbers and left as an
  explicit owner decision.
