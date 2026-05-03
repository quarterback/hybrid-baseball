# After-Action Report — Task #65: Expanded Rosters, Talent Tiers & Live Pitcher Roles

**Date completed:** 2026-05-03
**Branch:** main
**Commit:** d4a85c6

---

## What was asked for

1. Expand every team's roster to roughly 47 players (35 active + 12 reserve).
2. Roll every attribute — hitting skill, speed, Stuff, Stamina — **independently** from a 9-tier talent distribution so genuine star players exist alongside replacement-level depth.
3. Have the manager AI derive each pitcher's role **live** from current Stamina and Stuff at the moment of each appearance. No `pitcher_role` value would be stored or read; aging and attribute drift would re-cast arms naturally.

---

## What was built

### 1. 9-Tier talent ladder (`o27v2/league.py`)

A `_TALENT_TIERS` table maps cumulative probability to 20–80 scout grades:

| Tier | Prob | Grade range |
|------|------|-------------|
| Elite | 2 % | 75–80 |
| Excellent | 5 % | 65–74 |
| Very Good | 10 % | 60–64 |
| Good | 15 % | 55–59 |
| Above Average | 18 % | 50–54 |
| Average | 20 % | 45–49 |
| Below Average | 15 % | 40–44 |
| Replacement | 10 % | 30–39 |
| Sub-Replacement | 5 % | 20–29 |

`_roll_tier_grade(rng)` draws one attribute against this table. Because every attribute on every player is rolled in a separate call, a pitcher can have elite Stuff (75) but sub-average Stamina (28), producing the short-burst ace archetype without any explicit role tag.

### 2. Expanded roster generation

Two builder helpers replace the old monolithic generator:

- **`_make_hitter(rng, pos, is_active, name)`** — rolls `skill`, `speed`, and a capped emergency `pitcher_skill` independently.
- **`_make_pitcher(rng, is_active, name)`** — rolls `pitcher_skill` (Stuff) and `stamina` as **fully independent** tier draws. `pitcher_role` is always left blank.

Roster shape per team (47 total):

```
Active (34)
  12 fielders  →  8 starters (CF/SS/2B/3B/RF/LF/1B/C) + 4 bench
   3 DH/utility bats
  19 pitchers   (no role tags — all arms compete on attributes)

Reserve (13)
   8 position players  (is_active = 0)
   5 pitchers          (is_active = 0)
```

### 3. Schema changes (`o27v2/db.py`)

Two new columns added to `players` via `ALTER TABLE` (migration-safe, defaults applied automatically on existing DBs):

- `stamina INTEGER DEFAULT 50`
- `is_active INTEGER DEFAULT 1`

The seed `INSERT` in `league.py` was updated to populate both columns.

### 4. Engine `Player` dataclass (`o27/engine/state.py`)

Added `stamina: float` field (defaults to the same value as `pitcher_skill` for backward compatibility with pre-Task-65 DB rows).

### 5. Live SP selection (`o27v2/sim.py`)

`_db_team_to_engine` now:

1. Calls `_recently_used_pitcher_ids(team_id, game_date, days_back=4)` to build a rest-exclusion set from the last 4 sim days of `game_pitcher_stats`.
2. Picks today's SP as **the highest-Stamina active arm not in that set**, so the rotation falls out of the attribute distribution without any round-robin index. If every arm is "tired" it falls back to the full pool so games never stall.
3. Lineups are 8 fielders + SP + 3 DH = 12 batters, same slot count as before.

### 6. Live bullpen role derivation (`o27/engine/manager.py`)

`pick_new_pitcher` now scores each candidate at the moment of each appearance using `state.outs` (total outs recorded by the fielding team so far in the half):

| Outs recorded | Scoring rule | Intent |
|---|---|---|
| ≥ 19 | max Stuff | Late-inning / closer leverage |
| 10 – 18 | Stuff dominant; penalise high-Stamina arms | Keep workhorses available; use mid-relief types |
| < 10 | max Stamina | Long relief or spot start |

