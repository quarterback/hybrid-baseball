# AAR — CapSpace: sim date in the top bar, paginated pools, multi-position eligibility

## Context

Three play-testing asks, plus two terminology/clarity fixes, all from live use of
the deployed save:

1. "The fantasy app should have the corresponding date from the sim … it should
   be viewable from the top menu." The sim/slate date was only shown on desktop
   (`hide-mobile`) and labeled confusingly ("Sim day May 11").
2. "Scrolling to find players is a lot of friction … it should be different
   players ending pages." Every game's player pool rendered the whole list
   (Best Ball/Categories even hard-sliced to 120) — endless scroll on a phone.
3. "Players should be able to qualify [at] multiple positions … realistic …
   players who could play more positions." Every player was pinned to one slot.
4. "What the hell is a cashed entry?" — DFS jargon with no explanation.

All changes were checked with Babel transpile of every touched `.jsx`,
`py_compile` on `data.py`, and a Flask `test_client` render + API smoke against a
seeded DB (`pytest`/`flask` vary by sandbox).

## Changes

1. **Sim date in the top bar, everywhere.** The date chip is no longer
   `hide-mobile` — it shows on phones too, compacted. Reworded from "Sim day
   May 11" to a clean **"● Slate · Apr 1"** (the dot reads as a live indicator,
   the label clarifies it's tonight's slate date). The value is derived from the
   live `SLATE_DATE` (next-unplayed slate), so it tracks the sim correctly.

2. **Paginated player pools (no endless scroll).** New reusable `PagedList`
   pages any long list (25–30/page) with Prev / "Page 2 / 7 · 184 players" /
   Next controls, and resets to page 1 whenever the search or position filter
   changes. Wired into all six pools: DFS builder, Go Streaking, Sluggers,
   Pilots, Category Leagues, and Best Ball (the last two dropped their hard
   120-row cap).

3. **Multi-position eligibility (data-backed).** The engine already tracks every
   field position a player can man in `players.role_field_pos`; CapSpace now
   reads it. New `_eligible_positions()` returns each player's CapSpace slots
   (primary first, extras de-duped, LF/CF/RF→OF), surfaced as `posEligible` on
   every pool player. Effects:
   - **DFS slot-fill** now places a player in any open slot they're eligible for
     (dedicated spots before the flex), so a SS/2B fills either.
   - **Position filters** on the daily games (builder, Streak, Sluggers) match
     *any* eligible position — a 2B/SS shows under both 2B and SS.
   - **Badges** show eligibility (e.g. `SS/2B`, or `1B/OF+2` for super-utility)
     instead of a single position.
   - Verified: 76 of 208 hitters are multi-eligible on a seeded slate; pitchers
     stay `PILOT`-only.

4. **Plainer language.** "Cashes / entries cashed" → **"Paid finishes / entries
   that won money."** "Beat the field to cash" → "to win." The live board's
   "cash N" → "win line N." (Backend column/stat names like `cashes`,
   `cash_line` are unchanged — chat-facing copy only.)

## Notes / deferred

- Multi-position is applied to the daily/slate games (DFS, Streak, Sluggers).
  Category Leagues and Best Ball season drafts still bucket position
  server-side; their pools now carry `posEligible` and the client filter honors
  it (behavior-preserving via a `[p.pos]` fallback), but the server-side
  draft-coverage / auto-lineup logic that decides which slot a drafted player
  fills is unchanged. Teaching those season games true multi-slot coverage is a
  clean follow-up in `categories.py` / `bestball.py`.
- An environment reset early in this pass left the local checkout on a stale
  commit; the work was recovered by syncing to `origin/main` (which already
  contained the prior CapSpace features). No work was lost.
