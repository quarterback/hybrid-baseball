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

Broad sanity with clutch on: BA ≈ .47, R/G ≈ 35; super-inning 5.67% on a
300-game run (the ~10.8% seen on 120 games was small-sample). 26 o27 + 53 o27v2
engine tests green.

## Follow-up 3 — re-anchor the clutch form on the best hitter

Per user direction, the clutch form's quality anchor was changed from the
cleanup hitter (a fixed lineup slot) to the team's **best hitter** — the max
over the lineup of a power/skill blend, wherever he bats — with the manager
persona demoted from a co-equal driver to a small "vibes" nudge. "It should
just be over the best hitter on the team… based on performance and vibes,
although vibes can help a little bit." Weights: `RISP_CLUTCH_BAT_W = 0.85`
(performance) vs `RISP_CLUTCH_MGR_W = 0.15` (vibes).

Team-quality differentiation holds with the best bat planted in slot 6 (not
cleanup), confirming lineup slot is now irrelevant (direct sampling of the form
distribution, 8000 halves per profile):

| team profile | mean form | hot halves (>1.2) | cold halves (<0.8) |
|---|---|---|---|
| good (star bat slot 6 + clutch mgr) | 1.19 | 49.8% | 18.8% |
| average | 1.02 | 34.5% | 31.1% |
| bad (no bat + passive mgr) | 0.96 | 28.8% | 36.6% |

The re-anchor on the best hitter actually *widened* the team-level gap vs the
cleanup version (good now runs hot 49.8% of halves vs cold 18.8%), and it no
longer cares where that bat hits.

**Game-level effect is real but modest, and honestly confounded.** Good vs bad
offensive club, 300 games each (visitors measured; "good" = best bat boosted
+0.12 power/skill and clutch manager, "bad" = best bat −0.20 and passive
manager):

| club | R/g | H/g | R/H |
|---|---|---|---|
| good | 17.34 | 18.68 | 0.928 |
| bad  | 16.16 | 17.63 | 0.917 |

