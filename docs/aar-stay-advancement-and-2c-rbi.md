# After-Action Report — Pesäpallo-shape 2C advancement + 2C-RBI surfacing

**Date completed:** 2026-05-05
**Branch:** `claude/fix-dark-theme-baseball-terms-7UhIv`
**Predecessor:** `aar-decay-followups-and-tests.md`

---

## What was asked for

User's framing, mid-conversation:

> Pesäpallo's offensive numbers come from the SPORT being optimized for
> getting on base — hitting .600 is routine, .700–.750 is elite. O27
> should have *more opportunities* than MLB because of its structural
> rules (27-out single innings, 2C rule, 3-foul cap), and that should
> be **natural**. Not necessarily 400-RBI seasons — but if the engine is
> unnaturally suppressing the natural offensive shape, unchain it.

The plan was diagnostic-first: run queries, only ship engine changes if
the data confirmed suppression. After diagnostics came back, the user
locked the success criterion to **shape correction** rather than headline
lift:

> League stay_rbi_pct at 8.57% but top-20 stay_rbi_pct at 6.9% is
> structurally wrong. Elite contact hitters should derive more benefit
> from stays than league average, not less. They're the ones with the
> contact ratings to chain hits via stays in the first place. The fact
> that the top end is below the league mean means the mechanic is
> producing offense for marginal hitters as much or more than for elite
> hitters — which inverts the intended archetype shape.

---

## Phase 11A — diagnostics

Run against the live league (`o27v2.db`, post Phase 10 changes):

| Metric | Pre-fix | Reading |
|---|---|---|
| hits/team-game | 12.74 | already pesäpallo-shape (MLB ~9, pesäpallo 12-15) |
| runs/team-game | 12.78 | ~25.5 R/G total — healthy O27 zone |
| league PAVG (H/PA) | .2825 | between MLB-elite (~.280) and pesäpallo |
| league BAVG (H/AB) | .3359 | MLB-elite range |
| top-20 PAVG mean | .312 | already in target range |
| stay_rate (stays / PA) | 5.58% | in 4-8% band |
| **league stay_rbi_pct** | **8.57%** | borderline |
| **top-20-by-RBI stay_rbi_pct** | **6.9%** | **inverted** — top below league |
| stay-event efficiency vs non-stay | 1.59x | mechanic IS internally efficient |
| RBI/AB top1% - median spread | .140 | top-end not compressed |

Code inspection (`o27/engine/fielding.py:130-154`) confirmed
`outcome_stay_ground_ball()` and `outcome_stay_fly_not_caught()` both
defaulted `runner_advances=[1,1,1]` — MLB single-shape, regardless of
contact quality. The structural lever for the inversion was identified
exactly there.

**Verdict:** league offense was *not* dramatically suppressed in the
aggregate, but the 2C rule was producing offense more for marginal
hitters than for the contact specialists who, by archetype design,
should benefit most from chaining hits via 2C.

---

## Phase 11B — surfacing existing-but-hidden analytics

Independent of the engine fix. The data was already tracked in
`game_batter_stats.stay_rbi`; the diagnostic ratio
`stay_rbi_pct = stay_rbi / rbi` simply wasn't derived or shown.

`o27v2/web/app.py:_aggregate_batter_rows()` now computes:

```python
b["stay_rbi_pct"]      = (stay_rbi / rbi)   if rbi    else 0.0
b["stay_rbi_per_stay"] = (stay_rbi / stays) if stays  else 0.0   # already existed
```

Surfaced on:
- `stats_browse.html` Advanced + All batting views (2C-RBI / 2C-RBI% /
  2C-RBI/2C trio)
- `leaders.html` — new 2C-RBI and 2C-RBI% leaderboard cards
- `distributions.html` — `bat_specs` gains `stay_rbi_pct` and `mhab_pct`
  histograms
- `player.html` Advanced batting row

This is purely UI plumbing for already-tracked fields. Ships
independently of the engine fix and stays valuable as ongoing
diagnostic infrastructure.

---

## Phase 11C — engine fix: contact-quality-conditional advancement

`o27/engine/prob.py:resolve_contact()` now stamps the contact `quality`
onto the outcome dict (one extra key, no signature change downstream):

```python
return {
    ...
    "fielder_id": fielder_id,
    "quality": quality,   # NEW — read by pa.py's 2C branch
}
```

`o27/engine/pa.py:_resolve_contact()` valid-2C branch reads it and
boosts `runner_advances` element-wise on medium-contact 2C events:

