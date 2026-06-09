# AAR — Standings: de-conflate divisions, add a wild-card view

## Problem

Division standings were being flattened into a league-wide list. On the live
`/standings` page each **league** rendered as a single `<table class="sortable">`
with divisions reduced to `<tr data-divider>` sub-header rows *inside* that one
table. Two consequences:

1. Clicking any column header sorted **every team in the league together**,
   silently reordering teams across division lines — the divisions stopped being
   real containers.
2. There was no within-division rank, and no way to see the wild-card picture
   (which non-division-winners are in line for an at-large berth).

The almanac (`o27/almanac/templates/standings.html.j2`) had the same shape: one
flat sortable/heatmap table with `Lg`/`Div` columns but no grouping, so a sort
or heatmap spanned all divisions at once.

Markdown export (`text_export.export_standings`) and the `?format=json` payload
were already correctly nested (`league → division → teams`), so they needed no
change.

## What changed

**`o27v2/web/app.py`**
- Added `_playoff_picture(leagues)` — builds the per-league wild-card picture from
  the *same* rule the bracket already uses (`o27v2.playoffs`): division winners
  qualify first, then the best of the rest fill the wild-card spots. The number
  of wild-card berths is `teams_per_league − division_count`, read from
  `playoff_settings()`, so it is **config-driven** — no hardcoded "3 divisions /
  6 seeds". An 8-team league (or any config where the playoff field ≥ the team
  pool) yields `n_wc = 0` and simply shows no wild-card panel.
- WCGB is computed relative to the cut: in-spot teams show games **up** on the
  first team out (`+x.x`); teams out show games **back** of the last team in.
- Helpers `_pct_f` / `_gb_value` for the float math.
- `/standings` passes `playoff_picture` (empty for tiered universes, which use
  promotion/relegation instead).

**`o27v2/web/templates/standings.html`** (non-tiered branch only — the tiered
promotion/relegation layout is untouched)
- Each division now renders as its **own card with its own `<table>`**, ranked
  1..N within the division and independently sortable. A sort on one division
  can no longer reorder another, and can never lift a better-overall-record team
  above another division's leader.
- Division leader row is highlighted (`◆`).
- New per-league **Playoff Picture** panel: a division-winners table (seeds clinch
  by finishing first) beside a wild-card race table with a visual cut line,
  `z`/`WC` badges, and WCGB.

**`o27/almanac/templates/standings.html.j2`**
- Flat table replaced with one table **per (league, division)**, grouped via
  Jinja `groupby` (rows arrive pre-sorted by `(league, division, -pct)`, so the
  rank column is correct). Each table keeps `class="stat-table heatmap"`, so the
  existing JS scopes sort + heatmap shading to that division. Redundant `Lg`/`Div`
  columns dropped (now in the headers); a `#` rank column added.
- The live "Download CSV" button (it only ever captured one table) was replaced
  with the pre-rendered full-dataset CSV link; the JSON bundle link is unchanged.
- GB was already computed within `(league, division)` in
  `compute._build_standings` — no compute change needed.

**`o27/almanac/static/almanac.css`** — subtle division-leader row highlight.

## Why reuse `playoffs.py` instead of the MLB-shaped spec

The request came with an illustrative MLB wild-card spec (exactly 3 divisions,
6 seeds, head-to-head tiebreakers). Hardcoding that would break this sim's
8/12/16/24/30/36-team and tiered configs. `o27v2.playoffs._seed_one_league`
already encodes the correct, config-driven rule (division winners first by win
pct, then the rest), and it is what actually seeds the bracket — so the standings
"playoff picture" now matches the bracket by construction rather than
duplicating a parallel, divergent algorithm. The locked-seed edge case (a
division winner outranks a higher-record wild card) falls out for free because
winners are always seeded ahead of wild cards.

## Validation

- `python3 -m py_compile o27v2/web/app.py` — clean.
- Both templates parse under a standalone Jinja environment.
- Verified Jinja nested `groupby('league') → groupby('division')` preserves the
  within-division win-pct order used for the rank column.
- Unit-checked the playoff-picture algorithm on a synthetic 8-team AL
  (3 divisions, 6-team field): correct division winners, correct wild-card
  ordering, and WCGB matching by hand (e.g. last-in MIN at `+5.0` on the cut,
  first-out TB at `5.0` back).
- `db.fetchall` returns plain dicts, so the new `playoff_picture` is
  JSON-serializable in the `?format=json` path, consistent with the existing
  `leagues` payload.

## Not changed / out of scope

- The tiered (promotion/relegation) standings layout — it has no wild-card field.
- Markdown export and JSON payload — already correctly nested.
- The almanac doesn't render a wild-card panel: it can build from a season-bundle
  JSON that doesn't carry playoff settings, so `teams_per_league` isn't reliably
  available there. The de-conflation (the actual complaint) is fixed; a
  bundle-independent wild-card view would be a follow-up.
