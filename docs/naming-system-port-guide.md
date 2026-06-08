# Porting the O27 international name system (for the tennis sim)

A self-contained guide for an agent lifting `hybrid-baseball`'s player-naming
system into another sport sim. It generates culturally-coherent names + an ISO
country code for any nationality mix, for **both** men and women.

> Verified against the live data in this repo on 2026-06-07: **120 country
> codes**, and **every region's `first_keys` currently have non-empty female
> buckets** (so the women's side is usable out of the box — but see Pitfall #2/#3
> before you trust it).

---

## 1. What to copy

### Data (`o27v2/data/names/`)
- **`male_first.json`, `female_first.json`, `surnames.json`** — `{bucket_key: [names…]}`, ~150 culture buckets each.
- **`regions.json`** — `regions` (a region = a flag-bearing nationality: either flat `first_keys`/`surname_keys`, or a list of weighted `subregions`) + `presets` (named region-weight mixes).
- Only if you also bring the scrubber (recommended): **`hometowns.json`** + **`team_naming.json`** (cross-referenced by it).
- **Ignore** `business_names.json`, `corporations.json`, `public_authorities.json`, `*_teams.json`, `o27_global.json` — not player names.

### Code (extract — do NOT copy all of `o27v2/league.py`, it's a huge baseball file)
Lift just the picker surface into a standalone `names.py`:
- `_load_name_pools()`, `get_name_regions()`, `get_name_region_presets()`
- `make_name_picker(rng, gender, region_weights) -> () -> (full_name, country_code)`
- `make_country_pinned_picker(rng, region_id, country_code, gender) -> () -> (full_name, cc)`
- helpers: `_pick_weighted_key`, `_normalise_weights`, `_resolve_country`, and the `_NAMES_DIR` constant.

### Zaryanovia — keep it (you want it)
`zaryanovia` is a **fictional** nation (alt-history Russian Far East) and you're
keeping it. It is **not** a static pool — it's a procedural creole generator in
`o27v2/zaryan_names.py` (`draw_zaryan_name(rng, gender) -> (name, "ZR")`) that
pulls American/Black-American given names + surnames from the JSONs and
"russifies" them (phonology, patronymics, feminization, surname-path A/B/C). The
picker has hardcoded `if region_id == "zaryanovia":` branches in **both**
`make_name_picker` and `make_country_pinned_picker` that bypass the normal path
and call it.

To port it:
- Copy `o27v2/zaryan_names.py` (self-contained: stdlib + reads the same name
  JSONs — make sure its `_load_pool` points at your names dir).
- Keep both `zaryanovia` branches in the picker functions.
- Copy the **flag asset** `o27v2/web/static/flags/zr.png` and the `_CUSTOM_FLAGS`
  entry (see §5) — `ZR` has no emoji flag.
- `zaryan_names` already has a `_feminize()` — note it for Pitfall #3 (it's the
  model for gender-correct Slavic/Baltic surnames).
- `mongolia` / `north_korea` are real countries but were baseball "expansion
  market" flavor; keep or re-weight them in your tennis presets as you like.

---

## 2. How to use it

- **Pattern A — tour mix:** `pick = make_name_picker(rng, "female", TENNIS_PRESET)`; call `pick()` per player → `(name, cc)`. Bulk-spawn a field with a realistic nationality spread.
- **Pattern B — nationality-first (recommended for tennis):** choose `cc` from your own distribution, then `make_country_pinned_picker(region_for[cc], cc, gender)`. Tennis players have a definite flag and you'll want explicit counts per nation. Prefer the **standalone promoted regions** (`spain`, `serbia`, `germany`, `france`… → 1:1 with a country) over blended multi-country regions.

---

## 3. Pitfalls (priority order)

1. **Silent fallback to "Player 437".** A typo'd/missing bucket key resolves to an empty list and the picker emits `Player <n>` instead of raising. **Port `tests/test_name_regions.py`** — it asserts every key resolves and every region/preset yields real names. Non-negotiable safety net.

