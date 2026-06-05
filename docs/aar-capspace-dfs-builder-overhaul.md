# AAR — CapSpace DFS builder UX overhaul

## Context

Play-testing the DFS lineup builder surfaced five concrete usability gaps
(verbatim from the report): you can't see your own money or whether you're up
or down; the intro hero is always in the way; everything scrolls so your own
lineup is hard to find; it's easy to overspend on a stud and then be unable to
tell who you can still afford; and the player cards lean on *ratings* when the
fictional players have no name-recognition — what a DFS player actually reads is
**recent form and real stats**, not a 20–80 grade. This pass addressed all
five. Checked with `py_compile`, Babel transpile of every touched `.jsx`, and a
Flask `test_client` render against a seeded DB (`pytest` is absent here).

## Fixes

1. **You can see your own money everywhere.** Added a bankroll chip to the
   shared `TopBar` (`Balance · $X`), so your wallet rides along on every screen
   — including the builder, where previously only the *salary cap* was shown
   and your actual money was invisible. The label collapses on phones to stay
   compact.

2. **The intro hero is dismissible.** The Hub's big orange "Tonight's Daily
   Slate is live" hero now has a close button and is replaced by a slim
   "Play tonight's slate / Show intro" row once dismissed. The choice persists
   in `localStorage` (`o27.capspace.hero`), matching how "How it works" already
   remembers its collapsed state.

3. **Your picks sit on top.** The builder's roster rail is now first in the DOM,
   so on a phone (single-column) **your lineup renders above the player pool** —
   you see what you've drafted without scrolling. On desktop, explicit grid
   placement keeps the wide pool on the left and the lineup rail on the right,
   so the two-column layout is unchanged there.

4. **Budget-aware affordability.** `canAfford` now reserves the cheapest salary
   in the pool for each *still-open* slot, so "affordable" means you can pick
   this player **and still complete a legal lineup** — not just that the player
   fits under the raw remaining cap. Unaffordable rows are dimmed (not hidden)
   with a lock on the add button, and a new **"In budget"** filter chip hides
   everything you can't afford so you can shop only what's reachable with the
   money you have left.

5. **Stats over ratings on the cards.** Each pool row now carries a real season
   line drawn from persisted box-score data — `AVG · OBP · HR · RBI · SB` for
   hitters, `ERA · WHIP · K · QS` for pitchers — backed by a new batched
   `_season_lines()` query (two `GROUP BY` reads, keyed by player id). Salary is
   now always visible (it used to hide on mobile), the last-5 sparkline sits next
   to the projection, and ratings are demoted to the player drawer. Pre-season
   (no games yet) the row falls back to a quiet "No games yet · N% rostered"
   note instead of pretending to have a stat line.

## Notes / not changed

- The season line is cumulative across all phase-0 games, independent of the
  slate date — it's "who this player is this season," which is the right
  at-a-glance read. The per-game **last-5** detail still lives in the drawer.
- Dropped the standalone "VALUE" column from the pool row (it was `hide-narrow`
  and ancillary); pt/$ value now rides as a subscript under salary, and the full
  value tile remains in the drawer.
- Renamed the empty flex-slot label "Stay flex" → "Flex" — the word "stay" is
  O27 internal flavor and shouldn't leak into the UI (same reasoning as the
  batting-line cleanup).
