# After-Action Report — Force Full Field: never play a game with an empty position

**Date completed:** 2026-06-23
**Branch:** `claude/vigilant-davinci-hn34xy`

---

## The bug the user found

A live game (`/game/15`) fielded **no third baseman for all 27 outs** — the
defensive log showed `THIRD BASE (3B) | —`. A team had eight men on the field
(pitcher + seven fielders) instead of nine. A game must never be playable with a
hole in the field.

> "Positions HAVE to be forced … you HAVE to play a person at each field
>  position." … "It's EIGHT PLUS THE PITCHER FOR 9. If they don't have an
>  eligible player at that position, they should just play someone on the bench,
>  it doesn't matter!" … "Someone playing each position including pitcher." …
>  "Teams should always manage their rosters to have two of every position in
>  the infield and at least five outfielders — forcing this will help but no
>  game can be played without one of each position."

Two layers, in the user's priority order: a **hard game-time guarantee** (the
non-negotiable) and a **roster-depth contract** (the preventive "help").

## Root cause

`_assign_game_positions` (o27v2/sim.py) spreads the starting fielders across the
eight canonical slots, but it only fills slots for the bodies it is **given**.
With eight bodies it always covers all eight (verified). The old top-up only
borrowed from the joker/DH pool, so a league that carries **no joker/DH pool**
(e.g. an imported real-name league) plus one thin position — a bag drained by
injury with no like-for-like reserve — handed the assignment **seven** bodies,
and the last slot rendered as `—`. The defensive log was faithful; the lineup
genuinely had no one stamped `3B`.

## The fix — two layers

### 1. Game-time hard guarantee (o27v2/sim.py) — the non-negotiable

Every one of the nine positions is force-manned at build, from the widest pool —
*it does not matter who covers the bag*:

- **`_topup_to_eight(starting, *pools)`** — forces the fielders up to eight
  distinct bodies, pulling in preference order from leftover **bench bats → the
  joker/DH pool → a spare arm**. Replaces the old joker/DH-only top-up.
- **Pitcher floor (the ninth position)** — if the staff is empty (a roster with
  no eligible arm), force any body onto the mound (prefer a joker/DH so the
  field bats are untouched; a fielder only as a last resort).
- **`_verify_field_complete(starting_fielders, todays_sp)`** — last-line check
  after assignment: stamps the pitcher `P` and redeploys any spare starter into
  a still-open slot. Normally a no-op; it makes a hole structurally impossible.

The runtime substitution paths already preserve positions — `defensive_sub`
(and the injury path, which routes through it) sets `player_in.game_position =
out_pos`, and `joker_to_field` resolves to the vacated slot — so once the field
starts full, swaps keep it full.

### 2. Roster-depth contract (o27v2/league.py) — the preventive

`generate_players` already produced 2–3 of each infield slot and ~9–11
outfielders, but that was incidental. **`_enforce_roster_depth`** makes it a
*forced* contract: at least **two of each infield slot (C, 1B, 2B, 3B, SS)** and
**at least five outfielders** on the active roster, topping up any shortfall. A
no-op on today's mix, but it guarantees the depth holds if the composition is
ever retuned (and documents the contract: `_REQUIRED_INFIELD_DEPTH`,
`_MIN_OUTFIELDERS`).

## Validation

- **Unit (`o27v2/tests/test_position_coverage.py`, 8 tests):** eight bodies
  always cover all eight slots even with a duplicate native and no native 3B;
  seven bodies reproduce the exact hole (the documented failure mode);
  `_topup_to_eight` forces eight from bench/jokers before spending a spare arm,
  and falls back to a spare arm when no bats remain; `_verify_field_complete`
  stamps the pitcher and repairs a duplicated-slot hole; generated rosters meet
  the depth contract and `_enforce_roster_depth` tops up a thin one.
- **Regression:** `o27/tests` + `tests/test_stat_invariants.py` +
  `o27v2/tests/test_defensive_log.py` → **199 passed, 12 skipped** (skips are the
  flask/DB-bound o27v2 modules, environmental).
- **Live pipeline:** a 40-game sim → **all 80 team-games field all eight
  positions, zero gaps** (scan of persisted `game_batter_stats.game_position`).

## Scope / not changed

- **Mid-season roster *management* is not rebuilt here.** The depth contract is
  enforced at roster *generation*; continuous re-balancing as injuries/trades
  erode depth over a season is a larger, separate system. It is not required for
  correctness — the **game-time guarantee makes an empty position impossible
  regardless of roster state**, which is the rule the user set ("no game can be
  played without one of each position").
- The emergency fallbacks (a spare arm at a bag, a position player on the mound)
  only trigger on genuinely depleted rosters; on normal rosters every top-up is
  a no-op. They are deliberately "any warm body" — per the user, an empty
  position is never acceptable, and who fills it does not matter.
