# O27 / Super Innings — a comparison of the sim's iterations

This is a focused comparison of how the **simulation engine** and the **design
tool** (the steering/authoring surface a designer uses to shape a league) have
improved across iterations. It is deliberately *narrower* than
[`project-trajectory.md`](project-trajectory.md): the trajectory doc is a
feature changelog that interleaves the engine with the economy, UI, fantasy, and
audio layers; this doc filters to the two tracks that answer one question — **is
the sim a better sim than it was, and is it easier to design with?**

Each row links to the After-Action Report that landed it. Where a number is
quoted it comes from that AAR's validation section, not from memory.

> **Read this alongside the trajectory doc, not instead of it.** The trajectory
> is the full arc; this is the engine-and-tooling cross-section through it.

---

## The one-paragraph answer

The engine got better in two distinct ways, in this order. **First it got
*truer to its own premise*** — it stopped borrowing baseball's stats and
mechanics wholesale and started deriving them from the 27-out structure (the
variance-first pivot, the physics-first inversion, the 2C rework). **Then it got
*internally consistent*** — every number the UI shows is now the number that
flows into the summary stats, refit from each league's own simulated data rather
than from imported MLB constants (the native-ification + reconciliation pass).
In parallel, the **design tool** went from hardcoded constants to a
plain-English, LLM-drivable steering surface plus a composable optional-rules
system, so a non-programmer can shape a league's run environment and its
strategic texture without touching the engine.

---

## Track 1 — The simulation engine

