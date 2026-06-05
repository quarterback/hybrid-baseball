# After-Action Report — app-wide mobile overflow pass

**Date completed:** 2026-06-02
**Branch:** `claude/mobile-responsiveness-overflow-5dumJ`

---

## Context

The user runs O27 mostly on a phone and reported "pervasive" overflow
issues, attaching a screenshot of the **Build a Universe** page
(`/universe/new`). On the league-config cards the *Home region*, *Park
geometry* and *Power Play* selects were squeezed into a 2-up grid until
their option text truncated mid-word — "Americas Pr…", "(varied — all…"
— and the *Home region* label collided with its "🎨 Blend…" button.

That page was the trigger, but the brief was app-wide, so this turned into
a full audit of all ~50 templates (fanned out across three reviewers) for
real phone-overflow risks — fixed-width controls, non-wrapping flex rows,
rigid pixel grids — on top of the universe-builder fix.

## Audit finding: the foundation is mostly sound

`base.html` already carries a solid responsive layer that handles the
common cases, so the fixes below are surgical, not a rewrite:
- A global JS pass auto-wraps **every** `<table>` in a horizontal-scroll
  container with frozen label columns — wide tables are not the problem.
- `body { overflow-x: hidden }` + `overflow-wrap: break-word` at ≤768px.
- `.filter-bar`, `.nav-tabs`, `.hero`, `.stat-tiles` already had phone rules.
- Most inline widths are `max-width:` (safe — caps, never forces overflow).

The genuine gaps were: (1) the universe builder, the app's only Tailwind
page, which `base.html`'s rules don't reach; (2) filter-bar controls that
wrapped but kept cramped inline desktop widths; (3) a handful of
non-wrapping action-button groups and two rigid pixel grids.

## Root cause

`universe_new.html` is the app's **only** Tailwind-CDN page; every other
template rides on the Bootstrap + design-system CSS in `base.html`, which
already carries a comprehensive set of phone guards (`body { overflow-x:
hidden }`, `overflow-wrap: break-word`, `.table-responsive` horizontal
scroll, reduced container padding, 44px tap targets). The universe builder
is an island those rules don't reach.

Worse, `base.html` still carried a `@media (max-width: 768px)` block that
stacked a `<table id="league-table">` into labelled cards — but the
universe builder had since been rewritten from that table into a Tailwind
`.lg-card` stack. So the page had **no** working mobile layout: the live
markup got nothing, and 37 lines of `#league-table` overrides sat dead in
the global stylesheet. (Same stranded-feature shape the CLAUDE.md
scorecard story warns about, in the other direction — CSS left behind
after the markup it targeted was replaced.)

## What changed

**Universe builder (the screenshot):**
- **`universe_new.html`** — added a phone rule (`max-width: 639px`, below
  Tailwind's `sm`) scoped to `#uni-builder .lg-body`: the two short numeric
  fields (Teams, Divisions) stay paired on the first row, and every field
  from the third on (Style, Home region, Park geometry, Power Play) takes
  the full row so its label and selected option stay readable.

**Global, in `base.html` (helps every page at once):**
- Deleted the dead `#league-table` stacking block (37 lines targeting an
  element removed when the builder was rewritten to a Tailwind card stack)
  and left a pointer note.
- **Filter bars** — on phones each control now stacks full-width
  (`flex: 1 1 100%` + `width: 100% !important`), so the inline desktop
  widths (`style="width:200px"`) stop leaving cramped half-rows. This lands
  app-wide: transactions, free agents, stats browser, standings, schedule,
  leaders, financials, analytics, distributions — anything using `.filter-bar`.
- **Safety net** — `.form-select, .form-control { max-width: 100% }` at
  ≤575px so any stray fixed-width control in a non-wrapping row can't push
  the page sideways (covers e.g. `team_hof` `min-width:280px`,
  `college_import` / `pro_worldcup_team` `max-width:240px` selects).
- **Spray-chart panels** — drop the inline `min-width:340px` floor on the
  game page below 575px (SVGs are `width:100%` and rescale fine), which
  otherwise clipped a few pixels off the narrowest phones.

**Targeted per-page fixes:**
- **Non-wrapping action-button groups** — added `flex-wrap` to the header
  button rows on `auction.html` (6 long-labelled buttons — the worst),
  `pro_worldcup.html` (6), `league.html`, `youth.html`, `team.html`,
  `college_prospects.html`, `game_scorecard.html`, `pro_worldcup_team.html`,
  and the signing-round form on `free_agents.html`. On desktop they already
  fit one line, so `flex-wrap` is a no-op there; on a phone they now wrap
  instead of overflowing off-screen (and getting clipped by `overflow-x:
  hidden`, i.e. unreachable).
- **Rigid rating-slider grids** — `o27i_player.html` and
  `college_prospect_player.html` used `grid-template-columns: 160px 1fr
  88px`; the 248px of fixed columns starved the slider track on a ~340px
  row. Shrunk both to `92px 1fr 52px` below 575px.

## Validation

CSS / markup-only template edits (no engine, route, or data path touched).
Flask is absent from this sandbox (expected per CLAUDE.md), so this was not
exercised against a running server — the reasoning rests on the
grid/breakpoint math, the confirmed fact that `universe_new.html` is the
sole Tailwind page, and a brace-balanced check of the `base.html` stylesheet.
All new rules are gated at ≤575/639px, and the per-page `flex-wrap`
additions are no-ops on desktop (the rows already fit one line there), so
desktop layout is unchanged.

## Follow-up: frozen-column overlap on all-numeric stat tables

The horizontal-scroll wrapper's `freezeLabelCols` pins the leading identity
column(s) of a wide table so the player/team name stays visible while the
numbers scroll under it. It froze "through the first non-numeric column,
capped at 3" — but on a table whose leading columns are *all* numeric (the
player page's Advanced/Standard Batting stat strips, first col `BAVG`), it
never found a text column and pinned the first 3 *stat* columns instead.
Those sticky cells then stuck over the scrolling columns and overlapped —
only visible on a tinted/highlighted row (a normal row's opaque sticky
background hid it), which is exactly how the user spotted it.

Fix: only pin when a real non-numeric identity column actually exists among
the first three cells (`foundText` guard). All-numeric stat strips now freeze
nothing; roster / leaders / standings tables (which lead with a name, or a
rank number then a name) are unchanged.

## Not done / honest caveats

- Not verified on a real device — would benefit from a phone pass once the
  branch is deployed.
- The audit deliberately did **not** touch the dozens of `d-flex` rows that
  already carry `flex-wrap`, the Bootstrap `col-md-*` grids (which stack
  below `md` by default), or anything inside the auto-scrolling table
  wrappers — those were verified sound, not ignored.
- If a specific page still overflows on-device, a screenshot will pin it
  down faster than another blind sweep.
