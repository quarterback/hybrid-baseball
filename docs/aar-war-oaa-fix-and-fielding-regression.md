# After-Action Report — WAR/OAA fix: position-neutral OAA + reliability-regressed Field Runs into WAR

**Date:** 2026-06-06
**Branch:** `claude/war-oaa-reconciliation-koalas-7t0pG`
**Scope:** `o27v2/analytics/expanded.py` (OAA/Field Runs), `o27v2/web/app.py`
(WAR defensive input + baselines), `o27v2/web/templates/o27i_player.html`,
`o27v2/web/templates/o27i_advanced.html`, `tests/test_stat_invariants.py`.
**Predecessors:** `docs/aar-war-oaa-reconciliation-koalas.md` (the finding),
`docs/aar-stat-surface-reconciliation-comprehensive.md` (the deep-dive). This
AAR is the **fix**.

---

## TL;DR

The season card's WAR read a rating-derived **scout DRS** while the Savant page
showed event-based **OAA/Field Runs** — two independent defensive pipelines that
never reconciled (a Memphis Koalas CF showed WAR 3.65 with a +0.1 scout-defense
term next to a −19.9 Field Runs on the other surface). This fix:

1. **De-biases OAA** — the expected-out baseline is now conditioned on the
   fielder's **zone** (`out_rate[zone, EV, LA]`, not `out_rate[EV, LA]`), and
   **home runs are excluded** from the chance set. This kills a large systematic
   positional artifact.
2. **Regresses Field Run Value** toward league-average by sample reliability,
   because verification showed raw single-season OAA is mostly batted-ball noise
   (the engine resolves contact from EV/LA physics with only a team-level range
   shift + sparse individual gems).
3. **Anchors WAR's defensive term to that regressed Field Run Value** for
   qualified fielders (≥25 chances), the *same number the fielding/Savant surface
   displays*. Scout DRS is **retained as its own column** and used only as the
   small-sample fallback.
4. **Adds invariant 11**: WAR's defensive runs must equal the displayed Field
   Runs for the same player, and WAR must equal `war_off + dwar`.

## Why — the verification that drove the design

Built a fresh 12-team, 396-game sim DB (17,469 BIP) and measured OAA league-wide.

**The positional artifact was real (before fix):**

| | 1B | 2B | 3B | SS | LF | CF | RF |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| mean OAA | +2.2 | +3.3 | +5.5 | +11.5 | −17.5 | −16.6 | −9.2 |

Infielders systematically positive, outfielders systematically negative — because
OF regulars absorb every extra-base hit in their zone (charged `−exp`) while being
measured against an EV/LA out-rate inflated by easy infield outs in the same bin.
OAA spread was stdev **31**, range −72.6…+49.9. The Koala's −25.5 was *mild* here.

**After conditioning expected-out on zone + dropping HRs:** every position centers
at ~0 (CF −2.4, SS −0.5, LF −3.4, …), stdev drops to 23. Artifact gone.

**But OAA barely tracks skill.** Correlation of de-biased OAA to the player's
actual defense rating ≈ **0.10** (≈0 OF, ~0.2–0.3 IF), and to *team* defense
≈ 0.06. Root cause in the engine: contact is resolved from **EV/LA physics**
(`o27/engine/batted_ball.py`, post-inversion corr EV→hit 0.364); defense enters
only as a **team-level** range shift (`DEFENSE_RANGE_SHIFT_SCALE = 0.15`, max
~10% single↔out flip) plus sparse individual **gems** (`GEM_BASE_* ≈ 0.05–0.075`).
There is very little *individual-fielder* signal in whether a ball drops, so raw
single-season OAA is dominated by batted-ball variance.

**Implication:** feeding raw OAA into WAR would inject ±1.8 wins of fielding
*noise* per player. So Field Run Value is regressed toward 0 by
`reliability = chances / (chances + K)`, `K = 400`:

| | raw FRV (p10/p90) | dWAR span | K=400 FRV (p10/p90) | dWAR span |
| --- | --- | --- | --- | --- |
| (rpw = 20.05) | −26.4 / +23.2 | −1.84…+1.86 | −7.6 / +3.9 | −0.75…+0.39 |

