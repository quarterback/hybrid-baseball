# After-Action Report — Foul-Out Leaderboards, XO Crossover Consistency, and RA/27

**Date completed:** 2026-05-20
**Branch:** `claude/add-foul-outs-stat-OXRYr`

> The persistent Walk-Back runner rewrite shipped on this same branch and has
> its own deep-dive: `docs/aar-walk-back-persistent-runner.md`. This report
> covers the three stat-layer changes that rode alongside it.

---

## 1. Foul-Out leaderboards

### What was asked for

> "Foul out rates should probably be a leaderboard stat but I can't decide if
> they are a type of strikeout or a separate thing."

…and after settling on separate-but-related:

> "It goes on both — I want to know how often hitters do it and how much
> pitchers do that too."

### What changed

The data already existed (`game_batter_stats.fo`, `game_pitcher_stats.fo_induced`,
already aggregated in `leaders()`); only the surfacing was missing. Added:

- **Batting · Power & Counting:** `FO` (count) and `FO%` (FO / PA).
- **Pitching · Counting & Value:** `FO` (foul-outs induced).
- **Pitching · Workload & Stuff:** `FO%` (FO / BF), beside K%.
- Glossary entries for all four (`o27v2/web/glossary.py`).

Kept foul-outs as their own stat rather than folding them into the strikeout
counters, matching how the DB models them — while K% continues to blend them in
(`(K + foul-outs) / BF`), the established O27 convention.

---

## 2. XO Crossover consistency (the silent-miscalibration class)

### What was asked for

> "The crossover stats are [anchoring] instead of actual batting average… you
> need to use batting average as a one-to-one, not plate average, which is
> different."

…then, once the root cause was clear:

> "Yes do that and any others."

### Diagnosis

The XO crossover z-anchors a player's value against the O27 league
distribution, then maps it onto MLB mean/spread:

    xo = MLB_mean + ((value − O27_mean) / O27_sd) × MLB_sd

This is only correct if the **per-player value and the league baseline
distribution are computed by the same formula**. They had drifted, because
`_compute_xo_league_baselines` re-derives each stat with its own hand-coded SQL
loop separate from `_aggregate_batter_rows` / `_aggregate_pitcher_rows`:

- **AVG** — per-player fed `b["avg"]`, which is aliased to O27's headline
  **PAVG (H/PA)**, but the baseline distribution was **H/AB**. A plate average
  was being z-scored against a batting-average distribution, shifting every
  translated AVG downward (PAVG < BAVG systematically).
- **wOBA** — baseline used fixed MLB-ish weights; per-player uses the dynamic
  empirical O27 weights with a true-singles/stay split.
- **BABIP** — baseline `(H−HR)/(AB−HR)`; per-player excludes strikeouts:
  `(H−HR)/(PA−K−BB−HBP−HR)`.
- **pitcher oSLG / oOPS** — baseline used full total bases; per-player uses the
  crude `(H+3·HR)/AB_faced`.

The failure mode is silent: nothing errors, the leaderboards just show subtly
wrong MLB-equivalent numbers.

### What changed

`_compute_xo_league_baselines` now mirrors the per-player formula for every XO
stat (`o27v2/web/app.py`): AVG → H/AB (bavg), wOBA → dynamic weights + stay
split, BABIP → strikeout-excluded denominator, oSLG/oOPS → `(H+3·HR)/AB_faced`.
Added comments at both sites stating they MUST stay in sync.

### Verification

`mean(xo)` over qualified players now lands on the MLB anchor for **all six
batter stats** and the opponent-slash pitcher stats — the signature of a
correctly-centered z-anchor (a correct map sends the league mean exactly onto
the anchor mean). ERA/WHIP/K9/BB9/HR9 were already consistent and untouched.

---

## 3. RA/27 — runs allowed per 27 outs

### What was asked for

> "I'm wondering if there needs to be an out-share-type ERA rendering against
> runs allowed?"

ERA in O27 is already `er × 27 / outs` (earned runs on the out-share /
complete-game scale). The runs-allowed analog, `runs_allowed × 27 / outs`,
existed only as a league baseline (`out["ra27"]`), never surfaced per pitcher.
It matters in O27 specifically because the Walk-Back rule manufactures unearned
runs by design — real runs that ERA deliberately excludes.

The user first leaned native-only:

> "Or we can use RA/27 since [we] already have wERA or xRA… what makes sense?"

…then opted for both renderings:

> "Yes do the ERA-anchored XO twin."

### What changed

- **Native RA/27** (`o27v2/web/app.py`, `_aggregate_pitcher_rows`):
  `runs_allowed × 27 / outs`, shown beside wERA and xRA on the same O27 scale
  (lower = better). The trio reads together — wERA (arc-weighted *earned*), xRA
  (*expected*), RA/27 (*all* runs) — so `RA/27 − wERA` is the run damage the
  earned-run stats hide, legible at a glance.
- **ERA-anchored XO twin:** added `ra27` to `XO_PITCHER_STATS` with the MLB ERA
  anchor (4.30 / 1.05), and the baseline distribution (`runs_allowed × 27 /
  outs`, matching the per-player formula). The XO RA/27 card sits beside XO ERA
  in the MLB-Equivalent panel, where `XO RA/27 − XO ERA` is the same Walk-Back/
  unearned cost expressed on the familiar MLB ERA scale.
- Glossary entry + two leaderboard cards (native + XO).

### Verification

RA/27 ≥ ERA for every pitcher (total ≥ earned, as it must be). Native league
mean ~19–22 vs ERA ~15 — the ~4–7 run gap is the Walk-Back/unearned offense.
XO RA/27 centers on **4.302** (target 4.30), reading like a real MLB ERA.

---

## Notes / follow-ups

- **Root lesson (XO):** the crossover baseline and the per-player stat are two
  code paths computing the same quantity; any formula divergence silently
  miscalibrates the translation. A longer-term cleanup would extract a single
  shared rate-from-counts helper so they can't drift — deferred because
  `_aggregate_*_rows` calls back into `_league_baselines`, risking recursion.
- **BABIP denominator:** the per-player O27 BABIP excludes K and uses a PA-based
  denominator; the baseline now matches it. Whether that denominator is the
  "right" BABIP for O27 is a separate modeling question, untouched here.
- Suites run clean (render / analytics / park / walk-back); the only repo
  failures are the pre-existing `test_phase8_db_migration` idempotency cases and
  a flaky random trade-noise test, both unrelated and red on the base commit.
