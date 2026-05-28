# After-Action Report — Zaryanovia, Zora Currency, and the Live Financials Board

**Date completed:** 2026-05-28
**Branch:** `claude/name-sets-cities-expansion-OxkeB`
**Commits (oldest → newest):**
- `2132791` — Add Pro World Cup: regional qualifying, editable rosters, 24-nation main bracket
- `1ea25da` — Expand Pro World Cup rosters to 30 (WBC standard)
- `06281b8` — Add Zaryanovia: fictional nation with full Zaryanification name pipeline
- `cd0a2f0` — Drop in real Zaryanovia flag art
- `5486764` — Optimize Zaryanovia flag for inline use (478KB → 7KB)
- `6eb4bdc` — Add Zora as Zaryanovia's national currency (initial scaffolding)
- `e7a51e5` — Broaden name pools with WTA/ATP-era tennis names; fix H/W digraph phonology
- `d0f08e1` — Rewrite zora per the wiki: ₳ symbol, $13.50 baseline, JPY/KRW/USD/CNY basket
- `f0e4d1c` — Add Financials page with live FX board for both fictional currencies

---

## What was asked for

A multi-step build initiated by the user wanting to add a fictional country
to the game, evolving over the session into a complete worldbuilding +
financial-systems integration. In rough chronological order:

1. Add the Pro World Cup as the end-of-season showcase for pro players (a
   parallel to the existing youth Frontier Cup).
2. Add **Zaryanovia** as a fictional alt-history nation — country code,
   flag, regional bucket, name pool, hometowns.
