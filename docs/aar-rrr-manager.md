# After-Action Report — RRR manager AI (BOTH sides act on the run rate)

**Date:** 2026-06-23
**Branch:** `claude/adoring-meitner-7wsd8p`
**Status:** Shipped. **On by default** (`RRR_MANAGER_ENABLED = True`), toggleable
via config / the Engine Settings dashboard. Engine suite green with the feature
on; validated on a fresh flag-on sim DB and a league-level on/off smoke. Needs a
real-DB season smoke before release to confirm the balance shift is acceptable.

> **Update (same day):** initially shipped chaser-only. The owner pointed out
> that in cricket the side batting *first* also plays to a rate the whole way
> (CRR / projected total / par), accelerating through the death overs — so it
> shouldn't sit flat. Extended so the first-batting side paces against a **par**
> total. The sections below describe the final, symmetric design.

---

## 1. Why this happened

The analytics phase (`docs/aar-rrr-3o.md`) shipped RRR/3O as a display metric and
deliberately deferred the manager-AI half of the original T20 spec. The owner
then asked to wire the behavior in. Two decisions framed it:

- **Fold into the existing leverage framework**, not a parallel threshold ladder.
  `manager.py` already makes pace-ish decisions off score + outs
  (`_decisive_chase`, `_desperation_rally`, blowout/garbage-time rest), so a
  second independent A/B/C ladder would double-count leverage and destabilize
  tuned balance.
- **On by default** (owner's call), but kept as a toggleable bool so it's
  A/B-testable against the pace-blind manager.

The original spec's Thresholds A (≥3.0) / B (≥6.0) / C (≥12.0) map onto existing
systems rather than new machinery. Lever scope chosen: **substitution +
concession** and **contact aggression**; **baserunning skipped** (its
"station-to-station vs sell-out" intent is self-conflicting and second-order).

## 2. What got built

- **In-engine signal** — `o27/engine/state.py`: `GameState.chase_rrr_3o()`
  returns the chaser's RRR/3O (vs `target_score + 1`), else `None`.
  `o27/engine/manager.py:_pace_rrr(state)` generalizes it: chaser → real-target
  RRR; first-batting side → RRR-to-**par** (`state.par_score` or
  `cfg.RRR_PAR_SCORE`). Reuses `required_run_rate_3o` from `o27/stats/team.py`.
  No stored rate field → no staleness; every lever auto-no-ops outside a live
  innings. Added per-game override fields `rrr_manager_enabled` and `par_score`.
- **Flag + bands + par** — `o27/config.py`: `RRR_MANAGER_ENABLED = True` plus
  tunable bands `RRR_AGGRO_THRESHOLD = 3.0`, `RRR_DESPERATION_THRESHOLD = 6.0`,
  `RRR_CONCESSION_THRESHOLD = 12.0`, `RRR_CONTACT_LIFT_MAX = 1.20`, and
  `RRR_PAR_SCORE = 12` (≈ league-avg team total, measured from an 80-game sim).
  Auto-exposed by `o27v2/engine_config.py` (toggle added to "Optional rules").
- **Gate + band helpers** — `o27/engine/manager.py`: `rrr_manager_on(state)`
  (per-game override → cfg, mirroring `power_play_on`); `_pace_rrr`; `_is_chaser`;
  `rrr_aggressive`, `rrr_conceding`, `rrr_contact_mult`.
- **The chaser/first-team asymmetry** — `rrr_conceding` is **chaser-only**: a
  chase can be mathematically dead (RRR ≥ CONCESSION → freeze premium bench),
  but the first team never concedes its own innings — more runs always help, so
  above AGGRO it just keeps accelerating with no upper cutoff.
- **Lever 1 — substitution + concession (reuse):**
  - `_desperation_rally` gains a pace-aware OR branch — also True when
    `rrr_aggressive(state)`. This reuses the existing `DESPERATION_RALLY_BONUS`
    in `score_substitution`, so a chaser behind the required rate reaches for its
    best bats even when the raw deficit/outs-left gate wouldn't fire.
  - `should_pinch_hit` gains a concession gate before the leverage return: when
    `rrr_conceding(state)`, return `_scrub_pick(candidates)` — freeze the premium
    pinch-hitter on a dead chase (the offensive mirror of the blowout mop-up).
- **Lever 2 — contact aggression** — `o27/engine/prob.py` (~2949): the chaser's
  `_count_hard_mult` is multiplied by `mgr.rrr_contact_mult(state)` (ramps 1.0→
  `LIFT_MAX` across [AGGRO, DESPERATION], holds at the cap above). Swing-for-the-
  fences when behind pace.
- **Tests / docs** — `o27/tests/test_rrr_manager.py` (9 tests);
  `docs/stats-reference.md` updated; this AAR.

## 3. Design decisions / honesty

- **Determinism preserved by construction.** The contact lift only reshapes the
  weak/medium/hard probabilities *before* the existing single `rng` draw in
  `contact_quality` — it adds **no** new `rng` calls. So the seed stream length
  is unchanged: games stay seed-deterministic, and a flag-off game is identical
  to pre-feature behavior. (Test: a full game is byte-identical across two
  same-seed runs; flag-off runs are stable.)
  - Caveat surfaced while testing: the pre-game `home_bats_first` coin flip falls
    back to the **global** `random` when no rng is passed at setup
    (`manager.should_bat_first`), so the determinism test pins it. Pre-existing,
    not introduced here.
- **Bands are tunable, not calibrated.** League pace ≈ 1.3 RRR/3O, so 3 / 6 / 12
  read as behind / well-behind / hopeless — a reasonable shape, but the exact
  numbers are knobs, not empirically fit. The `RRR_*` config constants exist
  precisely so they can be tuned once there's season data.
- **Concession applies to the bench, not the bats.** A conceding team's regulars
  still swing big (contact lift holds at the cap through the concession band);
  only premium *pinch-hitters* are withheld. That matches "preserve assets for
  the next game" without making a lost cause swing meekly.
- **Effect size is modest, and the symmetry rebalances it.** Chaser-only, the
  feature moved the second-batting team's win% noticeably (a ~2pp swing in the
  smoke). Once the first team also paces to par, the net win-rate effect shrinks
  to near-noise (chaser ~0.502 → ~0.505, first team ~0.498 → ~0.495 over 400
  randomized games) while both sides' offense ticks up slightly (first team
  13.63 → 13.65, chaser 10.21 → 10.57 runs). The chaser still gains marginally
  more — a concrete target is a sharper signal than par — which is intended.