2. **Female coverage is your #1 risk.** The baseball app generates **male-only** rosters, so women's pools were barely exercised. They're complete *today* for in-use regions (audited), but `female_first.json` still has dead "female-only" fragment keys and thin buckets, and any new region/edit can reintroduce a gap a WTA tour will expose instantly. **Run the audit in §5 after every change.**

3. **Patronymic/gendered surnames are NOT gender-aware — visible bug for a women's tour.** Surname buckets are shared across genders, but Russian (`-ov`/`-ova`), Czech/Slovak (`-ová`), Polish (`-ski`/`-ska`), Latvian/Lithuanian, and Icelandic (`-son`/`-dóttir`) surnames must agree with gender. Baseball ignores this (male-only). Unfixed, you'll ship "Anna Medvedev" / "Linda Novak". Add a feminization pass for those cultures (model it on `zaryan_names._feminize()`), or make an explicit decision to accept it.

4. **ISO alpha-2 (data) vs IOC 3-letter (tennis).** The pools store alpha-2 (`RS`, `ES`, `GB`); tennis shows `SRB`, `ESP`, `GBR`. Use the map in §4. Edge cases: `scotland` is tagged `GB`; fictional `ZR` has no real flag.

5. **The presets encode a *baseball* fantasy — rewrite them.** `o27_year_1/5/10`, the "Malaysia growth market", cricket-conversion pipeline, Zaryan-expansion mixes. Copy the regions/buckets, but write your own **tennis** presets (heavy US/ESP/FRA/RUS/SRB/ITA/GER/ARG/AUS/CZE/SUI + Eastern Europe; little/no cricket weighting). Keep `zaryanovia` in at whatever share you want — just set it deliberately rather than inheriting the baseball weights.

6. **Keep names coherent — don't flatten subregions.** A subregioned region picks ONE subregion by weight and draws first AND surname from *that* one ("Babar Iqbal", not "Babar Iyer"). Merging keys reintroduces cross-culture mashups.

7. **The data was scraped then cleaned — don't re-scrape.** Bring `scripts/scrub_name_pools.py` (incl. the 838-token `SCRAPED_SPORTS_JUNK` guard) and `tests/test_name_pool_clean.py`. Originally polluted with football/basketball clubs, places, sports terms; scrubbed this session. The guard keeps re-pollution out on any future re-seed.

8. **Determinism:** the picker takes a seeded `random.Random` — thread your own seed for reproducible draws.

---

## 4. alpha-2 → IOC/ITF 3-letter code map (all 120 in-use codes)

IOC-style; adjust the handful where your federation differs (e.g. some use NIG/LBY/TGA/TRI). `ZR` is fictional.

```python
ALPHA2_TO_IOC = {
    "AE":"UAE","AF":"AFG","AG":"ANT","AL":"ALB","AO":"ANG","AR":"ARG","AS":"ASA",
    "AT":"AUT","AU":"AUS","AW":"ARU","BB":"BAR","BD":"BAN","BE":"BEL","BG":"BUL",
    "BM":"BER","BN":"BRU","BR":"BRA","BS":"BAH","CA":"CAN","CH":"SUI","CL":"CHI",
    "CN":"CHN","CO":"COL","CU":"CUB","CV":"CPV","CW":"CUW","CZ":"CZE","DE":"GER",
    "DK":"DEN","DO":"DOM","DZ":"ALG","EE":"EST","EG":"EGY","ES":"ESP","ET":"ETH",
    "FI":"FIN","FJ":"FIJ","FR":"FRA","GB":"GBR","GE":"GEO","GH":"GHA","GR":"GRE",
    "GU":"GUM","GY":"GUY","HK":"HKG","HR":"CRO","HT":"HAI","HU":"HUN","ID":"INA",
    "IE":"IRL","IL":"ISR","IN":"IND","IR":"IRI","IS":"ISL","IT":"ITA","JM":"JAM",
    "JP":"JPN","KE":"KEN","KH":"CAM","KP":"PRK","KR":"KOR","KZ":"KAZ","LA":"LAO",
    "LB":"LBN","LK":"SRI","LT":"LTU","LV":"LAT","LY":"LBA","MA":"MAR","MG":"MAD",
    "MM":"MYA","MN":"MGL","MU":"MRI","MX":"MEX","MY":"MAS","MZ":"MOZ","NA":"NAM",
    "NG":"NGR","NI":"NCA","NL":"NED","NO":"NOR","NP":"NEP","NZ":"NZL","OM":"OMA",
    "PA":"PAN","PE":"PER","PG":"PNG","PH":"PHI","PK":"PAK","PL":"POL","PR":"PUR",
    "PS":"PLE","PT":"POR","RO":"ROU","RS":"SRB","RU":"RUS","SA":"KSA","SE":"SWE",
    "SG":"SGP","SI":"SLO","SK":"SVK","SM":"SMR","SR":"SUR","TH":"THA","TN":"TUN",
    "TO":"TGA","TR":"TUR","TT":"TTO","TW":"TPE","TZ":"TAN","UA":"UKR","UG":"UGA",
    "US":"USA","UZ":"UZB","VE":"VEN","VN":"VIE","WS":"SAM","ZA":"RSA","ZR":"ZAR",
    "ZW":"ZIM",
}
```

