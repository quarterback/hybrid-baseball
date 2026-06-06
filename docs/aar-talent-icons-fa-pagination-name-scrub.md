# After-Action Report — Talent icons, free-agent pagination, name-pool scrub

**Date completed:** 2026-06-06
**Branch:** `claude/talent-icons-free-agent-page-eXlWD`

---

## TL;DR

Three loosely-related UI/data asks off a batch of mobile screenshots:

1. **Talent icons** — swap the Unicode `◆` diamonds + 🍼 baby-bottle emoji for
   crisp Bootstrap Icons stars (filled = current tier, outline = projected
   upside). Self-hosted the icon font under `static/vendor`, matching the
   existing first-paint-without-CDN convention.
2. **Free-agent page** — dropped the redundant per-position count tiles and
   added pagination (50/page). The table previously rendered the *entire* pool
   (~2,356 rows) on one page.
3. **Name generation** — the real bug. Several country pools were polluted with
   scraped sports-roster junk, producing names like "Hyun-soo Knights" and
   "Red Young-pyo". Scrubbed all three name files and added a guard test.

---

## 1. Talent icons → Bootstrap Icons

The headline overall chip (`_overall_chip` in `o27v2/web/app.py`) drew 1–6 gold
`◆` for the current rating band and a string of 🍼 for projected upside on
prospects (age ≤ 26). The diamond glyph rendered inconsistently across
platforms and the baby bottle is an emoji, so it picked up the OS emoji font.

- Self-hosted **Bootstrap Icons 1.11.3** (`bootstrap-icons.css` +
  `fonts/bootstrap-icons.woff2/.woff`) under `o27v2/web/static/vendor`, wired
  into `base.html` right after `bootstrap.min.css`. Same self-hosting rationale
  as the existing Archivo/Oswald/Bootstrap bundle — first paint never waits on
  a CDN.
- `_overall_chip` now emits `<i class="bi bi-star-fill ovr-star">` for the
  current tier and `<i class="bi bi-star ovr-star-upside">` (dimmed gold
  outline) for the upside gap. The tier math (`_overall_to_diamonds`) and the
  hover-title (`Overall: N · projected peak: M`) are unchanged, so behaviour is
  identical — only the glyphs changed. Every caller goes through the shared
  `overall_chip()` Jinja global, so players list, player page, compare, and the
  free-agent table all update at once.

The icon font is now available app-wide, so future UI can pull from the full
Bootstrap Icons set instead of reaching for more Unicode/emoji.

## 2. Free-agent pagination

`free_agents()` loaded every `team_id IS NULL` player and the template rendered
all of them — a heavy page on a populated league. It also showed a grid of
per-position count tiles that just duplicated the position dropdown.

- Removed the tile grid. The position dropdown now feeds off a lightweight
  `SELECT DISTINCT position` (`positions`) instead of the full count map.
- Added server-side pagination: `PER_PAGE = 50`, `?page=N`, clamped to range.
  The route passes `page`, `page_count`, `showing_from/to`; the template renders
  a Bootstrap pagination nav (first/last always shown, ±2 around current, `…`
  gaps) that carries the active `pos`/`kind`/`sort` filters across pages. The
  header now reads `… · showing 101–150`.

## 3. Name-pool scrub (the actual bug)

The buckets in `o27v2/data/names/{male_first,female_first,surnames}.json` were
seeded from scraped sports rosters/fixtures, which dragged in three kinds of
non-personal tokens:

- **Club mascots / sponsors** — `Antlers`, `Bluewings`, `Reysol`, `Steelers`,
  `Motors`, `VfB`, `Knights`, …
- **City / place names** — `Dortmund`, `Frankfurt`, `Hiroshima`, `Busan`,
  `Jakarta`, `Zagreb`, …
- **Misfiled name *parts*** — Korean *given* names dumped into the Korean
  **surname** bucket (`Hyun-soo`, `Heung-min`, `Young-pyo`); Chinese given-name
  romanisations and foreign-player surnames in the Chinese **surname** bucket
  (`Jianlian`, `Andersen`, `Đorđević`).

That is exactly why the UI produced "Hyun-soo Knights" / "Red Young-pyo".

