# After-Action Report — "Baseball Savant for O27": design + data sanity check

**Date completed:** 2026-06-01
**Branch:** `claude/peaceful-thompson-ZB5UY`
**Status:** Research / feasibility audit — **no app code shipped yet.** This
report exists to answer "*can* we build it, and *where*" before any route or
template is written.

---

## TL;DR

The ask was to build an O27 analog of [Baseball Savant](https://baseballsavant.mlb.com/)
— MLB's Statcast hub, whose signature surfaces are (1) **percentile-slider
player pages**, (2) **expected stats** (xwOBA/xBA/xSLG), (3) **Statcast
leaderboards** (Barrel%, Hard-Hit%, Sweet-Spot%, EV), and (4) **batted-ball
spray visuals**.

Two findings:

1. **The data is there.** `game_pa_log` already stamps
   `exit_velocity` / `launch_angle` / `spray_angle` on **100% of balls in
   play** (6,155 BIP rows over a 120-game seed), and `o27v2/analytics/`
   already contains an xwOBA engine, RE24, and percentile-distribution math.
   "Build Savant" is mostly **re-presentation**, not new data capture.

2. **The data is synthetic, and that bounds what the metrics can mean.** The
   EV/LA/spray triple is sampled by `o27/engine/batted_ball.py` *from the
   categorical outcome* (contact quality + hit_type + power rating) — it does
   **not** drive fielding. So Savant-style metrics built on it are a
   well-behaved **re-encoding of the categorical engine plus the power
   rating**, not an independent measurement layer. Leaderboards will rank
   hitters sensibly (avg-EV correlates **0.66** with the power rating and
   **0.68** with HR output); but an "expected stats / luck" page cannot reveal
   over/under-performance the way MLB's does, because the physics here *are*
   the outcome, not a separate observation of it. Build the leaderboards and
   slider pages; be honest (or skip) on the "expected vs actual luck" framing.

---

## What was asked for

> "how do i build baseball savant for O27? look it up first."
> "wrte the aar and do the sanity check yes"

So: research what Savant is, map it onto O27's architecture, and **validate
that the batted-ball data can actually carry Statcast-style metrics** before
committing to a build.

## What Baseball Savant actually is

From the [player-page redesign writeup](https://www.mlb.com/news/baseball-savant-statcast-player-pages-new-look)
and the live site:

- **Percentile rankings** with red→blue **slider bars** per metric — the
  iconic player-page element ([percentile-rankings](https://baseballsavant.mlb.com/leaderboard/percentile-rankings)).
  Pitchers also get fastball / breaking / offspeed pitch value.
- **Expected statistics** — xwOBA, xBA, xSLG vs. actual ([expected_statistics](https://baseballsavant.mlb.com/leaderboard/expected_statistics)).
- **Statcast leaderboards** — Barrels, Hard-Hit%, Sweet-Spot%, avg/max EV,
  HR distance ([custom leaderboards](https://baseballsavant.mlb.com/leaderboard/custom)).
- A global **search bar** + interactive charts ([savant home](https://baseballsavant.mlb.com/)).

## How O27 maps onto it (where each piece already lives)

| Savant surface | O27 equivalent / raw material | File |
| --- | --- | --- |
| Percentile slider bars | `/distributions` already computes per-stat league histograms **and an individual's percentile** — this is the slider math, just not drawn as sliders | `o27v2/web/app.py` (`distributions()`), templates `distributions.html` |
| Expected stats (xwOBA) | contact-quality-binned xwOBA engine already exists | `o27v2/analytics/expected_woba.py` |
| Batted-ball spray | game-level spray chart with park-fence overlay already drawn from EV/LA/spray | `o27v2/web/app.py` (game spray block), `o27/engine/park_effects._proxy_distance()` |
| Statcast leaderboards | derive Barrel%/Hard-Hit%/Sweet-Spot% from `game_pa_log` BIP rows | new aggregation; pattern: `o27v2/web/app.py` `leaders()` |
| Pitch-type value | `game_pitcher_stats.fastball/breaking/offspeed_pct`, `game_pa_log.pitch_type` | `o27v2/db.py` |
| Glossary / definitions | add new metric defs | `o27v2/web/glossary.py` |

Per `CLAUDE.md`: **all of this goes in `o27v2/web/` (routes/templates) and
`o27v2/analytics/` (math)** — never `o27/web/`.

## The sanity check (the part the user asked for)

Method: `initdb` + `sim 120` into a throwaway DB
(`O27V2_DB_PATH=/tmp/savant_sanity.db`), then queried `game_pa_log` directly.

**Coverage.** 6,155 BIP rows, EV/LA/spray populated on **100%** of them. No
sparsity problem.

**League distributions** (synthetic, by construction):

| metric | p10 | median | mean | p90 | sd |
| --- | --- | --- | --- | --- | --- |
| Exit velocity (mph) | 73.3 | 91.3 | 90.8 | 107.4 | 13.0 |
| Launch angle (°) | −5.8 | 17.1 | 16.4 | 37.1 | 16.0 |
| Spray (°) | −27.6 | −3.9 | −3.2 | 21.8 | 19.0 |

**Does the physics track the outcome?** Yes, cleanly and monotonically:

- By contact quality: weak 72.3 → medium 88.3 → hard 104.2 mph.
- By hit_type: HR 109.3 / triple 105.3 / double 97.7 → ground_out 79.7 /
  infield_single 70.2 mph.

**Do MLB thresholds transfer? Partly — recalibrate.**

- Hard-Hit% at EV≥95 = **38.9%** (MLB ~35–40%) — lands in range, but *only
  because the generator's means were MLB-tuned*, not because it was validated.
- Sweet-Spot% at LA 8–32° = **53.8%** vs MLB ~33% — **inflated**; the LA
  distribution is too concentrated. Thresholds must be refit to the O27 league,
  exactly as `o27v2/analytics/linear_weights.py` already refits MLB anchors.

**Do player-level aggregates separate hitters?** Yes (n=53 hitters, ≥20 BIP):

- avg-EV spreads **78.1 → 102.8 mph** (sd 5.46 across players).
- **corr(avg-EV, power rating) = 0.661**
- **corr(avg-EV, HR count) = 0.677**

So a percentile-slider page and EV/Barrel leaderboards will rank players in a
way that meaningfully reflects talent and production.

## The honest caveat

Because `batted_ball.py` samples EV/LA/spray *downstream of* the categorical
outcome (it explicitly states it "do[es] NOT drive the fielding outcome"), the
synthetic layer carries the **power rating + outcome** signal and little
independent information. Consequences for the build:

- ✅ **Slider pages, EV/Barrel/Hard-Hit leaderboards, spray charts** — all
  legitimate. They re-present real talent/outcome signal in Savant's idiom.
- ⚠️ **"Expected stats / luck" (xwOBA−wOBA gaps)** — structurally muted here,
  since the inputs are derived from the same outcome they'd be "predicting."
  Either present xwOBA as a descriptive contact-quality summary (not a luck
  detector) or omit the actual-vs-expected delta framing. Don't imply Statcast
  luck-correction we can't actually deliver.
- ⚠️ **Every threshold (Barrel, Hard-Hit, Sweet-Spot) must be O27-calibrated**
  off the league distribution, not copied from MLB.

## Recommended build order (not yet executed)

1. **Savant player page** `/player/<id>/savant` — percentile sliders reusing
   the `/distributions` percentile logic. Highest visual payoff, lowest new
   math. (red→blue slider = a small reusable SVG/CSS macro, percentile→x.)
2. **Statcast leaderboard** `/leaderboard/statcast` — Barrel%/Hard-Hit%/
   Sweet-Spot%/avg-EV/max-EV from `game_pa_log`, **with O27-calibrated cuts**.
3. **Season spray + EV/LA bin grid** on the player page.
4. **Glue** — global search box in `base.html`, `/savant` landing, glossary
   entries.

## What I did NOT do

- Did not write any route, template, or analytics module — this is feasibility
  only.
- Did not modify `batted_ball.py` or the engine; the synthetic-layer behavior
  described is its documented, intended design.
- Did not validate thresholds beyond the single 120-game seed; a larger sample
  (and a per-archetype breakdown) should precede shipping leaderboards.
