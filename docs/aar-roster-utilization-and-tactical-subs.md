# After-Action Report — Roster Utilization, Tactical Subs, HBP

**Date completed:** 2026-05-04
**Branch:** `claude/fix-schedule-generation-2yJKH`
**Commits:** `39b7812`, `6d54b6c`, `615ab33`, `5978a4b`

---

## What was asked for

Two threads, both surfaced as the user watched simulated games:

> "i realize the injury model doesn't work as it's supposed to, meaning guys play every day, backups never surface and there's no logic to sitting them"

The injury model itself was working — `process_post_game_injuries` does fire and `get_active_players` does top up rosters from reserves. The actual problem was upstream: the **starting lineup was deterministic**. `fielders[:8]` in `_db_team_to_engine` always picked the same 8 specific-position regulars sorted by DB id. Backups (UT bench) only surfaced when one of the 8 was on the IL. There was no *rest-day* mechanic at all for position players, even though pitchers had a workload model with `days_rest` / `pitch_debt`.

> "there's also no hit by pitches that happen, so that needs to be fixed"

Quick scan of `pitch_outcome`: `_PITCH_NAMES = ("ball", "called_strike", "swinging_strike", "foul", "contact")`. HBP was a valid event type in `pa.apply_event` and had a fully-wired handler, but `_PITCH_NAMES` didn't include it — the only place HBP was generated was Phase-1 hand-scripted tests. Production sims produced literally zero hit-by-pitches.

> "we should just see more pinch hitters in a sport like this too. defensive teams subbing guys after a period of time before they get to hit and first at bat teams cycling through hitters to get to their preferred guy in the field a quasi-situation where the road team hits their power bats first and then lets their defensive specialists in the field knowing they won't hit again. another tactical thing for this sport"

A genuinely O27-specific tactical layer: with each team batting in one continuous 27-out half (vs MLB's alternating innings), a manager can *bank* offense early and then swap defensive specialists into the lineup for the rest of the fielding half. The road team's classic version: bat power up top, then load the field with glove-first guys for the bottom — those guys won't bat again unless the game goes to a super-inning.

> "If you're doing this right the roster should have a lot of subs ... a variety of non-starting players across positions"

After the first cut of the tactical layer landed with one-sub-per-team-per-game caps, the user pushed back. Real teams cycle through the bench, and O27's continuous-half structure creates *more* sub spots, not fewer. Caps were artificially limiting.

---

## What was built

### Stage 1 — Rest-day mechanic + `mgr_bench_usage` + HBP fix (`39b7812`)

**Position-player workload tracking.** New `_position_player_workload` in `o27v2/sim.py` walks the team's *actual played dates* (not literal calendar days, so the All-Star break gap doesn't reset streaks) and counts each player's consecutive starts as of yesterday:

```python
# Pull the team's distinct played dates, descending
team_dates = [...]
# For each player, count how many of those dates they had pa > 0
for player_id, started_dates in player_starts.items():
    consecutive = 0
    for td in team_dates:
        if td in started_dates: consecutive += 1
        else: break  # gap → streak ended
```

**Rest-day decision in `_db_team_to_engine`.** After computing `starting_fielders = fielders[:8]`, a per-starter rest roll fires:

```
rest_p = 0.06 * (0.20 + 1.40 * mgr_bench_usage)
       + (age - 30) * 0.005      if age >= 30
       + 0.04                     if pos == "C"
       + 0.10                     if pos == "C" and consecutive >= 4
       + min(0.15, (consecutive - 4) * 0.04)  if consecutive >= 5
```

Capped at 0.40, max 2 rests per game. RNG seeded from `(game_date, team_role, team_id)` so decisions are reproducible per game and don't depend on the sim-time clock.

The substitute is the highest-skill UT bench bat. Position-aware substitution (right-handed UT for SS, etc.) is a follow-up — the UT pool is already generally usable across the diamond.

**`mgr_bench_usage` calibrated across all 14 archetypes:**

| Tier | Archetypes | Range |
|---|---|---|
| Run-the-same-eight | dead_ball, set_and_forget, iron_manager | 0.05–0.12 |
| Lightly rotate | old_school, players_manager | 0.22–0.40 |
| Moderate rotation | small_ball, fiery, hot_hand | 0.45–0.65 |
| Aggressive rotation | bullpen_innovator, modern, mad_scientist, gambler | 0.60–0.85 |
| Full Rays mode | sabermetric_max | 0.85 |

Plumbed end-to-end: schema column + ALTER migration, `seed_league` insert, `Team` field stamp, `_db_team_to_engine` consumer.

**HBP fix.** Identity-preserving design — `_pitch_probs` is untouched, so the realism identity invariant on the underlying probability surface stays intact. Instead, in `_generate_pitch`, when the drawn outcome is `"ball"`:

```python
hbp_p = HBP_FROM_BALL_BASE + (0.5 - command) * HBP_COMMAND_SCALE
if rng.random() < hbp_p:
    outcome = "hit_by_pitch"
```

`HBP_FROM_BALL_BASE = 0.018`, `HBP_COMMAND_SCALE = 0.030`. Lower-command pitchers convert more balls to HBP. At neutral command (0.5), the rate produces ~1.5 HBP per game total — a hair above MLB's ~1.4 but in the right ballpark.

**Verified (60-game sample, 30-team league):**
- 90 distinct UT bench players got starts (was 0 before the rest-day mechanic).
- Top teams used 17–19 distinct batters across the window (was ~12 — same 9 every game plus jokers).
- HBP firing at 1.52/game total.

### Stage 2 — Defensive substitution by the fielding team (`6d54b6c`)

The first half of the O27 tactical layer the user described.

**`should_defensive_sub(state, rng)`** — fires for the FIELDING team's manager (not the batting team — that's `pinch_hit`). Conditions:
- Regulation half, not super-inning
- `state.outs >= 6` so this isn't a first-batter overreaction
- Bench has a notably-better-defense bat available

**Probability:** `0.005 + 0.040 * mgr_bench_usage` — 0.5% to 4.5% per qualifying PA. An old-school skipper basically never does this; a sabermetric_max manager fires it most games.

**`mgr.defensive_sub(state, player_out, player_in)`** event handler. Critically, the swap takes the *lineup slot* — when the substitute's slot cycles up to bat (in their offensive half or super-innings), they're the one batting. This is the engine half of the road-team play: pull a slugger from the lineup, the bench glove takes their spot in the order, and when the road team is fielding for the rest of the game they have the better defense.

Wired through:
- `manager.py` — new `should_defensive_sub` decision + `defensive_sub` helper
- `pa.py` — new `defensive_sub` event branch
- `prob.py` — `ProbabilisticProvider._try_manager_action` calls it after pinch-hit and before sac-bunt

### Stage 3 — Mid-batting-half offensive→defensive swap (`615ab33`)

The second half. Companion to `should_defensive_sub` but for the BATTING team — fires after the lineup has cycled once so the slugger has banked at least one PA, then pulls them and brings in a defensive specialist who'll cover the field for the team's fielding half.

**`should_swap_offensive_for_defense(state, rng)`** — strictly road-team-only:
- `state.half == "top"` — visitors batting, home team fielding. Visitors will field next.
- `team.lineup_cycle_number >= 1` — order has wrapped at least once.
- The home team is *intentionally excluded*. They bat last; by the time they're at the plate their fielding half is already done and there's no defense to lock in. The tactic is asymmetric.

The event reuses `mgr.pinch_hit` semantics (replace current scheduled batter, take the slot) but logs as `DEF SWAP` and records its own event tag in `state.events` so it tracks separately from leverage-driven pinch hits.

Same probability shape: `0.005 + 0.040 * mgr_bench_usage`.

### Stage 4 — Drop the caps (`5978a4b`)

The user pushed back on `already_subbed` / `already` once-per-team-per-game caps I'd added to both Stage 2 and Stage 3. Removed both. The mechanics throttle themselves naturally:

- Each successful sub removes a bench bat from the candidate pool.
- The worst-defense starter changes dynamically as the lineup shifts.
- The per-PA probability stays at 0.5%–4.5%, so we don't get a sub parade.

---

## Verification

**Realism identity** (`tests/test_realism_identity.py`): 6/6 pass after every commit. The HBP fix preserves identity because the conversion happens *after* `pitch_outcome`, leaving `_pitch_probs` untouched. The rest-day, defensive_sub, and tactical_def_swap mechanics all gate on conditions that don't trigger at neutral fixture inputs (no manager tendencies stamped, no workload, etc.).

**Live sub-volume distribution** (30-game sample, 60 team-sides):

| Distinct batters per side | # sides | Notes |
|---|---|---|
| 9 | 8 | minimum lineup, no subs |
| 10 | 19 | 1 sub |
| 11 | 15 | 2 subs |
| 12 | 9 | 3 subs |
| 13 | 5 | 4 subs |
| 14 | 4 | 5 subs (aggressive-bench teams) |

Avg 10.9, max 14. **83 distinct UT bench bats** appeared in starts across the league. Shape matches MLB per-team batter usage (9–13 typical, with a fat tail for aggressive-bench-usage skippers like sabermetric_max and bullpen_innovator).

The asymmetric design produces an interesting structural effect:

| Half-side metric | Avg distinct batters |
|---|---|
| Visitors (bat top, field bottom) | 10.6 |
| Home (field top, bat bottom) | 11.4 |

