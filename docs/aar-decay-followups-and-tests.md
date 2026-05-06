# After-Action Report — Decay follow-ups + power-redistribute property tests

**Date completed:** 2026-05-05
**Branch:** `claude/fix-dark-theme-baseball-terms-7UhIv`
**Predecessor:** `aar-stamina-power-and-latek.md`

---

## What was asked for

The prior AAR parked three follow-up tickets:

> 1. Lineup-cycling drift offset for Decay. League-wide arc-3 K% will
>    always be higher than arc-1 K% because the 27-out single inning
>    cycles to weaker hitters. Use the league's natural arc-1→arc-3 K%
>    delta as the zero-point.
> 2. Selection bias on Q1 Decay. Low-stamina starters who blow up in
>    arc-2 get pulled before arc-3 — so the cross-arc sample for Q1 is
>    from their best outings, biasing Decay toward zero. A per-appearance
>    Decay metric would surface this.
> 3. HR-rate stability check. The redistribute is mathematically
>    sum-preserving, but the league HR rate post-change is 2.06% — would
>    need to compare to the exact same seed-set under the old additive
>    model to confirm zero drift.

User: "fix the follow ups". All three shipped.

---

## What was built

### 1. Drift-corrected Decay

`o27v2/web/app.py`. Added three new fields to the league baselines
emitted by `_league_baselines()`:

```
baselines["league_arc1_k_pct"]   = league SUM(k_arc1+fo_arc1) / SUM(bf_arc1)
baselines["league_arc3_k_pct"]   = league SUM(k_arc3+fo_arc3) / SUM(bf_arc3)
baselines["league_decay_drift"]  = (la1 - la3) × 100   # in points
```

In the qualifying live league this measures **-4.22 pp** — every
pitcher's "raw" Decay in O27 is structurally biased downward by ~4
points because the 27-out single inning cycles the lineup to weaker
hitters in arc-3, naturally lifting K%.

`_aggregate_pitcher_rows` now:
- exposes `decay_raw` (the original arc-1 - arc-3 delta, kept as
  diagnostic)
- writes drift-corrected `decay = decay_raw - league_decay_drift` in the
  same units (percentage points)

**New interpretation:**

| Decay | Reads as |
|---|---|
| 0 | matches league norm (lineup-cycling response only) |
| > 0 | fades MORE than the lineup naturally lifts K% — true fatigue signal |
| < 0 | holds K% better than league norm — durable arm |

Tooltips updated in `leaders.html`, `player.html`, `stats_browse.html`.

### 2. Per-appearance Decay + arc-3 reach rate

`o27v2/web/app.py:301-378`. Two new helpers:

- **`_pitcher_per_game_decay_map()`** — one cheap GROUP-BY query against
  `game_pitcher_stats`, returns
  `{player_id: {decay_pg, arc3_reach_rate, decay_pg_known, g_total,
  g_arc3_reach}}`. The per-game Decay is the mean of per-appearance
  raw arc-1 - arc-3 deltas across appearances where both arcs had
  sample. It equal-weights games (vs season Decay which weights by BFs).
- **`_stamp_per_game_decay(rows, drift)`** — stamps these onto every
  pitcher row produced by `_aggregate_pitcher_rows`, with the same drift
  correction applied to `decay_pg` so it sits in the same "0 = league
  norm" frame.

Wired into the aggregator so every multi-row and single-row pitcher
view picks it up automatically (one query per render — cheap, ~18k row
GROUP BY).

**arc3_reach_rate** is the actual survivor-bias signal. A pitcher with
clean Decay = -3 but `arc3_reach_rate = 30%` has his Decay computed
from the 30% of his appearances where he survived to arc-3. The bad
70% (where he got pulled before arc-3) are invisible to Decay. Now
they're visible to A3%.

Surfaced as new columns on `/stats?side=pit&view=all`:
- `Decay/G` — per-appearance mean (drift-corrected)
- `A3%` — arc-3 reach rate

### 3. Property + Monte-Carlo tests for power redistribute

`o27/tests/test_power_redistribute.py`. Seven tests, all passing:

| Test | What it asserts |
|---|---|
| `test_sum_preservation_hard` | `_redistribute(HARD, …)` invariant in total weight across power_dev ∈ {-1, -0.7, -0.4, -0.15, -0.01, 0, 0.01, 0.15, 0.4, 0.7, 1} |
| `test_sum_preservation_medium` | same, MEDIUM table |
| `test_sum_preservation_weak` | same, WEAK table |
| `test_identity_at_zero_power_dev` | `_redistribute(table, edges, 0.0) == table` for all three quality tiers |
| `test_directionality` | At power_dev=+1: HR↑ line_out↓ double↑ single↓ ; at -1: reversed |
| `test_montecarlo_hr_rate_stability` | Average HR/2B/3B rates over 20k league-grade samples within 1.5pp of unmodified table baseline |
| `test_montecarlo_per_player_spread` | Elite-power (grade 90) > weak-power (grade 25) XBH rate by ≥5pp on HARD contact |