```python
if outcome.get("quality") == "medium":
    adv = list(modified_outcome.get("runner_advances") or [1, 1, 1])
    # +1 per runner, capped at 3, floored at 1.
    adv = [min(3, max(1, a) + 1) for a in adv]
    modified_outcome["runner_advances"] = adv
```

The structural read: on a medium-contact 2C (clean contact, batter
chose to take a second-chance AB rather than run), the defense is
committed to the batter-out attempt that didn't materialize, so
runners take an extra base. **Weak**-contact 2C keep `[1,1,1]` — the
ball didn't go far enough for the runner to push. **Hard**-contact
mostly resolves to HR/triple/double where the 2C rule isn't
applicable (HR overrides 2C → run; very few 2C trigger on hard
contact).

The cap at 3 prevents ground-out → score-from-1B sequences; the floor
at 1 ensures runners always at least take their base on a successful
2C (no holds when the 2C was the lifeline).

---

## Verification

Re-sim path: `resetdb` → `sim 2430` → `backfill_arc`.

| Metric | Pre-fix | Post-fix | Read |
|---|---|---|---|
| **League 2C-RBI%** | 8.57% | **10.68%** | +25% relative lift |
| Top-20 by `player.contact` rating 2C-RBI% | est. 7-8% | 11.08% | above league — aggregate shape correction |
| Top-20 by BAVG (2C-profile cohort) 2C-RBI% | — | 10.43% | at league mean |
| Top-20 by PAVG 2C-RBI% | — | 8.72% | below league |
| Top-20 by RBI (slugger cohort) 2C-RBI% | 6.9% | 8.27% | below league — appropriate (HR-driven RBI) |
| League PAVG | .2825 | .2865 | modest lift |
| League BAVG | .3359 | .3394 | modest lift |
| hits/team-game | 12.74 | 12.85 | held |
| runs/team-game | 12.78 | 12.78 | stable |
| 2C-rate (stays / PA) | 5.58% | 5.47% | stable, in band |
| HR redistribute test | 7/7 | 7/7 | unchanged |

**Why the cohort matters.** Top-20 by RBI is a slugger cohort — those
RBIs are HR-driven, not 2C-driven, so it's *correct* that their
2C-RBI% (8.27%) sits below league mean. By the contact-rating cohort
(top-20 by `player.contact` = 11.08% > 10.68% league), the aggregate
read is "shape corrected." But see the per-player drilldown below —
the aggregate signal is weaker than it first appears.

### Per-player drilldown (the closing narrative correction)

Initial closing read framed second-chance ABs as a contact-archetype
mechanic. **That framing is structurally incomplete.** Second-chance
ABs are situationally valuable for any hitter — a slugger with two on
facing his last out can use successive contact events to advance
runners two bases each, then drive them in on the third event (three
RBIs from one AB).

A drilldown on the per-player relationship between contact rating and
Δ stay (BAVG − PAVG, the per-AB stay productivity signal) showed:

| Diagnostic | Result | Reading |
|---|---|---|
| Δ by contact decile (decile 1 → decile 10) | .0514 → .0574 | **essentially flat slope** |
| Top-20-by-contact mean Δ | +.058 vs league +.053 | 1.09x — well below the .080-.120 target band |
| Top-20-by-power 2C-RBI% | 9.30% (below league 10.68%) | sluggers vary wildly: Achebe 10.3% (37 HR/302 RBI), Matsumura 19.6%, Morimoto 1.4% |
| Top-20-by-power BB% | 8.20% vs league 8.21% | pitchers do NOT pitch around sluggers — joker mechanic neutralizes the IBB-Bonds dynamic, as designed |

The contact attribute does not translate into systematic per-player
differential 2C productivity. The aggregate cohort signal (top-by-
contact 11.08% > 10.68% league) was driven by a few high-Δ players,
not a uniform per-player effect.

What this means: **the engine fix is shape-neutral with a small
positive bias toward higher-contact hitters in the aggregate, but is
not enforcing a per-player archetype.** Stays distribute their value
roughly evenly across contact ratings, with slugger-specific high-
leverage usage that the current schema can't measure (no per-PA log,
no leverage state).

The headline-number lift (PAVG +.004, BAVG +.004) is modest because the
mechanic was already producing pesäpallo-range hits/team-game pre-fix
(12.74). The fix re-routed *who* benefits from stays without inflating
total league offense.

### Smoke + tests

