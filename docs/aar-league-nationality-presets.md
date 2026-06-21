# After-Action Report — Curated nationality presets

**Date:** 2026-06-21
**Branch:** `claude/vigilant-davinci-hn34xy`
**Status:** Shipped. Presets validated by generating names through the real
picker.

## Why

The country pool grew to 83 regions, but **50 of them were in no preset**
— including real baseball nations (Cuba, DR, Venezuela, Mexico, Brazil)
and most of the newly-added European/Asian/Caribbean countries. To use any
of them you had to hand-build a `{region: weight}` blend every time, which
made setting up a league with a chosen nationality mix tedious. Owner
wanted curated presets and an easy way to run an MLB-style league
*alongside* custom-nationality leagues in one universe.

## What changed

- `o27v2/data/names/regions.json`: added 10 curated presets so every
  region is now reachable from at least one preset (orphans: 50 → 0):
  `mlb` (authentic MLB demographics on the fine-grained baseball nations),
  `latin_america_full`, `caribbean_full`, `europe_full`, `balkans`,
  `nordic_baltic`, `caucasus_central_asia`, `middle_east`,
  `east_asia_full`, and `all_nations` (even global mix, ex-Zaryanovia).
  Weights are normalized fractions, matching existing presets.
- `o27v2/web/templates/universe_new.html`: the universe builder's
  quick-add league buttons now point the ⚾ MLB button at the new `mlb`
  preset and add one-click buttons for the new regional presets. Composing
  "MLB-style league + custom-nationality leagues" in one universe is now a
  few clicks — the per-league locale dropdown already listed all presets;
  this just makes the common shapes one-tap.

## Notes / scope

- No engine or schema change — presets are data, and the universe builder
  already accepted a per-league preset/region/blend (`lg_locale`). The gap
  was purely that the new countries weren't in any preset and the
  quick-adds were stale.
- The mixes and weights are my reasonable groupings; they're trivial to
  tweak in `regions.json` (relative weights, auto-normalized).
- Validated: each new preset run through `make_name_picker` produces
  country-coherent names with sensible distributions (e.g. `mlb` →
  US 65% / DO / VE / MX / CU; `all_nations` → 91 countries).
