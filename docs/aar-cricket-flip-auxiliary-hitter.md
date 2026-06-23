# After-Action Report — Cricket Flip: the Auxiliary Hitter ("aux")

**Date completed:** 2026-06-23
**Branch:** `claude/vigilant-davinci-hn34xy`
**Builds on:** `docs/aar-cricket-batting-order.md`,
`docs/aar-cricket-batting-order-manager-decision.md`,
`docs/aar-cricket-batting-order-flip-aware-lineups.md`. Living spec:
`docs/feature-cricket-batting-order.md`.

---

## The bug the user found

The Cricket Batting Order flip reverses the lineup `1-9 → 9-1` at the top of a
new cycle, so **old #9 leads off** the next cycle. But old #9 *just* batted (he
was the last PA of the prior cycle). If he ended that cycle by **reaching base**,
the flip brings him right back to the plate **while he is still the runner on
base** — a man can't bat and be on base at once.

I confirmed it was real and unguarded. Forcing the scenario:

```
PA9: B9 SINGLES -> on 1B; bases=['p9', None, None]
FLIP applied. New leadoff (current_batter) = B9 / p9
Is the new batter ALSO the runner on 1B? -> True
```

Bases are a 3-slot list of player ids assigned with a bare `bases[0] = batter_id`
and no dedup, so if B9 then reached base again **his id landed on a second base
slot — the same player on two bases** — plus his counting stats double. With the
rule on and the new high-OBP run environment, #9 reaching base to end a cycle is
*common*, so this fired often (146 duplicate-runner events in a 40-game stress
run before the fix).

## The user's design — the auxiliary

> "If a batter due to hit is on base already during his turn, the manager uses a
>  one-off pinch hitter called an auxiliary to hit in his place." An aux is **not
>  a joker**: jokers are a *designated* pool; an aux is drafted from *anyone*
>  available, and only ever in this stranded-batter situation. "Line cutters."

This also dissolves the deeper worry ("the rest of the order in reverse might all
be on base"): each stranded due batter triggers its own aux, so the order keeps
moving no matter how many runners are stranded.

## What was built

A new override channel parallel to the joker, wired through the same event spine:

- **`state.aux_override`** (`state.py`) — like `batter_override`, but with the
  opposite lineup semantics. `GameState.current_batter` returns it after the
  joker override.
- **`manager.select_auxiliary(state)`** — drafts the best available **bench** bat
  (a roster hitter *not* in the active lineup, not on base, not subbed out).
  Bench-only is deliberate: pulling a lineup player would just make *him* bat
  twice this cycle — the exact thing the aux prevents. Bat quality =
  contact+power+eye+skill. Returns `None` only if the bench is empty.
- **`prob._maybe_auxiliary(state)`** — checked in `_try_manager_action` right
  after the flip. Fires only when the *base-lineup* due batter is on base. Emits
  `aux_insertion` (draft a bench bat) or, when the bench is exhausted, `aux_skip`
  (forfeit the stranded turn — never bat a man on base).
- **`pa.apply_event`** — `aux_insertion` sets the override; `aux_skip` advances
  the lineup past the stranded batter.
- **`pa._end_at_bat`** — the key asymmetry vs. the joker: a joker PA does **not**
  advance the lineup (it's an extra PA); an **aux PA advances normally** (the
  on-base batter forfeits this turn and stays a runner), clearing the override.
- **`game.run_half`** clears `aux_override` at half start (no cross-half leak,
  mirroring the joker override).
- **`render.py`** treats `aux_insertion`/`aux_skip` as non-PA manager events and
  emits their precomputed lines.

### One subtle correctness point

The provider detects a "new PA" by comparing `current_batter.player_id` to the
last. If the aux were drawn from the *lineup*, the aux could equal the next due
batter and that id-compare would miss the new PA — skipping the aux re-check and
re-introducing the duplicate. Restricting the aux to the **bench** (a non-lineup
id) closes this automatically: the aux is never the next due batter, so the
re-check always runs. The bench-only rule is therefore both faithful to the
design and load-bearing for correctness.

## Validation

- **Forced-flip stress (engine, `should_use_flip` always true, 40 games each):**
  - *Real bench:* aux drafted on every stranded leadoff; **0 duplicate-runner
    violations**; every aux/skip fired only with the due batter genuinely on base.
  - *Bare 9-man roster (no bench):* the safety net forfeits the turn (`aux_skip`);
    still **0 duplicates**. The engine cannot bat a man on base regardless of
    roster.
- **Two new regression tests** in `test_cricket_order.py` lock both invariants
  (`test_aux_no_duplicate_runner_with_bench`,
  `test_aux_no_duplicate_runner_when_bench_exhausted`). Full suites:
  `o27/tests` + `tests/test_stat_invariants.py` → **all green** (24 cricket tests).
- **Live app:** enabled the rule on all 30 teams, simulated 40 games through the
  real `manage.py sim` pipeline — no crash, and **156 aux events** persisted to
  `game_pbp` with real bench hitters, e.g.
  *"AUX: Orcas sends in Eddie Lane-Ganus to hit for William Villanueva, stranded
  on base after the flip."*

## Scope / not changed

- **Off by default.** Cricket Batting Order is opt-in per league
  (`teams.cricket_order_enabled`) or via the global engine-config flag. With the
  rule off, `_maybe_auxiliary` returns immediately and the engine is unchanged.
- **The aux is auto-drafted, not a manager *choice*.** It fires whenever the
  conflict arises (the manager doesn't get to decline and strand the order). The
  *who* is "best available bench bat"; a richer persona-driven pick (save your
  best bat, platoon, etc.) is a possible follow-up.
- **No new box-score/stat surface for aux usage.** The aux's PA records normally
  under the aux's own line; there's no separate "auxiliary appearances" counter
  yet — derivable from `game_pbp` if it proves worth showing.
- **`aux_skip` (bench exhausted) is a rare safety net**, not an expected outcome
  in real play — real rosters carry a bench, and flips are persona/situational,
  not forced as in the stress test.