---

## 5. Flags — port these too

Flag rendering is `o27v2/web/formatters.py:_flag(country_code)`:
- **Real ISO alpha-2 → regional-indicator emoji pair** (e.g. `RS` → 🇷🇸). The OS/browser emoji font draws it; **no asset files needed** for the 119 real countries.
- **Fictional codes in `_CUSTOM_FLAGS` → an inline `<img>`** to `/static/flags/<file>`. Current registry is `{"ZR": "zr.png"}` (Zaryanovia). The asset lives in `o27v2/web/static/flags/` (`zr.png`, 171×96, ~7 KB; authoring/sizing notes in that dir's `README.md`).

**To port the flags:** copy `_flag()` + the `_CUSTOM_FLAGS` dict, the whole `o27v2/web/static/flags/` directory (`zr.png` + `README.md`), and the `.player-flag-img` CSS the templates use (`height:1em; vertical-align:-0.15em`). Since you're keeping Zaryanovia, `zr.png` is **required** — `ZR` has no emoji.

**Windows caveat:** regional-indicator flag emoji do **not** render on Windows (it shows the two letters instead). If your UI must show real flags on every platform, replace the emoji branch with an SVG flag set — the [`flag-icons`](https://github.com/lipis/flag-icons) library is keyed by alpha-2, so `RS` → `<span class="fi fi-rs">`, a near drop-in. Keep `_CUSTOM_FLAGS`/`zr.png` for `ZR` (and any other fictional nation) alongside it.

## 6. Female-coverage audit (re-run after any data edit)

```python
import json, os
ND = "data/names"  # adjust to your tree
reg = json.load(open(f"{ND}/regions.json"))["regions"]
female = json.load(open(f"{ND}/female_first.json"))

def first_keys(r):
    ks = set()
    for sr in (r.get("subregions") or [r]):
        ks |= set(sr.get("first_keys", []))
    return ks

gaps = {rid: [k for k in first_keys(r) if not female.get(k)]
        for rid, r in reg.items()}
gaps = {k: v for k, v in gaps.items() if v}
assert not gaps, f"female buckets empty/missing: {gaps}"
print("female coverage OK")
```

---

## 7. Validation checklist
- [ ] `test_name_regions.py` ported + green.
- [ ] `test_name_pool_clean.py` + scrubber ported + green.
- [ ] §6 female-coverage audit green (it's clean in source — keep it that way).
- [ ] `country -> region` map covers every nationality you spawn; alpha-2 → IOC map (§4) wired for display.
- [ ] Zaryanovia ported: `zaryan_names.py` + both picker branches + `zr.png` + `_CUSTOM_FLAGS["ZR"]`.
- [ ] Flags ported: `_flag()`, `_CUSTOM_FLAGS`, `static/flags/`, `.player-flag-img` CSS (+ optional `flag-icons` for Windows).
- [ ] Tennis presets written (not the baseball ones), with `zaryanovia` weighted deliberately.
- [ ] Gendered-surname feminization done for Slavic/Baltic/Icelandic (or accepted explicitly).