The directionality test caught a real coefficient bug: at the original
`POWER_REDIST_HARD_D2T = 0.20`, the flow `single→double` (gain) and
`double→triple` (loss) cancelled exactly on the doubles row at
power_dev=+1, leaving doubles unchanged with high power. **D2T dropped
to 0.10** so doubles rise net-positive on positive power. League 2B
rate held at 6.52% vs 6.43% prior — 0.09pp drift, well under tolerance.

Run from repo root:

```
python o27/tests/test_power_redistribute.py        # standalone
python -m pytest o27/tests/test_power_redistribute.py -v   # via pytest
```

---

## Verification (post-resim with all changes)

### Drift quantified

```
league_arc1_k_pct  = 22.09%
league_arc3_k_pct  = 26.31%
league_decay_drift = -4.22 pp
```

The 27-out single inning structurally lifts K% by 4.22pp from arc-1 to
arc-3 across all pitchers. That's the "free Decay" every pitcher gets
just from the lineup-cycle. Subtracting it isolates the fatigue signal.

### Decay quartile breakdown (drift-corrected)

| Q | Stamina | n | Raw Decay | **Corrected Decay** | Decay/G | arc3 reach |
|---|---|---|---|---|---|---|
| Q1 | 20-41 | 38 | -0.66 | **+3.56** | +57.55 | **86.5%** |
| Q2 | 41-57 | 70 | -3.77 | **+0.45** | +12.04 | 56.5% |
| Q3 | 57-63 | 84 | +1.76 | **+5.98** | +11.33 | 36.5% |
| Q4 | 63-94 | 86 | -1.40 | **+2.82** | +12.41 | 32.9% |

The corrected Decay column is what the user wanted: 0 = league norm.

The arc-3 reach rate column is the unexpected win. Q1 (low-stamina
arms) post **86.5% reach rate** — they're mostly used as relievers
who pitch the back of the arc, so reaching arc-3 is the norm for
their role. Q4 (workhorses) post only **32.9%** because they're
starters who often get pulled mid-arc when the pitch count or
performance demands it. Stamina rating doesn't translate to
"completes the arc"; it translates to "can carry a starter workload
without falling apart" — different signal.

### HR-rate stability check

| Metric | Pre-change baseline | Post-change | Drift |
|---|---|---|---|
| HR/PA | 2.057% | **2.052%** | -0.005pp |
| 2B/PA | 6.434% | **6.523%** | +0.089pp |
| 3B/PA | 1.244% | **1.218%** | -0.026pp |
| H/PA  | (n/a)   | 28.247%    | — |

All deltas under the 1.5pp tolerance asserted by the property test.
The 2B/PA rise (+0.09pp) is from the D2T coefficient fix — singles
now flow net-positive into doubles on positive power, exactly as
designed. HR rate held within rounding.

### Smoke test

13/13 routes return 200, including the new column layout on
`/stats?side=pit&view=all` showing wERA / xRA / Decay / Decay/G / A3%
/ LateK% as a six-column durability cluster.

---

## Files touched

| File | Change |
|---|---|
| `o27v2/web/app.py` | `league_arc1/3_k_pct` + `league_decay_drift` in baselines; `decay_raw` field; `_pitcher_per_game_decay_map()` + `_stamp_per_game_decay()` helpers; aggregator stamps both |
| `o27/config.py` | `POWER_REDIST_HARD_D2T` 0.20 → 0.10 (caught by directionality test) |
| `o27/tests/test_power_redistribute.py` | New — seven property + Monte-Carlo tests |
| `o27v2/web/templates/stats_browse.html` | Decay/G + A3% columns on the All pitching view |
| `o27v2/web/templates/leaders.html` | Decay tooltip rewritten for drift-corrected interpretation |
| `o27v2/web/templates/player.html` | Decay tooltip rewritten |

---

## Known characteristic to flag (not a bug)

Decay/G runs noisier than season Decay — by a lot. Q1 Decay/G mean is
+57.55 (vs season Decay +3.56). The variance comes from per-game
small-sample arcs: a relief outing facing 2 batters in arc-1 with one
K (50%) and 1 batter in arc-3 with no K (0%) contributes a per-game
decay of +50pp that gets averaged in equally with a 30-BF starter's
+2pp. Equal-weighting games over-amplifies the small samples.

Reading recommendation:
- **Decay** (season-aggregated, drift-corrected) for "is this pitcher's
  fatigue genuinely worse than league norm"
- **Decay/G** for diagnostic comparison only, not as a primary leader-card
  metric — too noisy at small sample
- **A3%** to know how much the Decay number can be trusted

The user's framing (per-appearance Decay would surface the selection
bias) holds in spirit — A3% is the cleaner direct surfaced signal of
that bias, and Decay/G now provides the equal-weighted version for
those who want it.
