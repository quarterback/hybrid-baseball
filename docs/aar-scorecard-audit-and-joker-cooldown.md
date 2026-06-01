# After-Action Report — Scorecard "bug" audit + per-cycle joker cooldown

**Date completed:** 2026-05-30
**Branch:** `claude/baseball-scorecard-bug-aM39l`
**Commit:** `ce87d19` (enforce per-cycle joker cooldown)

---

## TL;DR

The user pasted three box scores that "felt like a bug": a team with 11 ABs,
a team whose pitchers recorded only 23 outs, and a home team that won in 5
outs. Two separate questions were tangled together.

1. **The out-count anomalies were not bugs.** They were legitimate walk-offs.
   In O27 the home team may *elect to bat first* (`should_bat_first`, ~50/50
   by config), which makes the **visitor** the second-batting/walk-off team —
   so the side showing fewer than 27 outs can be the away team, which looks
   impossible until you know who batted first. I reproduced 150 games and
   confirmed the invariant holds with zero exceptions: **the losing team
   always bats exactly 27 outs; any sub-27 line is the walk-off winner.**

2. **The real bug was the jokers.** A single joker piled up 7–8 ABs in a half
   while regulars got 1 — the bloated DH/joker lines in the pasted scores. The
   per-turnover joker cooldown had been deliberately stripped out at some point
   (the code literally read *"Jokers are unrestricted on rate … any number of
   times per game"*). The rule the user wants — and the one this fixes — is
   **one deployment per joker per time through the order**, resetting only when
   the nine base hitters cycle.

The fix wires the cooldown back in using state that already existed
(`Team.jokers_used_this_cycle`, `lineup_cycle_number`) and was sitting marked
"legacy, unused." 300 stress games at max joker aggression: heaviest joker
drops from the 7–8 outliers to **2 PAs/game (0 jokers over the once-per-cycle
cap)**.

---

## What was asked for

The opening message was a box score and "Feels like this is a bug," followed by
running clarifications:

> "Cleveland having 11 at bats the whole game feels impossible"
> "In this one mariners only pitched for 23 outs"
> "They are not legitimate outcomes!"
> "The half terminates at 27 outs unless from 21-27 a team declares seconds …
> unless the 2nd batting team has a lead and the other team has exhausted their
> 27 outs"

Then, after the audit, the user identified the actual defect:

> "jokers are not supposed to hit more than once through the order no matter
> which joker it is … once you reset the order and all nine guys have hit any
> joker can go if they haven't already hit"
> "Once the 9 hitter has batted that counts … its just all 9 hitters not the
> jokers that count for it to cycle a lineup"
> "we should no longer see it where a joker has hit seven times and other people
> who only hit once because that's not how it should work"

So: two deliverables. Confirm/deny the out-count anomaly, and enforce the joker
cooldown.

---

## Part 1 — Diagnosis: the out counts are correct

The renderer (`o27v2/web/box_text.py`) is a pure function — it sums the rows it's
handed, so a wrong OUT column means wrong upstream data. I chased that down in
layers:

**In-memory engine (600 games, `run_game` directly).** Summed `outs_recorded`
per batting team off `state.spell_log`. **Zero** invariant violations: the
first-batting team always reached 27 (including 88 games where a manager declared
Seconds — the banked outs get played in the rebuttal half, still totalling 27).

**Persisted DB path (150 games on a fresh `O27V2_DB_PATH` league).** Reconciled
`game_pitcher_stats` against the same invariant. My *first* pass flagged 26/150
games as anomalies — but the invariant was wrong: it assumed the away team always
bats first. It doesn't. `o27/engine/game.py:82` lets the **home manager elect to
bat first** (`should_bat_first`, `BAT_FIRST_BASE = 0.50` ± 1%). Re-running with
the correct invariant — *the **loser** must bat exactly 27* — gave:

```
REAL violations (loser != 27) = 0
walk-off winners (<27 outs)   = 58
total non-SI games            = 144
  - away-team walk-offs (home batted first) = 26
  - home-team walk-offs (normal)            = 32
```

That fully explains the pasted scores:

- **Example 1 (Cleveland 8, Mets 7):** Cleveland (home) batted *second* and
  walked off at 5 outs. Mets batted 27. A normal walk-off — "rain-shortened /
  college walkoff," as the user put it.
- **Example 2 (Beavers 14, Mariners 12):** Seattle (home) **elected to bat
  first** and batted its full 27; Portland (away) batted second and walked off
  at 23. The visitor showing 23 outs is the signature of a home-bats-first game.

No engine change was warranted here. The user initially asked to surface "who
batted first / walk-off" in the box score, then noted that context is already
present elsewhere, so the box-score note I'd drafted was reverted before commit.

The one durable takeaway: **a team batting < 27 outs is a feature (walk-off), not
a miscount.** The diagnostic harnesses are in `/tmp` (throwaway) but the method —
reconcile `game_pitcher_stats` against "loser bats 27" — is the right probe if
this ever resurfaces.

---

## Part 2 — The real bug: jokers ignored the per-cycle rule

### Root cause

`can_insert_joker` and `insert_joker` (`o27/engine/manager.py`) had the cooldown
removed and documented as removed:

```python
# can_insert_joker, before:
#   "Jokers are unrestricted on rate: any joker in the pool can be
#    inserted in any PA, any number of times per game."
# insert_joker, before:
#   "The same joker can be re-inserted in any later PA — there is no
#    per-cycle or per-game cap on insertions."
```

So the manager AI could re-insert the *same* joker on back-to-back PAs. In a
high-scoring half that produced one joker with 7–8 ABs while the rest of the
lineup turned over once or twice — exactly the `S. Battery dh … 8 AB` /
`E. Siltanen dh … 6 AB` lines in the user's first box score.

This is the counterpart to the earlier **joker rating decay** work
(`docs/aar-joker-decay-and-intentional-walks.md`). That segment, under a "no hard
cap" directive, throttled overuse *softly* — productivity decay per use,
freshness-based selection, a pool-fatigue probability dampener. Those reduce how
*well* a spammed joker hits; they don't cap how *often* he bats. The actual rule
was always a structural one-per-cycle cooldown; it just wasn't in the code. This
fix restores it. The two are complementary and both stay: cooldown bounds
frequency to once per turnover, decay bounds productivity across turnovers.

### What "a cycle" means (per the user's clarification)

A time through the order is the **nine base hitters** batting. Joker PAs are
*extra* PAs that do **not** advance the base lineup (`pa.py:342-345` clears the
override without calling `advance_lineup`), so they never count toward a cycle.
`Team.lineup_cycle_number` already increments only on a real wrap of the base
lineup — so the cycle boundary the cooldown needs already existed.

### The fix — four wiring points, no new machinery

All four reuse the previously-dormant `Team.jokers_used_this_cycle` set.

1. **`state.py — Team.advance_lineup()`** — clear `jokers_used_this_cycle` when
   the base lineup wraps to the top (`new_pos == 0`). This is the reset: once all
   nine have batted, the whole pool is eligible again.

2. **`manager.py — can_insert_joker()`** — reject a joker already in
   `jokers_used_this_cycle` ("Joker already used this time through the order").
   This is the authoritative gate.

3. **`manager.py — should_insert_joker()`** — drop used-this-cycle jokers from
   the `eligible` candidate list (alongside the existing on-base safety filter),
   so the AI never even tries an ineligible joker.

4. **`manager.py — insert_joker()`** — record `joker.player_id` into
   `jokers_used_this_cycle` on a successful insertion.

The three stale "no cap" docstrings/comments (in both files) were rewritten to
describe the per-turnover cooldown and *why* it exists, so it doesn't get stripped
again.

No overall per-game cap is introduced — across a long half a joker can still
return cycle after cycle, just never twice within one cycle. That matches the
user's "I don't want a hard cap" stance from the earlier joker segment: this is a
rules-based per-turnover reset, not an arbitrary game ceiling.

---

## Validation data

Stress harness: 300 games, jokers populated from each team's three best bats,
`mgr_joker_aggression = 1.0` (worst case), counting `joker_pa` per joker against
its team's `lineup_cycle_number`.

| Metric                                   | Before* | After |
| ---------------------------------------- | ------: | ----: |
| Max joker PAs in a single game           |   7–8   |   2   |
| Jokers exceeding once-per-cycle          |   many  |   0   |
| Total joker PAs (300 games)              |    —    | 1821  |

\* "Before" max is from the user's box scores (8 ABs) and the pre-fix code path;
the post-fix harness reports max 2 and **0** violations of `joker_pa <= cycles`.

The drop to 2 (well under the once-per-cycle ceiling of ~3–4) is expected: the
freshness selector and pool-fatigue dampener from the decay segment spread usage
across the three jokers, so the same joker rarely gets picked every single cycle.
The cooldown is the hard rule; those soft limiters keep it from even approaching
the rule most of the time.

---

## Regression check

`o27/tests` (engine), `o27v2/tests/test_managers.py`, and
`o27v2/tests/test_engine_config.py` — **77 passed**. The o27v2 smoke test
(10 fixed-seed full games) passes.

Pre-existing environment limitations, unrelated to this change and confirmed
against a clean tree: `flask` is not installed (collection errors on
`app.py`-importing test modules), and standalone `pytest` invocation of some
`o27v2/tests` modules can't resolve `o27` on the path without the smoke-test
bootstrap. None of the joker-touching code is exercised by the
flask/DB-dependent suites.

No test asserted the old "unlimited joker" behavior, so nothing needed updating.

---

## What this does NOT change

- **Joker rating decay / freshness / pool fatigue.** All untouched — they remain
  the productivity-side governor; this adds the frequency-side rule.
- **The decision *whether* to insert** (`should_insert_joker` weak-hitter and
  leverage paths). Only the *eligibility* set was narrowed.
- **`_legacy_insert_joker`.** The old lineup-mutating path still uses
  `jokers_used_this_half`; it's not on the live o27v2 event path and was left
  alone.
- **Super-innings / Declared Seconds joker rules.** Super-innings still disable
  jokers entirely; the cooldown set persists across a team's seconds continuation
  (correct — the order continues, it doesn't reset).
- **The bat-first / walk-off mechanics.** Confirmed correct; no code change.

---

## Files touched

- `o27/engine/manager.py` — cooldown gate in `can_insert_joker`; eligibility
  filter in `should_insert_joker`; record-on-insert in `insert_joker`; three
  docstrings corrected. (+14 / −9)
- `o27/engine/state.py` — `advance_lineup` clears `jokers_used_this_cycle` on
  wrap; joker-pool field comment rewritten. (+15 / −5)

Total: 2 files, 32 insertions, 14 deletions.

---

## Follow-ups — worth a glance later

- **Bat-first asymmetry redesign (deferred).** The user floated an alternative to
  the per-game `should_bat_first` coin flip: in the regular season make it an
  *even* split (each team bats first in exactly half its games, schedule-assigned
  and deterministic); in the playoffs a coin toss with atmospheric/weather noise.
  Not implemented this segment — the user set it aside ("ignore the home/away
  thing, it's already in there"). If picked up, it belongs in
  `should_bat_first` + the schedule layer, not the box score.
- **README wording.** `README.md` currently says jokers have "No overall cap on
  deployments, so … a single joker can be brought back cycle after cycle." That's
  still true *between* cycles, but the prose should make explicit that it's capped
  at once *within* a cycle, to match the restored rule.
- **Box-score walk-off legibility.** The audit showed a visitor walking off (home
  batted first) reads as impossible to a cold reader. The data to label it
  (`home_bats_first`, `seconds_outcome`) is on the game row; if the existing
  surface the user referred to isn't enough, a one-line "X batted first · Y won on
  a walk-off" note under the line score is a cheap clarity win.
