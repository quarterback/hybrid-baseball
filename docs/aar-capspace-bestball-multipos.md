# AAR — CapSpace: true multi-slot coverage in Best Ball

## Context

Multi-position eligibility shipped for the daily games (DFS, Streak, Sluggers);
the season drafts were deferred because their slot logic lives server-side. This
pass finishes the job for **Best Ball** — the one season game with real
positional slots (C / 1B / 2B / 3B / SS / OF×2 + 2 SP). Category Leagues was
reviewed and needs nothing: it's Roto by category totals and its draft only
checks hitter/pitcher *counts*, so positions there are a display tag, not a
constraint.

Checked with `py_compile`, Babel transpile, and direct module + Flask
`test_client` tests on a seeded DB.

## What changed (Best Ball)

1. **Eligibility in the engine.** `_Ctx` now loads `players.role_field_pos` and
   computes each hitter's eligible slot set (via the shared
   `data._eligible_positions`). `by_pos` lists a hitter under *every* slot they
   qualify for, so synthetic field rosters can use a utility player to cover any
   of them.

2. **Optimal per-slate lineup (`score`).** The old code bucketed each hitter by
   one position and took the top-N per slot. It now solves the actual
   assignment: `_assign_max()` is an exact DP over the tiny remaining-capacity
   state space (≤96 states for the 7-slot hitter lineup) that maximizes total
   points letting a multi-position player fill whichever slot helps most.
   Verified: a C/OF flex correctly slots into C so all three of (OF 10, OF 8,
   C/OF 6) score → 24.

3. **Coverage-aware draft validation (`draft`).** "Roster must cover every slot"
   is now a max bipartite matching (`_missing_slots`, Kuhn's algorithm) over the
   drafted hitters' eligibilities — so a SS/2B counts toward either slot, and you
   can build a legal roster the realistic way. The error still names the short
   slots. Verified: 9 OF-only hitters are correctly flagged short at
   C/1B/2B/3B/SS; a coverable roster passes.

4. **Client coverage chips match the server.** `bbCoverage()` runs the same
   matching in the browser, so the requirement chips tick green and the **Lock**
   button enables exactly when the roster can actually cover every slot — not
   when you happen to have drafted a player whose *primary* position matches.
   Pool rows and selected chips now show eligibility (e.g. `SS/2B`) via
   `posLabel`.

5. **Performance.** Per-slate assignment is heavier than a sort, and `standings`
   scores 49 rosters across the whole season. It's now memoized on
   `(roster, #slates-played)` — a pure key that only changes when a new slate is
   simmed — so repeated screen loads are instant (0.018s cached vs the cold
   compute).

## Notes

- The DP and the matching are both exact (not greedy), so a drafted roster gets
  full credit for its best legal lineup every slate, and the field is scored the
  same way.
- Category Leagues pools were intentionally left on single-position display;
  adding positional slots there would be a new game rule, not a fix.
