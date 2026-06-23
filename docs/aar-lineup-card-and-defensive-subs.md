# After-Action Report — the lineup card + defensive subs that matter

**Date:** 2026-06-22
**Branch:** `claude/vigilant-davinci-hn34xy`
**Status:** Shipped. 144 engine tests pass (7 new in
`test_lineup_card.py`); o27v2 team-defense identity preserved (all-defaults
→ 0.5). The o27v2 web-sim suite needs flask/a DB and isn't runnable in the
bare sandbox (environmental).

---

## 1. Symptom

Box scores showed the team that **bats second** (and therefore fields the
entire first half before it bats) taking the field with a batting order that
was **missing a defensive position** — no shortstop for Kaunas, no second
baseman for Frankfurt, no shortstop *or* center fielder for Magadan (CF only
appeared via a sub "in the 3rd"). The first-batting team was always complete;
the second-batting team was a man short, every time. The owner's read: a
lineup that doesn't field all nine "is something besides baseball."

## 2. Diagnosis

O27 plays one continuous 27-out half per side, so the team batting second
fields all 27 outs first. In the engine the **batting order and the fielding
alignment were the same list** (`Team.lineup`), and `defensive_sub` mutated it
(`fielding.lineup[idx] = player_in`) and retired the displaced starter
one-way. The defensive-sub gate keyed on the **opposing** (batting) team's
cycle (`state.batting_team.lineup_cycle_number`), which turns over early in the
first half — so the second-batting team's manager spent that whole half
swapping its own "weakest-defense" starters out of the order **before any of
them had batted**. By the time it hit, those starters (and their positions)
were gone.

Two deeper facts shaped the fix:

- **`defense_rating` is computed once at game start (`sim.py`) and never
  updated.** So the old, order-corrupting defensive subs didn't even improve
  defense — they were all cost (a lost bat / a vanished position) and no
  benefit.
- The renderer already creates a "DEF" stat row for an incoming defensive
  player with zero PAs (`render.py`), so a field-only entrant still shows up as
  "Entered at X" without taking a bat.

## 3. Fix

### A — the batting order is a fixed lineup card
A tactical (non-injury) `defensive_sub` is now **field-only**: it records the
glove change and the box-score entry but does **not** touch `lineup` and does
**not** retire the displaced starter. The starter is "not out" — he keeps his
slot in the order all game; the substitute is a defense-only "nickel fielder."
New `Team.field_replacements` ({out_id: in_player}) lets `should_defensive_sub`
avoid re-covering a slot or re-using a glove. `_log_substitution` gained a
`one_way=False` mode so the field-only sub is logged without firing the
one-way exit. **Injuries are the one exception** (`defensive_sub(..,
injury=True)`, routed from `_apply_injury_sub`): an injured fielder is a real
out, so his replacement takes both the glove and the batting slot.

Consequence: every starter bats his first time through regardless of defensive
maneuvering, so the order always shows all nine — no more vanished positions.
(Offensive pinch-hit / pinch-run still change the order, but they're already
gated to fire only after the first cycle.)

### B — a position-coverage floor at construction
`o27v2/sim.py` now tops the starting fielders up to eight from the joker/DH
pool if the active fielder pool ever comes up short (a position drained by
injuries with no reserve), so the lineup card always covers all eight
positions. Normally a no-op — rosters carry 19 fielders.

### C — defensive subs that actually matter (and can backfire)
Because `defense_rating` was frozen, a defensive sub had no tactical effect.
Now a sub moves the fielding team's `defense_rating` by the **real marginal
value** of the swap at that position, and it can go up *or down* — a sub is
just a player. The position-aware rating (new shared module
`o27/engine/defense.py`, single source of truth for both the game-start
computation and the in-game delta) blends position-group glove fit, general
defense, a **speed/range** term (heavy in CF, nil at the corners) and a new
**arm** term (heavy at C / corner OF / left-side IF). A rangey, strong-armed
outfielder helps; an error-prone first baseman shoved into center with no arm
hurts. Both the speed and arm terms are deviations from neutral (0.5), so a
league of average players is unchanged and the all-defaults identity (→ 0.5)
is preserved. The exact marginal update is possible because `sim.py` now
stashes the rating's weighted/weight sums on the team
(`defense_weighted_sum` / `defense_weight_sum`); simple sims that hard-set
`defense_rating` (college / world-cup / youth) have weight_sum 0 and the live
update is a safe no-op.

## 4. Validation

