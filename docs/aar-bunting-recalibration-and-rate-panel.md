# After-Action Report — Bunting recalibration + rate analytics panel

**Date completed:** 2026-06-08
**Branch:** `claude/gracious-hopper-ltjrbc`

---

## TL;DR

Two questions, both grounded in a FanGraphs piece on the 2026 bunt resurgence:
(1) is O27's bunting worth tuning further, and (2) are bunt analytics worth
surfacing. The honest answer to both was yes — but the framing matters. O27 is
**not** chasing MLB numbers (27-out halves, 12-batter speed/contact lineups,
and — crucially — **pitchers still hit, no DH**). The article's universal-DH
0.9%-of-PA benchmark is the wrong barometer; bunting was always more common when
pitchers batted. Still, the sim's numbers were "wildly imbalanced," and a
300-game measurement bore that out.

**Before (300 Foxes-vs-Bears games):**

| Metric | Before | After | Real-MLB-2026 (barometer only) |
| --- | --- | --- | --- |
| Bunt rate (% of PA) | 4.9% | **3.5%** | 0.9% (universal DH) |
| Bunt → hit rate | 12.3% | **27%** | ~33% |
| Squeeze share of bunts | 41% | **10%** | small slice |
| Sacrifice share of bunts | 60% | **47%** | ~42% "moved over" |
| Pitcher bunt rate | **0%** | **~17%** | (pitchers were the classic bunter) |

The biggest structural fix: **the engine had pitchers *never* bunt**
(`should_bunt` returned `None` for `is_pitcher`), so in a pitcher-hitting league
the one hitter who *should* carry the sacrifice load did none of it, while
position players over-bunted at ~5%. That distribution was backwards. Pitchers
now lay down sacrifices; position-player bunting drops to ~2.7% and tilts toward
bunt-for-hit (the one bunt that meaningfully reaches base).

The second deliverable is a **"Bunting rates" panel on `/analytics`** — the rate
framing (bunt%PA, bunt-hit rate, sac/squeeze share, productive%, pitcher-vs-
position split, per-team table) that the raw box-score totals never exposed. It
doubles as the instrument that makes this kind of miscalibration visible next
time.

---

## What was asked for

The user landed on a clear, two-message brief:

1. **Tune the calibration** — not a blanket cut. Specifically: *raise the
   bunt-hit rate, lower the squeeze rate, lower the sacrifice rate*, with overall
   volume coming down somewhat.
2. **Let pitchers bunt** — O27 has no DH, and the weak-bat pitcher is the
   realistic sacrifice bunter; that's *why* bunting was historically more common
   when pitchers hit.
3. **Build a rate analytics panel.**

MLB benchmarks were explicitly *not* the goal — "a helpful barometer of how
far/less we are against realistic-ish baseball representation."

---

## The engine model (where the knobs live)

- Decision + outcome rolls: `o27/engine/manager.py:1927-2075` (`should_bunt`,
  `_roll_sacrifice`, `_roll_drag`, `_roll_squeeze`).
- Tunable constants: `o27/config.py:769-808`.
- Stat recording: `o27/render/render.py:1628-1664` — `bunt_att`, `bunt_hits`
  (`hit`/`squeeze_score_hit`), `sh` (`sacrifice`/`squeeze_score`), `sqz`,
  `sqz_rbi`.

## What changed — calibration

**Config (`o27/config.py`):**

| Constant | Old | New | Why |
| --- | --- | --- | --- |
| `SAC_BUNT_BASE_PROB` | 0.16 | 0.085 | position players sac'd ~5× too often |
| `SAC_BUNT_HIT_BASE` | 0.10 | 0.21 | good bunts should reach base; 0.10 made them near-useless |
| `SAC_BUNT_HIT_SPEED_SCALE` | 0.30 | 0.36 | reward speed on the beat-it-out leg |
| `DRAG_BUNT_BASE_PROB` | 0.06 | 0.095 | tilt the mix toward bunt-for-hit |
| `DRAG_BUNT_SPEED_GATE` | 0.55 | 0.52 | slightly wider drag pool |
| `DRAG_BUNT_HIT_BASE` | 0.30 | 0.47 | realistic drag-single clip; lifts overall hit rate |
| `SQUEEZE_BASE_PROB` | 0.07 | 0.025 | squeezes were ~40% of all bunts — wildly over-represented |
| `PITCHER_SAC_BUNT_BASE_PROB` | — (new) | 0.34 | the pitcher's own elevated sac rate |

