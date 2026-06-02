# After-Action Report — expanded metric suite + Prospect Index

**Date completed:** 2026-06-02
**Branch:** `claude/peaceful-thompson-ZB5UY`
**Scope:** `o27v2/analytics/expanded.py` (+ `__init__`), `o27v2/web/app.py`,
templates `o27i_advanced.html`, `college_prospects.html`, `base.html`.

---

## TL;DR

"Do it all" on expanded stats — eight new metric families, **all derived from
data already persisted** (game_pa_log state stamps + EV/LA/spray, the
batter/pitcher aggregates, the runner-advance tracking, and the college
scouting tables). No engine or schema change; I confirmed the schema already
carried everything (`outs/bases/score_diff_before/after`, `pitch_type`,
EV/LA/spray, `adv_op_*`/`adv_adv_*`, `*_tto*`, `po/a/e`).

## What shipped

**`/o27i/advanced` (seven leaderboards):**
- **Expected stats** — xBA / xSLG / xwOBAcon from (EV, LA) bins (true xBA/xSLG
  over AB so strikeouts count).
- **WPA + Leverage Index** — empirical win-expectancy table from this league's
  games (P(batting team wins | score_diff, outs, bases)); per-batter and
  per-pitcher WPA, average LI. *The Book*, finally.
- **Pitch arsenal** — RE24-based run value per 100 BIP by pitch type (contact
  quality by pitch; whiffs aren't in pa_log).
- **Baserunning** — Extra-Bases-Taken% + a baserunning run value from the
  advance-opportunity tracking, steals and outs.
- **Times-through-order K% penalty** — and the headline finding: O27's single
  27-out half shows **essentially no TTO penalty** (league 22.7 → 23.6 → 23.5%).
- **Second-Chance run value** — O27-native: each 2C event's RE24 run value, with
  the league mean as the break-even bar. Nobody else can have this metric.
- **Fielding OAA / Fielding Run Value** — league out-rate per (EV, LA) bin gives
  each ball a catch probability; balls are attributed to the fielding team's
  regular at the trajectory-implied position; OAA = Σ(out − expected).

**`/college/prospects` — the Prospect Index (ProspectSavant analog):**
Tools + potential + production percentiles for the college tier, with a
composite **Prospect Score** that rewards youth (a freshman at equal grades
ranks higher). Hitters and pitchers, sortable, linking to each prospect's
college page. Top hitter scored 98.4 (OPS 1.013, 97th-pct power); top pitcher
99.4 (28% K).

**Per-player drill-downs (follow-up commit):**
- **Advanced card on the O27i player page** (`/player/<id>/o27i`) — WPA +
  leverage, fielding OAA/runs, BsR/XBT%, and Second-Chance run value for the
  single player. Computed by calling the expanded builders with `min=1` (they
  build league context regardless of threshold, so this just guarantees the
  player appears). Hitters show offensive WPA, pitchers run-prevention WPA.
- **Per-prospect slider page** (`/college/prospects/<id>`) — the O27i red→blue
  percentile sliders applied to the college tier, with a Prospect Score badge
  and league rank. The PS bubble on the board links to it.

## Persistence upgrade (follow-up commit) — exact OAA + WP

Persisted the data that previously forced approximations (**new games only**;
legacy rows fall back automatically):

- **`game_pa_log.fielder_id`** — the engine's PO-credited fielder on outs,
  threaded from the outcome dict in `render.py` (no RNG consumed, so seed
  determinism holds — verified by the 90-green engine suite). Fielding OAA now
  attributes outs **exactly** (PO-consistent) and only falls back to the
  trajectory-zone heuristic for balls that fell in. On a 200-game seed, ~41% of
  chances are exactly attributed; the page shows the live `exact_pct`.
- **Bat-first half ordering** — no new column needed: `games.home_bats_first`
  was already persisted. WPA/Leverage now key the empirical win table on
  *(second-half?, score_diff, outs, bases)*, respecting O27's two sequential
  27-out halves instead of pooling them.

The migration is the usual additive `ALTER TABLE … ADD COLUMN` (mirrors the
EV/LA columns); existing DBs upgrade in place with NULL fielder_id and use the
heuristic until re-simmed.

## Remaining limitations

- **Fielding on balls that fell in is still heuristic** — no batted-ball
  coordinates, so a hit's responsible position comes from LA band + spray side
  → the team's positional regular. (The engine attributes a fielder only on
  outs; adding hit attribution would consume RNG and break determinism.)
- **Pitch-arsenal run value is contact-only** (pa_log logs BIP, not whiffs).
- **Expected stats** denominator is AB; xwOBAcon is per-contact by design.

## Validation

- All seven `expanded.py` builders return sane leaders on a 300-game seed
  (verified values: WPA top ~2.7 hitters / ~2.9 pitchers; OAA top +11.2; 2C RV
  league 0.43 R/2C; xBA leaders ~.61–.67 in O27's high-offense env).
- `/o27i/advanced` renders all 7 sections; `/college/prospects` renders both
  hitter and pitcher boards (seeded a college season to test).
- Advanced card renders on hitter and pitcher O27i pages (pitcher verified at
  −2.02 WPA / 1.03 LI); per-prospect slider page renders for hitters and
  pitchers and 404s on bad IDs.
- Nav links added; engine suite still **90 green** (analytics/web-only).

## Follow-ups

- Exact OAA on *hits* would need batted-ball coordinates (or a deterministic,
  RNG-free fielder attribution computed in the renderer post-outcome).
- The advanced builders re-scan pa_log per player-page load (consistent with
  the existing percentile scans, but a shared per-request cache would help if
  the page gets hot).
- Optional `manage.py` backfill that replays played games from their stored
  seeds to populate `fielder_id` on existing rows (we chose new-games-only).
