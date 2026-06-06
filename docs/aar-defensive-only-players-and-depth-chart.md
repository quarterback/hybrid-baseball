# After-Action Report — Depth Chart Rotation and Playing-Time Burial Fix

**Date completed:** 2026-06-06
**Branch:** `claude/defensive-only-players-hitting-GElTI`
**Commits:**
- `afefaa3` — Fix depth chart: ensure all active players get rotation time

---

## What was asked for

The user flagged a player — Spencer Badosa (3B, Nationals, age 27) — who appeared
to have no hitting ability. Initial assumption was that the game had generated
"defensive-only" players with deliberately blank hitting stats, which was never
requested and doesn't make sense as a game design.

Investigation revealed the opposite: Badosa has real hitting stats (Power 80 Elite,
Eye 56 Good, Hitting overall 51). The bug was that he had **zero game stats** — he
had never appeared in a game despite being on an active roster. The UI tabs for
Batting didn't even render because `bt_totals` was None.

The follow-up request was clearer: depth charts should optimize daily to ensure
every active player gets meaningful playing time, good players shouldn't sit
indefinitely, and players who are truly buried (good but blocked) should end up
somewhere they can contribute.

---

## Starting state

### How lineup selection actually worked

Players are loaded from the DB via `get_active_players()` ordered `BY id` —
insertion order. `_db_team_to_engine()` iterated that list and appended each
non-pitcher, non-joker into a `fielders` list in that same order. Then:

```python
starting_fielders = list(fielders[:8])
bench_fielders = list(fielders[8:])
```

No sort. The 8 starters were literally the first 8 position players by DB row ID.
Anyone inserted later — including mid-season additions, late-generated players,
or simply players whose team slot was numbered 9–12 — started every game on the
bench regardless of their actual skill.

### Why bench players rarely got in

The rest-day pass had a **6% base rate** (`0.06 * (0.20 + 1.40 * bench_usage)`).
At the default `bench_usage = 0.5`: `0.06 * 0.90 = 0.054` per starter per game.
With 8 starters and a cap of 2 rests per game, the expected rests per game were
`min(8 * 0.054, 2) ≈ 0.43`. Meaning roughly one game in every 2.3 would even
have *one* bench player start — and that single slot would go to the
highest-skill bench player, so the 3rd and 4th bench player could wait weeks.

There was also no protection against a starter riding 20+ consecutive games
without a single rest.

### The trade system's blind spot

The existing `_score_block_breaking` motivation (which does try to surface blocked
players as trade candidates) only fires for `is_active=0` reserve players blocked
by `is_active=1` starters. A player like Badosa — who IS active but just never
starts — was invisible to it.

---

## What shipped

### sim.py — lineup construction and rotation

**1. Sort fielders by bat_score before slicing.**

One line added before `starting_fielders = list(fielders[:8])`:

```python
fielders.sort(key=_bat_score, reverse=True)
```

`_bat_score` is `0.55 * skill + 0.15 * power + 0.20 * contact + 0.10 * eye`.
The 8 starters are now the 8 best available bats. A player with Power 80 and
overall 51 will rank by actual hitting talent rather than by when their DB row
was inserted.

**2. Double the rest-day base rate: 0.06 → 0.12.**

Expected rests per game at default `bench_usage` rises from ~0.43 to ~0.86 —
close to one planned rest per game. Bench players get roughly 2× more starts.

**3. Near-mandatory rest at 8+ consecutive starts.**

```python
if consecutive >= 8:
    rest_p = max(rest_p, 0.90)
```

No starter can crowd out the entire bench indefinitely. After 8 straight starts
the rest probability floors at 90%, regardless of manager conservatism
(`mgr_bench_usage`).

**4. Raise rest cap from 2 to 3 for deep benches.**

When `len(bench_fielders) >= 4`, `_rest_cap = 3`. A roster with 12 position
players has 4 bench spots; capping at 2 rests meant the 3rd and 4th bench
players had no path to a start even when 3 starters were simultaneously due
for rest.

**5. Bench-starvation guard.**

After all swap passes, a final check:

- Find bench players absent from `position_workload` (never started in the
  12-day lookback window).