A full-season qualified fielder lands at 0.2–0.46 weight — a believable ±0.5-win
fielding contribution rather than a ±1.8-win noise term. K is a documented,
tunable constant (`_FIELDING_REGRESSION_K`).

**The Koala, reconciled:** OAA −25.5 at ~250 chances → reliability ≈ 0.39 →
regressed ≈ −7.7 Field Runs → ≈ −0.4…−0.5 dWAR. WAR moves from 3.65 to ~3.1 — a
real, modest defensive ding, neither the −1.9 catastrophe nor the +0.1 whitewash.

## What changed, precisely

- **`build_fielding_value`** (`expanded.py`): two-pass — pass 1 builds
  `out_rate[zone, EV, LA]` (with an `(EV, LA)` fallback for sparse bins) over all
  non-HR BIP; pass 2 accrues `Σ(actual − expected)` against the zone-conditional
  baseline. Attribution unchanged (exact engine fielder on outs; trajectory-zone →
  regular on hits). Output gains `reliability`; `frv` is now the **regressed**
  run value (`oaa × 0.78 × reliability`); `oaa` stays the raw observed count.
  Leaderboards sort by `frv`.
- **`_league_baselines_compute`** (`app.py`): publishes `fielding_runs` and
  `fielding_chances` maps (the same `build_fielding_value` output, league-scoped),
  so every WAR consumer sees the displayed Field Runs without touching the ~18
  `_aggregate_batter_rows` call sites.
- **WAR block** (`_aggregate_batter_rows`): scout DRS still computed and stored
  (`drs`, `pos_def`); the WAR defensive term `def_runs` is the regressed Field
  Runs for fielders with ≥`_WAR_FIELDING_MIN_CHANCES` (25) chances, else the scout
  DRS projection. `def_runs_source` records which. `dwar = def_runs / rpw`.
- **Templates:** OAA/Field Runs tooltips updated to explain position-neutrality,
  HR exclusion, the reliability regression, and that FRV is the WAR-bound figure.
- **Invariant 11:** WAR def_runs == displayed FRV (source must be `field`) for
  qualified fielders, plus `WAR == war_off + dwar`.

## Validation

- **Distribution:** positional means collapse to ~0 (above); FRV/dWAR band
  believable (above).
- **Reconciliation:** WAR `def_runs` equals displayed `frv` to <0.05 runs for
  every qualified fielder (invariant 11, and a direct end-to-end check on real
  route rows: `source=field`, scout column preserved).
- **Tests:** `o27/tests o27v2/tests` → **235 passed, 1 skipped**;
  `tests/test_stat_invariants.py` → **12 passed** (was 11). Pages
  `/player/<id>/o27i`, `/o27i/advanced`, `/leaders`, `/players`, `/player/<id>`
  all render 200.
- Verified on a fresh `initdb 12teams` + 396-game sim (`O27V2_DB_PATH=/tmp/recon.db`).

## What I did NOT do / follow-ups

- **Did not touch ratings or the sim engine.** The weak individual-fielder signal
  is an *engine* property (defense is largely team-level); making OAA a strong
  per-player signal would require per-fielder catch influence in
  `resolve_contact` — out of scope and deliberately left alone.
- **The rating→OAA sample-growth blend is a follow-on.** Today it's a hard switch
  at 25 chances (scout below, regressed Field Runs above). A continuous blend
  (rating-weighted early, OAA-weighted as chances grow) is the natural next step
  and would smooth the boundary.
- **Did not add baserunning (BSR) to WAR.** Still surfaced-but-not-summed; a
  separate mitigation. The "surfaced ⇒ summed" principle now holds for fielding;
  BSR is the remaining gap.
- **`K = 400` is calibrated, not derived.** Set from one 396-game DB to land a
  believable fielding band; exposed for tuning like the engine's `RES_*` knobs.
- **Display nuance:** sub-25-chance fielders still show an OAA/FRV on their o27i
  page while their WAR uses the scout projection; the invariant scopes to
  qualified fielders accordingly.
