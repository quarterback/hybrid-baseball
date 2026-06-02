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

## Honest limitations (flagged in-page and here)

- **WP is approximate** — pooled across both halves (no persisted bat-first
  flag), so read leverage/WPA as directional, not exact.
- **Fielding zone attribution is heuristic** — no batted-ball coordinates, so
  the responsible position comes from LA band + spray side, mapped to the
  team's positional regular (ignores in-game subs/multi-position).
- **Pitch-arsenal run value is contact-only** (pa_log logs BIP, not whiffs).
- **Expected stats** denominator is AB; xwOBAcon is per-contact by design.

These are all data-availability limits, not bugs; each is a clean upgrade path
if we ever persist fielder_id / bat-first / pitch-level events.

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

- If we ever persist `fielder_id` + a bat-first flag, OAA and WP become exact.
- The advanced builders re-scan pa_log per player-page load (consistent with
  the existing percentile scans, but a shared per-request cache would help if
  the page gets hot).
