# O27 — Project Trajectory

A narrative of how O27 went from a tweet-sized idea to a fully simulated sport in
roughly a month, plus a dated changelog of everything that landed. The README is
the snapshot — *what exists now*. This is the arc — *how it got here, and why each
turn was taken*.

The design history in detail lives in the ~70 After-Action Reports under
[`docs/`](.). Each AAR records the ask, the tradeoffs, the verification numbers,
and the honest open gaps. This document is the reading order through them: the
spine that the AARs hang off.

> **A note on dates.** O27 began in Replit in early May and moved to Claude Code
> around May 3; the git history you see begins May 17–18, and the bulk of the
> early AARs were written up in a single documentation wave on May 20. So the
> commit dates don't track the real build order for the first half of the month.
> The phases below are the real chronology, reconstructed from the handoff
> documents, the design conversations in [`attached_assets/`](../attached_assets),
> and the AARs themselves.

---

## The arc

### Phase 0 — Genesis (early May)

The idea predates the repo by years. The bet: take cricket's T20 move — a
structural compression that changed the strategic shape of the sport — and apply
it to baseball, *not* by shortening the game but by restructuring the inning. The
five-inning version was tried and rejected early as "short, more random, even
more boring baseball." 27 outs in one continuous half-inning felt like "a marathon
and a sprint at once," and that was the version worth building.

The [genesis PRD](../attached_assets/Pasted--PRD-O27-A-baseball-cricket-hybrid-with-pes-pallo-seaso_1777752866752.txt)
fixes the core conceit: one inning of 27 outs per side, no resets, a **stay**
mechanic borrowed from pesäpallo (on contact, the batter may stay at the plate and
advance runners at the cost of a strike), three **jokers** a manager can drop into
any spot in the order, and a super-inning tiebreaker. Early sim tests put the run
environment around 23 R/game. The name is the rules: **O** for one inning, **27**
for the outs. The long-form design argument for why this beats five-inning baseball
became [the blog post](blog-o27-vs-five-inning-baseball.md).

### Phase 1 — The Replit build (early–mid May, Tasks #1–#65)

The first working build came together fast in Replit. By the
[May 3 handoff](HANDOFF-archive-2026-05-03.md) the spine was real and end-to-end:
an engine→stats pipeline where every stat row traces back to a simulated plate
appearance, a 30-team league with a full 162-game schedule, standings and W/L
attribution, and the first cuts of the systems that wrap the engine — injuries
with tiered ILs, a motivation-driven trade engine, waivers, and the guilder
economy. Task #65 expanded rosters to 47 players, rolled every attribute
independently against a 9-tier talent ladder, and made pitcher roles *live* —
derived at appearance time from where in the 27-out arc the call lands, never
stored.

This era ran hot — roughly 12 runs per team per game — and carried real scars:
duplicate pitcher stat rows, individual FIP values going negative at the tails,
and a super-inning assertion that could crash the sim outright. The structure
worked; the calibration and the edges did not yet.

### Phase 2 — The move to Claude Code and the variance-first pivot (~May 3 → May 17)

The project [moved from the Replit agent to Claude Code](../attached_assets/Pasted-Wrapping-up-here-and-moving-the-project-to-Claude-Code-_1777831310870.txt)
around May 3. The defining decision of the whole month came on May 17, and it was
philosophical, not mechanical: **O27 is variance-first. There is no R/G
calibration target.** The earlier handoff had treated 22–26 R/G as a band to tune
toward; that was never the intent. Run environment is whatever the mechanics
produce. Rate stats are *observed outputs*, not knobs.

The [May 17 tuning pass](aar-scoring-variance-and-fatigue-dominance.md) made the
mechanics carry that weight: contact rebalanced toward harder, more damaging
contact; the strikeout-inflation drivers pulled back; fatigue promoted to the
dominant pitcher axis with quadratic stamina scaling and a steeper post-threshold
cliff; daily form variance widened with an asymmetric clamp so deep off-days are
common and transcendent days are rare-but-reachable. The run environment moved to
~33 R/G with a standard deviation north of 9 — wild variance, by design. The
[May 17 handoff](../HANDOFF.md) rewrote the docs to kill the old target framing.
This is where the git history effectively begins.

### Phase 3 — The documentation wave (May 20)

