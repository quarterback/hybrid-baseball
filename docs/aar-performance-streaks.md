# After-Action Report — Performance streaks (multi-week hot/cold ramps)

**Date completed:** 2026-05-30
**Branch:** `claude/baseball-hits-runs-variance-Cv0iW`
**Related:** `aar-hits-runs-variance.md` (the in-game RISP clutch form — this is
its season-scale cousin)

---

## Context

Continuation of the hits/runs-variance work. The user wanted streaks that feel
*earned and developmental*, not per-game noise:

> "The improvement during a hot streak should be exponential over ratings — a
> hot streak can add as many as +18 per week to attributes, and each week that
> can go up +5. But a cold streak can do the same — like a flu, it starts slow
> and ramps up."

And from the prior turn: based on performance (the player's recent play), with
the streak as a temporary overlay that reverts when it ends; both individual
players and whole teams; persisted across the season.

This is a different mechanism from the per-game RISP clutch form (which lives in
the engine). Streaks are a **season-layer** system in `o27v2`: per-day persistent
state that overlays a player's attributes before each game and updates from his
game line after it.

## Design (locked with the user)

- **Magnitude: big but capped.** Week 1 of a streak adds **+18 grade points**
  to the hitter's offensive ratings; each completed week adds **+5 more**
  (accelerating); total swing **capped at ±30 grade** so a long run peaks
  elite-ish, not superhuman. Cold streaks are the exact mirror.
- **Who: both players and teams.** Each hitter runs his own streak; a lighter
  team-wide streak (×0.45) overlays the whole lineup when a club catches fire.
- **Trigger & revert: performance-ignited, reverting.** A rolling "heat" signal
  fills from good/bad game lines (decaying toward 0 so it needs *sustained*
  play). Cross ±0.55 and a streak of that polarity ignites; keep it up and it
  ramps a week every ~6 games; cool off (heat falls inside ±0.20 or flips) and
  it **breaks, reverting the player to his true stored rating.** The overlay is
  never written back to the stored attributes — it evaporates on the next load.
- **Persistence: DB.** New columns `streak_state / streak_weeks / streak_games /
  streak_heat` on both `players` and `teams`, so the ramp survives across the
  season and the live deployment.

## Implementation

New module **`o27v2/streaks.py`**:
- `streak_grade_delta(state, weeks)` — the capped accelerating ramp.
- `apply_player_streak(player, row, team_delta)` — the overlay: converts the
  grade delta to a unit delta (via scout's 0.70/60 slope) and adds it to
  `skill / contact / power / eye`, clamped to [0,1]. Pitchers' bats get only the
  team overlay (their pitching is governed by the existing condition systems).
- `update_player_streaks(batter_rows)` — post-game: folds each hitter's game
  grade into his heat and advances the streak. Modeled directly on the existing
  `_update_habit_cups`.
- `update_team_streak(team_id, won)` — same machinery off the team's W/L.

Wiring in **`o27v2/sim.py`**: overlay applied in `_db_team_to_engine` (the
DB→engine boundary, right where ratings are loaded); updates called post-game
alongside `_update_habit_cups` / `_motivator_cup_fill`. Regular-season only,
best-effort (legacy DBs without the columns no-op).

Schema in **`o27v2/db.py`**: columns added to both the `CREATE TABLE` blocks
(fresh DBs) **and** the idempotent `ALTER TABLE` migration (existing DBs). The
fresh-DB path was the bug I hit first — `resetdb` builds from the SCHEMA string,
so a migration-only column is invisible to a freshly seeded league.

## Verification

Ramp math (unit + integration tested, `o27v2/tests/test_streaks.py`, 14 tests):

| streak week | grade delta |
|---|---|
| 1 | +18 |
| 2 | +23 |
| 3 | +28 |
| 4+ | +30 (capped) |

Full 8-team season on a fresh DB (224 games), after recentering the per-game
heat grade on the O27 league's *median* game line (OBP .55 / SLG .85 — the run
environment is high, so the naive league-rate baseline skewed everything hot):

| state | share of position players | max weeks reached |
|---|---|---|
| HOT | 23% | 9 |
| COLD | 22% | 7 |
| none | 55% | — |

Roughly symmetric hot/cold, streaks ramping to the +30 cap over 7–9 weeks,
teams split 3 hot / 3 cold. A player on a long hot streak is carrying a real
+30-grade (~+0.35 unit) bump across his bat; when his heat fades he snaps back
to his true rating. That's the "starts slow, ramps like a flu, then breaks"
shape the user asked for.

Tests: 14 streak + 26 o27 engine + 77 o27v2 engine = **117 green**. The 7
stat-invariants that don't require flask pass against the streak-simulated DB
(the other 4 fail only on `ModuleNotFoundError: flask`, an environment gap
unrelated to this change — the streak overlay writes only its own `streak_*`
columns and touches no stat path).

## Tunables (all in `o27v2/streaks.py`)

`STREAK_WEEK1` (18), `STREAK_WEEK_STEP` (5), `STREAK_CAP` (30),
`STREAK_GAMES_PER_WEEK` (6); ignition/break `STREAK_IGNITE` (0.55) /
`STREAK_BREAK` (0.20) and the heat good/bad/decay rates; team scale
`STREAK_TEAM_SCALE` (0.45); and the `STREAK_OBP/SLG_BASELINE` neutral-game
centers (run-environment dependent — recenter if the offensive environment
shifts). Set `STREAK_WEEK1 = 0` (or never ignite) to effectively disable.

## Open items / caveats

- **Baseline is run-environment-coupled.** `STREAK_OBP/SLG_BASELINE` were tuned
  to one 8-team season's median line. If the league's offensive level moves
  (other configs, era presets), the hot/cold balance will drift; the centers
  should be re-derived. A more robust version would compute the neutral line
  from live league stats each cycle, like the analytics suite does.
- **Pitcher hot/cold streaks are out of scope** here — this layer is the bat
  streak the user described. Pitching day-to-day already has `today_form` /
  `today_condition`; a parallel pitcher-streak could follow this same shape.
- **No UI surfacing yet.** The streak state is persisted but not shown in the
  box score / almanac. A "🔥 hot / 🥶 cold" tag would make it legible to
  readers; left for a follow-up since web work needs flask.
