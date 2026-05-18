# After-Action Report — Baserunning Friction, Game-Ending Fixes, Situational Stat Bugs

**Branch:** `claude/add-situational-stats-mCE0A`
**Commits:** `f86601b` → `14d64d7` → `d61128c` → `075df2a` → `86752a1` → `97ae49f` → `bfa150e`

---

## What the user asked for

A long, branching session with several distinct asks that landed in this order:

1. **Situational Stats page renders all 0% / 0** across every per-base advancement and 2C conversion column.
2. **Errors should be far more prevalent** — the fielding leaderboard had hundreds of players at a 1.000 fielding percentage.
3. **Sim keeps resetting after 5/31** in production. Browser shows generic "Load failed", date badge snaps back to a day in May whenever the user tries to sim past it.
4. **Cap super-innings at 4 rounds** and let regular-season games end in genuine ties.
5. **The 2C / runner-advancement mechanic isn't realistic** — runners always advance, no defensive counterplay, hits and runs are functionally the same statistic.
6. **More CS by catchers, more OF assists at the plate, more baserunning errors.** Make the existing failure mechanisms fire often enough that the play-by-play actually reads like a defense is on the field.
7. **Don't chase MLB rate-stat targets** or hit numbers from a fabricated brief — just implement the mechanics and let the resulting numbers be the sport.
8. **A detailed brief** (which the user later disowned as "made up the wrong") covering probabilistic per-runner advancement with specific percentages, a TOA event, LOB tracking, and CS tuning notes.
9. **Apply both follow-up dials**: bump OUT% across all advancement tables (including the ones that previously had no out outcome) AND widen LOB to count any baserunner erased without scoring, not just runners standing on a base at half-end.
10. **Fix the home/visitor gap** — home was winning ~70% with a ~4 R/g lead.

---

## What landed

### 1. Situational Stats — the 0% rendering bug

Two layers were silently dropping the counters between the live engine tracking and the rendered HTML:

- `Renderer._stat_delta` (`o27/render/render.py:1558`) hand-rolled the set of `BatterStats` fields it carried into per-phase deltas. The c2_op_*, c2_adv_*, adv_op_*, adv_adv_*, rad_*, gidp, and gitp fields weren't in that list, so the per-phase row written to `game_batter_stats` always had them at 0 — even though `Renderer._update_stats` was incrementing them correctly mid-game.
- The almanac batting aggregator's `_BATTING_SUM_FIELDS` (`o27/almanac/compute.py:303`) was missing the `adv_op_*` / `adv_adv_*` keys, so even rows with non-zero raw values would have failed to sum into the season totals.

Both lists now cover the full set of per-PA / per-2C / RAD counters.

**Caveat captured in the commit message:** historical DB rows written before the fix still have those columns at 0 — they'll need a re-sim to backfill. New games populate correctly.

### 2. Errors — DEFENSE_ERROR tuned above the rounding-noise floor

`DEFENSE_ERROR_BASE` was sitting at 0.018 (~1.8% of would-be-outs at neutral D), tuned for MLB's ~1.4 errors/game. But O27 resolves far fewer BIPs as would-be-outs (the 2C mechanic and the high overall safe-rate eat the BIP-to-out conversion), so 1.8% rounded to "league fielding pct essentially 1.000". Lifted to 0.045 baseline, 0.090 cap, 0.060 scale, 0.010 floor. Errors now actually appear on the fielding leaderboard.

### 3. Sim keeps resetting — SI cap

The actual root cause traced through `run_game` (`o27/engine/game.py:163`) which had a bare `while not state.winner:` super-inning loop with **no round cap**. Two evenly-matched lineups producing identical 5-dismissal scores would lock the engine inside `simulate_game()`. The bulk-sim per-chunk wall-clock deadline only fires *between* games, so a single hung game silently consumed every HTTP chunk: Safari's fetch eventually died as "Load failed", the server kept running, the game stayed `played=0`, and every subsequent sim attempt got the clock yanked back to that game's date by `resync_sim_clock`.

