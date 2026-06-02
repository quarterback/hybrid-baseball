# Design / Plan — Physics-first contact resolution (the "big-bang" inversion)

**Status:** PROPOSED — awaiting approval. No engine code written yet.
**Branch:** `claude/peaceful-thompson-ZB5UY`

---

## 1. Goal

Make the batted-ball physics `(exit_velocity, launch_angle, spray_angle)` the
**primary driver** of every ball-in-play outcome, replacing the categorical
`WEAK/MEDIUM/HARD_CONTACT` probability tables that `resolve_contact()` currently
draws `hit_type` from. After the switch the dependency runs **talent → physics →
outcome**, not (as today) outcome → physics.

Payoff: contact quality genuinely decides whether a ball falls, and the Savant
"expected vs actual" surfaces become real (physics is a measurement layer, not
an echo of a pre-decided result).

## 2. The key simplification (why this is contained)

Today's `resolve_contact()` has two separable halves:

1. **Table-draw core** (`prob.py` ~1876-1948): power/form/RISP redistributions →
   `_apply_park` → `_pick_from_table` → base `hit_type`.
2. **Defense layer** (~1950-2160): range shift, defensive gems, shifts, errors,
   GIDP, fielders-choice, ITP-HR. This runs **after** the draw and just mutates
   whatever `hit_type` exists.

**We replace only (1). The entire defense layer (2), the caller's talent flex,
the stat counting, baserunning, render, persistence, and the `outcome_dict`
shape stay byte-for-byte.** The new resolver hands the defense layer a
physics-derived `hit_type` instead of a table-derived one; nothing downstream
can tell the difference except that the outcome now tracks EV/LA.

## 3. New architecture (three pieces, all in `o27/engine/`)

**(A) Keep `contact_quality()` unchanged.** It already folds the full talent
matchup (skill/stuff/movement/power/eye/command/platoon/form/condition/joker/
RISP/catcher/pitch-type/weather) into `weak/medium/hard`. We keep it because
`quality` is still consumed downstream (stay-decision hard rule, GIDP factor,
gem multiplier, render) **and** it's the cleanest contact-strength signal to
drive EV. No change.

**(B) New `generate_batted_ball()`** — replaces the hit_type-keyed
`sample_batted_ball()`. Inputs become **talent**, not outcome:
- `quality` + `batter.power` → EV centre (reuse the existing `_EV_BY_QUALITY`
  means + the ±10 mph power shift; drop the `_EV_HIT_TYPE_SHIFT` term, which is
  the circular one).
- `BATTED_BALL_WEIGHTS[quality]` tilted by power → `{dribbler, grounder, liner,
  flyball}` texture → LA centre (reuse the existing texture map; this is already
  talent-derived and outcome-independent).
- pitch `launch_angle_bias` → LA shift (already a parameter).
- `batter.bats` pull skew → spray (unchanged).
- **Re-homed levers fold in here:** per-half form and RISP shift the EV/LA means
  (hot form → higher EV tail / HR carry; RISP → trimmed XBH-grade EV); joker
  decay → lower EV (already flows through `quality`).

**(C) New `resolve_batted_ball()`** — replaces `_apply_park` + `_pick_from_table`.
Given `(EV, LA, spray, park_dims, park_hr, park_hits, defense context)` it
returns the base `hit_type` + `batter_safe` + `caught_fly`, by LA band:
- **LA < 10 (grounder):** EV → ground_out / single / infield_single /
  fielders_choice. Hard grounders find holes; foot-speed/infield-arm flex (the
  Tier-3 work) lives here.
- **10 ≤ LA < 26 (liner):** highest BABIP band → single / double / line_out
  (caught). EV scales single↔double↔triple.
- **26 ≤ LA ≤ 50 (fly):** `_proxy_distance(EV,LA)` vs the fence at that spray
  (the existing `park_effects` geometry) → hr / double (off wall) / fly_out.
  `park_hr` and weather scale the carry threshold.
- **LA > 50 (popup):** ~automatic fly_out.
- `park_hits` multiplies the in-band hit probability (re-homes the park-hits
  lever). This component **absorbs `o27/engine/park_effects.py`** — the geometry
  HR up/downgrade and the rules 6-14 we built become the resolver's core logic.

`resolve_contact()` is rewired to call (B)+(C) where the table draw was, then
fall straight into the unchanged defense layer.

## 4. Lever re-homing (nothing lost)

