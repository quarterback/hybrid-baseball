# After-Action Report — Postseason format choice + table-winner crown

**Date:** 2026-06-07
**Branch:** `claude/jolly-shannon-6mf74`
**Status:** Shipped & functionally validated against a temp DB. No Flask in the
sandbox, so the route/template were verified by Jinja parse + logic test, not a
live request.

---

## 1. The complaint

The owner loaded a pre-built **universe** save to test things quickly, opened
the Postseason page, and saw nothing but the AWARDS block plus the line
*"This league uses the table-winner model — no postseason bracket."* Two real
problems sat behind that:

1. **No choice.** Whether a league runs a bracket was dictated entirely by which
   config you picked, with no UI to choose. Universe/region configs hard-disable
   the postseason; presets/custom default to a bracket. The owner had no way to
   say "single table, but I still want playoffs" (or the reverse).
2. **No crown.** In the table-winner (soccer) model the page named *nobody*. The
   champion banner reads only from `playoff_series`, which is empty when there's
   no bracket — so a finished season showed awards and an apology, never the team
   that actually won the table.

The owner's framing was *"the templates aren't built for this new structure, so
they lack the schema to generate playoffs."* That turned out to be **the wrong
mechanism but the right instinct** — see below.

## 2. Root-cause findings

- There is **no DB schema** for postseason format. It's a single config field,
  `postseason`, read at runtime by `playoffs.postseason_disabled()` —
  `"none"` ⇒ soccer model, anything else ⇒ bracket. No migration could be
  "missing."
- `_active_config()` persists only the **config_id string** in `sim_meta`
  (`league_config`) and reloads the JSON by id. For **custom** saves the id is
  `"custom"`, which has no JSON on disk, so `_active_config()` returns `None` and
  the field is unreadable after creation. Any `postseason` set on the in-memory
  custom dict is dropped. So a per-save field that survives reload was needed,
  not a config edit.
- The loaded save was a **universe** config (the giveaway: the generic
  "Hitter/Pitcher of the Year" award names, set only by
  `build_universe_config()`), which hardcoded `"postseason": "none"`. The page
  was behaving *correctly* for that config — it was just empty and offered no
  alternative.

## 3. What changed

**Source of truth — a per-save override.** Added a `sim_meta` key
`postseason_format` (`"bracket"` | `"none"`). `postseason_disabled()` now reads
it first and falls back to the config field, so:
- new saves stamp an explicit choice that survives reload (custom included);
- old saves with no key keep their previous config-driven behavior (back-compat,
  including the owner's already-loaded universe, which now falls through to
  `postseason: none` and finally shows its table winners).

**Choice at creation, every path.**
- `/new-league` (preset + custom): a "Postseason format" `<select>` (default
  bracket). `new_league_post()` stamps the key after the save seeds.
- `/universe/new`: a "Postseason" `<select>` (default table winner — the
  universe norm). `build_universe_config()` gained a `postseason` param so the
  written JSON matches, and `universe_new_post()` stamps the key too.

**Crown the table winner.** New `playoffs.table_winner_champions()` returns the
best-record team per league (ties → more wins), but only when the postseason is
disabled *and* the regular season is complete. `playoffs.html` renders it as a
champion banner — a single big crown for a one-league world, a per-league grid
otherwise — with the "Advance to Next Season" button that bracket champions
already had. The fallback copy now points at Standings while a season is live.

## 4. Files touched

- `o27v2/playoffs.py` — override-aware `postseason_disabled()`,
  new `table_winner_champions()`.
- `o27v2/web/app.py` — `/playoffs` passes `table_champions`; `new_league_post`
  and `universe_new_post` stamp `postseason_format`.
- `o27v2/league.py` — `build_universe_config(postseason=...)`.
- `o27v2/web/templates/playoffs.html` — table-winner banner + smarter copy.
- `o27v2/web/templates/new_league.html`, `universe_new.html` — the selects.

## 5. Validation

- `postseason_disabled()` / `table_winner_champions()` exercised on a temp DB:
  no key ⇒ bracket; `none` ⇒ disabled + correct per-league table winners;
  `bracket` ⇒ enabled + empty champion list. ✔
- All three templates parse under Jinja2. ✔
- Python syntax check on all edited modules. ✔

## 6. What I did NOT change / follow-ups

- **Existing custom/preset saves** created before this change still have no
  override key, so they keep config-driven behavior. There is no backfill UI; a
  per-save "change postseason format" control on the league-edit page would let
  owners flip an existing league without recreating it. Deliberately out of
  scope here.
- The table-winner crown is **per league**, mirroring the season-archive logic
  in `app.py`. Per-division table winners (for multi-division single-league
  worlds) were not added.
- Not verified in a live browser (no Flask in the sandbox).
