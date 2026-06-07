# After-Action Report — Orphan name pools → nations, and a league-wide name scrub

**Date completed:** 2026-06-07
**Branch:** `claude/compassionate-clarke-L9oFl`

---

## TL;DR

Started from one observation — "Serbian name files exist but there's no Serbia
in the game" — which turned out to be two separate problems:

1. **Orphan name pools.** Five fully-curated national name pools
   (`serbian`, `albanian`, `estonian`, `latvian`, `georgian`) existed in the
   name files but were wired to **no region and no team**, so they never
   surfaced anywhere. Promoted each to its own region in `regions.json` **and**
   its own Frontier Cup team in `youth.py`. Backfilled the two empty female
   pools so each nation has both male and female names.
2. **Scraped pollution, league-wide.** The same scrape origin documented in the
   earlier name-scrub AAR was far more prevalent than previously cleaned — the
   European/African/Latin buckets were riddled with football/basketball club
   names, sports terms (`Calcio`, `Basket`, `Bàsquet`), cross-culture place
   names, and league/sponsor words. Removed **~2,370 junk tokens across ~90
   buckets** in all three name files.

---

## 1. Orphan pools → real nations

A nation is "in the game" only if it appears in **both** places:

* `o27v2/data/names/regions.json` — the name-region/country definition, and
* `o27v2/youth.py` (`_NATIONAL_TEAMS` / `_FRONTIER_TEAMS`) — the actual team.

Cross-referencing every name bucket against both surfaced exactly five buckets
that had a full curated name set but neither a region nor a team:
**Serbia (RS), Albania (AL), Estonia (EE), Latvia (LV), Georgia (GE)** — the
literal "serbian names but no Serbia" pattern.

For each I added:

* a **flat region** in `regions.json` (pinned to its ISO code, same shape as the
  existing `israel`/`iran`/`croatia` promoted regions), and
* its **own** Frontier Cup team in `_FRONTIER_TEAMS`, with a Europe entry in
  `_COUNTRY_REGION` for the `/youth` standings grouping.

The Frontier field grew 28 → 33 nations competing for 24 berths. `draw_groups`
already shuffles the field and lets the bottom slots miss out each season
(rotating qualification), so the larger field needed no code change — only the
narrative comment was updated.

**Female backfill.** `albanian` and `georgian` had empty `female_first` buckets
(`[]`). Since the user asked for both genders, I hand-curated ~32 authentic
female given names for each (Albanian: Albana, Besa, Vjosa…; Georgian: Nino,
Tamar, Salome…). Serbian/Estonian/Latvian already had female names.

### What I deliberately did NOT promote, and why

* **Generic blend / fallback pools** (`caribbean`, `nordic`, `east_asian`,
  `southeast_asian`, `scandinavian`, `indo_caribbean`, `french_african`,
  `swahili`, `australian`, `new_zealand`) — these are shared/legacy pools, not
  countries.
* **`austrian`, `belgian`, `swiss`, `kazakh`** — those countries already exist
  as teams; their regions just draw from a shared pool (e.g. Austria uses
  `german`). The curated bucket is unused, but the nation is present, so it's not
  the reported bug. Left as-is.
* **`icelandic`** — has male + female given names but **zero surnames** in the
  data (Icelandic patronymics were never seeded). A region needs resolvable
  `surname_keys`, so Iceland can't be added without a surname backfill. Left
  orphaned and flagged.
* **Female-only fragment keys** (`belarusian`, `chilean`, `colombian`,
  `mexican`, `peruvian`, `taiwanese`, `zimbabwean`, …) — keys present only in
  `female_first.json` with no male/surname counterpart. Most correspond to
  countries already in the game via other pools; they're dead data, not missing
  nations. Left untouched (out of scope).

---

## 2. League-wide name-pool scrub