On May 20 the entire design history to date was committed at once — roughly fifty
AARs in a single wave. This is the moment the project's working discipline became
explicit: every meaningful change ships with a written decision record. The wave
covered the engine's foundations (the
[stay / second-chance reframe](aar-2c-reframe-and-shifts.md), the
[defense model](aar-defense-model.md),
[baserunning and run-game events](aar-baserunning-and-run-game-events.md),
[parks, managers, arsenal and physics](aar-ballparks-managers-arsenal-physics.md)),
the statistics rebuilt from first principles (the
[sabermetric analytics suite](aar-sabermetric-analytics-suite.md), the
[pitcher stat suite](aar-pitcher-stat-suite.md),
[BaseRuns and cluster luck](aar-base-runs-cluster-luck.md),
[xRA](aar-xra-2c-and-talent-spread.md)), the world around the sport (the
[guilder currency](aar-guilder-currency-system.md),
[auction shape and sellback](aar-auction-shape-and-sellback.md),
[motivation-driven trading](aar-motivation-driven-trading.md),
[tiered leagues, youth, and auction](aar-tiered-league-and-youth-and-auction.md)),
and the [realism layer](aar-sim-realism-layer.md). The blog post landed the same
day. After this, the design history lives in writing, not in anyone's head.

### Phase 4 — The late-May acceleration (May 21 → May 30)

With the foundation documented, the back third of the month was a sprint of new
systems, each with its own AAR:

- **May 21** — [Declared Seconds](aar-declared-seconds-rule.md) got its
  end-to-end reference write-up (the manager can bank unused regulation outs for a
  second trip through the lineup later); [concurrent named save
  slots](aar-concurrent-saves-and-restart-data-loss.md) let multiple leagues
  coexist and fixed a restart data-loss bug; plus
  [custom-league editing](aar-custom-league-editing.md) and
  [box-score pitcher decisions](aar-box-score-pitcher-decisions.md).
- **May 24** — career multi-season leaderboards moved into the almanac, and the
  [stat-invariant suite was repaired](aar-career-leaderboard-and-stat-invariants.md)
  — every originally-failing invariant closed, locking in the gate that has to
  pass before any release.
- **May 25** — three big ones in one day: [fast-sim stat repair plus outs-based
  DIPS leaderboards](aar-fast-sim-stat-repair-and-dips-leaderboards.md); the
  [engine style library, eclectic randomizer, reseed-into-style, and
  infrastructure-driven regional/peer-universe leagues](aar-tuning-library-and-regional-leagues.md)
  (a region's style emerges from field geometry, climate, and talent pipeline, not
  national cliché); and the
  [WCAG-AA UI re-theme, nav IA, mobile layout, and web modularization](aar-ui-retheme-nav-and-web-modularization.md).
- **May 27** — [times-through-the-order familiarity and the softball/underhand
  arsenal](aar-times-through-order-and-softball-arsenal.md), deepening the pitcher
  model so lineup-adaptation effects show up as real splits.
- **May 28** — [Zaryanovia, the Zora currency, and the live financials
  board](aar-zaryanovia-zora-and-financials.md): a fictional nation with a full
  name pipeline and flag, a Pro World Cup with regional qualifying and a 24-nation
  bracket, and broadened name pools.
