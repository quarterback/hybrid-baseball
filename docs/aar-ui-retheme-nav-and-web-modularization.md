# After-Action Report — UI re-theme, nav IA, mobile, custom-league baselines, web modularization

**Date completed:** 2026-05-25
**Branch:** `claude/codebase-refactor-design-IgnTD`
**Predecessor:** `aar-custom-league-editing.md`

---

## Context

The session opened as an open-ended "does this codebase need refactoring?"
review, with three concrete asks from the user (later expanded to five):

1. The cream/navy/red "Baseball-Reference" theme read dated — move to a
   cohesive, WCAG-compliant, **non-boilerplate** palette with a **light/dark
   toggle** (the user specifically wanted a generated, readable scheme, not
   stock Bootstrap blue).
2. The top navigation was a **flat list of 23 links** with no grouping — hard to
   scan. Wanted **grouped dropdowns**.
3. General **refactoring** of the codebase, and removal of dead code.
4. **Mobile responsiveness** "still not optimal" — the universe-builder league
   table overflowed horizontally on a phone, cutting off the right-hand columns.
5. The **custom-league bias inputs** gave no sense of baselines: bare `0`-default
   boxes with no indication of neutral, range, or what "cranking up" an attribute
   does to league output.

Exploration findings that shaped the approach:

- **Stack:** Python/Flask + Jinja2, server-rendered. The active app is
  `o27v2/`; `o27/` is the game engine it depends on.
- The theme was already centralized in CSS custom properties in
  `o27v2/web/templates/base.html`, so a re-theme was low-risk and high-impact.
- `o27/almanac/static/almanac.css` already had a `[data-theme="dark"]` pattern
  to reuse.
- The nav was hardcoded as one flat `navbar-nav`.
- `o27v2/web/app.py` is an **8,336-line monolith** (84 routes, 168 defs) — the
  one real "god file".
- `lib/` (TypeScript Zod schemas + a React API client) was **unused** — an
  abandoned rewrite scaffold, zero references from Python or artifacts.

## What shipped

Each workstream is a separate commit on the branch.

1. **Removed dead `lib/` scaffold.** Deleted the four unused workspace packages
   and their `tsconfig` project references / `pnpm-workspace` globs; refreshed
   `pnpm-lock.yaml`. The Dockerfile is pure-Python, so this has zero deploy
   impact.

2. **Re-themed the UI (light + dark).** Reworked the `:root` token set in
   `base.html` into a "Twilight Diamond" palette — deep indigo primary + warm
   amber/leather accent. Key design decision: split tokens into
   **theme-flipping text/surface vars** and **fixed `--fill-*` vars**. Solid
   controls (buttons, badges, pills) point at the fixed fills so white text keeps
   AA contrast in *both* modes — the usual trap where a dark theme's lighter
   accent fills fail white-text contrast. Added a `[data-theme="dark"]` override
   block, a persisted **light/dark toggle** (pre-paint inline `<head>` script to
   avoid FOUC, defaults to `prefers-color-scheme`, syncs across tabs), and
   converted the flash banners from hardcoded inline colors to themeable classes.

3. **Grouped the nav into 6 dropdowns.** Games / Players / Stats / League /
   History / Manage. Each top menu highlights when the current endpoint belongs
   to it; collapsed (mobile) menus render inline as expandable sections instead
   of floating overlays. Endpoint names and `url_for` targets are unchanged.

4. **Made the universe builder responsive.** Root cause was a global
   `.table-responsive .dense-table { min-width: 720px }` floor (plus an inline
   `min-width:640px`) forcing the league table wider than any phone. Below 768px
   the `#league-table` now opts out of the floor and **stacks each league into a
   labelled card** (via `data-label` + a media query), so every field is
   reachable without horizontal scroll.

5. **Surfaced baselines in the custom-league builder.** Replaced the bare number
   boxes with **labelled sliders** showing a live ± readout, a one-line "what this
   pushes" hint per knob (e.g. Eye → "walks / on-base"), and an explicit
   `0 = neutral · −25…+25` scale legend. Added a collapsible **style reference**
   listing the built-in profiles' bias values, and a per-row **"Start from
   preset"** picker that copies a known profile (NPB, Dominican, …) as a starting
   point. Bias values and attribute metadata are sourced from `o27v2.league` via
   the route context (`_universe_custom_meta` in `app.py`) so the UI never drifts
   from the engine.

6. **Began breaking up the web monolith.** Extracted the nine pure presentation
   filters (scout grade, flag emoji, star bar, park JSON, pitch repertoire, money
   cell) from `web/app.py` into a new `o27v2/web/formatters.py`, re-registered as
   Jinja filters from `app.py`. No behavior change; `app` and all endpoint names
   stay put so the many cross-module importers (`manage.py`, `sim.py`,
   `season_archive.py`, tests) are untouched.

## Decisions & tradeoffs

- **Extract, don't rewrite.** The plan called for splitting all 84 routes into
  blueprints. I deliberately did only the safe, fully-verifiable slice (pure
  formatters) this session — see Follow-ups for why.
- **Fixed-fill color tokens.** The cleanest way to get a dark mode that actually
  passes contrast on solid controls without per-component overrides.
- **CSS-only mobile stacking** for the universe table (vs. rewriting the
  JS-cloned `<template>` into card markup) — keeps the row-serialization JS and
  the server's positional form parsing completely untouched, which is the
  lower-risk path.
- **Sliders over number boxes** for the bias knobs — a slider with a centered
  neutral and labelled ends communicates "0 is neutral, here's the range" far
  better than a `0`-default text field.

## Verification

This environment has **no browser and no populated DB**, so verification was via
the Flask test client and the test suites (Flask/Jinja/pytest installed via uv):

- `tests/test_template_renders.py` (renders base.html on every page) — green
  throughout; confirms no Jinja/`url_for`/filter regressions.
- Ad-hoc test-client renders of `/universe/new` confirmed: theme toggle +
  pre-paint script present, dark block present, 6 nav dropdowns with correct
  active-state, mobile stacking CSS present, style reference + sliders + presets
  rendered.
- End-to-end **POST** to `/universe/new` with a custom-bias league: 302 redirect,
  and the custom bias JSON persisted to `teams.style_profile` — the builder
  pipeline is intact.
- Full suites: **213 passed, 7 skipped**. The only failures (10) are
  `test_stat_invariants.py` erroring with `no such table: games` — a pre-existing
  environment issue (they need a seeded `o27v2.db`, absent in a fresh clone),
  unrelated to these changes.

## Follow-ups (open)

- **Route → blueprint split of `web/app.py`** (the bulk of the refactor) is
  intentionally **deferred**. It touches all 84 routes, and this environment
  can't browser-verify the result; the automated tests cover only a handful of
  routes. The safe path is to do it incrementally on a branch with staging /
  browser verification, blueprint-by-blueprint, keeping bare endpoint names so
  templates' `request.endpoint` / `url_for` checks don't move. The
  `formatters.py` extraction establishes the pattern (extract + re-export, no
  endpoint churn); `aggregations.py` (the `_aggregate_*` / `_pitcher_wl_map` /
  `_PSTATS_DEDUP_SQL` cluster imported by `season_archive.py` and tests) is the
  next clean extraction target.
- **Almanac palette:** `almanac.css` already has its own light/dark tokens; a
  later pass should align its hues to the new "Twilight Diamond" values so
  `/almanac` matches the rest of the site exactly.
- **Broader mobile audit:** the universe table was the screenshotted offender and
  is fixed; a sweep of the widest stat tables on a real device is worth doing.
