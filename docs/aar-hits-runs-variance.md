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

## Follow-up — the RISP wobble (the breakthrough)

After the first pass landed the hit increase but barely moved the correlation,
the user supplied the key idea: the engine *fiats* hits from talent + dice
**regardless of situation**, so make converting runners in scoring position a
genuine, high-variance struggle instead of a formality. Spec, in their words:
at RISP, knock the hitter's effective talent down ~29–41% (a per-at-bat random
draw — "the wobble"), and make the hits that do happen "largely singles," with
doubles/triples/HR rarer.

Implemented as two situational levers (both in `o27/config.py` +
`o27/engine/prob.py`, both individually disable-able):

- **Talent wobble** (`RISP_TALENT_PENALTY_MIN/MAX`): with a runner on 2B/3B, a
  fresh `1 - uniform(0.29, 0.41)` multiplier is folded into the batter's
  condition term in `contact_quality`, sagging matchup, power and eye together.
  Success simply becomes less likely, and the per-AB draw is the randomness.
- **XBH suppression** (`RISP_XBH_*`): a sum-preserving HR/triple/double→single
  redistribution at RISP, so runners advance station-to-station and pile up
  rather than being driven in all at once.

This is the lever that finally broke the structural floor — because it both
makes outs *and* weakens hits exactly where the runners are, runners strand:

| metric | baseline | after hit/form/DP | **+ RISP wobble** |
|---|---|---|---|
| mean hits / team | 14.88 | 19.62 | 17.94 |
| overall R/H | 1.134 | 1.037 | **0.916** |
| R/H per-game median | 1.12 | 1.00 | **0.89** |
| corr r(H,R) | 0.888 | 0.868 | **0.830** |
| R/H per-game std | 0.265 | 0.273 | **0.330** |
| R/H 10th / 90th pct | 0.80 / 1.45 | 0.68 / 1.38 | **0.55 / 1.31** |
| "few hits → many runs" | 0.9% | 1.7% | **3.1%** |
| "many hits → few runs" | 0.2% | 1.0% | **5.5%** |

Runs now sit clearly below hits (R/H 0.92), the correlation is off its ceiling,
and both efficiency tails are real — "many hits, few runs" went from a rounding
error (0.2%) to 1 game in 18 (5.5%). Broad sanity (tune.py): BA .411, SLG .643,
R/G 33, K% 12.9%, BB% 10.0%, super-inning 7.3% (<10% ✓).

Two walk-back tests asserted "≥1 HR in 200 hard-contact draws" against a
fixtured runner-on-2B state — an assumption the intended RISP suppression now
contradicts. Fixed the tests to run the HR demonstration with bases empty and
the form pinned neutral (those features have their own coverage). All 79
non-web engine tests pass.

## Limitations / honest take

The hit increase (primary ask) is solidly delivered and the run distribution is
now genuinely varied — the RISP wobble was the lever that did it, by attacking
the 87%-of-baserunners-score floor where the runners actually are. The H~R
correlation still won't go to zero (0.83 floor): the no-innings format means H
and R share a large common dependence on plate-appearance volume, and that's
the sport, not a bug. But the "dry 1:1" feel is gone — runs are below hits, the
ratio swings game to game, and blow-it-open and leave-em-loaded games both show
up at realistic rates.

Levers for a future pass: `RISP_TALENT_PENALTY_*` (harder/easier clutch),
`RISP_XBH_*` (how single-only RISP hits get), `SEQ_FORM_SIGMA` /
`SEQ_FORM_GIDP_SCALE` (rally-killer variance), `GIDP_MAX_PROB`.

`scripts/measure_hr_coupling.py` is committed as the diagnostic for any future
H~R work (Pearson r, partial-on-PA, R/H distribution, efficiency tails).
