# After-Action Report — mobile overflow on the universe builder

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

- **`universe_new.html`** — added a phone rule (`max-width: 639px`, i.e.
  below Tailwind's `sm`) scoped to `#uni-builder .lg-body`: the two short
  numeric fields (Teams, Divisions) stay paired on the first row, and
  every field from the third on (Style, Home region, Park geometry, Power
  Play) takes the full row so its label and selected option stay readable.
- **`base.html`** — deleted the dead `#league-table` stacking block and
  left a note pointing to where the universe builder's phone layout now
  lives. In its place, a small `max-width: 575px` override drops the inline
  `min-width:340px` floor on the game page's side-by-side spray-chart
  panels, which otherwise clipped a few pixels off the narrowest phones
  (the SVGs are `width:100%` and rescale fine without the floor).

## Validation

CSS-only template edits. Flask is absent from this sandbox (expected per
CLAUDE.md), so this was not exercised against a running server — the
reasoning rests on the grid/breakpoint math and the confirmed fact that
`universe_new.html` is the sole Tailwind page and the `#league-table`
selectors exist nowhere else in the tree. No engine, route, or data path
was touched.

## Not done

Did not audit every template pixel-by-pixel on-device. The rest of the app
leans on the existing `base.html` responsive system, which looked sound;
if specific non-universe pages still overflow, they'd want their own
screenshots to pin down.