Declared Seconds made the symptom more reachable: a successful comeback that lands the game tied feeds the SI loop more often than the engine had been seeing before.

`SI_MAX_ROUNDS = 4` cap added at the top of `game.py`. For playoff games the loop forces a deterministic winner via a stable hash of the team-id pair so the bracket keeps moving. Regular-season games are allowed to end in a tie at the cap — the sim's W/L update already guards against `None` winners, so this just falls through to the existing path with the game row's `winner_id` written as `NULL`. `GameState` got an `is_playoff` flag stamped from the game row in sim.py so the engine knows which fork to take. `Renderer.render_game_over` and the non-renderer fallback both grew a tie banner so the box score still closes cleanly when `state.winner` stays `None`.

### 4. Runner-out + 2C-failure realism pass

Lifted the existing failure-mode probabilities so the play-by-play actually feels like there's a defense on the field. None of these were chasing scoring targets — they're event-rate dials that had drifted too low:

- **Runners thrown out at the plate** (`RUNNER_THROWN_OUT_AT_HOME_*`): base 0.09 → 0.18, floor 0.02 → 0.05.
- **TOOTBLAN** (`TOOTBLAN_SAFE_*`): safe rate 0.78 → 0.62, ceiling 0.96 → 0.88, floor 0.45 → 0.32. Aggressive baserunning carries real cost.
- **Stolen-base success** (`SB_SUCCESS_*`): base 0.72 → 0.58, catcher-arm weight 0.20 → 0.35, pitcher influence 0.15 → 0.20, floor/ceiling pulled in. Elite catcher arm meaningfully matters.

**New mechanism in the 2C path:** a `STAY_DEFENSE_READ` roll inside `prob.py` resolves a fraction of valid stays as broken-up by the defense reading the play — catcher pickoff at second, OF charge-and-throw, IF rotation catching the lead runner. Scales with team defense and catcher arm. Lead runner is marked out. The talent-weighted expected-advance floors were also trimmed (weak `1.0 → 0.55`, medium `1.5 → 1.05`) so neutral-talent batters don't get a guaranteed-advance on every 2C.

### 5. Probabilistic baserunning advancement + TOA event + LOB

The big architectural change. Previously `runner_advances_for_hit` had hardcoded auto-advancement logic — `adv3 = 1` (3B always scores on single), `adv2 = 2` (2B always scores on double), etc. The 2B-from-single thrown-out-at-home check fired sometimes but every other "score" outcome was deterministic. Every hit basically became a run.

The new function drives each runner on base through an independent probability table, with `_avg_outfielder_arm` pulling LF/CF/RF arms from the fielding team's lineup and combining with the runner's speed to shift the table outcomes. The function now returns `(advances, out_idxs_list)` instead of `(advances, out_idx)` so multiple runner-out events on the same play are supported (e.g. 2B-runner nailed at home AND 1B-runner nailed at second on the same single).

The probability tables (deliberately fuzzy off-round numbers — see config):

| Runner | Hit type | Score | Hold     | Out  |
|--------|----------|-------|----------|------|
| 3B     | Single   | 71%   | 25%      | 4%   |
| 2B     | Single   | 54%   | 32%      | 14%  |
| 1B     | Single   | (12% to 3B) | 77% to 2B | 11% |
| 2B     | Double   | 83%   | 13%      | 4%   |
| 1B     | Double   | 43%   | 34% to 3B / 16% to 2B | 7% |

Plus `SPEED_ADVANCE_MOD = 0.12` and `ARM_ADVANCE_MOD = 0.11`: speed pushes the score outcome up (and hold down), outfielder arm pushes score down and out up.

**TOA (thrown out advancing):** added as a tracked stat on `BatterStats`. Credited to the *runner* who was nailed, not the batter at the plate. The advancement-table outs propagate through the outcome dict via a new `toa_runner_idxs` list; the renderer reads it, credits each runner with `outs_recorded += 1` and `toa += 1`, and subtracts that from the leftover-out reconciliation so the batter isn't double-charged.

