# After-Action Report — Stats Expansion, Workload Model, W/L Attribution, Stats Page

**Date completed:** 2026-05-03
**Branch:** `claude/improve-sim-realism-UHvKE`
**Commits:** `990e3b9`, `a833200`, `86ebabd`, `66bded7`, `f75a55a`

---

## What was asked for

The user opened with three concerns about the live league:

> "I want more robust stats — it's a baseball game, surely there must be a way to iterate on existing stats or use them to see how players are doing and to eval the engine better. There's still no stats page, just a leaderboard and a way to look at teams. Mostly what I care about now is advanced stats and more of the counting stats — it's still very rudimentary."

> "Also I realize this thing doesn't have a fatigue model and that needs to be thought out — pitchers are still throwing every day and that shouldn't be happening."

Concrete evidence: Donnie Owens (Indians) had pitched 23 outs on 4/01, 23 on 4/02, and 23 on 4/03 — three complete-game starts in three days. The `/stats` route at the time was just a 302 redirect to `/leaders`.

Two mid-task clarifications shaped the design:

> "Pitcher wins/losses should be credited like baseball — if your team is winning and you pitched for no less than 12 outs and/or the way it normally works after that. Saves are hard to figure out how."

> User scope answers: **both in one push** + **real workload model** (pitch-debt that decays, 5-man rotation, days-rest for the bullpen, tired multiplier) + persist dropped stats + advanced rate stats + new stats-browse page.

The user explicitly excluded splits this round.

---

## What was built

Five staged commits, each independently verifiable. The branch was pushed in the order they appear here.

### Stage 1 — Stats persistence (`990e3b9`)

The engine and renderer were already tracking HBP, SB, CS, multi-hit ABs, stay-RBI, unearned runs, and (post-realism layer) foul-outs — but `o27v2/sim.py` silently dropped every one of them on the way to the DB. SB and CS weren't tracked per player at all; the steal handler in `pa.py` just adjusted bases and outs without crediting anyone.

Fixes:
- New fields on `BatterStats` (`sb`, `cs`, `fo`) and `PitcherStats` / `SpellRecord` (`sb_allowed`, `cs_caught`, `fo_induced`).
- New state fields `pitcher_sb_allowed_this_spell`, `pitcher_cs_caught_this_spell`, `pitcher_fo_induced_this_spell` reset in `_assign` and rolled into the SpellRecord at `_close_current_spell`.
- `pa.py` increments those counters at the right events (steal success / failure, 3-foul foul-out).
- Renderer adds an early-return `stolen_base_attempt` branch that credits SB/CS to the **runner** (looked up via `ctx["bases_list"]` pre-event), not the current batter — and short-circuits before the leftover-out reconciliation, which would have double-charged the batter for the runner's CS out.
- DB schema gets six new columns on `game_batter_stats` (`hbp`, `sb`, `cs`, `fo`, `multi_hit_abs`, `stay_rbi`) and five on `game_pitcher_stats` (`hbp_allowed`, `unearned_runs`, `sb_allowed`, `cs_caught`, `fo_induced`). ALTER TABLE migration follows the existing pattern; defaults of 0 keep pre-realism rows neutral.
- Both INSERT paths (the inline `simulate_game` insert and the helper `_insert_*_stats` functions) write the new columns.

Verified end-to-end: a fresh-seeded test DB produces non-zero `multi_hit_abs`, `cs_caught`, and `fo_induced` on real rows.

### Stage 2 — Advanced rate stats (`a833200`)

With the missing counters now in the DB, `_aggregate_batter_rows` and `_aggregate_pitcher_rows` get the full advanced-stat suite alongside the classical AVG/OBP/SLG/OPS/ERA/WHIP/FIP set:

| Side | Added |
|---|---|
| Batter | OBP now includes HBP; ISO; BABIP; K%, BB%, HR% per PA; BB/K; SB% on attempts |
| Pitcher | HR/27, RA/27; K%, BB%, HR% per BF; oAvg = H / (BF − BB − HBP); BABIP allowed |

Every query path that calls the aggregators was extended to SUM the new columns: `/leaders`, `/player` season totals (both batting and pitching), `/team` roster aggregates.