**Manager (`o27/engine/manager.py`):**

- Removed the `is_pitcher → return None` gate. Pitchers now route to a dedicated
  branch: with a runner on first and outs to spare, they lay down a standard
  sacrifice (margin-aware), and they *only* sacrifice — never drag (too slow),
  never squeeze (rarely asked to execute under pressure).
- Squeeze call probability: the `run_game * 2.0` multiplier dropped to
  `run_game * 1.0` and the cap from 0.30 to 0.12, to match the cut base rate.

## What changed — analytics panel

- New module `o27v2/analytics/bunting.py` → `build_bunting_rates(team_ids=None)`.
  Aggregates the persisted counters from `game_batter_stats` (regulation,
  regular-season) into league totals, a pitcher-vs-position split, and a
  per-team breakdown sorted by bunt rate. Returns plain dicts (JSON-serialisable,
  matching the other analytics builders). Exported from
  `o27v2/analytics/__init__.py`.
- Wired into the existing `/analytics` route (`o27v2/web/app.py`) and rendered as
  a new "Bunting rates · league barometer" panel in
  `o27v2/web/templates/analytics.html` (headline rate cards + split table +
  per-team table). Scoped to the selected league like every other panel.

Rate definitions (identical to those used to calibrate the engine, so the panel
and the tuning speak the same language):

- bunt rate = `bunt_att / pa`
- bunt-hit rate = `bunt_hits / bunt_att`
- sac share = `sh / bunt_att` (`sh` also counts squeeze runs that scored)
- squeeze share = `sqz / bunt_att`
- productive = `(bunt_hits + sh) / bunt_att`

I deliberately surfaced this as a **panel on `/analytics`** (a league barometer)
rather than a per-player leaderboard. The article — and the user's question — is
about *rates and mix*, not naming a bunt champion. It's also the lowest-risk
surface: no new route/nav/template wiring in an 7,800-line `app.py` I can't run
in this sandbox (no flask).

---

## Validation

- **Engine suite:** `pytest o27/tests` → **129 passed**. The one test that
  failed was `test_pitcher_never_bunts`, which encoded the *old* behavior we
  deliberately reversed; it's been replaced by
  `test_pitcher_sacrifices_with_runner_on_first` +
  `test_pitcher_does_not_drag_or_squeeze`.
- **New analytics tests:** `o27v2/tests/test_bunting_rates.py` → **7 passed**
  (pure-math rates, league totals excluding playoff/non-reg rows, pitcher/
  position split, per-team sort, league scoping, empty-DB zeros). Verified
  against a synthetic temp DB — no flask.
- **Adjacent o27v2 analytics suites** (`test_linear_weights`,
  `test_streaks_records`, `test_engine_config`) → pass.
- **Template:** `analytics.html` compiles under Jinja2.
- Measurement harness: 300 games, Foxes vs Bears, aggregating
  `Renderer._batter_stats`.

### What I did *not* do / honest caveats

- **The measurement is a single matchup.** The Foxes are explicitly
  "high-contact, speed-oriented," which inflates drag rate and bunt-hit rate. A
  power-tilted roster would naturally show fewer drags, a lower bunt-hit rate,
  and a higher sac share. The *direction and magnitude* of the gaps (5× bunt
  rate, 41% squeezes) are far too large to be roster bias, but the exact landing
  numbers above should be read as "this matchup," not "the league." A
  representative randomized-roster harness would tighten this; there's no live DB
  in this sandbox to sample from.
- I did **not** build the article's headline metric, *leveraged run value per
  100 bunts*. It's feasible on the existing `o27v2/analytics/run_expectancy.py`
  RE24-O27 table (join bunt events from `game_pa_log` against RE-before/after),
  but it's a meaningfully bigger lift and was scoped as a fast-follow, not part
  of this pass.
- Sacrifice share (47%) is still on the high side of the target band. That
  residual is now mostly *pitcher* sacrifices — the realistic dynamic we just
  (correctly) added — so I stopped there rather than overfit the knobs to one
  speed-tilted sample.
