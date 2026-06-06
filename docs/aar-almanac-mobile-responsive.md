# After-Action Report — Make the almanac responsive on mobile

**Date completed:** 2026-06-06
**Branch:** `claude/almanac-mobile-responsive-HIvD1`

---

## TL;DR

On a phone the almanac scrolled sideways: the nav bar ran off both edges and
the schedule table's columns were clipped. The cause was a single
non-wrapping flex row in the header — there were **zero media queries** in
`o27/almanac/static/almanac.css`. I added a `@media (max-width: 760px)` block
(plus a `420px` refinement) that lets the header wrap and turns the nav into a
self-contained horizontal-scroll strip, so the page width now stays pinned to
the viewport. CSS-only change; no templates or Python touched.

## What was wrong

`_base.html.j2` already ships `<meta name="viewport" content="width=device-width, initial-scale=1">`, so the viewport was correct. The problem was layout:

- `.site-bar` is `display:flex` with `gap:16px` and **no wrap**. It holds the
  brand, ten uppercase nav links, a 180px search box and a theme toggle. That
  row is far wider than a ~390px phone, and because nothing wraps or clips, it
  stretched `<body>` itself wider than the viewport. The result is the whole
  page scrolling horizontally — the symptom in the screenshots where the nav
  reads "CHEDULE LEADERS CAREER…" on one scroll position and "O27 ALMANAC" on
  another.
- The data tables were **not** the root cause: they already live inside
  `.tablewrap { overflow:auto }`, which scrolls them independently. They only
  *looked* broken because the entire page was shifted by the header overflow.

## The fix

Appended a responsive block to the end of `almanac.css`:

- **`@media (max-width: 760px)`**
  - `.site-bar` gets `flex-wrap: wrap`. The brand takes row one
    (`flex: 0 0 100%`); search box + theme toggle share row two; the nav is
    pushed to the last row with `order: 1`.
  - `nav.top` becomes its own `overflow-x:auto` strip (`flex: 0 0 100%`,
    scrollbar hidden) so the long link list scrolls *within itself* and never
    widens the page — the standard mobile top-nav pattern.
  - Trimmed page padding/title sizes; let `.section-bar`/`.tabs` wrap; stacked
    the three-column `.player-header`/`.team-header` grids to one column; let
    `.boxscore-headline` wrap.
- **`@media (max-width: 420px)`** — hide the header search box (in-page filter
  boxes still cover search) and drop `.cards` to two columns.

## Validation

Honest about scope: I could **not** run the live app in this sandbox — `flask`
isn't installed and there's no seeded DB (both environmental, per CLAUDE.md),
and no headless browser was available to screenshot. I verified the stylesheet
is well-formed (balanced braces, valid media-query nesting) and reasoned
through the flex/order layout by hand. The change is purely additive CSS gated
behind `max-width` media queries, so desktop rendering is untouched.

## Files touched

- `o27/almanac/static/almanac.css` — added the responsive media-query block.
