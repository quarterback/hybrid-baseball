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

## What was deferred

**`work_ethic` + `work_habits`** (Phase 5e per the user's design).
Substantial enough scope for its own pass:
- Two new player columns (visible `work_ethic`, hidden `work_habits`).
- Re-roll cadence: `work_ethic` re-rolls each off-season under age
  30, locks at 30; `work_habits` dynamic until 27, locks at 27.
- "Cup" mechanic for `work_habits` — fills with success, drains
  with failure; situational in-game boost or slump penalty.
- Manager-AI integration: bench struggling players for similar-
  rated roster-mates with better habits.
- Engine integration: a new per-PA read alongside `today_form` /
  `today_condition` to apply the boost.

The current development engine handles the dynasty arc without these
columns; they're pure flavor + in-season variance. Tracked as the
next phase to revisit.

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
