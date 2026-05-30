# After-Action Report — Hits/runs variance & the structural H~R coupling

**Date completed:** 2026-05-30
**Branch:** `claude/baseball-hits-runs-variance-Cv0iW`
**Related prior art:** `aar-decouple-hits-and-runs-on-doubles.md`,
`aar-base-runs-cluster-luck.md`, `aar-scoring-variance-and-fatigue-dominance.md`

---

## Context

The user's complaint: hits and runs sit "too close together with a small
amount of variance" — runs track hits almost 1:1, with no realistic spread of
games where a few hits generate a lot of runs (or a lot of hits generate few).
The ask, in priority order:

1. **Increase the number of hits.** Explicitly does *not* care if scoring or
   batting average rises — O27 is a high-contact sport, "college baseball /
   softball" is a better allegory than MLB, and contact is preferred. Surpass
   MLB benchmarks.
2. **Decouple runs from hits.** It "shouldn't be a dry correlation"; a hit
   "shouldn't equal a run most of the time"; there should be genuine
   game-to-game variation in how hits convert to runs.

## Empirical baseline (before this session)

Measured per-team-per-game over 400 games (`scripts/measure_hr_coupling.py`,
foxes/bears reference lineups):

| metric | baseline |
|---|---|
| mean hits / team | 14.88 |
| mean runs / team | 16.87 |
| overall R/H | 1.134 |
| corr r(H,R) | **0.888** |
| R/H per-game std | 0.265 |
| "few hits → many runs" games | 0.9% |
| "many hits → few runs" games | 0.2% |

Two diagnostics reframed the whole problem:

- **Partial corr r(H,R | PA) = 0.544.** Most of the raw 0.888 is *volume*:
  r(H,PA)=0.85, r(R,PA)=0.89. Because outs are fixed at 27 and hits don't burn
  outs, a team that hits well simply bats longer (more PAs), inflating H and R
  together. The volume coupling is intrinsic to the no-innings format.
- **~87% of baserunners (H+BB) score** — versus ~30% in MLB. This is the real
  reason R≈H with low variance: **in a single continuous 27-out inning a
  stranded runner is never stranded — he waits through dozens more PAs and
  almost always comes around.** The format is a low-variance run-conversion
  machine. There is no inning-end to leave runners on base.

The headline finding: **the tight H~R coupling is overwhelmingly structural,
not a tuning artifact.** Conversion-side tuning sets the *mean* of runs-per-hit;
it cannot create the game-to-game *variance* the user wants, because the long
inning averages ~45 PAs/team and brings everyone around regardless.

## What was tried (and what each lever actually did)

Iterated empirically; recording the dead ends because they're the evidence for
the structural conclusion:

1. **Raise single weights in the contact tables.** Big, clean win on hits
   (14.9 → 22+). No effect on correlation (volume re-coupled it).
2. **Cut single→home advancement (strand more).** Lowered R/H mean slightly,
   **zero** effect on variance or correlation — stranded runners just scored
   later off an XBH.
3. **Per-half "sequencing form" on baserunning score-rolls.** A/B test
   (sigma 0 vs 0.9) moved R/H std by <0.02. The single-drives-in-runner channel
   is a *minor* run source; modulating it does nothing.
4. **Per-half form on contact *quality* (single↔XBH↔HR redistribution).**
   First lever to actually connect (corr(form, runs) 0.05 → 0.24, with
   corr(form, hits) ≈ 0 — slugging moves, hit count doesn't). Still muted,
   because in a never-ending inning XBH mostly change *when* a runner scores,
   not *whether*.
5. **The breakthrough: the GIDP gate was dead code.** Double plays — the one
   event that *erases* a baserunner before he scores — were gated on
   `state.outs < 2`, MLB's *per-inning* rule transplanted literally. In O27's
   single 27-out inning that means DPs fired only in the first 2 outs of the
   half and never again. Fixed the gate to fire all half long (room for two
   outs), and tied the DP rate to the per-half form.

## What shipped

A coherent per-half **offensive sequencing form** plus the DP fix, all in
`o27/config.py` + `o27/engine/prob.py`:

- **Contact tables** (`WEAK/MEDIUM_CONTACT`): single weights raised (WEAK
  0.18→0.34, MEDIUM 0.32→0.44) for the hit increase, with ground-ball volume
  kept high enough to feed double plays. `HARD_CONTACT` left untouched (it
  trips the power-redistribution stability invariant, and HARD is already 78%
  hits — the gains come from WEAK/MEDIUM).
- **Sequencing form** (`SEQ_FORM_*`): each half draws one `Normal(1, σ)` factor
  — a "the lineup is locked in tonight" vs "stranding everyone" knob, the
  macro analog of the existing per-pitcher `today_form`. It rides three
  channels at once, all correlated across the half's ~45 PAs:
  - a **dedicated, strong single↔XBH↔HR redistribution** (`SEQ_REDIST_*`,
    sum-preserving — slugging moves, hit count doesn't),
  - an additive **score-roll shift** on base advancement, and
  - a **double-play multiplier** (`SEQ_FORM_GIDP_SCALE`) — cold halves hit into
    rally-killing twin-killings.
  Set `SEQ_FORM_SIGMA = 0.0` to disable the whole mechanism (identity).
- **GIDP** (`prob.py` + `GIDP_*`): gate fixed (`outs < 2` → room for two outs
  before `out_cap()`); base/max rates raised so the now-live channel actually
  erases runners.

## Results

Per-team-per-game, 500 games:

| metric | baseline | after | note |
|---|---|---|---|
| mean hits / team | 14.88 | **19.62** | **+32%**, high-contact |
| overall R/H | 1.134 | 1.037 | toward hits ≥ runs |
| corr r(H,R) | 0.888 | 0.868 | small, structural ceiling |
| R/H per-game std | 0.265 | 0.273 | wider |
| R/H 10th pct | 0.80 | **0.68** | deeper strand tail |
| "few hits → many runs" | 0.9% | **1.7%** | |
| "many hits → few runs" | 0.2% | **1.0%** | 5× |
| run std (abs) | 7.50 | 10.0 | +33% |

Broad sanity (tune.py): BA .485, SLG .835, R/G 41, K% 12.8%, BB% 9.9%,
super-inning 6.7% (<10% ✓). All 79 non-web engine tests pass.

## Limitations / honest take

The hit increase (primary ask) is solidly delivered and the run distribution is
meaningfully more varied (run std +33%, both efficiency tails grew, low tail
deepened). **But the H~R *correlation* barely moved (0.888 → 0.868) and R/H is
still ≈ 1 — and this is structural, not a tuning shortfall.** The 27-out single
inning brings ~87% of baserunners home no matter how conversion is tuned; the
only way to move the correlation materially is to *erase* baserunners (the DP
lever) at rates that, pushed far enough, would make the sport feel like a
double-play derby. Real "16 hits, 4 runs" games come from stranding runners at
inning's end — a thing O27's format deliberately does not have.

Levers for a future pass that wants to push harder: `SEQ_FORM_SIGMA` /
`SEQ_FORM_GIDP_SCALE` (more rally-killer variance), `GIDP_MAX_PROB`, or a
fundamental reconsideration of whether the one-inning structure should permit
some form of rally-ending event.

`scripts/measure_hr_coupling.py` is committed as the diagnostic for any future
H~R work (Pearson r, partial-on-PA, R/H distribution, efficiency tails).