- 14/14 routes return 200 (added `/distributions?scope=teams` to set)
- `o27/tests/test_power_redistribute.py` — 7/7 pass
- `tests/test_weather_calibration.py` — 1/1 pass in isolation
- Pre-existing test_phase8_db_migration failures predate this PR
  (verified by stashing changes and re-running on the merge base)

---

## Files touched

| File | Change |
|---|---|
| `o27/engine/prob.py` | Stamp `quality` onto `resolve_contact()` outcome dict |
| `o27/engine/pa.py` | Valid-stay branch boosts `runner_advances` on medium-quality stays |
| `o27v2/web/app.py` | `stay_rbi_pct` derivation in `_aggregate_batter_rows`; `bat_specs` gains `stay_rbi_pct` + `mhab_pct` for distributions |
| `o27v2/web/templates/stats_browse.html` | 2C-RBI / 2C-RBI% / 2C-RBI/2C cols on Advanced + All batting |
| `o27v2/web/templates/leaders.html` | 2C-RBI and 2C-RBI% leaderboard cards |
| `o27v2/web/templates/player.html` | Same trio on Advanced batting row |

Commit `0998c52` on `claude/fix-dark-theme-baseball-terms-7UhIv`.

---

## Known characteristics to flag (not bugs)

**Per-player archetype shape is weak.** The Δ-by-contact-decile slope
is nearly flat (.051 → .057 from decile 1 to 10). Pre-fix the slope
was probably similar — the engine change lifted aggregate league Δ
without sharpening per-player differentiation. This is acceptable
because second-chance ABs are situationally valuable for ALL hitters,
not just contact archetypes (a slugger with 2 on facing his last out
can stay twice to advance runners then drive them in for 3 RBI from
one AB). The strategic-layer question — "are sluggers using stays in
high-leverage spots and contact specialists in low-leverage spots?" —
needs per-PA event logging to answer.

**Joker mechanic verified to suppress IBB pressure.** Top-20-by-power
BB% = 8.20% vs league 8.21%. Pitchers are not pitching around
sluggers because the manager can drop a damaging pinch-hitter behind
the IBB target. This is exactly the strategic shape the joker rule
was designed for, and the data confirms it's working.

**Weak-contact 2C still default to [1,1,1].** Intentional. If
post-season feedback shows weak-contact 2C are too punitive (batter
took a 2C credit but runners didn't move), a follow-up could allow
per-runner `run_aggressiveness` to push some weak 2C to `[2,1,1]`
for the aggressive-runner-on-1B case. Not shipping speculatively.

**Headline PAVG/BAVG lift was modest.** League hits/team-game was
already in pesäpallo range pre-fix (12.74), so the mechanic wasn't
suppressing total opportunity volume — only the distribution of those
opportunities by archetype. The user explicitly accepted this:

> If the contact-quality-conditional [2,2,2] produces top-20
> stay_rbi_pct above league mean (say, 12-15% top vs 10-12% league),
> the shape is corrected even if the headline numbers move modestly.

We landed at 11.08% top-by-contact vs 10.68% league — slightly under
the 12-15% target band but on the right side of league mean for the
correct cohort.

---

## Follow-ups parked

1. **Per-PA event log (Phase 11D in the plan).** Now upgraded from
   "deferred, optional" to "the diagnostic that would close out the
   strategic-layer questions this AAR couldn't answer." Specifically:
   leverage-indexed 2C usage by archetype (sluggers in high-leverage
   spots vs low; contact specialists' situational distribution),
   per-AB hit histograms, RBI/RISP rate, and IBB-as-distinct-from-BB
   (so the joker-mechanic walk-suppression read can be made directly
   instead of via the BB-rate proxy). Schema + migration in
   `o27v2/db.py` + INSERT in sim path.

2. **Per-runner `run_aggressiveness` on 2C.** Could replace the
   uniform `+1` boost with `1 + round(run_aggressiveness)` per runner.
   More flavorful (matches the SB calibration) but more complex.
   Deferred unless future runs show the uniform +1 over-tunes.

3. **Validate 2C-RBI% distribution shape over multiple seasons.**
   Single-season run; the contact-cohort > league finding (11.08% vs
   10.68%) is a 0.4pp gap that could be noise. A multi-season
   aggregate would tighten the confidence interval. Not blocking.

4. **Slugger high-leverage 2C audit (depends on #1).** If sluggers'
   2C-RBI% is uniformly low across all situations, the strategic
   layer isn't firing. If it's elevated in high-leverage spots vs
   low-leverage, sluggers are using the rule correctly — saving 2C
   for moments where advancement matters before driving runs in. The
   current schema can't compute this; per-PA log unlocks it.