**Fix:** `scripts/scrub_name_pools.py` — idempotent, prints a full per-bucket
removal report, `--dry-run` supported. Strategy, deliberately conservative to
avoid nuking real names:

- **Surnames** — drop single-token city names (from `hometowns.json`) minus a
  whitelist of city words that *are* legit surnames (`Mendoza`, `Santiago`,
  `Cruz`, `Khan`, `Hamilton`, …). City-as-surname is rare; the whitelist covers
  the real ones.
- **First names** — place-as-given-name is a real Western convention (`Paris`,
  `Victoria`, `Dallas`, `Milan`, `David`), so the broad city sweep is **not**
  applied here. Only an explicit list of foreign cities that are never given
  names is stripped (`Busan`, `Beijing`, `Yokohama`, …).
- **All pools** — drop mascot/club/sponsor junk (`team_naming.json` mascot
  words + an explicit club list).
- **Korean & Chinese surnames** — too polluted with misfiled given names +
  foreign players for a blocklist to save. Rebuilt by **intersection with a
  canonical surname allowlist** (Hundred-Family-Surnames for Chinese; standard
  romanised Korean surnames). Hyphenated tokens, always given names in Korean,
  fall out automatically.

**Result:** 779 junk tokens removed. Korean keeps 29 canonical surnames,
Chinese 52 — both realistic (Korea genuinely has a small surname set). Sample
output post-scrub: `Yong-soo Choi`, `Woo-jin Kim`, `Sun Gao`, `Hideki Kato`.

### Follow-up: Korean & Chinese *first-name* pools

The first pass only fixed the surname buckets; the same scrape had also dumped
*surnames* into the Korean/Chinese **given-name** slots (`Kim`/`Cho`/`Choi` and
`Wang`/`Chen`/`Zhang` as "first" names), plus Chinese provinces (`Fujian`,
`Guangdong`) and foreign players (`Michael`, `Aleksandar`). Those buckets are
now rebuilt:

- **Korean** — given names are reliably hyphenated, so the rebuild keeps
  existing hyphenated tokens (+ a small keep-list like `Bora`) and adds a
  curated canonical given-name set; single-token surnames/junk fall out.
- **Chinese** — single-syllable surnames and given names overlap too much for a
  blocklist, so every surname/place/foreign token is stripped and the pool is
  re-seeded from a curated given-name list (which legitimately re-introduces
  syllables like `Wei`/`Tao`/`Hao`).

Another 213 tokens removed. Post-fix samples: `Min-jun Ahn`, `Yong-soo Seo`,
`Jin-ah Song` (KR); `Tianyu Zhou`, `Jiahao Feng`, `Li Feng` (CN). The rebuild is
a fixpoint, so the idempotency guard still holds.

### Follow-up: bolster the KR/CN sets

The scrape-derived buckets were thin (whatever the roster snapshot happened to
contain), so all six CJK pools were enriched. Surnames now seed the **full
canonical list** rather than intersecting with the scrape, and the given-name
sets roughly doubled:

| pool | before | after |
| --- | --- | --- |
| KR surnames | 29 | 71 |
| CN surnames | 52 | 95 |
| KR first (m / f) | 57 / 46 | 96 / 81 |
| CN first (m / f) | 72 / 69 | 112 / 104 |

The Chinese surname bucket is now consistent Mandarin pinyin — the Cantonese
romanisations the scrape dragged in (`Chan`, `Wong`, `Ng`, `Tse`…) were dropped,
since the `chinese` bucket represents the mainland and HK/overseas spellings
belong to their own pools.

**Guard:** `tests/test_name_pool_clean.py` re-runs the scrubber in dry-run mode
and asserts zero residual removals, so a future re-seed that reintroduces junk
fails loudly. The existing `tests/test_name_regions.py` invariants (every bucket
ref resolves, no `Player N` fallbacks, every country pin produces real names)
were re-checked manually and still pass.

---

## Validation & what I did NOT do

- `flask` is absent in this sandbox, so the app couldn't be booted. Validated
  instead with `py_compile`, Jinja parse + a mocked render of `free_agents.html`
  (pagination nav, showing-range, ellipsis, filter-carrying links all correct),
  and direct exercise of the name pipeline (`make_name_picker` /
  `make_country_pinned_picker`).
- Did not touch the engine, schema, or any non-web behaviour.
