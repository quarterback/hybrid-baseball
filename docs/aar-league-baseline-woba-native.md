# After-Action Report — league-baseline wOBA goes native (wRC+ re-centres)

**Date:** 2026-06-06
**Branch:** `claude/war-oaa-reconciliation-koalas-7t0pG`
**Scope:** `o27v2/web/app.py` — `_league_baselines_compute()`.
**Context:** the follow-up flagged in
`docs/aar-native-stat-audit-and-advancement-correction.md` §3c, after the
native-wOBAScale recalibration. Closes the wOBA pipeline's last imported-constant
seam.

---

## TL;DR

The wRC+/VORP centering point — league wOBA — was still computed with **hardcoded
MLB linear weights** (`0.72·BB + 0.74·HBP + 0.95·1B + 1.30·2B + 1.70·3B +
2.05·HR`) while every individual player's wOBA used the O27-native, RE-fitted
weights from `derive_linear_weights()`. The two weight systems gave league means
~5% apart (0.5136 MLB vs ~0.489 native), so wRC+ centred at **~91** (full pop)
instead of 100. This commit makes the baseline use the **same native weights the
players use**, so the league baseline equals the PA-weighted mean of player wOBAs
by construction and wRC+ centres at **~100**.

## The change

`_league_baselines_compute()`:
- Added `stay_hits` to the league batting-totals query so the baseline can mirror
  the player aggregator's 2C/stay split exactly.
- Replaced the hardcoded MLB `woba_num` with the native weights from
  `_linear_weights()["woba_weights"]`, including the stay-hit split (2C hits
  credited at the lower `STAY` weight, not the `1B` weight) — identical to the
  per-player formula in `_aggregate_batter_rows`. Falls back to the MLB weights
  only when no fitted weights exist (empty DB).

Because `replacement_woba = 0.85 × league_woba` and the corrected league wOBA is
lower (0.4893 vs 0.5136), replacement also drops, which lifts VORP/WAR slightly.

## Validation (recon DB: 12 teams, 398 games)

| | before (MLB baseline) | after (native baseline) |
| --- | ---: | ---: |
| league baseline wOBA | 0.5136 | **0.4893** (≈ league OBP 0.4882) |
| replacement wOBA | 0.4366 | 0.4159 |
| wRC+, full pop (PA-wtd) | 91 | **101** |
| wRC+, regulars ≥50 PA | 97 | 107 |
| WAR top-5 | 8.7 … 6.0 | 9.0 … 6.2 |
| Σ net position WAR (target ≈ 94) | 72 | **105** |

So this **improves both** anchors it touches: wRC+ centring (91→101, i.e. ~100 for
the full population, with regulars correctly reading above) and the WAR
reconciliation (Σ net 72→105 — now 11 off the ~94 target instead of 22 under).
Regulars sitting at 107 is expected: the regular population genuinely out-hits the
high-churn replacement tail.

- `tests/test_stat_invariants.py` → 12 passed (WAR == war_off + dwar + bwar_base
  still holds; `bsr_runs`/`def_runs` reconciliations intact).
- `o27/tests o27v2/tests` → full suite green.
- `/leaders`, `/players`, `/o27i/advanced` render 200.

## Provisional / what's next

- **The stay-hit split here is provisional.** It mirrors the *current* player
  formula, which treats every 2C hit as a discounted single (`true_singles =
  singles − stay_hits`, stays at the `STAY` weight). That assumption is itself
  slated to change: 2C hits are being redesigned to resolve through the real
  hitting engine and can come out doubles/triples/HRs (and outs). When that
  lands, `stay_hits` will no longer be a subset of singles, and **both** the
  per-player wOBA and this baseline will need to credit 2C hits by their real
  type. The two formulas are intentionally kept identical so that future change
  is a single coordinated edit in both places.
- The native-wOBAScale recalibration (prior commit) plus this re-centring leave
  the WAR ladder slightly hot at the very top (9.0 over 66 games); the reconcili-
  ation Σ (105 vs ~94) suggests replacement could be nudged up a hair later, but
  it's within the fuzziness of the target and not worth tuning ahead of the 2C
  mechanic change that will move the run environment anyway.