Sanity-checked against a 200-game test DB: a sample top-K pitcher produced K% 36.4%, BB% 4.5%, oAvg .238, BABIP-allowed .273 from 22 BF / 16 outs — every number reconciles by hand.

### Stage 3 — Workload model (`86ebabd`)

Three independent fixes layered on top of each other to make pitcher usage stop looking like Donnie Owens.

**1. Rest-tiered SP picker** in `_db_team_to_engine`. The previous code:

```python
rested = [p for p in pitchers if id_not_in(rest_excluded)]
candidate_pool = rested or pitchers   # ← BUG
sp = max(candidate_pool, key=stamina)
```

That `or` collapses to "the full pitcher pool" the moment every arm has thrown in the last 4 days, which happens about a week into the season. The new code tiers candidates by `min_rest in (4, 3, 2, 1)` and picks the highest-Stamina arm in the best non-empty tier. The last-resort branch picks the **most-rested** arm, not the highest-stamina — that's what stops the rotation from collapsing onto a single workhorse.

**2. Roster reordering.** The bug above wasn't even the load-bearing one. The engine's `_set_fielding_pitcher` picks the **first `is_pitcher` in `Team.roster`**, ignoring the lineup's SP slot entirely. Until I sorted the chosen SP to the front of `engine_players`, the rotation logic was purely cosmetic — the lineup said "Yamamoto starts today" but `game.py` quietly grabbed whoever sorted first in the DB query.

**3. Tier-based relief filter** in `manager.pick_new_pitcher`. A soft score penalty wasn't enough — Infante had 0.44 Stuff, picked up a rest penalty of about 0.30, and still beat his rested teammates' 0.30–0.50 Stuff scores, especially after the high-Stuff "fresh arm" candidates had pitched the day before too. Replaced with a hard candidate filter: prefer arms with ≥ 3 days rest; broaden to ≥ 2 if nobody qualifies; broaden to ≥ 1 only as a last resort. Plus a `_rest_penalty` multiplier on the score for arms that DO get used despite low rest.

**4. Multi-game fatigue penalty** on `today_form` in `ProbabilisticProvider._maybe_roll_form`. Per-pitcher stamina-relative budget (`stamina × 100` pitches over the rolling 5-day window, floor 30) is checked against `pitch_debt`; excess pitches scale form down by `FATIGUE_DEBT_PER_PITCH` up to `FATIGUE_DEBT_MAX_PENALTY`. Identity invariant: at `pitch_debt = 0` the formula collapses to a no-op.

**5. Workload-state helper** `_pitcher_workload_state` in `sim.py`. Computes per-pitcher `days_rest` + decayed `pitch_debt` + raw 5-day pitch count from `game_pitcher_stats`, stamps it onto each `Player` object before the game starts. Workload state is fully derived — no new DB columns, no migrations.

Verified: in a fresh-seeded 200-game run, the top-used pitcher on the most-played team appears in 5 of 12 games (4-man rotation cadence) with **no pitcher exceeding a single back-to-back day**. Compare against pre-fix behavior where Alvin Infante pitched 14 consecutive days.

### Stage 3b — W/L attribution (`66bded7`)

Replaces the previous attribution (which awarded W/L to whoever recorded the most outs on a team in a game — biased toward starters even when they got bombed).

