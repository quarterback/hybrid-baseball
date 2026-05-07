# After-Action Report — Playoffs, Dynasty Development, and Supporting Fixes

**Date completed:** 2026-05-07
**Branch:** `claude/fix-machine-limit-error-KtDSp`
**Builds on:** `aar-single-div-schedule-and-snake-draft.md` (Phase 1) and
`aar-match-day-waivers-and-bad-day-variance.md` (Phases 2 + 3).

---

## What was asked for

After Phase 1 + 2 + 3 produced a properly-distributed standings shape
(see prior AARs) the user pivoted to two longer-term mechanics and a
short list of operational fixes:

1. **Playoffs** were never built and were blocking everything that
   needed a "season-end" hook.
2. **A dynasty model.** The user wanted talent to start in true
   parity (no Elite+ outliers handed out at seed) and let elite
   talent emerge across multiple seasons, gated by team development
   infrastructure. Org_strength becomes the development-rate
   multiplier instead of the per-pitch multiplier (which was the
   bug we removed in Phase 1). Org_strength itself rolls year-over-
   year "like a stock or bond market" — sustained winning bumps it
   up, mean reversion pulls it back, no team stays elite forever
   without performance backing it up.
3. **Operational fixes** that surfaced once the user actually tried
   to use the deployed app:
   - The match-day sweep was "impossibly slow" — sim got stuck at
     April 4 (the day before the first Sunday).
   - The version footer printed "build dev" in production — the
     git-binary lookup didn't work in the slim Python image.
   - Sometimes new-league creation crashed with `FOREIGN KEY
     constraint failed` — turned out to be a race with the
     multi-season runner background thread.

---

## What was built

### 1. Multi-season-runner guard (`8e09967`)

`/new-league` POST and `/api/season/reset` both call
`drop_all → seed_league → seed_schedule` on the shared SQLite DB.
The multi-season runner thread (`o27v2/season_archive._run_multi_season_thread`)
runs the same loop. When they overlapped, the runner's `drop_all` could
fire **between** the request's `seed_league` and `seed_schedule`,
wiping the teams table — `seed_schedule` then tried to insert games
referencing now-missing team IDs, surfacing as the FK violation in the
trace the user posted.

Fix: surface `multi_season_status().running` as a precondition. If the
runner is active, `/new-league` flashes a "wait for it to finish" error
and re-renders the form; `/api/season/reset` returns HTTP 409 with the
current season index. Refusing to silently wait was the right call —
the runner takes 10+ minutes per season.

### 2. Version footer (`f27a0cd` + `5465209`)

`/_app.py` now exposes `app_version.sha` + `app_version.booted_at` to
every template via a context processor. Every page footer reads:

```
build f27a0cd · booted 2026-05-07T05:44:21Z
```

Initial implementation invoked `git rev-parse --short HEAD` via
`subprocess`. That fell back to `"dev"` in production because
`python:3.12-slim` doesn't ship the `git` binary — visible in the
screenshot the user posted post-restart. Replaced with a direct
read of `.git/HEAD` (parses the ref pointer, then reads the loose
ref file or falls back to `packed-refs`). The Dockerfile already
ships `.git` via `COPY . .`, so no infrastructure change needed.
Truncates the SHA to 7 chars to match the `git --short` output.

A `+dirty` tag appears if `git status --porcelain` is non-empty (best-
effort — only runs when git is on PATH, which is dev-only).

### 3. Match-day waiver sweep performance (`f9015f3`)

The Phase 2 sweep made ~10K SQL queries per Sunday. For each of N teams
it called `_pick_best_claim`, which iterated every FA (~266) and
ran a fresh `db.fetchall` for the team's worst player at each FA's
position. Then each claim re-queried the bucket and did one UPDATE
per player to reflag `is_active`. Result: April 1-4 simulated fast
(no Sundays), then the 4/5 sweep effectively stalled the sim.

Rewrite: one `SELECT * FROM players` populates in-memory
`team_buckets[tid][bucket]` and `fa_pool`. The 5-round / pick / swap
loop runs as pure-Python on dicts. At the end:
- one `executemany` to rewrite `team_id` for moved players
- one `executemany` to clear `is_active` on the cut-to-FA group
- one `executemany` to reflag `is_active` across every dirty bucket
- one bulk `log_many` for the transaction rows

