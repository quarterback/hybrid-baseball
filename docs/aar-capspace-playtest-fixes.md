# AAR — CapSpace play-testing fixes

## Context

Live play-testing the deployed save (`superinnin.gs`) surfaced a cluster of
bugs and UX gaps across the games. This pass cleared them. Theme: the games
worked in isolation on fresh test DBs, but real use — a mature save, a stale
page, a phone viewport — exposed the seams. Everything below was reproduced,
fixed, and checked via the Flask `test_client` + direct module calls
(`pytest` is absent in the sandbox).

## Fixes

1. **Pitcher cards showed a batting game log (and a wrong projection).**
   `_build_logs` filled each player's last-5 from batting first, so any pitcher
   who also takes ABs got a *batting* line — and the card's projection was the
   average of those batting FPs (a pilot reading PROJ 7.5 instead of his real
   pitching number). Made the log strictly the player's **primary side**:
   pitchers get pitching lines + a pitching projection, position players keep
   batting. This also corrected the Pilots pool projections, which shared the
   bug. Verified a real pitcher now shows `arc/K/ER` lines only.

2. **DFS: "A picked player isn't on this slate."** `build_slate_data()` always
   rebuilt the *current* (next-unplayed) slate, and `_pool_for(slate_date)`
   returned an **empty** pool whenever the contest's slate ≠ the current one —
   so once the live slate advanced (a game went final), entering an
   already-built lineup rejected every player. Parameterized
   `build_slate_data(slate_date)` so a contest re-derives its **own** slate's
   pool regardless of where the live clock is. Verified entry succeeds on a
   past slate (pool went 0 → 185).

3. **Sportsbook bet slip — couldn't confirm.** The slip was `position: sticky;
   bottom: 0`, but the bottom nav is `position: fixed; bottom: 0; z-index: 50`,
   so it covered the slip's lower row — the **Place button**. Pinned the slip
   `fixed` above the tabbar (`bottom: calc(68px + safe-area)`, `z-index: 60`)
   so Place is always reachable.

4. **No position filtering in the draft/pick pools.** Added a reusable
   `PosFilter` (tappable C/1B/2B/3B/SS/OF chips) and wired it into Go Streaking,
   Sluggers, and Category Leagues; Best Ball already had position-aware
   requirement chips that double as filters. Categories pool positions are now
   bucketed (CF/LF/RF → OF) so filtering is clean. Pilots (all one position)
   intentionally shows none.

5. **"Nothing appears" — bets weren't tracked across games.** The Entries
   screen only listed **DFS** lineups, so Sportsbook bets and the other games'
   buy-ins never showed (compounded by #2 and #3 meaning nothing actually
   placed). New `GET /api/activity` aggregates everything — DFS lineups,
   Sportsbook bets, and all buy-ins (Sluggers / Pilots / Category Leagues /
   Best Ball) — into one feed. The screen is now **"My Action"** with Live /
   Settled tabs; each row shows the game, what you played, your stake, and the
   result (+win / −loss / push / live). Verified it surfaces a bet, a season
   league, and a slate buy-in together.

## Earlier in the same play-test arc (already shipped)

- **USD default.** CapSpace read the engine's shared currency key and inherited
  its canonical-guilder default; it now keeps its own preference defaulting to
  USD, so the app opens in dollars.
- **"How it works"** collapsible instructions on every game mode (objective,
  steps, scoring, buy-in, payout), remembered-collapsed per mode.

## Notes / not changed

- The unified feed dropped the old per-DFS "tap to open the live board" link
  (activity rows don't carry a contest id); the Live screen is still reachable
  from the nav. Re-linking is a small follow-up if wanted.
- A position player who mops up on the mound (rare) correctly keeps his batting
  log — only `is_pitcher` players switch to a pitching log.
