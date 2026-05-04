# After-Action Report — Schedule Generator, Manager Personas, Pinch Hits, Run Game, Line-Score Errors

**Date completed:** 2026-05-04
**Branch:** `claude/fix-schedule-generation-2yJKH`
**Commits:** `65469ec`, `dec42b0`, plus the follow-up "wider bands + speed range + joker label + AAR" commit

---

## What was asked for

The session opened with a single complaint:

> "it's clear the schedule generation is broke and needs a lot of work, i try to sim to the all-star break and all of the data seems like it's simulating multiple games across the same days and there's no connection to a real schedule"

That kicked off a multi-strand expansion as the user looked at the actual game output and surfaced more issues:

1. **Schedule generation** — packed every team into every calendar day for 162 straight days; no series, no off-days, no real All-Star break gap, no MLB-shaped calendar. Wanted full MLB-like structure and a fresh schedule whenever a different rng_seed is used.
2. **Box score line score** — wanted real R/H/E. (After investigation: the engine isn't innings-based, so the user agreed to "just add errors, defense should already have them anyway".)
3. **Pitching hook** — saw a starter eat 10 ER on 11 hits without ever being pulled. User specifically routed this through *managerial discretion* rather than the pitching model: "it could just be that you need managerial tendencies instead of messing with the pitching model".
4. **Manager personas** — "rather than making 4 or 5 and having it be boring, i'd rather there be a few overarching classes, then inside of them lots of situational variation". Then expanded to "more than that, i think you can stretch that to at least a dozen, with some more aggressive, some more conservative, representing different eras of baseball and some downright odd joe maddon style hybrids or tampa bay rays coded unthorodhox".
5. **Wide intra-archetype bands** — "i want all of the managerial profiles to have relatively large bands rather than tight ones, intentionally to be able to see weird shit happening".
6. **Pinch hits / platoons / run game** — "right now the game doesn't use pinch hitters at all and it should be, platoons don't happen, there's just a lot more to do".
7. **Speed → fielding range** — "speed should be an attribute and running ability for fielding players".
8. **Joker label** — "jokers designated hitter should not be UT they should be J in box scores".

Two operating constraints carried through the whole session:
- Manager personas must be **re-rolled per league seed**, not bound to franchises.
- The schedule must **refresh when you run a different seed**.

---

## What was built

### Schedule generator — full rewrite (`o27v2/schedule.py`)

**Old behaviour.** Greedy day-by-day scheduler. For 30 teams × 162 games it emitted exactly 15 games per day for 162 consecutive days. Every team played every day from April 1 through September 9. No series structure. No off-days. The "All-Star date" surfaced in the UI was just the calendar midpoint — no actual gap in the schedule, so `simulate_through(asb_date)` happily played through the games on that date.

**New pipeline.** Three stages:

1. **Directed pair generation** — refactored but functionally the same as the old intra-/inter-division weighted matchup logic. For 30 teams, an intra-division weight of 0.46 produces ~75 intra games per team (~19 against each of 4 division rivals — close to MLB's traditional 19-game series rivalries) and ~87 inter-division games (~3-4 against each of 25 cross-division opponents).
2. **Pair → series partitioning.** For each unordered (A, B) pair, count how many games A is home and B is home, then greedily chunk each side's count into series of length 2/3/4 (with rare 1-game make-ups when arithmetic forces it). Series are shuffled per-pair so a team's home/away rhythm against a single opponent is varied across the season.
3. **Calendar scheduling.** Walk days from the configured season start, skipping ASB days. Each day, advance any in-progress series (same opponents, next day at same venue), then for free teams try to start a new series from their queue with a partner who's also free. Off-days fall out naturally when a team's queue has no compatible free partner.

**Calendar anchors** are configurable per league config:
- `season_year` (default 2026)
- `season_start_month` / `season_start_day` (default Apr 1)
- `all_star_break_month` / `all_star_break_day` / `all_star_break_days` (default Jul 13, 4 days)

**Reseed-on-different-seed.** `seed_schedule` consults `get_active_league_meta()`. If games already exist *and* the active meta seed differs from the requested `rng_seed`, the games table (plus `game_batter_stats`, `game_pitcher_stats`, `team_phase_outs`, and the cached `sim_date`) is wiped before regeneration. Same-seed calls remain no-ops. This means rerunning `seed_schedule(rng_seed=99)` over a league seeded at 42 produces a fresh calendar without requiring an explicit `db.drop_all()`.

**`get_all_star_date`** rewritten in `o27v2/sim.py` to scan distinct game dates and find the largest consecutive-day gap. The day before that gap is the ASB target — `simulate_through(this_date)` runs everything up to the break, and the next-day clock advance lands on the resume day. Falls back to calendar midpoint for legacy schedules with no real gap.

**End-to-end verification (30-team, seed=42).** 2,430 games (correct: 30 × 162 / 2). Each team plays exactly 162. April 1 → September 22. 171 distinct game dates (vs the old 162 — extra days come from off-days plus the 4-day ASB). Largest gap: July 12 → July 17 (5 days = the carved-out ASB July 13–16). Different seeds (42 vs 99) produce demonstrably different schedules.

Caveats deliberately left in:
- The series partition occasionally produces 1-game series and back-to-back same-opponent runs that exceed 4 days (e.g., a 6-game homestand). Tolerable shape for now.
- Existing leagues seeded under the old generator keep their packed schedule until reseed.
- A reseed mid-season wipes played games — by-design per the requirement, but worth noting.

### Manager personas — `o27v2/managers.py` + schema + engine wiring

**14 archetypes** spanning eras and styles. Each carries centre values for seven tendency axes plus a per-archetype noise band:

| Key | Label | Era / vibe |
|---|---|---|
| `dead_ball` | Dead-Ball Traditionalist | 1900s, never pulls SP |
| `iron_manager` | Iron Manager | 1970s workhorse |
| `old_school` | Old-School Skipper | mid-century, slow hook |
| `small_ball` | Small-Ball Tactician | bunt + steal |
| `players_manager` | Players' Manager | trusts vets |
| `set_and_forget` | Set-It-and-Forget-It | minimal in-game decisions |
| `balanced` | Balanced Skipper | middle of the road |
| `fiery` | Fiery Competitor | emotional, quick to react |
| `hot_hand` | Hot-Hand Hunter | results-driven hooks |
| `bullpen_innovator` | Bullpen Innovator | LaRussa-coded specialist |
| `modern` | Modern Tactician | analytics-aware |
| `sabermetric_max` | Sabermetric Maximalist | full Rays mode, opener-friendly |
| `mad_scientist` | Mad Scientist | Maddon-coded chaos |
| `gambler` | Gambler | high-variance every axis |

**Tendency axes** (all in 0..1):
- `quick_hook` — pull pitchers who get tagged
- `bullpen_aggression` — willingness to use multiple relievers
- `leverage_aware` — situation-weighted decisions
- `joker_aggression` — joker pinch-hitter usage
- `pinch_hit_aggression` — bench-bat permanent substitutions
- `platoon_aggression` — willingness to swap for L/R matchup
- `run_game` — SB attempt rate + speed-threshold scaling

**Noise bands.** Default 0.22, mad_scientist 0.32, gambler 0.30. Intentionally wide so two managers nominally sharing the same archetype routinely diverge enough that the league produces visible weirdness. A "set-and-forget" who happens to roll high `run_game`, an old-school skipper with surprising bullpen aggression — these are meant to happen.

Empirically (200 rolls per archetype): the default 0.22 noise produces a ~0.44-wide window per axis, with std ≈ 0.13. Mad-scientist/gambler hit ~0.65-wide windows with std ≈ 0.18.

**Persistence.** New columns on `teams`: `manager_archetype` plus seven `mgr_*` REAL columns. ALTER-TABLE migrations follow the existing pattern. Rolled in `seed_league` using the same RNG that seeds park factors. Stamped onto the engine `Team` object in `_db_team_to_engine` so the engine reads everything off the in-memory state — no DB calls during the game loop.

### Pitching hook — managerial discretion layer

`should_change_pitcher` in `o27/engine/manager.py` previously fired *only* on fatigue thresholds (spell-count vs stamina-derived bases) with one buried bailout: workhorse SPs at ≥ 8 spell runs *and* past `RELIEVER_ENTRY_OUTS_MIN`. That gate didn't trip for the user's example case (Hank Martin, high stamina, 10 ER) because the half wasn't far enough along.

New `_manager_hook_check` runs *before* the fatigue check. Layered triggers:

- **Run threshold** — `max(2, round(8 - 6 * quick_hook))`. A quick-hook 0.9 manager pulls at 3 runs; a 0.1 manager waits for 7+.
- **Leverage adjustment** — `leverage_aware` lowers the threshold further in tied/one-run games and raises it in blowouts.
- **Hit pile-up trigger** — `bullpen_aggression ≥ 0.5` managers also pull on `hits_in_spell ≥ max(4, round(10 - 6 * bullpen_agg))` even when not all hits have scored.
- **Minimum sample gate** — at least 4 batters faced before any manager-discretion hook can fire (keeps a single hot inning from yanking a starter at PA 2).

### Pinch hits — actually fire now

The pre-existing `should_pinch_hit` had a dead gate: `if not batter.is_pitcher: return None`. Pitchers don't bat in O27 (12-man lineup is all hitters), so the function was unreachable in production.

Rewritten to be tendency-driven:
- Eligibility: runners on **or** (tight-game ≤ `PINCH_HIT_SCORE_DIFF_MAX` and late-half outs ≥ 18).
- Probability: `0.10 + 0.50 * pinch_hit_aggression + 0.15 * leverage_aware (in tight games) + 0.10 (late + tight)`, capped at 0.7.
- Two upgrade paths: skill upgrade (bench bat ≥ scheduled batter + `PINCH_HIT_SKILL_EDGE`), or platoon upgrade (bench bat has L/R edge vs current pitcher and scheduled batter doesn't, gated by `platoon_aggression ≥ 0.45`).
- High-`platoon_aggression` skippers prefer the platoon swap when both paths qualify.

Verified across 30-game samples: ~85% of team-sides now use more than 9 distinct batters per game (mix of joker insertions and pinch-hit substitutions).

### Run game — `mgr_run_game` scales SB attempts

`prob.between_pitch_event` reads the batting team's `mgr_run_game` and applies two scaling factors:
- **Speed threshold** — lerps from `1.30 * SB_ATTEMPT_SPEED_THRESHOLD` (passive) to `0.65 * SB_ATTEMPT_SPEED_THRESHOLD` (aggressive). High-run-game managers will run with average speed; passive ones wait for elite.
- **Per-pitch attempt probability** — lerps from `0.4 * SB_ATTEMPT_PROB_PER_PITCH` to `1.8 *` of the same.

Across the 30-game sample, all 30 games produced stolen bases (vs an unknown frequency before — the user reported low usage). Average ~4 SB per game across both teams.

### Speed → fielding range

`_position_defense_rating` in `o27v2/sim.py` now layers a speed adjustment on top of the existing `0.6 * sub-rating + 0.4 * general` blend. Per-position weights:

| Position | Speed weight |
|---|---|
| CF | 0.30 |
| LF, RF | 0.22 |
| SS, 2B | 0.18 |
| 3B | 0.08 |
| 1B | 0.04 |
| C | 0.00 |

The adjustment is `weight * (speed - 0.5)` — neutral at speed = 0.5, so the realism identity invariant is preserved (verified: existing identity tests still pass). At elite speed (0.9), a CF gets a ~0.12 bump on top of base; at 0.1 speed, a -0.12 hit. This propagates through `_team_defense_rating` into the per-team defense layer that drives range and error rates in `prob.py`.

### Line score errors

Errors were already being generated (`prob.py` rolls `is_error` based on team defense, `render._credit_fielder` increments `e` on the responsible fielder, `sim.py` persists `game_batter_stats.e`). The box score's `_line_score` helper just summed runs and hits per phase — no E column anywhere.

`app.py:_line_score` now also sums per-phase `e` totals, and `game.html` shows `R H E` columns. With `DEFENSE_ERROR_BASE = 0.018` (~1.8% of would-be-outs), a typical game shows 0–2 errors per side, which is in line with MLB's ~1.4 errors per game.

### Joker label in box scores

Joker positions show as **`J`** instead of `UT`. Implemented as a `CASE WHEN p.is_joker = 1 THEN 'J' ELSE p.position END` projection on the box-score batter queries in `app.py`. Stays out of the template so other surfaces (player detail, team page, leaderboards) are untouched.

---

## Process notes

This session was unusually iterative — the user added scope four separate times after the initial schedule complaint. Approach taken:

- **Surface engine fundamentals before committing to a path.** When the user said "the box score should show R H E", I read `o27/engine/game.py` first and discovered the engine isn't innings-based (one 27-out half per team). Surfacing that to the user *before* fake-bucketing 27 outs into nine "innings" prevented building a UI fiction the user would have rejected. They responded "just add errors" — a 30-line fix instead of an engine rewrite.
- **Match implementation depth to where the user routed the fix.** When the pitching hook came up, the user explicitly said "managerial tendencies instead of messing with the pitching model". I left the existing fatigue logic untouched and added a parallel manager-discretion layer in front of it. Same effect on the visible behaviour, far less risk.
- **Investigate before assuming a feature is missing.** "Pinch hits don't happen" turned out to be a single bad guard (`if not batter.is_pitcher`) on otherwise-functional logic. Rewriting around the dead gate was a much smaller change than building pinch hits from scratch would have been.
- **Build the schema for what's coming, even if the engine doesn't consume it yet.** Adding `mgr_platoon_aggression` to the schema and stamp pipeline before fully wiring it into starting-lineup platooning means the next layer doesn't need a migration.

---

## What's still open

Called out explicitly because the user has been iterating quickly:

- **Starting-lineup platoons.** `mgr_platoon_aggression` currently only drives mid-game pinch-hit choices. A pre-game L/R adjustment to the 8th hitter / DH spot based on the opposing SP's hand is the natural next step.
- **Defensive replacements.** Late-and-close swap of a glove for a bat at 1B / corner OF when up a run with 3-6 outs to go. Schema already has the hooks (per-position defense ratings + speed); the manager just needs a `should_defensive_sub` decision function.
- **Manager profile in the team UI.** Right now the only way to see a team's archetype is to query SQL. Surface `manager_archetype` and the seven tendency values on the team detail page so users can actually read who's running each club.
- **Series shape polish.** The schedule occasionally emits 1-game series or back-to-back same-opponent runs that exceed 4 days. A merge-pass on adjacent same-pair series would tighten this.
- **Hit-and-run / sac bunt.** `mgr_run_game` only scales straight steals today. Real "run game" includes hit-and-run and sacrifice bunts — both would need new event types in `prob.between_pitch_event` and `pa.apply_event`.
- **Manager-aware lineup construction.** The 9-batter lineup is still ordered by talent. Small-ball managers should bias toward speed at the top; modern managers should pack OBP high and slugging behind it.

---

## File-level summary

**New files**
- `o27v2/managers.py` — archetype catalogue + roll function
- `docs/aar-schedule-managers-and-game-realism.md` — this file

**Modified files**
- `o27v2/schedule.py` — full rewrite for series-aware scheduling + reseed-on-seed-change
- `o27v2/sim.py` — `get_all_star_date` gap detection; manager fields stamped on `Team`; speed → fielding range in `_position_defense_rating`
- `o27v2/db.py` — new `manager_archetype` + seven `mgr_*` columns on `teams` (CREATE + ALTER)
- `o27v2/league.py` — `seed_league` rolls a manager per team and inserts the seven tendency values
- `o27v2/web/app.py` — line-score E column; joker `J` position label in box-score queries
- `o27v2/web/templates/game.html` — `E` column in line-score table
- `o27v2/data/league_configs/*.json` — `season_days` bumped, calendar anchors added (`season_year`, `season_start_*`, `all_star_break_*`)
- `o27/engine/state.py` — `Team` carries `manager_archetype` and seven `mgr_*` fields
- `o27/engine/manager.py` — `_manager_hook_check`, rewritten `should_pinch_hit`, joker prob scaled by `joker_aggression`
- `o27/engine/prob.py` — `between_pitch_event` SB scaling by `mgr_run_game`; `should_pinch_hit` call site passes RNG

**Tests**
- `tests/test_realism_identity.py` — 6/6 still pass (defense identity preserved through the speed-range addition)
- `tests/test_stat_invariants.py` — 9 pre-existing DB-fixture tests; not affected by this work
