# After-Action Report — Home-runs-by-count: O27 vs real MLB

**Date completed:** 2026-06-16
**Branch:** `claude/new-session-w4nwix`

---

## Context

The user shared a heatmap (Philip Bump / CT Insider, source: Retrosheet)
showing **what share of all MLB home runs from 1910–2025 were hit on each
ball-strike count**. The classic finding: 18.3% of all home runs are hit on
0-0 (the first-pitch ambush), the count distribution then spreads across the
grid, and deep counts stay live — 2-2 and 3-2 each account for ~9% of homers.

The task was to **compare O27's home-run-by-count distribution against that
real-world reference** — an engine-realism check, not a tuning change.

## Method

The engine *does* model pitches and a ball-strike count (`Count` in
`o27/engine/state.py`), and the per-count pitch outcome probabilities live in
`o27/config.py:PITCH_BASE`. But the count at which an outcome occurs is **not
persisted** — `game_pa_log` (`o27v2/db.py`) has no balls/strikes columns. So
the live DB can't answer "how many HRs on 0-0"; the count flows through the
render layer and is discarded.

Instead we run fresh headless games and capture `state.count` at the instant
the provider yields each home-run `ball_in_play` event. That instant *is* the
count the HR was hit on: the provider returns the event, and `apply_event()`
only afterward mutates the count, so `state.count` still holds the pre-contact
tally. Harness: `scripts/hr_by_count.py` (Foxes vs Bears, the `main.py`
rosters; `ProbabilisticProvider` wrapped to tally HRs *and* all balls-in-play
by count).

Sample: 2,500 games → 5,311 home runs / 126,718 balls in play.

## Results

| Count | O27 HR% | MLB HR% | diff | O27 BIP% | HR / BIP |
|-------|--------:|--------:|-----:|---------:|---------:|
| 0-0 | 35.0 | 18.3 | **+16.7** | 34.8 | 4.22% |
| 1-0 | 12.4 | 12.0 | +0.4 | 11.8 | 4.38% |
| 2-0 | 5.4 | 5.6 | −0.2 | 4.5 | 4.99% |
| 3-0 | 1.9 | 0.6 | +1.3 | 1.9 | 4.21% |
| 0-1 | 13.5 | 9.7 | +3.8 | 13.6 | 4.17% |
| 1-1 | 7.3 | 11.1 | −3.8 | 7.9 | 3.92% |
| 2-1 | 4.4 | 8.3 | −3.9 | 3.9 | 4.64% |
| 3-1 | 2.1 | 4.9 | −2.8 | 2.0 | 4.41% |
| 0-2 | 6.5 | 3.5 | +3.0 | 7.1 | 3.82% |
| 1-2 | 5.5 | 7.6 | −2.1 | 6.2 | 3.71% |
| 2-2 | 3.6 | 9.1 | **−5.5** | 4.0 | 3.83% |
| 3-2 | 2.4 | 9.2 | **−6.8** | 2.4 | 4.33% |

Summary readings:

- **First-pitch (0-0):** O27 35.0% vs MLB 18.3% — nearly **double**.
- **Two-strike:** O27 18.0% vs MLB 20.2% — close in aggregate…
- **…but the deepest counts are missing:** 2-2 is −5.5 pts and 3-2 is −6.8 pts.
- Sum of absolute differences (≈2× total variation): **50.2**.

## Diagnosis

Two findings, one root cause.

**1. A home run in O27 is count-agnostic.** The `HR / BIP` column is flat —
between 3.7% and 5.0% at every count, with no systematic tilt toward hitters'
counts. As a result the O27 `HR%` column is nearly identical to the O27 `BIP%`
column: **the HR-by-count distribution is just the ball-in-play-by-count
distribution.** In real baseball it is not — hitters do disproportionate damage
ahead in the count (2-0, 3-1) and put weaker, more defensive contact in play
with two strikes, so HR/BIP rises in hitters' counts. O27 has no such effect.

**2. O27 puts far too many balls in play on the first pitch.** 34.8% of all
balls in play happen at 0-0, because every plate appearance flows through 0-0
and `PITCH_BASE[(0,0)]` assigns a 0.23 contact (ball-in-play) probability to
the first pitch. Real hitters take the first pitch roughly two-thirds of the
time; O27 puts it in play ~23% of the time. That single number drives the
+16.7-pt 0-0 spike — and, because at-bats end early, it starves the deep
counts (2-2, 3-2) that carry ~18% of real homers but only ~6% of O27's.

The shapes diverge because O27's pitch model is **front-loaded and
count-flat**: contact probability barely moves across the count (0.23 at 0-0 →
0.20 with two strikes in `PITCH_BASE`), so the count-distribution of contact
decays monotonically from 0-0 instead of bulging in the deep two-strike counts
the way real plate appearances do.

## What this is (and isn't)

This is a realism *audit*, not a tuning change — no engine code was modified.
The comparison uses the two `main.py` demo rosters (a contact club and a power
club), so absolute HR volume is roster-specific; the **count distribution**,
however, is a property of `PITCH_BASE` + the count-agnostic HR resolution and
is stable across seeds.

If a future session wants O27's homers to bucket like the reference chart, the
two levers are: (a) lower the 0-0 contact weight in `PITCH_BASE` (push first
pitches toward called strikes / balls so fewer PAs end on pitch one and more
reach deep counts), and (b) make HR-per-contact count-aware so mistakes ahead
in the count and defensive two-strike contact differ. Lever (a) is the
high-order term — it alone closes most of the 0-0 gap and feeds the deep
counts.

## Reproduce

```
python3 scripts/hr_by_count.py --games 2500 --seed 1000
```

Real-MLB reference values are embedded in the script (`REAL`).
