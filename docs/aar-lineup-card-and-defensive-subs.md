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

## 5. Follow-ups / not done

- The marginal defense update keys on a player's canonical `position` to match
  the game-start basis. For a starter assigned out of his canonical position
  at game build (rare), the delta basis can differ slightly from his
  game_position — bounded and re-derived from the ratio, but not worth the
  extra plumbing yet.
- The arm-term weights are first-pass estimates; tune against a full-season
  sim if defensive spread feels off.