3. Get the user's custom flag rendering inline next to every Zaryan player
   name (the regional-indicator emoji trick doesn't work for non-ISO codes).
4. Build a **Zaryanification name converter** implementing a spec the user
   provided: phonology (H→G, W→V, TH→T), surname paths A/B/C, patronymic
   from a separately-drawn father's name, register-aware feminization.
5. Add the **Zora** as Zaryanovia's national currency — symbol, code,
   plurals, subdivision, basket math.
6. Broaden the US name pool with diverse international surnames (the user
   pointed at ATP/WTA tennis rankings as the source).
7. Add a **financials page** showing both in-game currencies plus every
   real-world currency the game references, with rates pulled live from
   the internet.

---

## Starting state

- The pro game had no international tournament. A youth Frontier Cup
  existed (`o27v2/youth.py`); pro players had nothing comparable.
- Country handling was via ISO 3166-1 alpha-2 codes everywhere. The flag
  filter (`o27v2/web/formatters.py:_flag`) converted codes to
  regional-indicator emoji pairs — fine for real countries, useless for
  fictional ones.
- Name pools (`o27v2/data/names/regions.json`) were already rich for 70+
  real regions, including elaborate multi-source mixes like Kazakhstan
  (Kazakh + Korean Koryo-saram + Russian). No mechanism existed for
  generating creole names — every region drew first + last from
  same-bucket pools.
- The currency module (`o27v2/currency.py`) had one canonical unit (the
  guilder, ƒ), with USD and EUR as display modes computed from a fixed
  ƒ100/$1 anchor. Caribbean-Pacific 9-currency basket existed as
  worldbuilding metadata only — didn't actually drive the rate.
- No financials page existed. No outbound HTTP for FX data. Money cells
  cached their guilder/USD/EUR labels on the server side; a JS toggle in
  `base.html` cycled the visible label.

---

## What shipped

### 1. Pro World Cup

`o27v2/pro_worldcup.py` — end-of-season tournament for the pro player pool.

- Three-phase pipeline: regional qualifying → roster lock → main bracket.
- Regional pots (Americas 9 / Asia 7 / Europe 6 / Other 2) sum to 24-nation
  main bracket; top 12 nations per region enter qualifying based on top-30
  pool composite strength.
- 4 groups of 6 → top 2 advance to 8-team knockout (QF → SF → Final).
- Engine path mirrors `o27v2.youth_sim` — runs `o27.engine.run_game`
  against converted pro `Player` objects without the youth YPI governor
  (pros play at their true grade).
- Per-game stats land in `game_wc_batter_stats` / `game_wc_pitcher_stats`
  so the WC has its own box-score history separate from the regular season.
- Interactive roster editor at `/pro-worldcup/team/<id>` lets the user
  swap players before rosters lock; auto-roll endpoint backfills a
  30-man squad from the eligible pool.
- Roster size = 30 (WBC standard, replacing the initial 22-man scaffold).
  Slot shape: 8 starters + 6 backups + 3 jokers + 13 pitchers — the
  3-joker count matches `league.py:ACTIVE_JOKERS`, which the engine's
  `_ordered_lineup` expects in `jokers_available`.

### 2. Zaryanovia country wiring

Registered as a top-tier National Team across the codebase.

- **Country code:** `ZR` (defunct Zaire ISO, no real-country collision).
- **Registry entries:** `_NATIONAL_TEAMS` (Asia bucket), `_COUNTRY_REGION`
  in `youth.py`; `_COUNTRY_DISPLAY` in `pro_worldcup.py` (display name
  "Zaryanovia", IOC abbreviation "ZAR").
- **Hometowns:** 69 cities (28 primary + 41 secondary/tertiary) in
  `hometowns.json` under the `ZR` key, drawn from the user's toponymy
  spec. Nogliki de-duped between the two lists.
- **Team naming:** new `eurasia_zaryanovia` entry in
  `team_naming.json:city_to_locale` with 51 primary + spoken/drifted
  single-word secondaries, locale `russian` (so generated club names get
  the "Garrison Beysbol'nyy Klub" treatment).
- **Backfill:** new `ensure_world_teams()` in `youth.py` mirrors the
  existing `ensure_frontier_teams` so existing saves pick up ZR on next
  league touch without needing a fresh seed.

### 3. Custom flag pipeline

Extended `_flag` filter in `o27v2/web/formatters.py` to return an inline
`<img>` for codes registered in a new `_CUSTOM_FLAGS` dict (real ISO codes
still go through the regional-indicator-emoji path unchanged).

- Flag art lives at `o27v2/web/static/flags/zr.png`.
- Initially landed at full-res (1456×816, 478 KB) before the user pointed
  out it was being shipped uncompressed for inline use. Reshipped at
  171×96 / 64-color palette: **7 KB** (98.5% smaller), still crisp at 2×
  hi-DPI inline rendering.
- README at `o27v2/web/static/flags/README.md` documents the convention
  for swapping the art or adding more fictional countries.

### 4. Zaryanification name converter

`o27v2/zaryan_names.py` — a dedicated creole converter (not expressible in
the existing `regions.json` subregion schema). Implements the user's spec:

1. **Phonology** (`_apply_phonology`): regex-based H→G, W→V, TH→T,
   digraph-aware — Sh/Ch/Ph/Wh/Gh/Kh/Rh and Wh preserved so Shaq stays
   Shaq, Whistler stays Whistler. (First implementation used naïve
   `str.replace` and produced "Sgaq", "Vhistler", "Prakasg" — fixed in
   commit `e7a51e5`.)
2. **Surname path A** (`_russify_surname`): drop trailing vowel cluster
   then add `-in` (Riley → Ril → Rilin); otherwise add `-ov` (Brooks →
   Brooksov). Existing Slavic-looking endings (`-ov`, `-ev`, `-in`,
   `-sky`, `-skiy`, `-tsky`, `-ich`) are left alone.
3. **Surname path B** (`_PATH_B_SURNAMES`): 60-entry meaning dictionary
   for translated-Slavic surnames — Wood → Lesov, Smith → Kuznetsov,
   Hill → Gornov, Bell → Kolokolov, Cross → Krestov, etc. Authored from
   the spec's worked examples plus natural extensions (occupational
   surnames, color surnames, weather/nature surnames).