- **May 30** — [hits/runs variance and the structural H~R coupling](aar-hits-runs-variance.md)
  (hits and runs were sitting too close together; this widened the tails);
  [performance streaks](aar-performance-streaks.md) (multi-week hot/cold ramps
  surfaced as an almanac badge); and the **Power Play** — the
  [first optional, per-league rule](aar-power-play-nickel-fielder.md) (a "nickel
  fielder," off by default so existing universes are byte-for-byte unchanged) with
  its [UI, presence effect, and stat rack](aar-power-play-ui-and-stats-segment.md).

The throughline of the late-May work is the same discipline that crystallized on
May 20: calibration over assertion, willingness to revise the rules when the data
argues (super-innings became 3-out extra innings; the Walk-Back was rebuilt from a
one-pitch phantom into a persistent runner), and refusing the easy version (the
worldwide-leagues feature was rebuilt to derive style from infrastructure rather
than national stereotype).

---

## Changelog

Grouped by phase, newest first. Each entry links to its AAR. Dates for the
late-May entries are the "date completed" recorded in the AAR; the earlier
foundational work was built across the Replit era and mid-May and written up in
the May 20 documentation wave.

### Late-May acceleration

**2026-05-30**
- The **Power Play** (the nickel fielder) — the first optional, per-league rule;
  ships off by default. [base rule](aar-power-play-nickel-fielder.md) ·
  [UI / presence effect / stat rack](aar-power-play-ui-and-stats-segment.md)
- **Performance streaks** — multi-week hot/cold ramps as an almanac-only badge.
  [AAR](aar-performance-streaks.md)
- **Hits/runs variance** — fixed the too-tight H~R coupling; widened the run/hit
  tails. [AAR](aar-hits-runs-variance.md)

**2026-05-28**
- **Zaryanovia, the Zora currency, and the live financials board** — fictional
  nation + name pipeline + flag, a 24-nation Pro World Cup, broadened name pools.
  [AAR](aar-zaryanovia-zora-and-financials.md)

**2026-05-27**
- **Times-through-the-order familiarity + softball/underhand arsenal** — deeper
  pitcher model; lineup-adaptation effects as real splits.
  [AAR](aar-times-through-order-and-softball-arsenal.md)

**2026-05-25**
- **Engine style library + eclectic randomizer + reseed-into-style +
  infrastructure-driven regional/peer-universe leagues.**
  [AAR](aar-tuning-library-and-regional-leagues.md)
- **WCAG-AA UI re-theme, nav IA, mobile layout, web modularization.**
  [AAR](aar-ui-retheme-nav-and-web-modularization.md)
- **Fast-sim stat repair (wERA archive) + outs-based DIPS leaderboards.**
  [AAR](aar-fast-sim-stat-repair-and-dips-leaderboards.md)

**2026-05-24**
- **Career multi-season leaderboards into the almanac + stat-invariant suite
  repair** — every originally-failing invariant closed.
  [AAR](aar-career-leaderboard-and-stat-invariants.md)

**2026-05-21**
- **Declared Seconds** — end-to-end reference for the bank-your-outs lever.
  [AAR](aar-declared-seconds-rule.md)
- **Concurrent named save slots + restart data-loss fix.**
  [AAR](aar-concurrent-saves-and-restart-data-loss.md)
- **Custom-league editing.** [AAR](aar-custom-league-editing.md)
- **Box-score pitcher decisions.** [AAR](aar-box-score-pitcher-decisions.md)

### Phase 3 — Documentation wave (written up 2026-05-20)

The foundational engine, stats, economy, and realism work, documented in one wave.
Highlights (full set in [`docs/`](.)):
- Engine: [stay / 2C reframe + shifts](aar-2c-reframe-and-shifts.md),
  [defense model](aar-defense-model.md),
  [baserunning + run-game events](aar-baserunning-and-run-game-events.md),
  [parks, managers, arsenal, physics](aar-ballparks-managers-arsenal-physics.md),
  [pitch types, defense, variance](aar-pitch-types-defense-and-variance.md),
  [pre-modern park revival](aar-pre-modern-park-revival.md),
  [inside-the-park HR + PBP](aar-inside-the-park-hr-and-pbp.md),
  [walk-back persistent runner](aar-walk-back-persistent-runner.md),
  [super-innings → 3-out extra innings](aar-super-innings-to-3-out-extra-innings.md).
- Stats: [sabermetric analytics suite](aar-sabermetric-analytics-suite.md),
  [pitcher stat suite](aar-pitcher-stat-suite.md),
  [BaseRuns + cluster luck](aar-base-runs-cluster-luck.md),
  [xRA + 2C + talent spread](aar-xra-2c-and-talent-spread.md),
  [SB recalibration + O27 sabermetrics](aar-sb-recalibration-and-o27-sabermetrics.md),
  [game-score normalization](aar-game-score-normalization.md),
  [decay follow-ups + tests](aar-decay-followups-and-tests.md).
- World: [guilder currency](aar-guilder-currency-system.md),
  [auction shape + sellback](aar-auction-shape-and-sellback.md),
  [live auction replay](aar-live-auction-replay.md),
  [motivation-driven trading](aar-motivation-driven-trading.md),
  [tiered leagues + youth + auction](aar-tiered-league-and-youth-and-auction.md),
  [playoffs, awards, scouting, xRA, aging](aar-playoffs-awards-scouting-xra-aging.md),
  [expanded rosters (Task #65)](aar-task-65-expanded-rosters.md).
- Realism: [sim realism layer](aar-sim-realism-layer.md),
  [weather + newspaper box score](aar-weather-and-newspaper-box-score.md),
  [newspaper box score + game positions](aar-newspaper-box-score-and-game-positions.md).
- The design argument: [O27 vs five-inning baseball](blog-o27-vs-five-inning-baseball.md).

### Phase 2 — Variance-first pivot (2026-05-17)

- **The defining decision: variance-first, no R/G calibration target.** Contact
  rebalanced, K-inflation pulled back, fatigue made the dominant pitcher axis
  (quadratic stamina), form variance widened. Run environment → ~33 R/G with wild
  variance, by design. [AAR](aar-scoring-variance-and-fatigue-dominance.md) ·
  [Handoff](../HANDOFF.md)

### Phases 0–1 — Genesis and the Replit build (early–mid May)

- **Genesis.** The 27-out conceit, the stay mechanic, jokers, the super-inning
  tiebreaker. [PRD](../attached_assets/Pasted--PRD-O27-A-baseball-cricket-hybrid-with-pes-pallo-seaso_1777752866752.txt)
- **The first working build (Tasks #1–#65).** Engine→stats pipeline, 30-team
  league, 162-game schedule, standings/W-L, injuries, trades, waivers, the guilder
  economy, and live pitcher roles. [May 3 handoff](HANDOFF-archive-2026-05-03.md)

---

*Maintained alongside the README. When a new system lands, add its AAR to the
changelog here so the trajectory stays current — that's the point of the doc.*