- `pytest o27/tests` — **144 passed**, incl. new `test_lineup_card.py`:
  field-only sub leaves the order untouched and the starter un-retired;
  injury sub replaces in-order and retires; `should_defensive_sub` never
  re-covers a slot or re-uses a glove; a full round of defensive subs leaves
  all nine card slots and every position intact; a better glove raises team
  defense, a butcher lowers it; all-defaults identity → 0.5.
- o27v2 `_team_defense_components` verified directly: all-default eight →
  weighted/weight 7.25/14.5, rating 0.5; `POSITIONAL_VALUE` is now the shared
  engine object.

## 5b. Follow-up (2026-06-22): joker/phase subs + position eligibility

Two consistency items from §5 are now done:

- **`joker_to_field` and `phase_transition_swap` are field-only too.** Both now
  install the incoming glove and move team defense by the swap's marginal value
  but leave the batting order alone — the outgoing players are "not out" and
  keep their slots (recorded via `field_replacements` + `one_way=False`). The
  joker is still removed from the joker pool (committed to the field); the
  phase-transition regulars (who have already batted) keep batting into
  super-innings.
- **Position eligibility is enforced on every defensive placement.** New
  `defense.eligible_positions(player)` = primary `position` ∪ the
  `role_field_pos` list (the per-position glove-threshold encoding already
  produced at generation/development). `should_defensive_sub`,
  `should_joker_to_field` and `should_phase_transition_swap` now only place a
  player at a position he's eligible for — an error-prone first baseman can't
  be slid to center unless his glove cleared the center-field bar. Forced
  injury replacements remain the one exception (emergencies can field a player
  out of position).

## 5c. Follow-up (2026-06-22): defensive-sub timing gates

A defensive replacement is a late-inning lock-in, not an early-game move.
`should_defensive_sub` now has two timing gates on top of the cycle gate:
a **hard floor** (`DEFENSIVE_SUB_MIN_OUTS`, default 3 — never in the opening
outs of any game) and a **rarity window** before the late-game out
(`DEFENSIVE_SUB_LATE_OUT`, default 16): even when leverage clears, an early
defensive sub only fires on a small probability roll
(`DEFENSIVE_SUB_EARLY_RATE`, default 0.05). Super-innings are late by
definition and skip the rarity roll. The catcher rotation (its own out gate,
6) and joker-to-field (out ≥ 24) already gated their own timing.

Note on persisted games: a box score is stored when the game is simmed, so
already-played games keep their old lines — only games simmed after the engine
restart reflect these rules.

## 5d. Follow-up (2026-06-22): the last order-mutating swap

