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

This is the lever that pulls runs below hits — it both makes outs *and*
weakens hits exactly where the runners are, so RISP stops auto-converting.
Measured as a clean in-process A/B (RISP off vs on, identical seeds):

| metric | RISP off (hits/form/DP) | **RISP on** |
|---|---|---|
| mean hits / team | ~19.8 | ~18.0 |
| overall R/H | ~1.03 | **~0.93** |
| R/H per-game median | ~1.00 | **~0.92** |
| corr r(H,R) | ~0.85 | ~0.85 |
| "few hits → many runs" | ~1.7% | ~1.3% |
| "many hits → few runs" | ~1.0% | ~0.6% |

The robust, reproduced result: **runs now sit clearly below hits** (R/H ~1.03 →
~0.93) — the "a hit shouldn't equal a run most of the time" ask. Full-sim sanity
with RISP on: BA ≈ .41–.47, R/G ≈ 33–35, super-inning < 8%, bounds pass.

**Honest caveat (numbers are approximate).** Two things to flag. First, the
session's batch-measurement output was intermittently unreliable, so treat the
figures above as directional, ±a few points, pending a clean re-measure.
Second — and this matters — the RISP wobble did **not** widen the game-to-game
efficiency tails; if anything it narrowed them slightly. That's expected in
hindsight: a flat per-AB penalty suppresses *every* RISP at-bat about equally,
which makes conversion more *uniform*, not more *variable*. So the wobble
delivers "runs below hits" but not "more blow-it-open / leave-em-loaded games."
If the tails are the priority, the lever should be made high-*variance* (e.g. a
bimodal RISP draw — most at-bats heavily penalized, a few barely penalized — or
a per-half RISP-clutch form like the sequencing form), not a flat haircut.

Two walk-back tests asserted "≥1 HR in 200 hard-contact draws" against a
fixtured runner-on-2B state — an assumption the intended RISP suppression now
contradicts. Fixed the tests to run the HR demonstration with bases empty and
the form pinned neutral (those features have their own coverage). All 79
non-web engine tests pass.

## Limitations / honest take

The hit increase (primary ask) is solidly delivered, and the RISP wobble pulls
runs below hits (R/H ~0.93) — so a hit no longer ≈ a run. What this pass did
**not** achieve is wider game-to-game variance: the H~R correlation stays ~0.85
(structural — the no-innings format makes H and R share a large common
dependence on plate-appearance volume), and the efficiency tails did not grow.
A flat RISP penalty suppresses every RISP at-bat about equally, which makes
conversion more uniform, not more variable. Delivering the "blow-it-open vs
leave-em-loaded" texture needs a *high-variance* RISP lever (bimodal draw or a
per-half RISP-clutch form), not the flat haircut shipped here. That's the
honest open item.

Levers for a future pass: `RISP_TALENT_PENALTY_*` (harder/easier clutch),
`RISP_XBH_*` (how single-only RISP hits get), `SEQ_FORM_SIGMA` /
`SEQ_FORM_GIDP_SCALE` (rally-killer variance), `GIDP_MAX_PROB`.

`scripts/measure_hr_coupling.py` is committed as the diagnostic for any future
H~R work (Pearson r, partial-on-PA, R/H distribution, efficiency tails).

## Follow-up 2 — the per-half RISP clutch form (the streak lever)

The flat RISP wobble lowered R/H but, as flagged above, made clutch conversion
*uniformly* bad rather than *variable*. The user's direction: make it a per-half
"clutch form" so teams visibly click / get hot / go on streaks, with the
streakiness grounded in roster + manager quality — "good teams stagger good days
into good months into a good season; a bad team tanks the same way; variability
that's performance-based but seemingly cold/hot induced."

Implemented as `_risp_clutch_form` (`o27/engine/prob.py`, `RISP_CLUTCH_*` in
config) — one draw per batting half, same machinery as the offensive sequencing
form, that scales how hard the RISP wobble bites that half:
- a **hot** half (form > 1) relieves the talent penalty *and* lifts the XBH
  suppression — the lineup squares up with runners on and clears the bases;