- Find starters with 5+ consecutive starts.
- If both exist, force one swap — the best-bat starved player gets the start,
  subject to a 12-point `_bat_score` tolerance (won't bench a clear star for
  a scrub).

This is the "last resort" path: even if rest rates are low and the habit-bench
pass didn't fire, a completely unused player will eventually force their way
into a lineup by displacing an iron-man starter.

### trades.py — playing_time_buried motivation

New motivation added to the trade engine's scoring system.

**Scoring function** (`_score_playing_time_buried`):

- Fires only after the team has played 25+ games.
- Queries `game_batter_stats` for each active non-pitcher's games started
  (PA > 0, phase = 0).
- Flags players with < 20% games-started share who have `trade_value >= 0.30`
  (i.e., not replacement-level garbage — an actual human being should be
  playing them somewhere).
- Score = `(1.0 - share) * trade_value * 0.8`. A player with 0% starts and
  solid value scores ~0.64; one with 15% starts scores lower.

**Partner targeting** (`_candidate_partners`):

Teams with no one at the buried player's position go to the front of the
partner queue. Teams with a weak starter there come next. This means the
buried player is most likely to land on a team that genuinely needs them.

**Offer construction** (`_build_offer`):

The initiating team sends the buried player; receives a comparable-value
player back via `_pick_by_value` with a 0.25 tolerance. Standard fair-value
trade, not a fire sale.

**Strategy weights** (`STRATEGY_MULT`):

| FO strategy | Weight | Rationale |
|---|---|---|
| `develop` | 1.6 | Actively find homes for buried prospects |
| `balanced` | 1.2 | Willing to move excess depth |
| `rebuild` | 1.1 | Moving pieces that don't fit the rebuild |
| `contend` | 0.8 | Hoard depth for injury insurance |
| `win_now` | 0.5 | Never voluntarily move depth |

---

## Things that were not changed

- **Player generation.** No changes to how players are created. All active
  players already have contact/power/eye ratings; there was never a
  "defensive-only" player type without hitting stats. That wasn't the bug.
- **`_try_habit_bench`.** The habit-cup swap (which fires when a starter is
  slumping) was left as-is. It was already working correctly; the only issue
  was the base ordering.
- **`position_workload` lookback window (12 days).** Not changed. The starvation
  guard uses this as-is.
- **The `role_hit` / `roster_slot` flags.** These exist and are loaded, but
  they drive manager AI substitution candidate filtering during a game, not
  the pre-game lineup construction. That separation is correct and wasn't
  touched.

---

## Validation

**Import check passed:** `python3 -c "import o27v2.sim; import o27v2.trades"`.

**Syntax check passed:** `python3 -m py_compile o27v2/sim.py o27v2/trades.py`.

**Live game regression not run.** No DB is present in this environment —
`pytest o27v2/tests` would require a seeded DB. The changes are localized:
- The `_bat_score` sort is idempotent if the list is already ordered.
- The rest-rate change is a scalar multiplier — can't produce NaN or crash.
- The starvation guard has a `break` after the first swap and can't loop.
- The new trade motivation has the same call signature as existing ones and
  falls through to `return [], []` on any missing-data edge case.

The question of whether the *rates* are well-calibrated — whether 12% base
and a cap of 3 produce the right playing-time distribution across a 162-game
season — requires a live sim run. These numbers are better than before
(0.06, cap 2) but the ideal calibration is empirical.

---

## Open items

- **Empirical playing-time validation.** After simming a full season, run
  a query against `game_batter_stats` to check the games-started distribution
  for bench players. If the 9th-12th fielders are still accumulating < 20%
  starts on average, the base rate or cap needs tuning.
- **Position-aware bench swap.** The starvation guard and rest-day pass both
  pull the highest-skill bench player regardless of defensive position. A 3B
  starting at SS because SS is resting is a little wrong. Left for follow-up —
  the engine's `_assign_game_positions` does handle mismatches gracefully, but
  position-fit is the right next step.
- **Trades actually completing.** The `playing_time_buried` motivation scores
  and picks partners correctly, but whether partner FOs actually accept
  (via `_evaluate_offer`) at a reasonable rate is untested. If acceptance
  rates are too low (because both sides need to clear the `ACCEPTANCE_THRESHOLD`),
  buried players might score high but never actually move.