4. **Patronymic** (`_patronymic`): drawn from a *separately* drawn
   father's English first name (the spec's signature move — a Zaryan's
   patronymic is from their father, not their own name). Phonology
   applies; suffix is `-ovich`/`-ovna` after consonants, vowel-aware
   `-evich`/`-evna` after vowel endings (e.g. Kodya → Kodyevich,
   Genri → Genrievich, Mikey → Mikeyevich — no double-vowel artifacts).
5. **First name** (`_convert_first_name`): ~85-entry Russianization
   dictionary (Marcus → Maks, Mary → Maria, Cody → Kodya, Henry → Genri,
   etc.) plus a Biblical-heritage retention list (Elijah, Isaiah,
   Solomon, Booker, Marcus, Coretta, Ada) — those stay as-is ~55% of
   the time even when a Russian form exists.
6. **Feminization** (`_feminize`): Path B always feminizes for women;
   Path A/C feminize ~15% of the time to mark older/rural register.

Wired into both `o27v2.league.make_name_picker` and
`make_country_pinned_picker` via a `region_id == "zaryanovia"`
short-circuit that bypasses the subregion machinery and calls
`zaryanify_draw(rng, gender)`.

The `regions.json:zaryanovia` entry exists as a fallback shape with
minority subregions for the four non-creole streams (Russian / Koryo-saram
Korean / African+Ethiopian / Chinese / Kazakh).

### 5. Name-pool expansion via Wikipedia tennis-player categories

Pulled from Wikipedia's nationality-bucketed tennis-player categories
(the ATP site itself is Cloudflare-walled — 403s without a JS-capable
browser). Wrote `/tmp/pull_tennis.py` (not committed; reproducible) to
walk the Wikipedia category API with pagination.

| Pool | Before | After | New |
|---|---|---|---|
| `american_general` (surnames) | 2,827 | 4,064 | +1,237 |
| `russian` (surnames) | 50 | 296 | +246 |
| `japanese` (surnames) | 28 | 225 | +197 |
| `american_south/NE/MW/W` (first names, ×2 genders) | ~180-350 each | ~620-770 each | +380-460 per bucket |
| `russian/japanese` (first names, ×2 genders) | 44-50 each | 80-138 each | +30-92 per bucket |

Skipped Korean & Chinese surname pulls — Wikipedia titles for those
nationalities aren't consistently Given-Family order, so extraction
would have polluted the pools with given-name leaks.

The Zaryanification pipeline automatically benefits since it draws its
English-input names from `american_general` + `black_american` +
`american_*` first-name pools.

### 6. Zora — Zaryanovia's currency

`o27v2/currency.py` ZORA section. Per the user's wiki:

- **Symbol:** `₳` (austral sign, U+20B3) — the one canonical glyph.
  (Earlier scaffolding split it across formal `₴` + handwritten `₳` +
  dead `ЗР`; only `ЗР` survives as lore-only documentation for the dead
  Russian-era ruble heritage.)
- **Code:** `ZRZ` (reusing the ZR ISO 3166-1 root; ZRN/ZRZ were the
  defunct Zaire codes).
- **Baseline:** 1 ₳ = $13.50 USD — a STRONG, high-PPP currency (Swiss
  franc psychology, not yen/won). Floats freely on the Norwegian model;
  reserve war chest in SGD/CHF/EUR/gold documented as lore.
- **Basket:** JPY 35% / KRW 35% / USD 15% / CNY 15%. The Russian ruble
  is deliberately excluded — being the stable alternative to ruble
  instability is the whole haven-currency value proposition.
- **Rate computation** (`zora_usd()`): per-currency
  `index = baseline / current` (index > 1 means strengthened against
  baseline). Weighted sum gives the basket multiplier;
  `13.50 * mult` is the candidate rate, clamped to [$6.55, $19.97].
