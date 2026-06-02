# After-Action Report — Baseball-Savant percentile player page (Phase 1)

**Date completed:** 2026-06-01
**Branch:** `claude/peaceful-thompson-ZB5UY`
**Scope:** `o27v2/web/app.py`, `o27v2/web/templates/savant.html`,
`o27v2/web/templates/player.html`.

---

## TL;DR

The first Savant surface for O27: a `/player/<id>/savant` page with the iconic
red→blue **percentile slider bars**, plus a wOBA / xwOBA / luck callout. It
reuses the existing `_percentile_ranks()` and `build_xwoba_table()` machinery,
so it's mostly re-presentation. This is the payoff of the physics-first
inversion — exit velocity now *drives* the outcome, so Hard-Hit%, Barrel%, avg
EV and the xwOBA−wOBA gap are real signal, not an echo of a categorical roll.

## What it shows

Per-batter, ranked league-wide (scoped to the selected independent league):
xwOBA, Avg/Max Exit Velo, Hard-Hit%, Barrel%, Sweet-Spot%, Walk%, Strikeout%
(reversed — lower is better), and the O27-native Stay%. Each is a slider whose
marker sits at the player's percentile (red = elite, blue = poor), with the raw
value alongside. A header card shows wOBA vs xwOBA and the signed luck gap.

## Implementation notes

- **`_savant_batter_rows(team_ids, min_bip)`** builds the metric rows for every
  qualified batter from `game_pa_log` (EV/LA) + `game_batter_stats` (PA/K/BB/
  stays) + `build_xwoba_table`, then stamps `<key>_pctile` via the existing
  `_percentile_ranks()`. Computing the whole league makes the bars relative.
- **Qualifier** scales to season completeness like `/analytics`:
  `min_bip = max(15, games_played // 30)`. Non-qualifiers get an explanatory
  alert instead of bars.
- **O27-calibrated cuts**, NOT MLB's: Hard-Hit EV≥100, Barrel EV≥104 & LA
  10–35°, Sweet-Spot LA 8–32°. These are exposed as module constants. (Per the
  feasibility AAR, MLB's 95-mph anchor doesn't transfer; the *percentile* makes
  the bars robust regardless, but the absolute % is now sane for O27.)
- Slider colors via inline HSL on the percentile (hue 240→0), so red marks
  elite — matching Savant's convention.
- Linked from the player page header (`Savant` button) and `?format=json` works
  through the standard `_serve()` path.

## Validation

Flask test-client against a 200-game seed:
- JSON 200 — a top batter showed xwOBA .807 (96.9th pctile, rank 10/294),
  avg EV 95.6, max EV 119, Hard-Hit 39.6%, Barrel 16.7%, Sweet-Spot 70.8%,
  K% rank reversed correctly — all sliders sane.
- HTML 200 with slider markers + the wOBA/xwOBA luck card.
- Non-qualifier (insufficient BIP) → the alert, not a broken page.
- Player page renders with the new Savant link; engine suite still 90 green.

## Follow-ups (later Savant phases)

- xwOBA here still uses the contact-quality (weak/med/hard) bins from
  `expected_woba.py`. Now that EV/LA drive outcomes, an EV/LA-binned xwOBA would
  be more honest — a natural next step.
- Pitcher percentile panel (the page is batter-only today).
- Phase 2: a `/leaderboard/statcast` page (Barrel%/Hard-Hit%/EV leaders).
- Phase 3: season spray chart + EV/LA bin grid on the page.
- Phase 4: global search box + a `/savant` landing page.
