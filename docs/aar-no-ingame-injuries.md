# After-Action Report — no in-game injuries; offensive-sub gate is absolute

**Date:** 2026-06-22
**Branch:** `claude/vigilant-davinci-hn34xy`
**Status:** Shipped. 135 engine tests pass; 224-game DB sim verifies the rule
holds 100% of the time.

This closes out the lineup-integrity arc. Read first:
`aar-sub-gate-and-in-game-injuries.md` (the original gate + the now-removed
injury feature) and `aar-joker-leverage-only.md` (jokers as a leverage tool).

---

## 1. The rule, stated correctly

The owner's spec, verbatim: *"you can replace fielders at will when you're on
defense in the first half — it's just saying that once you actually go to bat,
whoever is in that position last actually has to hit before you can sub people
offensively (barring injury, which cannot happen in this game)."*

Two consequences I initially got wrong:

1. **Defensive substitutions are unrestricted.** O27 plays sequential 27-out
   halves (one side bats out its whole half, then the other). The team batting
   second fields the entire first half and may swap fielders freely. Those
   replacements then bat when that team comes up — so the *batting lineup* is
   "whoever holds each slot at first pitch of your half," not the original
   nine. **No gate belongs on `should_defensive_sub` / `should_swap_catcher`.**

2. **There are no in-game injuries.** The only conceivable reason a slot's
   occupant wouldn't bat before being subbed for is injury, and this game has
   none. So the offensive-sub gate is **absolute** — no bypass.

## 2. What I checked (and the false alarm)

A 224-game 8-team sim, analyzed from `game_batter_stats`:

- **Jokers:** ~1.08 insertions / team-game, concentrated in tight, late,
  runners-on spots (mean score gap **3.1** at insertion vs **9.1** for all PAs;
  mean runners **1.9** vs **1.3**). The weak-hitter bench is gone; the pitcher
  (weakest bat) bats ~3.2×/game and is never benched. ✓
- **False alarm:** counting *original starters* who batted, the second-batting
  team showed <9 in 84% of games — which looked like a gate failure. It was a
  measurement error: those teams legally swapped fielders on defense, and the
  *replacements* batted. Counting actual lineup occupants (starter **or**
  defensive replacement) who got a PA before any offensive sub:

  | | full games | all 9 slots batted before any offensive sub |
  |---|---|---|
  | First-batting | 222 | **100%** |
  | Second-batting | 151 | **100%** |

  The `lineup_cycle_number >= 1` gate already enforces the rule exactly. Good
  that I asked before adding a wrong defensive-sub gate.

## 3. Fix — remove the in-game injury feature

Deleted Part B in full so the gate has no bypass and the codebase matches the
"no in-game injuries" design:

- **Engine:** removed `o27/engine/injury.py`; the per-PA injury roll in
  `prob.py` (`_injury_checked`, `injury.roll_injury_event`, the import); the
  `_apply_injury_sub` executor and the `injury_sub` dispatch in `pa.py`; the
  `state.in_game_injuries` field; and the `INJURY_INGAME_*` constants in
  `config.py` (replaced with a note).
- **o27v2:** removed `apply_in_game_injuries` from `injuries.py` and its call /
  parameter / import in `sim.py`'s `_post_game_roster_processing`. **Off-field,
  between-game injuries are untouched** (`process_post_game_injuries`,
  `process_returns`, the IL) — those are a roster-management feature, not an
  in-game event, and the owner's rule is specifically about in-game play.
- **Tests:** dropped the two injury cases from `test_sub_first_cycle.py` and
  updated its docstring; the gate/joker cases remain.

## 4. Validation

- `pytest o27/tests` — **135 passed**.
- `o27v2/manage.py initdb + sim` — imports clean, games sim end-to-end with the
  injury code gone.
- Removing the bypass only tightens the gate, so the 100%-all-9-bat result
  above is preserved (and now unconditional).

## 5. Not done / notes

- Off-field injuries (IL stints between games) remain by design. If the intent
  is *zero* injuries of any kind, say so and I'll gate `process_post_game_
  injuries` too — but that's a roster-depth feature, not the in-game rule.
- The two superseded AAR sections in
  `aar-sub-gate-and-in-game-injuries.md` are kept for history with a banner;
  the Part-B code they describe is gone.