- **Subdivision:** 100 luchi = 1 zora (singular `luch`). Sub-zora
  amounts render natively in luchi ("7 luchi" not "₳0.07") — matches
  the wiki's cost-of-living idiom (espresso 18 luchi, transit 5 luchi).
- **Plurals:** `zora_plural(n)` returns `zora` (1) or `zory` (else) —
  the creole-regularized two-form. Archaic genitive `zor` documented
  but not produced.
- Site-wide JS money toggle (`base.html`) extended to cycle
  ƒ → $ → € → ₳.
- **Spot check vs the wiki:** $743 1BR-Garrison rent renders as ₳55,
  matching the wiki's stated 55 ₳/mo exactly.

Subregions in `regions.json:zaryanovia` rebuilt to the wiki's
four-stream founding model: 65% creole / 12% Russian (the "marked"
minority) / 8% Koryo-saram Korean / 7% African (incl. Ethiopian
thread) / 5% Chinese / 3% Kazakh. New `koryo_saram` surname pool
(Kim, Pak, Tsoy — Russian-transliterated forms distinct from South
Korean Park/Choi). Japanese dropped from the mix — not a founding
stream per the master wiki.

### 7. Financials page (`/financials`)

`o27v2/fx.py` + `o27v2/web/templates/financials.html` + route in `app.py`.

**Live FX module** pulls from `open.er-api.com` (free, no API key, 166
currencies — covers the full Caribbean-Pacific small-currency set that
ECB feeds skip). In-process cache with 1-hour TTL; force-refresh from
the page's "Refresh now" button. Graceful fallback to last-known stale
cache on fetch failure with a yellow warning banner.

`apply_to_zora_basket()` writes the live JPY/KRW/CNY rates into
`ZORA_CURRENT_RATES` so `zora_usd()` floats with real markets.

Page sections (League → Financials in the nav):
1. **At-a-glance cards** — Guilder · Zora · USD · EUR with
   cross-conversion; fictional currencies badged.
2. **Zora basket** — live per-component math (baseline, current, index
   green/red, weighted contribution, basket multiplier, clamped rate)
   + ruble-exclusion callout + reserve war chest list.
3. **Guilder basket** — informational only (fixed ƒ100/$1 anchor),
   shows nominal-vs-live drift on each of the 9 Caribbean-Pacific
   components.
4. **Cross-rate matrix** — 4×4 pairwise of the headline currencies.
5. **All tracked currencies** — full table of 24 real-world rates with
   role badges (zora basket / guilder basket / zora reserve / excluded
   by zora policy).

On the day it was wired up, live FX gave JPY 159 / KRW 1500 / CNY 6.79
vs the 150 / 1350 / 7.20 baselines → basket multiplier 0.953 → zora
trading at **$12.87/zora** (down from $13.50 baseline, well inside the
clamp).

---

## Things that went sideways

- **First flag upload:** dropped the 478 KB full-res PNG in next to every
  player name. Should have optimized at upload time; user caught it and
  it's now 7 KB.
- **Initial zora rate** (the early `6eb4bdc` commit): guessed 250 zora/USD
  thinking the zora was a weak post-Soviet currency. The wiki specified
  the opposite — 1 zora = $13.50 USD, a strong currency. Rewritten in
  `d0f08e1`.
- **Initial zora symbol:** scaffolding split the currency across three
  glyphs (formal ₴ / handwritten ₳ / dead ЗР) based on an earlier
  description. The wiki collapsed to a single canonical `₳`; revert
  shipped in the same `d0f08e1`.
- **Phonology bug:** first cut of `_apply_phonology` did naïve
  `str.replace`, transforming H/W inside digraphs (Sh, Ch, Ph, Wh) and
  producing artifacts like "Sgaq", "Vhistler", "Prakasg". Fixed in
  `e7a51e5` with digraph-aware regex.
- **Wikipedia category extraction:** Korean and Chinese tennis player
  Wikipedia titles aren't consistently Given-Family order, so the
  last-word-is-surname heuristic would have polluted those pools. Both
  excluded from the merge. Japanese is fortunately consistent (Western
  order on English Wikipedia).