- **Why the first-team effect is small at the default par.** `RRR_PAR_SCORE = 12`
  ≈ the league mean, so a typical innings is already at/above par and doesn't
  accelerate; only a below-pace innings slogs late. Raise par for a more
  aggressive first-innings persona — it's a tuning knob, not a fixed law.

## 4. Validation

- `pytest o27/tests/test_rrr_manager.py` — 14/14 (signal gating both sides, flag
  override, band helpers, par pacing, first-team-never-concedes asymmetry, par
  override, desperation fold-in, concession freeze, determinism, on≠off).
- `pytest o27/tests` — **198 passed** with the feature on by default (no pinned
  seeded-outcome regressions).
- Fresh flag-on DB (`initdb` + `sim 80`): `pytest tests/test_stat_invariants.py`
  → 13 passed; `/game/<id>` renders with the RRR column + Pressure-Curve panel.
- League on/off smoke (400 randomized games): see §3 — both-sided pacing keeps
  win% near even; offense ticks up slightly; no crashes; determinism holds.

## 5. What I did NOT change / known limitations

- **Baserunning untouched** (owner's scope call): `mgr_run_game` / stay EV are
  not RRR-aware.
- **Regulation only.** The pace signals use the 27-out envelope and exclude
  super-innings and Declared-Seconds frames; those chase phases aren't modeled.
- **Par is a single tunable constant.** `RRR_PAR_SCORE` is league-wide; a per-
  game park/era-adjusted par can be stamped on `state.par_score` later (the hook
  exists) — not wired to any data source yet. "Projected Total" (CRR × 27) as a
  *display* metric is a separate, easy follow-up, not built here.
- **No per-league opt-out column.** Power Play / Cricket Order have dedicated
  team-row columns; RRR rides the global cfg flag (on) plus the per-game
  `state.rrr_manager_enabled` override (used by tests). A per-league checkbox
  would need a small schema add — deferred as unneeded for an on-by-default rule.
- **Bands + par not yet calibrated** against real distributions (see §3).
