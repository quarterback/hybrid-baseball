# After-Action Report — Guilder (ƒ) Currency System with Indian Numbering and USD/EUR Toggle

**Date completed:** 2026-05-08
**Branch:** `claude/guilder-currency-system-Eu726`
**Commits (in order):**
- `b35b611` — Add the guilder (ƒ) currency system with Indian numbering and per-value USD/EUR toggle

---

## What was asked for

The session opened with a long-form worldbuilding brief: O27's
canonical financial register should be the **guilder** (ƒ, U+0192),
denominated in **Indian-style numbering** (lakh = 1,00,000;
crore = 1,00,00,000), anchored to a synthetic **basket** of Caribbean
and Pacific currencies (HTG / JMD / PHP / GYD / TTD / DOP plus FJD /
XCD / VUV), with a per-value **toggle** that lets the reader switch
the displayed money between guilder, USD, and EUR.

The brief sketched a much larger stack — salary tiers, league-tier
caps (Galactic 900 cr / Premier 650 cr / National 400 cr / Association
225 cr), PPP adjustments, persisted contracts, transaction strings
written in crore terms, fan-vocabulary phrases like "100-crore
signing." Through `AskUserQuestion` we narrowed PR-1 to the
worldbuilding chassis: the formatter and the toggle, demonstrated
on a derived "estimated value" computed from the existing
`trade_value()` score. No DB changes, no PPP, no persisted contracts.

Concrete answers from the user:

- **Scope:** formatter + toggle, demo on derived value (no salary persistence yet).
- **EUR rate:** "Fixed in basket config, recompute on the fly" — keep
  the basket as the worldbuilding source of truth.
- **PPP:** out for this PR.
- **Toggle UI:** inline next to each money figure (per-value pill that
  cycles), not a global topbar dropdown.

---

## What shipped

### Currency module (`o27v2/currency.py`)

Pure Python, no Flask deps — every formatter and conversion lives in
one place so the front-end and the back-end share a single source of
truth. The module exposes:

- `GUILDER = "ƒ"`, `LAKH`, `CRORE` constants.
- `BASKET_WEIGHTS` / `BASKET_NOMINAL_RATES` — the Caribbean-Pacific
  weights from the brief, plus rough per-USD nominal rates. Kept
  honest about being a worldbuilding artifact: the headline anchor
  (`GUILDER_PER_USD = 100.0`) is still fixed so values don't shift
  every pageload, but the basket is exposed via
  `basket_synthetic_usd_per_guilder()` for the future league-info
  page.
- `format_indian(n)` — last 3 digits, then pairs from the right.
  4_70_00_00_000 → "4,70,00,00,000". Negative values flip a sign
  prefix.
- `format_crore(n)` — spells in crore for ≥ 1 cr (one decimal where
  it adds info, whole otherwise: "1.5 crore" vs "470 crore"), in lakh
  for 1 lakh ≤ n < 1 cr, falls back to comma form for < 1 lakh.
- `to_usd(g)` / `to_eur(g)` / `format_usd(g)` / `format_eur(g)` —
  USD anchored at ƒ100 = $1; EUR derived from a fixed EUR/USD
  nominal so €1 ≈ ƒ108. Western formatter is compact M/B/K.
- `format_money(g, mode)` — single dispatch entry point used by the
  Jinja filter and the JS toggle.
- `rates_for_js()` — the snapshot the front-end imports.

### Valuation module (`o27v2/valuation.py`)

`estimate_player_value(player, league_name=…)` wraps the existing
`trades.trade_value` 0..1 score and multiplies by a tier-cap × roster
share (30%) so a single star tops out around 25% of a team cap.
Tier cap lookup is case-insensitive — `Galactic` / `Premier` /
`National` / `Association` resolve, anything else gets the ƒ400 cr
default. `estimate_team_payroll(team_id)` sums across the active
roster.

Smoke-tested values land in the user's bands: a 28-year-old, 78-skill,
power-archetype Galactic-League batter ≈ ƒ216 cr; a peak-age
contact star at the default tier ≈ ƒ76 cr.

### Display layer

- **`money` Jinja filter** in `o27v2/web/app.py`. Each call emits a
  pre-baked `<span class="o27-money">` with all three labels
  (`data-label-guilder` / `-usd` / `-eur`) baked into data-attrs and
  a `<button class="o27-money-pill">` next to the visible label.
  Returns `markupsafe.Markup` so Jinja doesn't escape the wrapping
  span; inputs are integers so there's no XSS surface.
- **`inject_currency_rates` context processor** exposes
  `currency_rates` to every template.
- **`base.html` additions:**
  1. CSS for the pill — slotted into the existing design-token palette
     so it inherits the cream-and-navy chrome.
  2. `<script>window.O27_RATES = {{ currency_rates | tojson }};</script>`
     — JS reads from the same Python source of truth.
  3. A vanilla-JS IIFE that delegates pill clicks (works for
     dynamically-injected money cells too), cycles
     ƒ → $ → €, persists `o27.currencyDisplay` in localStorage, and
     syncs across tabs via the `storage` event. Mirrors the
     compare-basket pattern at `base.html:867-1007`.

### Demo surfaces

