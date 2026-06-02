# After-Action Report — Savant Phases 2–4 (leaderboard, pitcher panel, EV/LA xwOBA, landing)

**Date completed:** 2026-06-01
**Branch:** `claude/peaceful-thompson-ZB5UY`
**Scope:** `o27v2/web/app.py`, `o27v2/analytics/expected_woba.py` (+ `__init__`),
`o27v2/web/templates/{statcast_leaderboard,savant,savant_home}.html`, `base.html`.

---

## TL;DR

Completed the Savant build-out on top of the Phase-1 percentile page, in the
order the owner requested (season spray charts deferred):

1. **Phase 2 — `/leaderboard/statcast`**: sortable batted-ball leaderboards
   (xwOBA, avg/max EV, Hard-Hit%, Barrel%, Sweet-Spot%, BB%, K%, Stay%), reusing
   `_savant_batter_rows` (now also stamped with player name/team). Linked from
   the Stats nav.
2. **Pitcher percentile panel**: `_savant_pitcher_rows` (EV-against, Hard-Hit/
   Barrel allowed, K%, BB%, HR%) with suppression-as-elite reversed percentiles;
   the player page now shows Hitting and/or Pitching panels per qualification.
3. **EV/LA-binned xwOBA**: `build_xwoba_ev_table` — expected wOBA from the
   league-average value of each (exit-velocity, launch-angle) bin, Statcast's
   actual method. The Savant page + leaderboard use it.
4. **Phase 4 — `/savant` landing**: a player search bar (datalist → percentile
   page) plus five EV/contact leader snapshots; nav points "Savant" here.

## Why EV/LA xwOBA matters now

With the physics-first inversion, the (EV, LA) trajectory *drives* the outcome,
so binning expected value by trajectory is finally meaningful. Validation: the
luck-gap (wOBA − xwOBA) stdev **tightened from 0.154 (quality buckets) to 0.107
(25 EV/LA bins)** — the finer surface explains more of actual production, which
is exactly what a better expected-stat should do. League xwOBA is unchanged
(both calibrate to the league total), confirming it's a re-attribution, not a
shift.

## Validation (Flask test client, 200-game seed)

- `/leaderboard/statcast` — sorts by every metric incl. K% ascending; HTML +
  JSON 200. Leaders sane (Barrel% leader 43.8%, avg-EV leader 105.7).
- Pitcher panel — pitcher rendered Avg EV Against 85.0 (88.5th pctile = elite
  suppression), Barrel 5.3%, K% 22.2%; reversed percentiles correct.
- `/savant` — 200; 296 qualified batters, 2,016-player search datalist, all five
  leader cards populated; search box + nav link verified on every page.
- Engine suite unaffected (90 green); these are web/analytics-only changes.

## Follow-ups

- **Deferred (owner):** season spray charts + EV/LA bin grid on the player page.
- Pitcher xwOBA-against (the pitcher panel is EV/contact + rate stats today).
- The nav search is the `/savant` home datalist; a truly global (every-page)
  search box was intentionally not added to avoid embedding the player list on
  every page.