| Current lever (table edge) | New home |
| --- | --- |
| Power redist (S→2B, LO→HR, GO→FO) | EV/LA means in (B) |
| Per-half form (S→HR decoupler) | EV tail + resolver XBH/HR threshold |
| RISP XBH suppression | EV trim for XBH-grade contact in (B) |
| Pitch `launch_angle_bias` | LA mean in (B) — already wired |
| Park HR / park hits (multiplicative) | carry threshold + in-band hit mult in (C) |
| Joker decay, platoon, catcher, RISP penalty | unchanged — already upstream in `contact_quality` |
| Range shift, gems, shifts, errors, GIDP, fielding fatigue | unchanged — defense layer runs after, on the physics hit_type |
| Foot-speed / infield-arm (Tier 3) | folds into the grounder band of (C) |

## 5. Invariants & shape that MUST be preserved

- `outcome_dict` keys: `hit_type, batted_ball, batter_safe, caught_fly,
  runner_advances[3], runner_out_idx, extra_runner_outs, toa_runner_idxs,
  is_error, fielder_id, quality, shift_effect, gem_effect, nickel_play,
  fielder_pos`.
- `hit_type` string set unchanged (14 values).
- Every BIP categorized as exactly one `hit_type` (PA identity).
- Out reconciliation (27 outs/half), H reconciliation, OR reconciliation, TTO
  buckets — `tests/test_stat_invariants.py`.
- Same `(EV, LA, spray)` that drove the outcome are the ones persisted to
  `game_pa_log` (today they're sampled separately at the caller; we thread the
  resolver's values through so physics and result never disagree).

## 6. File-by-file changes

- `o27/engine/batted_ball.py` — add `generate_batted_ball()` (talent-keyed); keep
  the taxonomy/`classify_batted_ball`. Old `sample_batted_ball` retained behind
  the flag (§8), deleted after cutover.
- `o27/engine/park_effects.py` — its geometry + EV rules become
  `resolve_batted_ball()`'s fly/grounder logic (moved, not deleted).
- `o27/engine/prob.py` — `resolve_contact()`: replace ~1876-1948 with calls to
  (B)+(C); thread EV/LA/spray out so the caller persists the same values; defense
  layer untouched. Generate physics in the caller before `resolve_contact`.
- `o27/config.py` — new `PHYSICS_FIRST_RESOLUTION` flag + EV/LA band cut points
  and the re-homed-lever scales (form-EV, RISP-EV, park-hits-mult).
- Tests: new `o27/tests/test_physics_resolver.py` (band → outcome, monotonicity,
  identity at neutral park), keep all existing.
- `docs/aar-physics-first-inversion.md` on completion.

## 7. Calibration & validation

**Targets (current committed engine, 400-game seed):** R/G **24.23**; per-BIP
hit-type mix — single 29.4%, double 19.4%, ground_out 14.0%, fly_out 12.6%,
line_out 8.5%, hr 6.1%, fielders_choice 3.9%, error 2.2%, triple 1.8%,
double_play 1.6%, infield_single 0.8%.

**Method:** a calibration harness sims N games and reports R/G + the full mix +
2B/3B and per-park splits. We tune the band cut points and re-homed scales until
the mix lands within tolerance (target: each major type within ~1.0 pt, R/G
within ~0.5). Then verify:
- `test_stat_invariants.py` — same 5-fail/6-pass (env only), zero new.
- New monotonicity checks: corr(EV, hit) and corr(EV, xBA) now strongly positive
  at the *event* level (the whole point — physics drives result).
- Savant: xwOBA−wOBA now has real spread.

## 8. Migration & rollback

A `PHYSICS_FIRST_RESOLUTION` config flag selects new resolver vs the old table
draw. Both paths live during calibration so we can A/B on identical seeds and
roll back instantly if the run environment won't calibrate. **Once green, we
delete the old table-draw path and the flag** (the tables `WEAK/MEDIUM/HARD_
CONTACT` and `_pick_from_table` retire) so there's no dead code — that's the
"big-bang" completion. Each step is a green commit; no long-lived broken branch.

## 9. Risks

- **Calibration drift** — the tables are the current calibration; the resolver
  must re-hit 24.23 R/G. Mitigated by the harness + the flag A/B. Biggest time
  sink, not a correctness risk.
- **RISP / form re-homing** — these are O27's H~R decouplers; moving them from
  categorical edges to EV shaping must preserve the decoupling. Validated by the
  existing run-distribution / cluster-luck checks.
- **Stay-decision ordering** — stay reads final `hit_type` + `quality`; we keep
  quality from `contact_quality` and resolve hit_type before the stay call, as
  today.

## 10. Sequencing (each a green, committed step)

1. `generate_batted_ball()` + tests (no wiring yet).
2. `resolve_batted_ball()` (absorb park_effects geometry + EV rules) + tests.
3. Wire both behind `PHYSICS_FIRST_RESOLUTION=True`; thread EV/LA to persistence.
4. Calibrate to §7 targets via the harness; iterate.
5. Invariant + monotonicity + Savant validation.
6. Delete the old table path + flag; final AAR.