Total SQL per sweep: ~5 statements regardless of claim count.

| Check | Before | After |
|---|---|---|
| Single sweep (50 claims) | hung the sim | **0.18s** ✓ |
| Full 30-game season | jammed at 4/4 | **21.5s for 210 games + 5 sweeps** ✓ |

Behavior unchanged: same auto-policy (largest positive overall delta),
same banking (single `picks_available` counter), same `is_active`
reflag rule (top-N per bucket).

### 4. Phase 4 — Postseason bracket + regular-season awards (`95705ed`)

**Field formula:**

```
n_div_champs  = number of distinct division winners
n_wild_cards  = max(0, (team_count - 24) // 2)
playoff_teams = max(4, n_div_champs + n_wild_cards)
```

Matches the user's spec exactly: 30-team → 9, 36-team → 12, 14-team
single-div → 4 (floor). Single league-wide bracket — division champs
seed first by win pct (ties broken by overall record), wild cards
fill 5..N-by-record from the rest.

**Bye logic:** for non-power-of-2 fields, top seeds bye round 1 until
the survivor count IS a power of 2. So a 9-team field puts seeds 1-7
on bye and runs #8 vs #9 as the play-in, then 8 → 4 → 2 → 1.

**Best-of-N progression** (per the user's spec — 3/5/7/7), keyed off
rounds-from-final:

| Round | Best of |
|---|---|
| Final (`rounds_to_final == 0`) | 7 |
| LCS / Semis (`== 1`) | 7 |
| DS / Quarters (`== 2`) | 5 |
| WC / earliest (`>= 3`) | 3 |

So a 4-team bracket runs both rounds best-of-7 (semis + final). A
9-team bracket runs WC bo3, QF bo5, Semi bo7, Final bo7 — MLB-shape.

**Calendar:** 3-day gap after the last regular-season game per spec.
Within a series, one game per day, alternating venues (higher seed
hosts game 1 of bo3, games 1/3 of bo5, games 1/3/5/7 of bo7). Series
in the same round play in parallel; the next round's first game lands
the day after the previous round wraps. Each series is scheduled
incrementally — game N+1 only enters the games table after game N
completes, so the bracket-advance hook can compute dates against the
actual play-out timing instead of pre-baking unneeded games.

**Sim driver hooks:**
- `simulate_through` / `simulate_next_n` / `simulate_date` re-query
  `games` after each iteration so newly-scheduled playoff games whose
  dates fall inside the target window get drained in the same call.
- `simulate_game`'s W-L update now skips when `game.is_playoff = 1`
  so `teams.wins/losses` stays the regular-season record (postseason
  standings live on `playoff_series` rows).
- A post-game playoff hook updates the series row, schedules the
  next game, and advances the bracket round when complete.

**Awards (Phase 4e):**
- **MVP**: highest OPS among hitters with `pa >= 3.1 × team_games`.
- **Cy Young**: lowest ERA (runs allowed × 27 / outs — this is a
  27-out single-inning sport) among pitchers with `outs >= 3.0 ×
  team_games`.
- **Rookie of the Year**: best ≤23 player by max of `OPS / 0.800`
  and `4.0 / ERA`, so a young bat and a young arm compete on the
  same axis. Age-23 cap is the proxy for "first-year" since the DB
  doesn't track service time.
- **World Series MVP**: best player on the championship team during
  the final series only. Candidate hitters need 4+ PA in the series;
  pitchers need 9+ outs. OPS for hitters; pitchers compete on a
  scaled `1.6 - era / 8.0` so a dominant 0.50-ERA reliever can beat
  a hot bat.

Regular-season awards fire at playoff initiation; WS MVP fires when
the final series ends. Both are idempotent — re-running won't dupe
rows.

**UI:** `/playoffs` route. Shows champion (when crowned), awards
(once given), projected field (pre-init), and a per-round bracket
grid with current series scores, winner highlighting, and BYE markers.
Linked from the topbar.

**Smoke (14-team / 30-game):** 17 playoff games, Athletics crowned
in 6 (final 4-2). MVP Tavoy Moss 1.409 OPS / 51 RBI; CY Amier Guzman
4.58 ERA; WS MVP Jay Herrera 1.340 OPS (3 HR / 11 RBI in the final).

**Smoke (30-team / 30-game):** 9-team field with 7 byes in WC round,
4-round bracket through bo7 final. Cubs (CHC) crowned over Orcas
(VCO) 4-1.

### 5. Phase 5 — Cap-80 seed + multi-season development engine (`e20e1cc`)

**Cap-80 at seed time** (`league.py:_roll_tier_grade`): every player
attribute is capped at 80 on initial roll. The Elite+ tier (81-95) is
reachable only via development. Year-1 leagues start in true parity —
no random "transcendent" outliers — and dynasties emerge organically.

**Org_strength restored as a real attribute.** Rolled at seed time
from the full 9-tier ladder (uncapped at 95 — `_roll_org_grade`).
~7% of teams start in Elite/Elite+ band, ~12% Excellent, etc. The
post-draft "recompute from roster mean" step is gone. Org_strength
now exclusively drives **multi-season player development** — never
biases per-pitch outcomes. (That role was the bug we removed in
Phase 1; not coming back.)

**Development pass (`o27v2/development.py:run_offseason`):**

For every player on every team, plus all free agents:
- `age += 1`
- For each engine-relevant attribute, draw a delta:
  `Δ ~ N(μ_age + μ_org, σ=1.5)`, applied. Some attributes can push
  past 80 here — that's how Elite+ talent emerges.

Age curves shifted late to fit the sport's longer pitcher careers:

| Age | μ_age | Notes |
|---|---|---|
| <21 | +2.5 | prospect breakout band |
| 21-25 | +1.5 | prime growth |
| 26-30 | +0.5 | late-prime improvement |
| 31-33 | 0.0 | plateau |
| 34-36 | -0.7 | gentle decline |
| 37+ | -1.8 | sharp decline |

Org bonus banded:

| Org strength | μ_org |
|---|---|
| ≥75 (Elite/Elite+) | +1.0 |
| 60-74 (Good/Excellent) | +0.4 |
| 45-59 (Average band) | 0.0 |
| 30-44 (Below avg) | -0.4 |
| <30 (Cellar) | -1.0 |

**Pitcher decline is grit-modulated.** High-grit pitchers (>0.6 unit,
derived from stamina since the DB doesn't persist grit explicitly)
age slower — decline magnitude × 0.6. Low-grit (<0.4) ages faster —
× 1.4. Identity at 0.5. Growth (μ ≥ 0) is unmodulated; gritty kids
hold onto talent longer, they don't develop faster.

**Bust events.** 1.5% chance per attribute per season → `N(-3, 1.5)`
draw regardless of age/org. Real baseball has unexplained collapses;
the league gets a few each year.

**Org-strength bond-market roll** (per-team year-end):

```
new_org = 0.7 × old_org + 0.3 × 50 + (winpct - 0.5) × 60 + N(0, 3)
```

Clamped to [20, 95]. Mean reversion (30% pull toward 50 each year)
+ performance feedback. Smoke output:

| Team | Old → New | Δ | W-L |
|---|---|---|---|
| WSN | 29 → 36 | +7 | 17-13 (.567) |
| MIL | 65 → 51 | -14 | 12-18 (.400) |
| CHW | 69 → 57 | -12 | 13-17 |
| VAN | 59 → 65 | +6 | 19-11 |

Exactly the bond-market behavior the user spec'd — sustained winning
gets rewarded, sustained losing gets punished, but a team's prior
reputation also pulls them back toward league-average over time.

**Dynasty rollover endpoint (`/api/season/advance`):**
- Refuses while the multi-season runner is active or before a champion is crowned.
- Archives the just-completed season (best-effort).
- Runs `run_offseason` (development + org_strength roll).
- Resets `teams.wins` / `teams.losses` to 0.
- Wipes `games`, `playoff_series`, `season_awards`, per-game stat
  tables, transactions, and the sim-clock keys in `sim_meta`.
- Bumps the season counter (`sim_meta.season_number`).
- Re-seeds the schedule for the new year with `last_seed + 1` so
  weather/sim variation differs season-over-season.

UI: a `Advance to Next Season →` button on `/playoffs` (visible only
once a champion exists). JS fetch with confirm dialog spelling out
what'll change ("every player ages +1, development applies, W-L
resets, schedule re-generates, rosters preserved"). Reloads to
`/league` on success.

**Smoke:** after one season + offseason on a 14-team config:
- All seed-time attributes ≤ 80 ✓
- Org_strength range 29-69 (rolled, not derived) ✓
- After offseason: max hitter skill 83, max pitcher_skill 83
  → Elite+ talent emerged via dev as designed ✓
- 14 teams + 266 free agents all developed; ages bumped ✓

---

### 6. Phase 5e — `work_ethic` + `work_habits` with in-season cup (`96ec734`)

The user's "leaders / fell-off / fun randomness" mechanic, layered on
top of the dynasty engine.

**Schema additions** (3 columns on `players` + ALTER TABLE migration):

| Column | Type | Visibility | Notes |
|---|---|---|---|
| `work_ethic` | INTEGER 20-80 | shown on player page | re-rolls each off-season under age 30; locks at 30 |
| `work_habits` | INTEGER 20-80 | hidden | re-rolls under age 27; locks at 27 |
| `habit_cup` | REAL 0..1 (default 0.5) | hidden | in-season "cup" — fills with success, drains with failure |

**Initial seed:** `_make_hitter` / `_make_pitcher` roll both attributes
from the same 9-tier ladder (capped at 80 like every other seed-time
attribute). `habit_cup` starts at 0.5 (neutral).

**Game-day integration:** `_roll_today_condition` (the Phase 3 daily
wellness draw) now folds in ethic + cup-modulated habits:

```
ethic_shift  = (work_ethic  - 50) / 500   →   range ±0.06
habits_raw   = (work_habits - 50) / 500   →   range ±0.06
cup_factor   = (cup - 0.5) × 2            →   range −1.0 .. +1.0
μ_player    += ethic_shift + habits_raw × cup_factor
```

A great-ethic / hot-cup player on a mild day has μ ≈ 1.12 (capped by
the [0.85, 1.15] floor on the gauss draw); a bad-ethic / cold-cup
player on a hot rainy day has μ ≈ 0.83 — frequent off games. Total
μ shift range from ethic + habits: ±0.12 on top of the existing
weather penalties.

**Per-game cup updates** in `_update_habit_cups` after each
regular-season game (playoffs don't move the season-arc cup):

| Player | Good day (cup +0.04) | Bad day (cup −0.04) |
|---|---|---|
| Hitter | 1+ H AND OBP ≥ .333 | 3+ AB, 0 H, 0 BB, 0 HBP |
| Pitcher | 9+ outs AND ≤2 ER | (6+ outs AND ≥4 ER) OR (≤3 outs AND ≥3 ER) |

Idle bench days don't move the cup either way. Step size is ±0.04 so
a real streak takes ~12 games to swing the full 0→1 range.

**Off-season re-rolls** in `development._develop_player` use a soft
formula:

```
new = round(0.6 × old + 0.4 × fresh_tier_roll)
```

so values persist year-over-year instead of bouncing wildly. Once a
player crosses the lock age (`work_ethic` at 30, `work_habits` at
27), the attribute is frozen — the high-ethic 32-year-old who shows
up ready every day stays that way; the cellar-ethic veteran also
stays bad. `habit_cup` resets to 0.5 every off-season.

**UI:** the player page surfaces `work_ethic` with a "Locked" badge
once the player hits 30. `work_habits` and `habit_cup` stay hidden —
they shape outcomes invisibly so the user can't game them.

**Smoke (14-team / 30-game season + offseason):**
- Seed-time `work_ethic` range 20-80 ✓
- Post-season cup distribution: mean 0.555, σ 0.158, range 0.180-1.000.
  Real streak shape — 51 players ended ≤0.4, 107 ended ≥0.8. The
  mass clustered near 0.5 (no movement) is players with limited
  playing time / mostly-mediocre games.
- Off-season locks fire correctly: an age-33 player kept his
  ethic 37 / habits 27; an age-22 player got both re-rolled
  (42→48 ethic, 80→64 habits).
- Cup resets to 0.5 for every player at off-season ✓

**What was deferred** — actually, *not* deferred. See section 7
below. The manager-AI habit-bench pass shipped in `75b1b79` the
same day.

### 7. Phase 5f — Manager-AI habit-bench pass (`75b1b79`)

The "bench struggling player for similar-rated player with better
habits / work-ethic" half of the Phase 5e spec.

A new pass in `_db_team_to_engine` runs BEFORE the existing rest-day
pass. Logic:

```
for starter in starters_by_cup_ascending:
    if starter.habit_cup >= 0.30:        # not a real slump → done
        break
    candidates = bench_fielders where:
        |bat_score(cand) - bat_score(starter)| <= 6.0  AND
        cand.habit_cup >= starter.habit_cup + 0.30
    if candidates:
        replacement = max(candidates, key=cup)
        swap(starter, replacement)
        break                              # one swap per game
```

Why before rest-day: the rest pass walks `starting_fielders` to
decide who's tired. If habit-bench had run after, the rest pass
would have considered the post-habit-swap lineup — conflating "who's
slumping" with "who's tired", which are separate signals. Running
habit-bench first means the rest pass sees the lineup the manager
*chose*, not the lineup that arrived by accident.

**Threshold knobs** — sensitivity scales with `mgr_bench_usage` so
old-school skippers swap less aggressively than analytics-forward
ones. Skill tolerance stays flat across managers (every skipper
agrees not to bench a stud for a scrub):

| `mgr_bench_usage` | Cup threshold | Required cup gap |
|---|---|---|
| 0.00 (old-school) | 0.150 | 0.400 |
| 0.25 (classic) | 0.225 | 0.350 |
| 0.50 (default) | 0.300 | 0.300 |
| 0.75 (modern) | 0.375 | 0.250 |
| 1.00 (analytics-forward) | 0.450 | 0.200 |

Formula: `threshold = 0.15 + bench_usage × 0.30`,
`required_delta = 0.40 − bench_usage × 0.20`,
`skill_tolerance = 6.0` (constant). An old-school skipper waits
for a 8-game crash before considering a swap and demands a clearly
better bench guy; an analytics-forward skipper acts on a 2-game
slump if the bench has anyone meaningfully hot.

**Safety property: no permanent burial.** The cup resets to 0.5 every
off-season, so a player whose cup crashed early in a season at worst
loses that season's PA share — they return next year with a clean
slate. No bench-and-forget failure mode.

**Smoke** (artificially crashed Kaliq Dawkins' cup to 0.05, bumped
four UT bench cups to 0.95):

| Player | Expected PA | Actual PA | Behavior |
|---|---|---|---|
| Kaliq Dawkins (cold cup, CF) | ~120 | **24** | benched ~26 of 30 games |
| (His backups, hot cups, UT) | low | high | rotated in instead |

League shape unchanged: `.367-.633` win-pct spread, consistent with
prior smoke runs. The mechanism doesn't distort parity — it just
tactically rotates within talent-similar bands.

### 9. Phase 5g — Motivator-archetype cup-fill

The user added a leadership/morale layer on top of the cup mechanic:
some manager archetypes can fill a player's cup a small amount each
game, gated by a dice roll whose probability scales with grit,
talent, and the team's recent form.

**Eligible archetypes** (the morale-coded skippers in `managers.py`):
`players_manager`, `iron_manager`, `fiery`. Other archetypes don't
fire this mechanic — those teams rely on the standard performance-
driven cup updates only.

**Per-player roll each game:**

```
chance = 0.02
       + 0.08 × grit_unit       # stamina mapped to [0, 1]
       + 0.08 × talent_unit     # skill (or pitcher_skill) mapped to [0, 1]
       + 0.05 × max(0, last10_form_unit)
```

Where `last10_form_unit = clip((last10_winpct - 0.5) × 2, -1, 1)` —
a hot team contributes positively, a cold team contributes nothing
(no morale tax on losing streaks; we just don't get the boost).

Probability ranges:
- **Floor (low-grit / low-talent / cold team):** 2% per game.
- **Ceiling (max-grit / max-talent / 10-0 hot streak):** 23% per game.

**Fill amount:** `+0.02` (half the regular `±0.04` step). "Small
amount" per the user's spec.

**Trigger:** only fires for players who appeared in the game (had
≥1 PA or recorded ≥1 out). Bench-warmers don't get the morale boost
on idle days — leadership transfers through participation.

**Smoke (14-team / 30-game season, A/B halved):**

| Group | n cups | Mean cup |
|---|---|---|
| Motivator-managed teams (3 each: players_mgr, iron_mgr, fiery) | 329 | 0.594 |
| Non-motivator (set_and_forget × 7) | 329 | 0.579 |

Differential: **+0.014**. Small per the spec, but the per-team
breakdown shows it's consistent — every motivator team ends in
the upper half of the cup range, set-and-forget teams cluster
lower. Aggregate behavior: ~10% of player-games on motivator teams
trigger a fill across the season, raising team-mean cup steadily.

This compounds with the existing performance-driven cup mechanic:
a hot-talent player on a winning team under a Players' Manager
gets BOTH the performance boost AND the leadership boost, so their
effective work_habits stays elevated all season — exactly the
"team leader" archetype the user described.

### 10. Drop UT as a position (`1e54168`)

The user noticed UT-tagged bench players kept showing up in places
where canonical positions or jokers should — box scores read as "UT"
instead of "backup CF", the FA browser was a UT-heavy soup, and the
rest-day / habit-bench passes pulled UT bodies into canonical-
position slots without typing the swap.

Fix: every hitter now has a real position. UT is gone as a roster
slot. Bench depth is distributed across canonical positions so
backups are typed:

- **Active backups** (1 each, on top of the existing 1 starter at
  these positions) at the high-rotation spots where real teams
  rotate every day: **CF, SS, 2B, C** — 2 active per team.
- **Reserve depth** (1 each) at every canonical position: 3B, 1B,
  LF, RF get 1 active starter + 1 reserve; CF/SS/2B/C add 1 reserve
  on top of their 2 active.

Per team:

| Position | Total | Active | Reserve |
|---|---|---|---|
| CF, SS, 2B, C | 3 each | 2 | 1 |
| 3B, 1B, LF, RF | 2 each | 1 | 1 |
| DH | 3 | 3 | 0 |
| P | 24 | 19 | 5 |

Total still 47 / team — same composition as before, just typed.

**Code changes:**
- `_DRAFT_SLOTS` rewritten — multiple entries per position now
  allowed (CF appears twice: once as starter, once as
  active+reserve backup).
- `_generate_draft_pool` aggregates slot counts per position
  before sizing the pool, so the multi-entry layout doesn't
  undercount high-rotation positions.
- `_make_hitter` dropped the `pos == "UT"` utility-roll
  short-circuit. The 10% Zobrist-style utility archetype is still
  available as a secondary trait, applied randomly regardless of
  primary position.
- `generate_players` (legacy non-league callers — smoke_test,
  batch.py) now distributes bench / reserve to canonical positions
  via a round-robin instead of stamping "UT".
- `waivers._BUCKET_ACTIVE_SLOTS` rewritten — UT removed; per-
  position active-slot counts (CF/SS/2B/C: 2 active, 3B/1B/LF/RF:
  1 active, DH: 3, P: 19) so the post-claim is_active reflag works
  correctly.

**Backward-compat fallbacks** (the position-defense bonus map's UT
entry, the "p.position or 'UT'" no-position-stamp guard, and the
UT entries in app.py's stat-page filters) are left in place. They
won't fire on rosters seeded post-1e54168 and can be removed when
no DB rows with UT exist.

**Smoke (14-team / 30-game):** 0 players with UT position;
canonical positions distributed correctly (39 each at 1B/3B/LF/RF,
59 each at CF/SS/2B/C/DH for the FA pool); full season + playoffs
in 25s; champion crowned (Astros); standings spread .233-.667 —
same shape as prior smokes.

---

## What's still on the table

- **Playoff travel days / 2-2-1 / 2-3-2 home patterns.** Current
  v1 alternates home strictly — higher seed hosts odd games. Real
  MLB does 2-2-1 (bo5) and 2-3-2 (bo7) so the higher seed hosts
  more games. Easy follow-up; just a scheduler change.
- **Champion's parade page / season recap.** Once a champion is
  crowned the `/playoffs` page lights up but there's no archived
  "season recap" view tying together standings + awards + bracket.
  `season_archive` already persists most of this; needs a UI route.
- **Re-running awards selection.** The category-level idempotency
  is in but there's no "reselect" button — if the user wants to
  re-run after fixing an aggregator bug, they'd have to clear
  `season_awards` rows by hand.
- **Multi-season-runner UX after the guard.** Right now you get
  flashed "wait for it to finish." A nicer follow-up: surface
  `multi_season_status` on `/league` so the user sees progress
  without polling `/api/sim/multi-season/status`.

---

## Files touched

| File | Phase / commit | Changes |
|---|---|---|
| `o27v2/web/app.py` | 8e09967 | Multi-season-runner guard on `/new-league` POST + `/api/season/reset`. |
| `o27v2/web/app.py` | f27a0cd → 5465209 | `_resolve_app_version` context processor. Reads `.git/HEAD` directly + `.git/refs/heads/<branch>` (or `packed-refs`) to avoid `git` binary in container. |
| `o27v2/web/templates/base.html` | f27a0cd | Footer SHA + boot timestamp. |
| `o27v2/waivers.py` | f9015f3 | Full rewrite — bulk `SELECT * FROM players`, in-memory rounds, batched executemany at the end. ~5 SQL statements per sweep. |
| `o27v2/db.py` | 95705ed | `playoff_series` + `season_awards` tables; `games.series_id` + `games.is_playoff` columns + ALTER TABLE migration block; `drop_all` extended. |
| `o27v2/playoffs.py` | 95705ed | New module. Field sizing + seeding (`compute_field`), bracket layout w/ byes (`_round_one_pairings`), series scheduling, `initiate_playoffs`, `_maybe_advance_round`, `post_playoff_game`, `get_bracket`, `champion`, `maybe_initiate`. |
| `o27v2/awards.py` | 95705ed | New module. MVP / Cy Young / RoY selection at playoff init; WS MVP at final's end; `get_awards` for UI. |
| `o27v2/sim.py` | 95705ed | Post-game playoff hook (uses existing `winner_team_id` — engine emits `"visitors"`/`"home"` not `"away"`). W-L update guarded on `is_playoff = 0`. `simulate_through` re-queries `games` between iterations to drain newly-scheduled playoff games. |
| `o27v2/web/app.py` | 95705ed | `/playoffs` route. |
| `o27v2/web/templates/playoffs.html` | 95705ed → e20e1cc | New template — champion, awards, projected field, bracket grid with bye markers and winner highlighting. Adds "Advance to Next Season →" button (Phase 5d). |
| `o27v2/web/templates/base.html` | 95705ed | Topbar Playoffs link. |
| `o27v2/league.py` | e20e1cc | `_roll_tier_grade` clamps both ends of the range to 80; `_roll_org_grade` helper rolls org_strength on full 9-tier ladder uncapped at 95; seed_league inserts the rolled value (no post-draft recompute). |
| `o27v2/development.py` | e20e1cc | New module. Age curves (`_mu_age`), org bonus (`_mu_org`), grit modulator, per-attribute development draw with bust events, free-agent dev pass, org-strength bond-market roll, `run_offseason` top-level entry. |
| `o27v2/web/app.py` | e20e1cc | `/api/season/advance` dynasty rollover endpoint. |
| `o27v2/db.py` | 96ec734 | `players` adds `work_ethic`, `work_habits`, `habit_cup` columns + ALTER TABLE migration. |
| `o27v2/league.py` | 96ec734 | `_make_hitter` / `_make_pitcher` roll the new columns; `seed_league` INSERT extended. |
| `o27v2/sim.py` | 96ec734 | `_db_team_to_engine` stamps the new attrs on engine Player; `_roll_today_condition` folds them into μ; `_update_habit_cups` runs after each regular-season game. |
| `o27v2/development.py` | 96ec734 | Off-season re-roll with age-27 (habits) / age-30 (ethic) locks, soft re-roll formula, cup reset to 0.5. |
| `o27v2/web/templates/player.html` | 96ec734 | Surfaces `work_ethic` with "Locked" badge once frozen. |
| `o27v2/sim.py` | 75b1b79 | Habit-bench pass (`_try_habit_bench`) in `_db_team_to_engine`, before the rest-day pass. Swaps slumping starters for similar-skill bench fielders with healthier cups. |
| `o27v2/league.py` | 1e54168 | `_DRAFT_SLOTS` rewritten with per-position backups; `_generate_draft_pool` aggregates slots per position; `_make_hitter` drops the UT-utility short-circuit; legacy `generate_players` distributes bench across canonical positions. |
| `o27v2/waivers.py` | 1e54168 | `_HITTER_BUCKETS` and `_BUCKET_ACTIVE_SLOTS` updated for the per-position bench layout. |
| `o27v2/sim.py` | 756209a | Habit-bench thresholds scale with `mgr_bench_usage`: old-school skippers fire on cup ≤ 0.15 with +0.40 gap required; analytics-forward fire on cup ≤ 0.45 with +0.20 gap. |
| `o27v2/sim.py` | (next commit) | Phase 5g — `_motivator_cup_fill` + `_team_last10_winpct`. Per-game per-player dice roll on motivator-archetype teams (`players_manager`, `iron_manager`, `fiery`) for a small `+0.02` cup boost, gated by grit + talent + last-10 team form. |

---

## Decision log

- **Single league-wide bracket, not per-subleague.** The user's spec
  ("30-team league: division champions and 3 wild cards") parses as a
  total field count. Per-league brackets at 30 teams would require
  splitting 9 unevenly. Keeping a single bracket also works for the
  14-team / 1-subleague floor case (top 4 by record). When 2 leagues
  exist, the AL champ might still meet the NL champ in the final
  under chalk seeding — but it's not guaranteed by structure.
- **Field formula `(team_count - 24) // 2` for wild cards.** Fits
  the user's two data points (30 → 3, 36 → 6) with the cleanest
  linear-in-team-count rule. Floors at 0 for ≤24 teams; the min-4
  override handles small leagues (14-team gets 1 div champ + 0 WC
  → falls back to top-4 by record).
- **W-L excludes playoffs.** Real baseball does the same — 100-win
  regular-season teams stay 100-win even after a WS appearance. This
  also makes the playoff bracket display match what the field
  computation showed at init time, which avoids a confusing UX
  where the seeded field looks different from the current standings
  table mid-playoffs.
- **Cap at 80 at seed, but org_strength uncapped at 95.** The user's
  exact spec — "81-95 happens but it's a slow growth developmental
  improvement." Org_strength represents development infrastructure,
  not present talent, so it can legitimately start in Elite+
  territory at seed.
- **Bond-market α = 0.30.** Stronger than nothing (otherwise dynasties
  run forever) but weak enough that one bad year doesn't crush a
  perennial contender. With +60-scaled perf bonus and σ=3 noise, a
  team holding .550 ball each year stabilizes around org 65-70; a
  team yo-yoing between .450 and .550 stays close to 50.
- **Bust at 1.5% per attribute, not per player.** Per-attribute means
  a player can lose ground in a single skill while keeping the rest
  intact — e.g., a hitter losing eye while keeping power. That's the
  shape of real baseball aging arcs better than a per-player "you
  busted" coin flip would.
- **Snake-draft jitter (`±6` from prior PR) stays in.** Combined with
  cap-at-80, the FA pool now overlaps team rosters at the margins
  while team-mean parity stays tight. Match-day waivers continue to
  do meaningful work in week 1 of every season.
- **No `today_form` / `today_condition` widening.** Phase 3's per-
  game variance already produces a healthy score-diff distribution
  (30% close games, 24% 8+ run blowouts in the smoke). Adding more
  variance on top of the new dynasty mechanics could over-correct
  toward soup. Will revisit if standings don't have enough texture
  after a multi-season run.
- **work_ethic / habits via μ shift, not a separate multiplier.**
  Considered making them an independent multiplicative term in
  prob.py alongside `today_condition`. Rejected because the existing
  `today_condition` machinery already supports the exact shape we
  needed (gauss draw with a μ shift), and the user's spec was
  "boost attributes 1-5 points during a season" — folding into the
  μ shift gets us roughly that magnitude without touching prob.py
  at all. Kept the implementation contained to sim.py.
- **Cup step ±0.04 over ±0.10.** Tested both. ±0.10 made hot/cold
  streaks too volatile — a single bad game could flip a player from
  full cup to neutral, which doesn't match real-world momentum.
  ±0.04 means it takes a 5-6 game streak to meaningfully shift the
  cup, which matches how baseball commentary actually talks about
  slumps and hot streaks.
- **Idle days don't move the cup.** A pinch-hit appearance (1 PA)
  shouldn't crash a starter's cup; a bench day shouldn't reset
  anything. Required `pa >= 2` (hitters) / `outs >= 1` (pitchers)
  to update.
- **Soft re-roll (60/40) over full re-roll on ethic/habits.** Real
  baseball "leaders" stay leaders for years. A full re-roll each
  off-season would mean a high-ethic age-25 had a coin-flip chance
  of being mediocre at age-26, which doesn't match the user's
  "leaders / fell off" framing. The 60/40 weighted average preserves
  identity year-over-year while still allowing meaningful drift.