- **ATP web fetch:** `atptour.com` returns 403 Cloudflare challenges
  without a real browser. Pivoted to Wikipedia's tennis-player
  categories instead — more nationalities, cleaner extraction, no
  anti-scraping issues.
- **FX provider choice:** initial plan used `frankfurter.dev` (ECB
  feed). Frankfurter only covers ~30 major currencies and misses the
  entire Caribbean-Pacific guilder basket. Switched to
  `open.er-api.com` (166 currencies, free, no key) before the page
  shipped.
- **Pre-existing test failure:** `tests/test_youth_substitution_economy.py::test_seed_roster_shape`
  hardcodes 48 youth teams; with the Frontier Cup expansion (long
  before this branch) it's been failing at 76. My +1 ZR addition bumps
  it to 77. Verified on HEAD (before this work) that the test was
  already failing — not introduced here.

---

## Decisions you made along the way

These were resolved by the user mid-stream via AskUserQuestion / explicit
follow-up messages:

- Country code: **ZR** (chosen over fictional `ZY` recommendation —
  defunct Zaire ISO has no live conflict and the OS emoji font won't
  render a flag for it, which makes the custom-image path the natural
  fit).
- Competition tier: **top-tier National Team** (not Frontier Cup).
- Name mix: **Trinidad-style creole** majority — per the master wiki, a
  Russian-grammar wrapper around an English / Black-American root, with
  minority Russian / Koryo-saram / Chinese / African / Kazakh streams.
- Flag: **the orange/ebony-sun art the user uploaded is canon** — the
  purple/gold/white design described in the master wiki was an earlier
  iteration the user explicitly told me to ignore.
- Currency baseline: **1 ₳ = $13.50 USD**, basket-driven, ruble
  excluded by design.

---

## Where things live (relay reference)

| Feature | File |
|---|---|
| Zaryanification converter | `o27v2/zaryan_names.py` |
| Pro World Cup engine + rosters | `o27v2/pro_worldcup.py` |
| Country registry (`ZR`) | `o27v2/youth.py` (`_NATIONAL_TEAMS`, `_COUNTRY_REGION`) |
| Custom-flag filter | `o27v2/web/formatters.py:_CUSTOM_FLAGS` |
| Flag art | `o27v2/web/static/flags/zr.png` |
| Zora currency + basket math | `o27v2/currency.py` (ZORA section) |
| Live FX fetcher | `o27v2/fx.py` |
| Financials page route | `o27v2/web/app.py` (`financials_view`) |
| Financials template | `o27v2/web/templates/financials.html` |
| Zaryan name region | `o27v2/data/names/regions.json` (`zaryanovia` key) |
| Koryo-saram surnames (new pool) | `o27v2/data/names/surnames.json` (`koryo_saram` key) |
| 69-city Zaryan hometown pool | `o27v2/data/names/hometowns.json` (`ZR` key) |
| Zaryan team-name city pool | `o27v2/data/names/team_naming.json` (`eurasia_zaryanovia` key) |

---

## Open items not yet started

- **Procedural toponymy engine** — the four name generators (truncated
  fusion, phonetic drift, administrative double-barrel, indigenous
  substrate) from the toponymy spec for generating *new* Zaryan place
  names on demand. The 69 hand-authored cities are plenty for current
  use; the engine is for future expansion.
- **Dedicated `/zaryanovia` country info page** — would surface flag,
  history, oblast list, currency lore, basket live-status, hall of fame
  for Zaryan players, etc. — all the worldbuilding the wiki captures,
  in a single in-game page.
- **Master-wiki sport thread** — the wiki claims Zaryan baseball ("O27")
  was invented at a Zaryan university in 1991 and is governed
  internationally from Zaryanovia. Currently the in-game sport has no
  in-game origin attribution; tying the codebase's existing `o27` name
  to Zaryanovia in the lore would close that loop.