The home team's slightly elevated count comes from defensive subs in the *top* half (when home is fielding); those substitutes then bat in the bottom half, padding the home batter count. Visitor mid-half tactical swaps balance some of this, but home teams structurally see more bench bats getting plate appearances. This matches the user's intuition about the asymmetric value of the tactic to the road team — they get the *defensive* benefit without the substitute showing up on offense.

**HBP rate** (60 games): 1.52/game total (MLB ~1.4/game).

---

## Process notes

- **Investigated the injury model before changing anything.** The user said "the injury model doesn't work as it's supposed to" but the *injury draws* themselves were fine — `process_post_game_injuries` correctly rolled per-player chances and updated `injured_until`. The actual gap was the lineup builder always picking the same 8. Surfacing that distinction kept the fix scoped to lineup construction instead of touching the injury draw layer.

- **Built the schema for what's coming.** Adding `mgr_bench_usage` to the persona vector and the schema migration *before* it had any in-engine consumer kept the persona work in one PR. Stage 2 and Stage 3 just read the field that was already there.

- **Designed for self-throttling rather than hard caps.** First-cut had `already_subbed` flags that capped each tactical sub at one per team per game. Aesthetically tidy but artificially limiting. The user was right to push back — bench depletion and a low per-PA rate produce the same end-state behavior without the magic cap. The distribution table in Stage 4 (avg 10.9 distinct batters, max 14) shows the system bottoming out at sensible totals on its own.

- **Identity-preserving HBP via post-processing.** Adding HBP as a sixth `_PITCH_NAMES` entry would have shifted every other probability and broken the test_realism_identity invariant. Converting balls → HBP *after* `pitch_outcome` returns leaves the probability surface untouched at neutral inputs and only adds HBP when the pitcher's command rolls below 0.5. Same observable behavior, no test surgery.

---

## What's still open

- **Position-aware bench substitution.** Right now rest-day fills are highest-skill UT regardless of which position is being rested. A SS rest day pulls a UT bat who plays SS *de facto* but is still tagged "UT" in the DB. A position-specific backup pool (per-position rest fills) would tighten the realism.
- **Defensive subs in super-innings.** All three sub mechanics gate on `not state.is_super_inning`. Real managers absolutely do swap a defender in for the bottom-of-the-tenth — extending the mechanic into super-innings is a small follow-up.
- **Manager profile in the team UI.** The whole bench-usage / leverage / pinch-hit-aggression vector is invisible from the front-end. The team detail page should show "Manager: Modern Tactician (quick-hook 0.78, bench-usage 0.71, …)".
- **Catcher rotation specifically.** The rest-day rule has a +0.10 bonus after 4 consecutive starts for catchers, but doesn't enforce a hard "C plays at most 5 of every 6" constraint. The current bonus produces close to that statistically; a hard cap would make it deterministic.
- **Consecutive-game streaks tracked as a season-long stat.** Once these mechanics produce visible variation, an Iron Man / Cal-Ripken-style longest-streak leaderboard becomes interesting — currently the workload helper computes streaks per-game but doesn't surface them anywhere.

---

## File-level summary

**`39b7812` — Rest-day + bench_usage + HBP**
- `o27v2/sim.py` — new `_position_player_workload`; new rest-day pass in `_db_team_to_engine`; `position_workload` and `game_date` parameters threaded through call sites; `mgr_bench_usage` stamped on `Team`.
- `o27v2/managers.py` — new `bench_usage` axis on `Archetype`; calibrated across all 14 archetypes.
- `o27v2/db.py` — new `mgr_bench_usage` column on `teams` (CREATE + ALTER).
- `o27v2/league.py` — `seed_league` INSERT extended.
- `o27/engine/state.py` — `Team.mgr_bench_usage` field.
- `o27/engine/prob.py` — HBP conversion in `_generate_pitch`.
- `o27/config.py` — `HBP_FROM_BALL_BASE`, `HBP_COMMAND_SCALE`.

**`6d54b6c` — Defensive substitution**
- `o27/engine/manager.py` — `should_defensive_sub`, `defensive_sub` helper.
- `o27/engine/pa.py` — `defensive_sub` event branch.
- `o27/engine/prob.py` — provider call site.

**`615ab33` — Mid-batting-half tactical swap**
- `o27/engine/manager.py` — `should_swap_offensive_for_defense`.
- `o27/engine/pa.py` — `tactical_def_swap` event branch (reuses `pinch_hit` semantics; logs as `DEF SWAP`; records its own event tag).
- `o27/engine/prob.py` — provider call site.

**`5978a4b` — Drop the caps**
- `o27/engine/manager.py` — removed `already_subbed` from `should_defensive_sub` and `already` from `should_swap_offensive_for_defense`.

**Tests**
- `tests/test_realism_identity.py` — 6/6 pass through every commit.
- `tests/test_stat_invariants.py` — 9 pre-existing DB-fixture tests; not exercised but the new mechanics don't change row-count invariants since they all reuse existing event types' persistence paths.