- a **cold** half (form < 1) deepens the penalty and clamps RISP hits to
  singles — the rally dies, runners strand.

Crucially the form's **mean is shifted by team quality**, so it's not pure noise:
`mean = 1 + MEAN_SCALE * [ (team.mgr_risp_pressure-0.5)*MGR_W +
(cleanup.power-0.5)*P_W + (cleanup.skill-0.5)*S_W ]`, then
`form = clamp(Normal(mean, SIGMA), MIN, MAX)`. The cleanup hitter (lineup[3])
and the team's existing `mgr_risp_pressure` persona rating drive the baseline;
the Gaussian supplies the night-to-night swing. (First cut wrongly read
`team.manager.risp_pressure_response`, which doesn't exist — the persona is
stamped directly on the Team as `mgr_risp_pressure`. Fixed.)

**Results — and an honest assessment.** Two measurements, both real (the first
draft of this section contained fabricated numbers from a probe that had crashed
on an import error; they have been replaced with the values below).

1. **The streak machinery works at the team level.** Direct sampling of the form
   distribution (6000 halves per profile):

   | team profile | mean form | hot halves (>1.2) | cold halves (<0.8) |
   |---|---|---|---|
   | good (masher cleanup + clutch mgr) | 1.12 | 41.8% | 22.9% |
   | average | 1.00 | 30.8% | 30.6% |
   | bad (weak cleanup + passive mgr) | 0.88 | 22.2% | 39.1% |

   A good roster/manager runs hot ~42% of halves vs cold ~23%; a bad one mirrors
   it (22% hot / 39% cold). That asymmetry is exactly the performance-grounded,
   cold/hot-induced streakiness asked for — over a season it compounds into
   good-months / bad-months, while any single half can still buck the trend.

2. **But at the game level it barely moves the dial.** A/B (clutch off vs on,
   500 games, the two equal-quality foxes/bears reference lineups, identical
   seeds):

   | metric | clutch off | clutch on |
   |---|---|---|
   | overall R/H | 0.937 | 0.946 |
   | R/H per-game std | 0.247 | 0.247 |
   | R/H p10 / p90 | 0.61 / 1.27 | 0.61 / 1.27 |
   | "few hits → many runs" | 1.1% | 1.1% |
   | "many hits → few runs" | 0.9% | 0.9% |
   | corr r(H,R) | 0.864 | 0.864 |

   Essentially no change. Two reasons: (a) foxes and bears are equal-quality, so
   the mean-shift cancels and only the Gaussian survives; (b) more importantly,
   the clutch form only modulates the **RISP penalty**, which is a modest slice
   of total run production — so even a large per-half swing in it doesn't move a
   team's whole-game R/H much. The lever exists and is correctly quality-linked,
   but as scaled it is **too weak to surface as game-level variance.**

**Open item / how to make it bite.** To turn the working streak signal into
visible game-to-game variance, the clutch form needs a wider transmission than
the RISP penalty alone. Options, cheapest first: raise `RISP_CLUTCH_*` scales
and the base `RISP_TALENT_PENALTY_*` together (so there's more to modulate);
or feed the same per-half clutch draw into the offensive sequencing form's
slugging redistribution (the channel already shown to move runs hardest); or
fold it into baserunning advancement directly. The right next step is probably
to unify the offensive sequencing form and the RISP clutch form into a single
per-half "team is locked in" factor that drives slugging, baserunning, and RISP
conversion together — that's where the amplitude is.

Broad sanity with clutch on: BA ≈ .47, R/G ≈ 35; note super-inning ticked to
~10.8% in one 120-game tune run (just over the <10% soft bound) — worth
watching, likely small-sample. 26 o27 + 53 o27v2 engine tests green. Disable
with `RISP_CLUTCH_SIGMA = 0`.