**LOB:** new `lob: int` field on `Team`. Tallied at:
- Half-end (`run_half` in `game.py`) when the half closes naturally at out 27 with runners still standing.
- Declaration time (`pa.py` declaration handler) — every runner standing when a team declares Seconds is stranded; they don't carry over to the rebuttal.
- Any base-erasure event: CS (in the renderer's `stolen_base_attempt` branch), TOA, FC lead-runner-out, GIDP-runner-out (all in the renderer's `ball_in_play` branch). These are baserunners who left the bases without scoring — the standard "wasted baserunner" reading of LOB.

Combined output (200 games, foxes @ bears): TOA `~3.7/g`, CS `~2.4/g`, LOB `~10.7/g` combined (~5/team), sample finals show real per-game texture — `V 5R 11H 12LOB` (wasted offense) and `V 17R 15H 4LOB` (efficient slugfest) as distinct stories.

### 6. Home/visitor gap — partial fix

`BAT_FIRST_BASE` was hardcoded at 0.65 with a comment in config.py explicitly calling it a "retcon for the league's existing home-scores-more asymmetry." That baseline translated into home batting first 71.5% of games and home winning ~70% with a ~4.3 R/g lead.

Dropped to 0.50. Home-bats-first is now ~56% (the residual tilt comes from the persona / park / starter scalars, which respond to actual game context and are fine to leave). But home still wins ~70%, gap still ~4 R/g. **Forced-bat-first testing confirms this is not a bat-order issue** — when visitors are forced to bat first, home still wins 73% of those games.

The structural home advantage that remains is something else. Best guess: a combination of the walk-off mechanic favoring the second-batting team plus the fact that home is the ONLY team with a tactical bat-order choice, so even a fair 50/50 base lets home pick the more favorable option most games. This deserves its own investigation segment.

---

## What was decided NOT to do

- **Chase MLB rate stats.** O27 is its own sport — 27-out halves, 2C mechanic, declared Seconds. Trying to land it at MLB's .245 league AVG / 4.5 R/team is fighting the design. The user reinforced this multiple times mid-session, including disowning a fabricated brief that had specific target ratios.
- **Touch the stay mechanic baseline conversion rate (~70%).** The 2C is core to the sport's identity. The realism pass added a defensive failure mode without trimming the success rate.
- **Touch K%, BB%, HR/PA.** Not in scope.
- **Modify the declaration AI itself.** Only added LOB tracking to the declaration event.
- **Modify pitcher fatigue or the arc structure.**
- **Add a brand-new mechanic** for "outfield assists at 3B" or "catcher pickoffs at first" as their own event types. The existing TOA / CS / defense-read paths cover the same conceptual ground without inventing new event categories.

---

## Files changed

- `o27/config.py` — DEFENSE_ERROR, TOOTBLAN, SB_SUCCESS, RUNNER_THROWN_OUT_AT_HOME, BAT_FIRST_BASE, new ADVANCE_* tables, SPEED_ADVANCE_MOD / ARM_ADVANCE_MOD, STAY_DEFENSE_READ_*
- `o27/engine/prob.py` — full rewrite of `runner_advances_for_hit`, new `_avg_outfielder_arm` and `_resolve_table` helpers, new STAY_DEFENSE_READ block, multi-out outcome plumbing through `resolve_contact`, talent-weighted 2C floor trim
- `o27/engine/game.py` — `SI_MAX_ROUNDS` cap, tie path through `_game_over`, LOB credit on half-end in `run_half`
- `o27/engine/state.py` — `lob: int` on Team
- `o27/engine/pa.py` — LOB credit on declaration
- `o27/render/render.py` — `_stat_delta` field list fix, TOA crediting block in `ball_in_play`, CS-LOB credit in `stolen_base_attempt`, tie banner in `render_game_over`
- `o27/stats/batter.py` — `toa: int` on BatterStats
- `o27/almanac/compute.py` — `_BATTING_SUM_FIELDS` adv_op_* addition
- `o27v2/sim.py` — `state.is_playoff` plumbed from the game row