A fresh box score (game #33) showed the first-batting team's order churned by
"Replaced X at CF/C/1B" entries whose replacements *batted* — the
single-player **offensive→defensive swap** (`tactical_def_swap`), the one
defensive executor still routing through `pinch_hit`. Converted it to
field-only: `should_swap_offensive_for_defense` now mirrors
`should_defensive_sub` (worst card defender + best eligible bench glove) and
the new `offensive_to_defensive_swap` executor stages that glove for the
team's fielding half — team defense moves by the swap's value, but the batting
order is untouched. The renderer's `tactical_def_swap` branch now mirrors the
`defensive_sub` branch (DEF row for the incoming glove, no PA). With this,
**every** defensively-motivated executor (defensive_sub, catcher rotation via
defensive_sub, joker_to_field, phase_transition_swap, offensive→defensive
swap) is field-only; only genuine offensive moves (pinch hit / pinch run) and
injuries change a batting slot.

Deferred (owner call): a dedicated O27 box-score layout that separates the
offensive batting card from the defensive alignment. The engine is correct;
the current renderer reuses the DEF-row convention. Box-score redesign is a
nice-to-have, not core.

## 5e. Follow-up (2026-06-22): the defensive log

With the batting card and the fielding alignment now decoupled, the box score
gained a per-team **DEFENSIVE LOG** — each position's coverage by out-envelope,
the natural way to read field-only subs. Built in the **live** path
(`o27v2/web/box_score.py`, not the engine renderer, which the web app doesn't
use for the box). Pitcher envelopes come from cumulative `ip_outs`; the eight
field positions from the starter plus any defensive entries (DEF /
joker-to-field), ordered by `entered_inning` with boundaries at
`(entered_inning − 1) × 3`. Offensive subs (PH / PR) are excluded — they change
the batting card, not the field, so a pinch-hit-for starter still shows as the
fielder. All nine positions always listed; unchanged ones read "(Outs 1-N)".
No schema change for the first cut — it reused already-persisted
`game_position` / `entry_type` / `entered_inning` / `ip_outs`. **Exact
out-envelopes followed (owner request):** a new `entered_outs` column records
the precise team-out count at entry (engine `BatterStats.entered_outs` →
sim persist → `game_batter_stats` column + migration), so a mid-inning sub now
reads its true out (e.g. 1-4 / 5-27, not the inning edge 3). Legacy rows
without `entered_outs` fall back to the inning boundary. Pitcher envelopes were
already exact (cumulative `ip_outs`). Owner deferred a broader offense/defense
box redesign; this is the targeted slice that makes the field-only model
legible.

## 5f. Follow-up (2026-06-23): blowout management

Box scores showed the *winning* team in laughers (51-4, 35-10, 24-6) riding its
starter to a 120-130-pitch complete game and batting the same nine 6-8 times —
nobody rests when up 40. Two context-dependent "rest the starters" paths,
gated so they ONLY fire when the lead is decisive (silent in close games):

- **Pitcher pull** (`should_change_pitcher`): if the pitcher's team leads by
  `BLOWOUT_PULL_LEAD` (10) once `BLOWOUT_PULL_MIN_OUTS` (12) outs are in, pull
  the STARTER to rest him (first spell only; relievers keep mopping up). In
  O27's structure this naturally hits the first-batting team defending a big
  lead in the second half. Needs a bullpen to execute — the engine's 1-arm
  dummy roster can't, but real rosters (17 pitchers) can.
- **Position rest** (`should_pinch_hit`): after the leverage path declines, if
  the batting team leads by `BLOWOUT_REST_LEAD` (10) and the order has turned
  `BLOWOUT_REST_MIN_CYCLE` (2) times, rotate the best available bench bat in for
  the regular due up. Low-leverage by design (the "we're up 20, empty the
  bench" path), self-limiting to bench size.

**Last-licks deploy (same follow-up).** The flip side of resting in a laugher:
in a *close* game the second-batting team (O27's bottom-of-the-9th — its at-bats
are do-or-die) should reach for the bench to manufacture situational runs, not
let a non-star bat through a key spot. `score_substitution` now adds
`DECISIVE_HALF_LEVERAGE_BONUS` (0.12) to the pinch-hit / pinch-run leverage when
`_decisive_chase` holds: the batting team bats second, the gap is within
`DECISIVE_HALF_MAX_GAP` (3), and it's past `DECISIVE_HALF_MIN_OUTS` (12)
(super-innings always qualify). The first-batting team (building a total) and
blowouts (gap too large) are excluded, so it never churns early or in laughers —
it only sharpens deployment in the spots that decide games.

**Platoon pinch-hitting late + pinch-run specialists (same thread).** Two more
"smart looks":
- `score_substitution` adds `PLATOON_LATE_BONUS` (0.10) × lateness when a bench
  bat *flips* an unfavorable handedness matchup to favorable (cand has the
  platoon edge, the batter doesn't) — the classic late-game lefty/righty pull,
  a non-factor early and a real move late.
- `should_pinch_run` adds `PR_SPECIALIST_BONUS` (0.10) for a dedicated burner
  (`roster_slot='pr_specialist'` or `role_run`) so the manager sends the right
  legs, not just any faster bat — and (with the last-licks boost) actually
  deploys him close-and-late.

**Blowout bench works BOTH ways + live workload rest (same thread).**
- A 28-1 box showed the *trailing* team batting its same nine passively ("yeah
  we'll just lose"). The blowout-rest path was lead-only; switched it to the
  absolute margin so both benches empty in a laugher — the leader rests
  starters, the trailer gives the bench a look / tries to spark something.
- **Live workload rest** (#4): `sim.py` stamps a per-game `rest_pressure` on
  each starter (consecutive starts blended with a cold habit-cup);
  `should_pinch_hit` then gives a worn/cold regular the back third off late in a
  DECIDED game (`WORKLOAD_REST_SAFE_GAP` ≤ margin < blowout, past
  `WORKLOAD_REST_MIN_OUTS`, `rest_pressure ≥ REST_PRESSURE_THRESHOLD`), never in
  a do-or-die spot. Only the worn sit — not blanket churn.

Tests: `test_blowout_management.py` (15) — pull/rest fire in laughers, quiet in
close/early; last-licks boost scoped right; platoon edge grows late; pinch-run
prefers the specialist; trailing team empties the bench down 17; worn regular
rested in a 6-run game, fresh regular not, worn regular kept in a one-run game.
168 engine tests pass. Still standing: double switches.

## 5. Follow-ups / not done

- The marginal defense update keys on a player's canonical `position` to match
  the game-start basis. For a starter assigned out of his canonical position
  at game build (rare), the delta basis can differ slightly from his
  game_position — bounded and re-derived from the ratio, but not worth the
  extra plumbing yet.
- The arm-term weights are first-pass estimates; tune against a full-season
  sim if defensive spread feels off.
