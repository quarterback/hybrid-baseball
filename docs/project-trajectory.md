# O27 — Project Trajectory

A narrative of how O27 went from a tweet-sized idea to a fully simulated sport in
roughly a month, plus a dated changelog of everything that landed. The README is
the snapshot — *what exists now*. This is the arc — *how it got here, and why each
turn was taken*. New to the project and not sure where to start? Read the
[user guide](user-guide.md) first; this doc is the history behind it.

The design history in detail lives in the ~100 After-Action and feature reports
under [`docs/`](.). Each AAR records the ask, the tradeoffs, the verification
numbers, and the honest open gaps. This document is the reading order through
them: the spine that the AARs hang off.

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

### Phase 5 — Depth, debugging, and onboarding (May 31 → June 1)

The turn of the month deepened the engine and, for the first time, turned the
project's attention outward — toward a reader coming in cold and toward steering
the sim in plain language.

On the engine side: the run-game got more texture with the
[double-play band raised and triple plays revived](aar-double-triple-play-rates.md),
a [scorecard audit that hardened the per-cycle joker cooldown](aar-scorecard-audit-and-joker-cooldown.md),
a [defense / battery / runs-hits decoupling pass](aar-defense-battery-and-runs-decoupling.md),
and [randomized weather plus location-based first-pitch times](aar-randomized-weather-and-start-times.md)
replacing the old fixed per-tier values. The Power Play got three more segments of
operator-driven debugging —
[why the nickel never deployed](aar-power-play-deploy-and-eligibility-segment.md)
and [why it wasn't showing in the box-score notes](aar-power-play-box-score-notes-segment.md) —
the kind of close-the-loop work that only surfaces once a real operator is running
the live site.

Then three larger systems landed on June 1. The pitching model was reframed around
a [**nautical crew**](feature-pitching-crew-roles.md): instead of role-less,
fully-live-derived arms, a staff now carries skill-derived, team-relative roles
(Helms, the Changes, Bosun, Skidder, Anchor, Pilot) that re-derive on any staff
change but still flex live by fatigue, Stuff, and matchup. A whole
[**college tier**](aar-college-tier.md) — 64 NCAA-inspired programs with their own
schedule, postseason, potential-growth model, and a draft/sign pipeline into the
pro league — gave the youth system a feeder above it. And the
[**Gazette**](feature-gazette.md) shipped: a read-only, LLM-ready news desk that
turns a day's slate into a steerable, voiced writeup with no engine or schema
changes.

Crucially, June 1 is also when the project started explaining itself. The
[**LLM tuning guide**](aar-llm-tuning-guide-and-softball-scoring-presets.md) (a
self-contained [`tuning-guide-for-llms.md`](tuning-guide-for-llms.md) you paste
into any capable model to generate Engine-Tunables blobs from a plain-English
style) plus softball-derived scoring presets made the sim *steerable by anyone*.
This [user guide](user-guide.md) and the expansion of this trajectory doc are part
of the same turn: the build was far enough along that the bottleneck stopped being
features and started being onboarding.

### Phase 6 — The spectator economy and the rebrand (June 2 → June 6)

The early-June burst — roughly a hundred commits across three days — is the turn
where the project stopped being only an instrument and grew the apparatus that
makes a real sport a *sport*: an audience around it. The premise was renamed in the
process — the product is now **Super Innings — An O27 Baseball Simulator**
(`superinnin.gs`) — and the engine's user-facing language was scrubbed to match the
variant, with "innings" purged from pitching (scoreless streaks and longest outings
measured in **outs**), the second-chance/"stay" jargon pulled out of batting lines,
and a save-equivalent **Finisher (F)** stat added to scorecards.

The two big new layers are both, deliberately, *read-only consumers of the sim's
persisted truth* — neither ever re-simulates. [**CapSpace**](aar-capspace-games.md)
is a fantasy-and-betting metagame (~7,300 lines under `o27v2/web/fantasy/`): six
modes — a Walk-Back home-run game, a Finisher-based pitching game, daily-fantasy
slates, streak picks, 5×5 roto categories, draft-once best ball — plus an actual
[sportsbook](aar-capspace-realism.md) with moneylines and run totals, all staked
against a [real per-save wallet and bankroll economy](aar-capspace-bankroll.md)
with a Rookie→Hall-of-Famer career ladder. The lineup math is exact, not greedy:
best ball solves the optimal lineup by dynamic programming and validates draft
coverage with bipartite matching, because a fictional player has no name
recognition, so [recent form and real stats](aar-capspace-dfs-builder-overhaul.md)
are the only thing a DFS player can read. [**o27audio**](aar-agentic-audio-game-of-the-week.md)
is the broadcast booth: a standalone service where Claude scripts a two-host
play-by-play/color call from the persisted pitch-by-pitch log, a TTS model voices
the two announcers, and stdlib audio stitching renders a clip you press "Listen"
to hear on your phone — a per-game show in Stage 1, a league roundup and an
auto-generating worker in Stage 2.

The same window paid the unglamorous tax that earns the fun claims. A
[whole-page HTML result cache](aar-perf-html-result-cache.md), keyed on a cheap
data fingerprint and invalidated only on sim/trade, took the leaders page from
~2,548 ms to 2.3 ms (~1100×) — the lag was never the database (SQL ran in
milliseconds) but per-load recompute ballooning under contention — alongside
[hot-table indexes and request-scoped caching](aar-perf-indexes-and-request-caching.md),
self-hosted fonts and Bootstrap, and a dedicated Fly CPU. Two divergent algorithms
for pitcher W/L attribution that disagreed on 30 of 120 finals were
[collapsed into one canonical path](aar-capspace-games.md) (0 mismatches). The
[org-strength grade stopped being a random seed](aar-org-strength-live-roster-proxy.md)
and is now recomputed live from the active roster, and
[estimated value was split from salary](aar-player-value-vs-salary.md) so contract
bargains and overpays finally show. And because the owner plays mostly on a phone,
a [mobile-responsiveness pass](aar-mobile-universe-builder-overflow.md) audited ~50
templates and [finally gave the almanac media queries](aar-almanac-mobile-responsive.md)
it had never had.

### Phase 7 — Native-ification and reconciliation (June 6 → June 7)

The most recent turn is not a feature burst — it's a **maturity pass on the
engine and the stat surface**, driven by a single thesis the owner had been
circling for weeks: *a fictional sport must generate its own ground truth and
then measure it; borrowing baseball's constants is a category error.* Where the
spectator economy (Phase 6) grew the apparatus *around* the sim, this phase went
back into the sim's measurement layer and made it honest about itself. A
[dedicated cross-section of the engine's iterations](comparison-sim-iterations.md)
covers this in full; the spine is four coordinated moves.

The big mechanic change was the [**2C rework**](aar-2c-hitting-engine-rework.md):
the second-chance "stay" used to force `batter_safe = True` on every valid stay,
credit a flat single regardless of contact, and run with *no cap* — a hot batter
could stay forever. It now resolves through the **real hitting engine** (a 2C can
come out a double, a triple, or an out), is **EV-driven and skill-differentiated**
(stay-rate climbs poor 16% → elite 55%, jokers leverage it most), and caps at
**three batted balls** per AB. This [reverses prior documented
design](design-2c-hitting-engine-rework.md) — and the AAR says so in a warning
box, because the design history is supposed to record reversals, not hide them.

The other three moves replaced **imported MLB constants** with O27-native,
self-derived values. Baserunning value was the worst offender — MLB's `+0.25` per
extra base [*penalized fast runners*](aar-bsr-o27-native-and-into-war.md) in a
27-out half (where a runner usually scores anyway, an extra base is worth ~0
runs); re-deriving it from O27's own run-expectancy surface flipped BSR's speed
correlation from −0.01 to +0.16. The [arc-weighted **wERA** was retired for
**xRA**](aar-wera-retirement-xra-headline.md) — "late runs hurt more" is an
artifact of innings, and O27 has none. And a
[**WAR/OAA reconciliation**](aar-war-oaa-reconciliation-koalas.md) closed a drift
where the season card read scout-grade DRS while the Savant page showed
event-based OAA — two defensive pipelines that never met — by feeding the
de-biased, reliability-regressed Field Runs (and BSR) into WAR and adding an
invariant that the *surfaced* component must equal the *summed* one
([deep-dive](aar-stat-surface-reconciliation-comprehensive.md) ·
[fix](aar-war-oaa-fix-and-fielding-regression.md)). Supporting recalibrations —
[native wOBA scale](aar-native-stat-audit-and-advancement-correction.md),
[native league-baseline wOBA](aar-league-baseline-woba-native.md) (wRC+ re-centred
91 → ~100), the [Finisher credited as a reliever stat](aar-finisher-reliever-credit-fix.md) —
fell out of the same pass. The invariant suite grew 9 → **12**.

Alongside the stat work, the **design tool** gained one more composable optional
rule: [**Cricket Batting Order**](feature-cricket-batting-order.md) — a per-league,
off-by-default lever that lets a side flip its order 1-9 → 9-1, *earned* by a
joker-free trip and spent use-or-lose, so jokers and the flip trade off against
each other. Like Power Play before it, off means the engine is byte-for-byte
unchanged.

---

## Changelog

Grouped by phase, newest first. Each entry links to its AAR. Dates for the late-May
and June entries are the "date completed" recorded in the AAR; the earlier
foundational work was built across the Replit era and mid-May and written up in
the May 20 documentation wave.

### Phase 7 — Native-ification & reconciliation (June 6 → June 7)

Engine/stat-surface maturity pass — stop borrowing MLB constants, make the
surfaces agree. Full cross-section in
[comparison-sim-iterations.md](comparison-sim-iterations.md).

**2026-06-07**
- **Cricket Batting Order** — optional, per-league, off-by-default rule: an
  earned, use-or-lose 1-9 → 9-1 lineup flip that trades off against jokers, with a
  derived `mgr_flip_aggression` persona and a flip-aware "valley" lineup.
  [Feature report](feature-cricket-batting-order.md) ·
  [scaffold](aar-cricket-batting-order.md) ·
  [manager decision](aar-cricket-batting-order-manager-decision.md) ·
  [flip-aware lineups](aar-cricket-batting-order-flip-aware-lineups.md)

**2026-06-06**
- **2C resolves through the real hitting engine** — EV-driven, skill-differentiated,
  max-3-batted-balls; a 2C can now be an extra-base hit or an out. Reverses prior
  "no cap / runner-advancement-only" design.
  [AAR](aar-2c-hitting-engine-rework.md) · [Spec](design-2c-hitting-engine-rework.md)
- **Baserunning value (BSR) made O27-native** — RE-derived run values replace MLB
  constants; BSR speed-correlation flips −0.01 → +0.16, then enters WAR.
  [AAR](aar-bsr-o27-native-and-into-war.md)
- **wERA retired → xRA** — dropped the arc-weighting fallacy; xRA anchored to
  league RA/27 exactly. [AAR](aar-wera-retirement-xra-headline.md)
- **WAR/OAA reconciliation ("surfaced ⇒ summed")** — de-biased + reliability-
  regressed Field Runs and BSR fed into WAR so the displayed component is the
  summed one; invariants 11–12 added.
  [Finding](aar-war-oaa-reconciliation-koalas.md) ·
  [Deep-dive](aar-stat-surface-reconciliation-comprehensive.md) ·
  [Fix](aar-war-oaa-fix-and-fielding-regression.md)
- **Native wOBA scale + native league-baseline wOBA** — wRC+ re-centres 91 → ~100;
  one O27-native scale shared by wRC+/VORP.
  [Audit](aar-native-stat-audit-and-advancement-correction.md) ·
  [Baseline](aar-league-baseline-woba-native.md)
- **Finisher (F) corrected to a reliever credit** — never the starter or the
  winning pitcher; 4-out floor; no finish on a complete game.
  [AAR](aar-finisher-reliever-credit-fix.md)

### Phase 6 — Spectator economy & rebrand (June 2 → June 6)

**2026-06-06**
- **Almanac made mobile-responsive** — header wraps instead of forcing horizontal
  scroll (PR #217). [AAR](aar-almanac-mobile-responsive.md)
- **Gazette** — second-chance (2C) totals dropped from standout batting lines
  (PR #219).

**2026-06-05**
- **o27audio** — agentic two-host AI broadcasts. Stage 1 in-app "Game of the Week"
  (PR #212); Stage 2 league roundup show + auto-generate worker (PR #214);
  parallelized TTS and a one-tap Daily Recap dashboard card (PRs #215, #216).
  [AAR](aar-agentic-audio-game-of-the-week.md)
- **CapSpace** — fantasy/betting metagame over persisted sim data: real per-save
  [wallet economy](aar-capspace-bankroll.md) (PR #197),
  [sports-career tiers](aar-capspace-bankroll.md) (PR #199),
  [DFS builder overhaul](aar-capspace-dfs-builder-overhaul.md) (PR #210),
  [Best Ball multi-slot coverage](aar-capspace-bestball-multipos.md) (PR #218),
  Go Streaking milestone pots (PRs #209, #213), and a
  [play-test fix pass](aar-capspace-playtest-fixes.md) (PR #208).
  [Games overview](aar-capspace-games.md) · [Sportsbook/realism](aar-capspace-realism.md)
- **Performance overhaul** — [whole-page HTML result cache](aar-perf-html-result-cache.md)
  (leaders ~2548 ms → 2.3 ms, ~1100×; PR #202),
  [hot-table indexes + request caching](aar-perf-indexes-and-request-caching.md),
  pitcher-stats composite index (PR #201), self-hosted fonts + Bootstrap (PR #200),
  dedicated Fly CPU (PRs #195, #202).
- **Pitcher decision attribution unified** — two divergent W/L algorithms collapsed
  into one canonical path (30/120 mismatches → 0; PR #198).
- **Org strength → live roster grade** — recomputed from the active roster + bench
  instead of a random seed (PR #207). [AAR](aar-org-strength-live-roster-proxy.md)
- **Est. value split from salary** — live market value with a surplus/deficit badge
  (PR #204). [AAR](aar-player-value-vs-salary.md)
- **Mobile-responsiveness pass** — ~50-template overflow audit, frozen-column and
  dark-mode-contrast fixes (PRs #205, #206).
  [AAR](aar-mobile-universe-builder-overflow.md)

**2026-06-03 → 06-05 (language & flavor)**
- **Rebrand to "Super Innings — An O27 Baseball Simulator"** (`superinnin.gs`).
- **"Innings" purged from pitching** — scoreless streaks and Longest Outing shown
  in outs, not IP (PR #211).
- **Finisher (F) stat** added to scorecards (O27's save-equivalent); Walk-Back-run
  stat wired into box scores and the career almanac.
- **DOS/SimCity-style boot preloader** (PR #203).

### Turn of the month — depth & onboarding

**2026-06-01**
- The **pitching crew** — skill-derived, team-relative nautical staff roles (Helms,
  Changes, Bosun, Skidder, Anchor, Pilot) replacing the role-less model.
  [Feature report](feature-pitching-crew-roles.md)
- **College tier** — 64 NCAA-inspired programs, schedule, postseason,
  potential-growth model, draft/sign pipeline into the pros. [AAR](aar-college-tier.md)
- The **Gazette** — read-only, LLM-ready, voiced news desk over the existing DB.
  [Feature report](feature-gazette.md)
- **LLM tuning guide + softball-derived scoring presets** — steer the run
  environment from a plain-English style.
  [AAR](aar-llm-tuning-guide-and-softball-scoring-presets.md) ·
  [Guide](tuning-guide-for-llms.md)

**2026-05-31**
- **Randomized weather + location-based first-pitch times** replacing fixed
  per-tier values. [AAR](aar-randomized-weather-and-start-times.md)
- Power Play, [deploy/eligibility debugging](aar-power-play-deploy-and-eligibility-segment.md)
  and [box-score notes](aar-power-play-box-score-notes-segment.md) segments.

**2026-05-30 (engine depth)**
- **Defense / battery / runs-hits decoupling** pass.
  [AAR](aar-defense-battery-and-runs-decoupling.md)
- **Double-play band raised + triple plays revived.**
  [AAR](aar-double-triple-play-rates.md)
- **Scorecard audit + hardened per-cycle joker cooldown.**
  [AAR](aar-scorecard-audit-and-joker-cooldown.md)

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