---

## Addendum — Home/visitor follow-up: items 1/2/3, edge cap, and the fixture-artifact reveal

After the initial AAR, the user asked for items 2 and 3 from a follow-up brief (target pressure + fielding fatigue), then item 1 (rebuttal-phase offense tilt), then a 1% cap on the home strategic edge in `should_bat_first`. All implemented as symmetric-by-role nudges that route through the existing `contact_quality` shift and `def_dev` paths:

- **Target pressure** (`TARGET_PRESSURE_SHIFT=0.030`, fades to 0 by PA 13): contact-quality tilt for the team batting second during their early PAs. Fires in regulation only.
- **Fielding fatigue** (`FIELDING_FATIGUE_PENALTY=0.030`, gate at `state.outs >= 20`): `def_dev` penalty applied to the first-batting team's defense in the late arc of the second half — they've been on the field longer.
- **Rebuttal offense** (`REBUTTAL_OFFENSE_SHIFT=0.035`): contact-quality tilt during seconds rounds and super-innings, capturing "pitchers cool off during the declaration pause, batter timing stays sharp."
- **`BAT_FIRST_HOME_EDGE_CAP=0.01`**: clamps the situational scalars (park / starter / persona / bullpen / weather) so the home bat-order decision can never deviate from `BAT_FIRST_BASE` by more than ±1%. Removes the strategic asymmetry where home is the only team with a tactical bat-order lever.
- **`should_swap_offensive_for_defense` symmetric**: was gated on `state.half == "top"` (the cricket "visitor bats first then fields" assumption). With bat-order now coin-flip, that silently fired only for visitors. Rewired to fire for the first-batting team regardless of identity.
- The bat-first test that asserted `home_bat_first > 50%` (per the old 0.65 retcon) was updated to assert "near-50% with binomial CI" — the new design intent.

### The diagnostic that mattered

After all of the above, foxes-vs-bears smoke tests still showed home winning 73% with a ~4 R/g gap and home converting 30% more runs per PA. Score-state splitting ruled out a "leading vs trailing pitcher composure" effect — home was more productive in every bucket. The trace pass instrumented `contact_quality`, `resolve_contact`, and `runner_advances_for_hit` to capture per-call inputs and outputs grouped by `state.batting_team.team_id`, and the result was definitive:

```
PITCHER STUFF facing each batting team:
  home      avg pitcher_stuff faced: 0.5268    (= make_foxes()'s pitcher)
  visitors  avg pitcher_stuff faced: 0.6264    (= make_bears()'s pitcher)
```

Then on the supposed "swap" (visitors=make_bears, home=make_foxes), the trace showed home STILL facing 0.5268 stuff and visitor STILL facing 0.6264. The swap was a no-op.

The cause: `make_foxes()` hardcodes `team_id="visitors"` and `make_bears()` hardcodes `team_id="home"`. The engine routes batting/fielding via the `team_id` *string*, not by which slot the team object occupies on `GameState`. So `state.batting_team` (which checks `state.half`) looks up by slot, but `_score_run` writes to `state.score[team_id]` keyed on the team's intrinsic id. With the fixture's hardcoded ids, swapping the GameState slots semantically did nothing — bears stayed routed as "home", foxes stayed routed as "visitors", and the test was running the same matchup 200 times with different labels.

Properly swapping (reassigning `foxes.team_id = "home"` and `bears.team_id = "visitors"`):

```
home(foxes)    32% wins   12.20 R/g
visitor(bears) 68% wins   15.50 R/g
gap -3.30 (home scoring 3.30 LESS than visitor)
```