- **`/player/<id>`** — header strip now shows
  `Est. value ƒ216 crore [ƒ]`. Click the pill, all money cells on
  the page (and any open tab) cycle to USD or EUR.
- **`/team/<id>`** — header strip shows `Payroll ƒ670 crore [ƒ]`.
  The team route computes the payroll from the roster it already
  loads, so no extra DB round-trip.

### Tests

`tests/test_currency.py` — 30 cases: comma formatter edge values
(0, 99,999, 1,00,000, 9,99,99,999, the canonical 4,70,00,00,000,
negatives), lakh/crore dispatch boundaries, USD anchor round-trip
(100 cr ≈ $10M), EUR via the basket, compact western format
(M / B / K), `format_money` mode dispatch, zero handling, and the
`rates_for_js` shape. All 30 pass; no regressions in the rest of
the suite (the pre-existing `test_weather_calibration` flake is
unchanged on either side of the patch).

---

## What I'd do differently

**Should have asked about the calibration of the lower band.** The
user's brief explicitly framed replacement-tier rookies as
"ƒ20-50 lakh" — but `trade_value()` for a 35-skill, 22-year-old
rookie produces ~0.34, which through the current pipeline lands at
ƒ23 cr instead. The headline crore-tier numbers are right; the lakh
tier isn't. I made a deliberate call to ship the formatter and let
the calibration be a follow-up, but I should have flagged the
mismatch in an `AskUserQuestion` before committing — the user might
have wanted a non-linear curve in `valuation.py` (e.g., trade_value
exponentiated, or a separate "replacement floor" carved out for
rookies under ~40 skill) baked into PR-1.

**The early `_money_safe` / `_Markup` two-step.** First pass
registered a plain `_money` filter, then re-registered a
`_money_safe` wrapper a few lines below to apply `Markup`. Worked,
but it left dead code and an awkward import in the middle of the
filter region. Caught it before commit and consolidated into a
single `_money` returning `Markup` directly — but the lesson is to
think about Jinja's auto-escape contract before writing the filter,
not after smoke-testing it.

**No template-rendering test.** I smoke-tested the Jinja
environment manually (`env.from_string('{{ N | money }}').render()`)
and via a synthetic `request_context`, but I didn't add a pytest
case that exercises the `money` filter through the Flask app
fixture. The next person who edits `_money` won't have a regression
guard. A trivial test that loads the app, renders `'{{ 4700000000 |
money }}'`, and asserts the three data-labels match
`format_money(g, mode)` would close that gap.

**Front-end CSS lives in `base.html` instead of a stylesheet.** I
followed the existing convention (the file already has 600+ lines
of inline `<style>`), but a cleaner home would be a dedicated
`o27v2/web/static/css/money.css` or moving toward extracting the
inline styles wholesale. Not the right PR to start that migration.

---

## Pointers for follow-up work

1. **Persist salaries.** Add a `players.salary` (INT, guilders)
   column via `db.py`'s ALTER-table migration pattern (the codebase
   already does this for archetype / pitcher_role / stamina). Seed
   at league creation in `league.py` using the same valuation pipe.
   Once persisted, every page that mentions a player can opt-in to
   `{{ player.salary | money }}` without re-deriving anything. A
   `contracts` table with year-by-year breakdowns is the natural
   next step after that.

2. **Calibrate the lakh tier.** Add a curve to `valuation.py` so
   replacement-tier rookies (skill < ~40) land at ƒ20-80 lakh
   instead of being floored at ƒ20 lakh. Either a piecewise function
   or `trade_value(p) ** 1.4` would compress the bottom end. Worth
   eyeballing actual league outputs before settling on a curve.

3. **Spelt-form prose helper.** `format_crore` returns "ƒ470 crore"
   for headlines. The user wanted a parallel `format_crore_long`
   that returns just "470 crore" for prose like "Trinidad reached
   a deal with Marchetti for 150 crore over three years." Trivial
   to add — the dispatcher already has the pieces.

4. **Wire the filter into more surfaces.** `/transactions` (trade
   detail strings), `/leaders` (a "highest-paid" leaderboard),
   `/free_agents` (asking-price column), `/standings` (per-team
   payroll column) all become trivial once the salary field is
   persisted. Currently the pages don't render any money at all.

5. **PPP toggle.** The user asked to defer this; the natural
   implementation adds a fourth display mode and a per-team
   `ppp_factor` (probably from city + country). The toggle JS is
   already structured around an array of mode strings, so adding
   `'ppp_usd'` is a one-element extension to the cycle plus a
   new pre-baked `data-label-ppp_usd`.

6. **League-info page surfacing the basket.** The brief framed the
   basket as part of the league's identity, not just a conversion
   footnote. A `/league/finance` (or a section on `/league`) showing
   the nine basket weights, the synthetic USD-per-guilder rate, the
   four tier caps, and a sample contract distribution would make
   the worldbuilding visible to readers instead of buried in code.

7. **Internationalize the comma format.** `format_indian` is
   currently the only number formatter. A western-comma helper
   (4,700,000,000) would let users in a "western numbering" locale
   read full guilder figures without the lakh/crore shorthand —
   complementary to the existing currency toggle, not redundant.