| # | Iteration | Date | The defining change | How it's *better* | AAR |
|---|---|---|---|---|---|
| 0 | **Replit build** | early May | structurally complete, uncalibrated | end-to-end engine→stats pipeline exists; ~12 R/team/G, role-less live pitchers, real scars (dup stat rows, negative-FIP tails, super-inning crash) | [task #65](aar-task-65-expanded-rosters.md) · [May 3 handoff](HANDOFF-archive-2026-05-03.md) |
| 1 | **Variance-first pivot** | May 17 | *removed* the R/G target | run env became an **output**, not a knob; fatigue promoted to the dominant pitcher axis (quadratic stamina + post-threshold cliff); form variance widened asymmetrically → ~33 R/G, SD > 9 by design | [scoring variance & fatigue](aar-scoring-variance-and-fatigue-dominance.md) |
| 2 | **Documented foundations** | May 20 | engine texture written down | stay/2C reframe, defense model, baserunning & run-game events, parks/managers/arsenal/physics — the engine acquired its grain, and the design history moved into writing | [2C reframe](aar-2c-reframe-and-shifts.md) · [defense model](aar-defense-model.md) · [baserunning](aar-baserunning-and-run-game-events.md) |
| 3 | **Late-May depth** | May 21–31 | rules revised when the box score argued back | times-through-order familiarity; DP/TP bands; randomized weather; **super-innings → 3-out extra innings**; the **Walk-Back rebuilt from a one-pitch phantom into a persistent runner** | [super-innings](aar-super-innings-to-3-out-extra-innings.md) · [walk-back runner](aar-walk-back-persistent-runner.md) · [TTO & softball arsenal](aar-times-through-order-and-softball-arsenal.md) |
| 4 | **Physics-first inversion** | June 1 | **outcome→physics flipped to talent→physics→outcome** | **100% of batted balls are physics-decided** — EV/LA sampled from talent *before* the result, hit type *derived* from the trajectory. Event-level **corr(EV, hit) = 0.364 (causal)**, where before it was an echo. This is what makes xwOBA a real measurement instead of a tautology | [physics-first inversion](aar-physics-first-inversion.md) |
| **5** | **Native-ification & reconciliation** | **June 6–7** | **stopped borrowing MLB constants; made the surfaces agree** | see the dedicated table below — this is the most recent and most consequential engine iteration, and it is **not yet in the trajectory doc's changelog** | (multiple — below) |

### Iteration 5 in detail — the latest pass

This iteration is *maturity*, not features. Its thesis: **a fictional sport must
generate its own ground truth and then measure it — borrowing baseball's numbers
is a category error.** Four coordinated moves:

| Move | Before | After | AAR |
|---|---|---|---|
| **Baserunning value (BSR) → O27-native** | imported MLB weights (+0.25/extra base, +0.20/SB, −0.42/CS); noise-dominated, **penalized fast runners** (corr speed **−0.01**) | RE-derived from O27's own run-expectancy surface: an extra base is worth **≈ +0.001 run** (a runner usually scores anyway in a 27-out half), SB **+0.325**, CS **−0.408**. BSR vs speed flips to **+0.16**, sd tightens 3.33 → **1.09** | [BSR native + into WAR](aar-bsr-o27-native-and-into-war.md) |
| **wERA retired → xRA** | earned runs weighted by arc (outs 1–9 ×0.85, 19–27 ×1.20) — "late runs hurt more," a theory with **nothing to stand on** when there are no innings | **xRA** = expected runs allowed from the actual events, no positional theory, anchored to league RA/27 **exactly** (invariant 8 was 0.062 off, now ~0) | [retire wERA, anchor xRA](aar-wera-retirement-xra-headline.md) |
| **WAR "surfaced ⇒ summed"** | WAR's defensive term read **scout-grade DRS** while the Savant page showed **event-based OAA/Field Runs** — two pipelines that *never met* (one CF: +0.1 dWAR next to −19.9 Field Runs). Baserunning surfaced but **not summed** | OAA de-biased (zone-conditional expected-out, HRs excluded), reliability-regressed (`K=400`), and fed into WAR as the **same Field Runs the UI shows**; BSR added as a WAR term. New **invariant 11**: displayed component == summed component | [finding](aar-war-oaa-reconciliation-koalas.md) · [deep-dive](aar-stat-surface-reconciliation-comprehensive.md) · [fix](aar-war-oaa-fix-and-fielding-regression.md) |
| **2C resolves through the real hitting engine** | every valid stay forced `batter_safe=True`, credited a **single regardless of contact**, overrode advancement with a flat talent gate, and had **no cap** (a hot batter could stay forever) | **EV-driven, max 3 batted balls** (each stay burns a strike); resolves to the **real hit type** (a 2C can now be a double, triple, or an out); skill-differentiated (stay-rate poor **16%** → elite **55%**; jokers leverage it most). Run env preserved (~8.9–11.4 R/half) | [2C hitting-engine rework](aar-2c-hitting-engine-rework.md) · [spec](design-2c-hitting-engine-rework.md) |

Supporting reconciliations in the same window: the **wOBA scale** switched from
MLB's 1.20 to the O27-native OBP-scale factor (~0.86)
([native-stat audit](aar-native-stat-audit-and-advancement-correction.md)); the
**league-baseline wOBA** moved to native weights so wRC+ re-centred 91 → ~100
([league-baseline wOBA native](aar-league-baseline-woba-native.md)); and the
**Finisher (F)** credit was corrected to be a *reliever* stat, never the
starter/winner ([finisher credit fix](aar-finisher-reliever-credit-fix.md)).

**The pattern across iteration 5:** the engine now refits its run values from
each league's own data *per render cycle*, and the invariant suite grew from 9 →
**12** assertions — the signature move being *adding the invariant that would
have caught the drift*.

---

## Track 2 — The design tool

The "design tool" is the surface a designer/operator uses to shape a league:
the tunables, the style system, and the optional-rules framework. It improved on
a parallel curve toward *steerable-by-anyone*.

| Iteration | Date | The design surface | How it's *better* | AAR |
|---|---|---|---|---|
| **Hardcoded constants** | early | `config.py` only | a programmer edits Python | — |
| **Style library + eclectic randomizer + reseed-into-style** | May 25 | named engine styles; **infrastructure-driven** regional leagues | a region's style **emerges** from field geometry, climate, and talent pipeline — not national cliché; you can reseed an existing league into a new style | [tuning library & regional leagues](aar-tuning-library-and-regional-leagues.md) |
| **LLM tuning guide + scoring presets** | June 1 | [`tuning-guide-for-llms.md`](tuning-guide-for-llms.md) | paste it into any capable model, **describe a style in plain English**, get an Engine-Tunables blob — the sim became steerable without writing code; softball-derived presets shipped as worked examples | [LLM tuning guide](aar-llm-tuning-guide-and-softball-scoring-presets.md) |
| **Optional rules as a composable system** | May 30 → latest | per-league rules that ship **off by default / byte-for-byte unchanged** | **Power Play** (the nickel fielder) established the pattern; **Cricket Batting Order** (latest) added a pure *manager-decision* lever — an earned, use-or-lose 1-9 → 9-1 flip that **trades off against jokers**, driven by a derived `mgr_flip_aggression` persona and a flip-aware "valley" lineup. Controls compose across global / per-league / per-universe / edit-existing | [Power Play](aar-power-play-nickel-fielder.md) · [Cricket Batting Order](feature-cricket-batting-order.md) |

**The pattern across the design tool:** every lever added since May has been
*additive and reversible* — off by default, so existing universes are unchanged —
and the authoring surface moved from "edit Python" to "describe what you want in
English" to "compose strategic rules per league from the UI."

---

## What the comparison shows

1. **The engine's improvement curve has two phases.** *Truth to premise* (iters
   1, 4, 5-2C: variance-first, physics-first, EV-driven stays) came first, then
   *internal consistency* (iter 5's native-ification + reconciliation). You can't
   reconcile surfaces until the surfaces are measuring real things — so the order
   was forced, not incidental.

2. **"Stop borrowing MLB's constants" is the single biggest engine story, and
   it's the most recent one.** BSR, wERA→xRA, the wOBA scale, the league
   baseline, OAA — every one was a case of an imported constant that was either
   *perverse* in O27 (MLB-valued extra bases penalized fast runners; arc-weighted
   ERA asserted a theory that needs innings) or simply mis-scaled. The fix was
   never to patch the sim — it was to make the *metric* native.

3. **The design tool improved by becoming additive and legible.** Optional rules
   that are off-by-default mean the design space can grow without ever breaking a
   saved universe; the LLM guide means a designer no longer needs to read the
   engine to steer it.

4. **The discipline is itself a feature that improved.** The invariant suite
   (9 → 12) and the AAR corpus (~100 reports) are why drift like the WAR/OAA
   split was *findable* — and the latest iteration's habit of *shipping the
   invariant that would have caught the bug* is the clearest sign the project's
   correctness machinery matured alongside the sim.

---

*Companion to [`project-trajectory.md`](project-trajectory.md) (the full arc)
and [`portfolio-o27.md`](portfolio-o27.md). When a new engine or design-tool
iteration lands, add a row to the relevant track above.*