The good club scores **+1.18 R/g** more — but note the R/H ratios are nearly
identical (0.928 vs 0.917), and the good club also collected more hits. So most
of that run gap comes from the boosted bat producing more offense *generally*,
not from dramatically better clutch *conversion*; the pure clutch signal at the
game level is still small (consistent with Follow-up 2's finding that the form
only modulates the modest RISP-penalty slice). The re-anchor did what the user
asked — the streak now rides on the best hitter, wherever he bats, with the
manager as a minor nudge — and it strengthened the *team-level* hot/cold
distribution, but it did **not** by itself make clutch a large game-to-game
swing. That still wants the wider transmission noted in Follow-up 2 (unifying
the clutch draw with the sequencing form's slugging channel). 26 o27 + 53 o27v2
engine tests green. Disable
with `RISP_CLUTCH_SIGMA = 0`.

## Follow-up 4 — unify the two per-half draws (the wider transmission)

This is the step Follow-ups 2 and 3 kept pointing at. The diagnosis was never
"the clutch lever is too small" — it was "too **narrow** and **uncorrelated**."
There were **two independent per-half Gaussian draws** in `o27/engine/prob.py`:
`_batting_seq_form` (slugging redistribution, baserunner score-roll, GIDP) and
`_risp_clutch_form` (RISP talent penalty, XBH suppression). Because they rolled
independently, a hot-slugging half and a hot-converting half rarely lined up —
within a game the channels averaged out instead of compounding, so the tails
never widened.

**The change.** Collapse both into ONE latent per-half draw — `_locked_in_form`
("the lineup is locked in tonight") — keeping the best-hitter + manager-vibes
anchor from Follow-up 3, fed to **all five** channels at once. Mechanically:
`_risp_clutch_form` and `_batting_seq_form` are now thin shims that both return
`_locked_in_form(rng, state)`, so every channel reads the *same* cached draw per
half. The per-channel strengths stay as their own live constants
(`SEQ_FORM_POWER_SCALE`/`SCORE_SCALE`/`GIDP_SCALE`, `RISP_CLUTCH_PENALTY_RELIEF`/
`XBH_RELIEF`) — only the **draw** is shared. New config block `LOCKED_FORM_*`
governs the shared draw (`SIGMA/MIN/MAX/MEAN_SCALE/MEAN_BASE/BAT_*/MGR_W`); the
old `SEQ_FORM_SIGMA` and `RISP_CLUTCH_SIGMA`/anchor constants are retained for
reference but no longer roll anything (clearly marked "superseded" vs "LIVE").

**RNG note:** collapsing two `rng.gauss` calls into one shifts the downstream
stream, so seeded outputs change — this is a behavior change, and "split vs
unified" is a distributional comparison, not a same-seed diff.

**Tuning.** The tail-widening is real and immediate, but correlated hot halves
stack *convexly* (a hot half relieving the RISP penalty AND slugging AND running
adds more runs than an equally-cold half strands), so pushing σ up also drifts
mean R/H upward — undoing "a hit ≠ a run." Added `LOCKED_FORM_MEAN_BASE` (base
center, set 0.94) to pull the mean back while keeping the spread. Settled on a
**moderate** amplification that widens the tails without blowing R/G out of band
(σ 0.66, clamp [0.08, 2.15], gains slg 1.45 / score 1.20 / gidp 1.20 /
risp-relief 0.95 / xbh-relief 1.00). Over-amplified variants (σ 0.85, gains ~2×)
widened the tails further but pushed R/H to ~1.07 and run-std to 15 — too hot.

**Results.** In-process A/B on the foxes/bears reference lineups, off (σ=0) vs
the shipped unified form, plus the committed-config direct measure
(`scripts/measure_hr_coupling.py`, `scripts/ab_locked_form.py`):

| metric | OFF (σ=0) | **unified (shipped)** |
|---|---|---|
| mean hits / team | 17.86 | 18.68 |
| mean runs / team | 16.07 | 18.07 |
| run std (abs) | 7.09 | **9.73** |
| overall R/H | 0.900 | 0.967 |
| R/H per-game std | ~0.22 | **0.269** |
| R/H p10 / p90 | 0.62 / 1.16 | **0.62 / 1.30** |
| **p90−p10 spread** | **0.54** | **0.68** |
| "few hits → many runs" | 0.9% | **2.4%** |
| "many hits → few runs" | 0.8% | **1.3%** |

The efficiency tails finally widen — the metric **every prior pass failed to
move**: the spread grows ~26%, run-std +37%, and both tail shares roughly
double-to-triple. Mean R/H stays **0.96** (a hit still ≠ a run).

**Performance grounding is now strong (and unconfounded).** Good vs bad club,
300 games each (best bats ±, measured as visitors):

| club | R/g | H/g | R/H | run-std |
|---|---|---|---|---|
| good (+0.12 bats) | 20.07 | 19.96 | **1.006** | 10.72 |
| bad (−0.20 bats)  | 13.55 | 16.10 | **0.841** | 7.12 |

A **+6.5 R/g** gap — and unlike Follow-up 3 (where the good club's *R/H* was
nearly identical to the bad club's, so the gap was just "more hits"), here the
good club **converts better too** (R/H 1.006 vs 0.841) and swings wider
(run-std 10.7 vs 7.1). The unified draw turned the already-working team-level
streak signal into real game-to-game variance that rewards the better roster
with blow-it-open games.

Broad sanity (tune.py, 200 games): BA .482, SLG .773, K% 13.2%, BB% 10.1%,
**super-inning 3.0% (<10% ✓)**, all bounds OK. 42 o27+RISP +
(archetypes/linear-weights/managers/engine-config/streaks) 67 o27v2 tests green.

**Disable / tune.** `LOCKED_FORM_SIGMA = 0` turns the whole mechanism off (every
half plays at form 1.0, every channel identity). Widen the tails further by
raising `LOCKED_FORM_SIGMA` + the per-channel gains together, and pull the mean
back with `LOCKED_FORM_MEAN_BASE`. Harness: `scripts/ab_locked_form.py`
(off/ported/amp/mod/risp/base scenarios on identical seeds).

**Honest caveats.** (1) The convex stacking means spread and mean trade off —
you can't widen the tails arbitrarily without lifting R/H; the shipped values
are a deliberate middle. (2) Numbers are from the two equal-quality reference
lineups + synthetic good/bad clubs, not a full-league season; directionally
solid, but a multi-team season measure would firm them up. (3) The mean R/H
crept from ~0.93 (Follow-up 3) to ~0.96 — still "a hit ≠ a run," but slightly
hotter; `LOCKED_FORM_MEAN_BASE` is the dial if it should come back down.