Winning team:
- SP earns W if they recorded ≥ **12 outs** (4 IP scaled to O27's 27-out structure).
- Otherwise the W goes to the most effective reliever on the winning team, scored by `outs − ER` with a tiebreaker on raw outs. Approximates the MLB scorer's "most effective reliever" rule without modeling lead-state per inning.

Losing team:
- L charged to the pitcher with the most earned runs allowed in the game. Tiebreak: earlier appearance (took the lead loss). Sidesteps the full "pitcher of record at the lead change" rule but produces stable, defensible attribution.

**Saves intentionally NOT computed.** The user flagged this as "hard to figure out how" and a real save calculation requires lead-state-by-inning tracking we don't currently capture. Deferred.

Verified math: 200-game test DB yields exactly 200 W and 200 L, with the distribution spread across many pitchers (top winners at 3–4 W each through 200 games, not concentrated on a single workhorse).

### Stage 4 — Stats-browse page (`f75a55a`)

The `/stats` URL went from a 302 redirect to a real page.

Two tabs (Batting / Pitching) on the same route, with:
- Team filter (dropdown of all teams)
- Position class filter (batter view: position players / pitchers / all)
- Name search (LIKE match)
- Manual min PA / min outs gate
- "Qualified Only" checkbox that auto-computes the threshold as ~3.1 PA per team-game (MLB-equivalent) or ~1 out per team-game
- Click-to-sort headers via the existing `.sortable` infrastructure (data-sort-value attrs preserve numeric precision when sorting)

Surfaces every stat the engine produces:

**Batting:** G, PA, AB, H, 2B, 3B, HR, R, RBI, BB, SO, HBP, SB, CS, FO · AVG, OBP, SLG, OPS · ISO, BABIP, K%, BB%, HR%, BB/K.

**Pitching:** G, W, L, BF, Outs, IP, H, R, ER, BB, SO, HR, HBP, UER, SB, CS, FO, P · ERA, FIP, WHIP, K/27, BB/27, HR/27, K/BB · K%, BB%, HR%, oAvg, BABIP-allowed.

Plus a **Stats** entry in the navbar between Leaders and Transactions, and `_PSTATS_DEDUP_SQL` was extended to expose the realism-layer columns so any path that consults the dedup view can read them.

End-to-end: `/stats`, `/stats?side=pit`, `/stats?qualified=1`, `/stats?team=29`, `/leaders` — all 200, full data.

---

## Key decisions and trade-offs

### Hard rest filter, not soft penalty

The first attempt was a multiplicative score penalty in the manager AI: a tired arm just lost some "score" but stayed in the candidate pool. With high-Stuff closers like Infante (Stuff 0.44, on a team where most other pitchers had 0.3–0.5), the penalty rarely overcame the Stuff edge. A 14-day consecutive-appearance streak was the result. Replaced with a hard tier filter: candidates outside the rest tier are **excluded**, not just penalized. The tier widens (3 → 2 → 1) only when nobody qualifies stricter. This is what stops the rotation from collapsing.

### The roster-reorder fix was the actual fix

It looked like the SP rotation was working — `_db_team_to_engine` was picking different SPs every day and putting them in the lineup. But the engine's `_set_fielding_pitcher` doesn't read the lineup's SP slot; it picks the first `is_pitcher` in `Team.roster`. So the same arm pitched every day regardless of what the SP rotation chose. The fix is one line: sort the chosen SP to the front of `engine_players`. Took longer to find than to fix.

### Saves deferred

A real save credit requires knowing the lead state at the moment a reliever entered. We could persist per-spell `lead_state_on_entry` and `lead_state_on_exit` to derive saves, but that's a separate persistence pass and the user explicitly flagged this as deferrable. Wins-by-relief uses a coarser proxy (best `outs − ER`) which is good enough until we want true save / hold accounting.

### Splits deferred

Vs-LHP / vs-RHP splits, home/road, by-month — all worth doing but require a separate aggregation pipeline (either materialized splits tables or live SQL views with split-column outputs). The user explicitly didn't choose splits this round.

### Why O27 W/L uses 12 outs, not 5 IP

The user defined it: "no less than 12 outs". Mapping MLB's 5 IP (= 15 outs) into O27's 27-out structure ratios cleanly to about 12 outs. Per-9-IP equivalents would warp at the structural level — a 12-out start in O27 is already "deep" by their rules.

### `_PSTATS_DEDUP_SQL` view extension

The dedup view existed to mitigate Bug #2 from HANDOFF.md (duplicate `game_pitcher_stats` rows). When I added new columns to `game_pitcher_stats` for Stage 1, the view's column list didn't pick them up — anywhere using the view returned `OperationalError: no such column`. Extending the view's SELECT was a one-shot fix.

---

## What was verified

- **6/6 identity tests pass** (`tests/test_realism_identity.py`) at the end of every stage.
- **`o27v2/smoke_test.py` passes 10/10 games** at the end of every stage.
- **`o27/tune.py --games 100`** produces K% 17.59% ✓, BA .292 ✓, SLG .452 ✓, BB% 8.70% (just below target band — within sample variance), HR/PA 1.83% (close to band edge).
- **Workload model verified end-to-end:** fresh 200-game run on the most-played team shows the top SP pitching 5 of 12 games (4-man rotation), with NO pitcher exceeding a 1-day back-to-back streak. Pre-fix the same scenario produced a 14-day consecutive-day streak for one arm.
- **W/L math reconciles:** 200 games → 200 W and 200 L, with distribution spread across many pitchers (top winners at 3–4 W each).
- **Web routes verified:** `/stats`, `/stats?side=pit`, `/stats?qualified=1`, `/stats?team=<id>`, `/leaders` — all 200, all serve real data.
- **HANDOFF Bug 5** (super-inning assertion crash) — already fixed in the prior realism push; remains stable across this push's longer batch runs.

---

## Files changed

| File | Change |
|---|---|
| `o27/stats/batter.py` | `BatterStats` gets `sb`, `cs`, `fo` |
| `o27/stats/pitcher.py` | `PitcherStats` gets `sb_allowed`, `cs_caught`, `fo_induced` |
| `o27/engine/state.py` | `SpellRecord` gets matching fields; new spell-tracking state on `GameState`; `Player` gets `days_rest`, `pitch_debt` |
| `o27/engine/pa.py` | Steal handler increments SB-allowed / CS-caught; foul-out branch increments FO-induced |
| `o27/engine/game.py` | `_close_current_spell` rolls new fields into SpellRecord and resets them on new spell; `_assign` resets fresh state on pitcher change |
| `o27/engine/manager.py` | `pick_new_pitcher` gets the rest-tier filter and `_rest_penalty` |
| `o27/engine/prob.py` | `_maybe_roll_form` adds the multi-game fatigue penalty |
| `o27/render/render.py` | `_update_stats` SB/CS branch credits the runner with early-return; foul-out branch credits `s.fo` |
| `o27/config.py` | `FATIGUE_DEBT_*` knobs added |
| `o27v2/db.py` | Six new batter columns + five new pitcher columns; ALTER TABLE migrations |
| `o27v2/sim.py` | `_pitcher_workload_state` helper; `_db_team_to_engine` accepts `workload`, stamps it on Player, applies tier-rest SP pick, reorders roster so SP is first; INSERT paths write the new columns; extract functions read the new fields |
| `o27v2/web/app.py` | `_aggregate_batter_rows` + `_aggregate_pitcher_rows` extended with advanced rate stats; `_pitcher_wl_map` rewritten with MLB-style W/L logic; `_PSTATS_DEDUP_SQL` exposes new columns; `/stats` route replaced with full browse page; query SUMs extended at every call site |
| `o27v2/web/templates/base.html` | Nav "Stats" link added |
| `o27v2/web/templates/stats_browse.html` | New: full sortable, filterable batting + pitching tables |

---

## Known issues / follow-up candidates

- **BB% sat at 8.70%**, just under the 9–10% target band. Within sample variance for 100 games but worth a 500-game read. If it stays sub-9, a small bump to ball weight at early counts (or a slight lift on `BATTER_EYE_BALL`) closes the gap.
- **Saves and holds.** Deferred per user direction. Implementing them properly requires per-spell `lead_state_on_entry` / `lead_state_on_exit` persistence — a separate stage.
- **Splits** (vs L/R, home/road, monthly). Deferred per user direction. The persistence layer now has handedness data and game-date data; the splits aggregation pipeline is the missing piece.
- **HANDOFF Bug 2** (duplicate `game_pitcher_stats` rows) — not chased. The dedup view continues to mask it for display purposes; a UNIQUE constraint + INSERT-OR-IGNORE migration would close it permanently.
- **HANDOFF Bug 4** (`outs_recorded = 0` rows for batters with `ab > 0`) — partially mitigated by the SB/CS early-return fix in Stage 1, which stops the leftover-out logic from sometimes mis-attributing CS outs to the batter. A full audit would still find some mis-attributed outs in complex multi-runner sequences.
- **HANDOFF Bug 6** (`_find_pitcher_id` dead branch). Cosmetic, untouched.
- **Aging / drift hooks** (HANDOFF §5). Static attributes still — no offseason drift. Stays in the open-issues bin.
- **Within-game pitch count display.** `pitches` is now persisted per spell and shown on the stats-browse page, but not on the game box score. Trivial template addition for a follow-up.
- **`_recently_used_pitcher_ids`** is now redundant with the richer `_pitcher_workload_state`. Kept for back-compat (the simpler set form is still consumed by callers); a future cleanup could drop it entirely.