`scripts/scrub_name_pools.py` already cleaned the CJK/Japanese buckets via
allowlists, and `tests/test_name_pool_clean.py` guards their idempotency. But
that scrubber never covered the European/African/Latin buckets — and adding the
new regions immediately exposed how bad they were: "Nevena **Calcio**", "Elena
**Basket**", "United **Wielkopolski**", "Saarlouis **Excellence**", "Jemal
**Cremonese**", "Wolfsberger Mamardashvili".

**Approach.** Dispatched three subagents, one per file
(`surnames.json`, `male_first.json`, `female_first.json`) — disjoint files, so
no write conflicts — each scanning all ~150 buckets and removing pollution under
conservative keep-rules. Each skipped the four scrubber-owned buckets
(`korean`, `chinese`, `japanese`, `chinese_taiwanese`) so the idempotency test
stays green.

| File | Removed | Buckets hit |
| --- | --- | --- |
| `surnames.json` | 1,167 | 55 |
| `male_first.json` | 889 | 69 |
| `female_first.json` | 310 | 46 |

Removed: soccer/basketball/MLS/NBA club names, sports terms in many languages,
league/federation/sponsor words, and cross-culture place names sitting in the
wrong bucket. **Kept** (per a "when 50/50, keep" rule): legit place-derived
given names (Victoria, Paris, Milan, Sofia, Rosario, Wellington), African virtue
names (Gift, Faith, Knowledge), and native toponym-surnames already on the
scrubber's whitelist (Houston, Hastings, Soto, Medina, Lima, Ponce).

**Second pass.** Sampling the new nations after the scrub caught a residue the
agents had kept: `Rīga` (Latvia surnames), and `Bar`/`Budućnost`/`Podgorica`/
`Vojvodina`/`Shkëndija` (Serbia surnames) — all towns/provinces/clubs. Removed
those six by hand. Cross-culture but genuine *given* names (Milan, Luca, Solomon)
were left — they're real diaspora names, not pollution.

---

## Validation

* All three name files + `regions.json` parse cleanly; no bucket emptied
  (`swahili` female was already `[]` in the source and is referenced by no
  region).
* `tests/test_name_pool_clean.py` logic (scrubber dry-run) reports **0 residual
  junk** — no regression against the existing scrubber's domain.
* `tests/test_name_regions.py` logic: every region/subregion key resolves to a
  real bucket; no region and no preset produces a `Player N` fallback; the five
  new countries pin correctly via `make_country_pinned_picker` and generate
  coherent male **and** female names.
* `pytest` is absent in this sandbox, so the two test modules were executed as
  inline logic rather than via the runner.

## What I did NOT change (honest caveats)

* **The scrubber script was not extended.** The pollution was removed directly
  from the committed JSON (the authoritative source), which keeps the
  idempotency test green, but `scrub_name_pools.py` does not *know about* the new
  junk types. A future re-seed from raw scrape would reintroduce club/place
  tokens that the script wouldn't catch. Extending its blocklists is follow-up
  work.
* **The subagent cleanup is heuristic.** ~2,370 tokens across ~90 buckets in
  languages I can't all read fluently — some misfiled cross-culture *given*
  names (diaspora footballers) almost certainly remain, and a few borderline
  toponyms were kept by design. The clear non-personal junk (clubs, sports
  terms, capital cities) is gone; the long tail of "real name, wrong bucket" was
  not chased.
* **No preset / league wiring for the new nations.** The five regions are not
  added to `regions.json` `presets` or `international.json` league name-mixes —
  they exist as standalone national pools/teams, matching how the other promoted
  micro-nations (Croatia, Slovenia, Lithuania) are wired.
* `icelandic` left orphaned (no surnames); female-only fragment keys left as
  dead data.

## Commits

* `b353fac` — add Serbia/Albania/Estonia/Latvia/Georgia regions + Frontier teams
* `7dd1235` — backfill Albanian/Georgian female names; scrub `female_first.json`
* `feaad8b` — scrub `surnames.json`
* `4c419ca` — scrub `male_first.json`
* `691b609` — second-pass residual place/club surname removal