Bears as visitors still won 68%. The "home advantage" was always just the team-strength gap between two unbalanced fixtures.

Confirming with two genuinely identical teams built inline (same pitcher_skill, same speed, same everything):

```
=== 300 BALANCED games ===
  W-L:  home 152 (50.7%) / visitor 148 (49.3%)
  R/g:  home 14.94  visitor 14.79  gap +0.16
  HBF:  51.7%
```

The engine has no structural home bias. The 4 R/g gap I was chasing for hours was a phantom created by:
1. The two test fixtures having a +0.10 `pitcher_skill` differential (bears 0.62 vs foxes 0.52)
2. The fixtures hardcoding `team_id` strings, which silently prevented any of my "swap" tests from actually testing what they purported to test

### What stays from the home/visitor follow-up

All the items-2/3/1 mechanics, the edge cap, and the `should_swap_offensive_for_defense` symmetry fix remain committed. They're good engineering on their own terms:
- The symmetric-by-role tilts make bat-order strategy more interesting and the seconds-round arc feel different from regulation.
- The edge cap removes a strategic asymmetry that *would* favor home if the persona/park/starter scalars ever produced strong signals.
- The `should_swap_offensive_for_defense` fix was a genuine cricket-logic gate that no longer matched the bat-order reality.

None of them needed to exist to "fix the home advantage" because that advantage was never there. But they're net-positive for the engine regardless.

### Where this points for the production app

If the deployed league shows home winning more often, the question to ask is **whether the league's home teams have meaningfully stronger rotations than the road teams in aggregate**. A simple league-balance audit (mean pitcher_skill across each team's projected starters, plotted against home record) would surface that quickly. Without that audit, "home advantage" in production stats is indistinguishable from "the better team is more often the home team in the games we sampled."

---

## Open items (revised)

1. **Production league balance audit.** If home teams consistently outscore visitors in the deployed app, check whether the rotation strength is balanced league-wide before assuming an engine bias.
2. **User's dial recommendations for the advancement tables.** Still pending: the user proposed `ADVANCE_2B_ON_1B_SCORE` to 0.48-0.52 (currently 0.54) and `ADVANCE_1B_ON_2B_SCORE` to 0.38-0.42 (currently 0.43). Suggested when they thought the baseline was 0.58 / 0.47; needs confirmation with the actual current values.
3. **Historical data backfill.** The situational-stats DB fix only applies going forward. Pre-fix rows still have zeroed advancement / 2C / RAD / gidp / gitp columns. A re-sim would backfill them; alternatively the almanac could read zeros as "no data" and present accordingly.
4. **`make_foxes()` / `make_bears()` hardcode `team_id` strings.** This makes the fixtures unsuitable for testing home/visitor symmetry. Should be parameterized or renamed so the `team_id` follows the slot they're placed in. A test-only ergonomics issue but cost hours of diagnostic work to discover, so worth flagging.

---

## Things to remember (updated)

- The user does not want MLB-target chasing. Knobs should be tuned by what plays right in the engine on its own terms, not by what MLB looks like.
- The user can't watch games on the deployed app directly — diagnostic numbers need to be surfaced via the smoke tests or commit messages so they can make tuning calls without playing through games themselves.
- The user is willing to commit fuzzy off-round numbers in config when the alternative reads "designed" too obviously (e.g. 73/27 instead of 75/25, 13% instead of 15%).
- The user wants the brief / spec to be authoritative *only* if they wrote it. AI-suggested specs are treated as suggestions; the user's instinct on what feels right beats the spec.
- **Always verify test fixtures before running a long diagnostic on them.** `state.batting_team`, `state.fielding_team`, and the `state.score` dict are all keyed off `team_id` *strings*, not the GameState slots. Test fixtures that hardcode `team_id="home"` or `team_id="visitors"` can't be swapped at the GameState level — they have to be rebuilt with new ids. Burning hours hunting a "structural home advantage" that was actually a fixture-strength gap is the cautionary tale.