No `pitcher_role` column is read anywhere in the selection path.

### 7. One-for-one IL replacement (`o27v2/injuries.py`)

`get_active_players` now:

1. Pulls `is_active = 1` healthy players as the base active roster.
2. Counts pitchers and position players.
3. If either count is below the active-roster target (19P / 15 position), promotes the best available matching reserves **in memory only** — DB flags are never flipped.
   - Pitcher fill: sorted by `stamina DESC`.
   - Hitter fill: same-position reserves come first, then highest-skill bats.
4. When the injured player's `injured_until` date passes, they reappear in the `is_active = 1` query and the ephemeral call-up stops automatically.

---

## Key decisions and trade-offs

### Why independent rolls instead of correlated attributes?

Previous generators drew attributes from a gaussian centred on a team-profile value, which meant every player on a "pitching team" was above average at pitching. The tier ladder with independent rolls produces genuine outliers — an elite bat on a weak team, a one-pitch reliever with elite Stuff but no stamina — which is essential for meaningful leaderboards.

### Why no stored pitcher_role?

Storing a role tag means every aging event, trade, or injury needs a re-tagging pass and a UI sync. Live derivation means a 34-year-old workhorse whose Stamina has drifted from 62 to 41 just quietly stops getting starts — no migration, no backfill, no UI chip to remove.

### Why ephemeral reserve promotion rather than flipping is_active?

Flipping the flag would require a second flip (or an aging job) when the injured player returns, plus a waiver/DFA pass if both players are healthy at the same time. The ephemeral model keeps the DB append-only for injury events and makes the active roster a pure function of today's game date.

---

## What was verified

- **All 9 stat invariant tests pass** (`tests/test_stat_invariants.py`) — both before and after the code-review fix round.
- **Roster shape confirmed**: `SELECT COUNT(*) … GROUP BY team_id` returns 47/team, 34 active, 24 pitchers (19 active + 5 reserve) for every team in the fresh seed.
- **Attribute distribution**: skill 26–66, Stuff 27–68, Stamina 20–66 across a sample team — the full tier ladder is reachable.
- **Injury fill verified** end-to-end: injuring 2 active pitchers + 1 active fielder on team 1 and calling `get_active_players` returns exactly 2 reserve pitchers + 1 reserve hitter promoted, restoring the totals to 19P / 15 position.
- **Games sim cleanly**: 60+ games played without errors; pitcher lines show 2–5 arms per game with realistic outs-recorded distributions.

---

## Files changed

| File | Change |
|------|--------|
| `o27v2/db.py` | Added `stamina` + `is_active` columns to migration path |
| `o27v2/league.py` | `_TALENT_TIERS`, `_roll_tier_grade`, `_tier_unit`, `_make_hitter`, `_make_pitcher`, `generate_players` (full rewrite), seed INSERT |
| `o27/engine/state.py` | Added `stamina` field to `Player` dataclass |
| `o27/engine/manager.py` | Rewrote `pick_new_pitcher` with outs-based attribute scoring |
| `o27v2/sim.py` | `_db_team_to_engine` (SP by Stamina/rest), `_recently_used_pitcher_ids`, `_promote_pitcher_role` (simplified), `simulate_game` caller |
| `o27v2/injuries.py` | `get_active_players` (one-for-one IL fill with same-position preference) |
| `replit.md` | Updated architecture notes |

---

## Known issues / follow-up candidates

- **Super-inning overrun assertion** (`game.py:191`) — intermittent pre-existing edge case in the engine that can surface in very long sims; not introduced by this task.
- **Aging/drift hooks** — Stamina and Stuff are currently static after seeding. A future task could add per-season attribute drift (±1–3 points per year, age-curve weighted) so arms genuinely shift tiers over time, completing the "aging re-casts roles" loop.
- **Leaderboard by tier** — now that grades are on the scout 20–80 scale, a stats page could surface the top 10 Stuff arms and top 10 Stamina workhorses league-wide as a complement to the existing hitting leaderboard.
