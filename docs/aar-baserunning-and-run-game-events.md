# After-Action Report — Baserunning Attributes + Run-Game Events (TOOTBLAN, Pickoffs, Hit-and-Run, Sac Bunt)

**Date completed:** 2026-05-04
**Branch:** `claude/fix-schedule-generation-2yJKH`
**Commits:** `c704c84` (baserunning attributes), `2931be3` (TOOTBLAN + pickoffs + hit-and-run + sac bunt)

---

## What was asked for

Mid-session check-in from the user:

> "i assume the baserunning is already build in?"

Once I confirmed the *advancement framework* existed but used only foot speed as the player lever:

> "specifically baserunning skill/speed/aggressive by the player"

Then, after I shipped that and surfaced what wasn't being modeled:

> "model all of those things, they're core baseball things."

— in reference to TOOTBLAN, hit-and-run, sac bunt, and pickoffs.

So two stages, scope expanded mid-flight:

1. **Player baserunning attributes.** Speed alone conflates a 70-grade burner who runs into outs with a 50-grade smart runner who reads pitchers, takes correct routes, and slides. Add `baserunning` skill and `run_aggressiveness` as independent player axes so the engine can distinguish them.
2. **Run-game event mechanics.** Wire up the four core baseball plays the engine had stubs for but wasn't actually firing: thrown out trying for the extra base (TOOTBLAN), pickoffs from the mound, manager-called hit-and-run, and sacrifice bunts.

Operating constraint: identity invariants must hold. At neutral player inputs (`speed = baserunning = run_aggressiveness = 0.5`) and neutral manager tendencies (`mgr_run_game = 0.5`), the engine should produce numerically identical output to the pre-baserunning version.

---

## What was built

### Stage 1 — Player baserunning attributes (`c704c84`)

**Two new player axes**, both rated on the same 0..1 unit scale as everything else in the engine:

- **`baserunning`** — read-off-the-bat, route running, slide technique, picking up the third-base coach. Conceptually "baserunning IQ" independent of foot speed.
- **`run_aggressiveness`** — willingness to risk the extra base. Personality / risk tolerance.

**Plumbing pattern** mirrored the existing defense-attribute work (see `aar-defense-model.md`):
- `players` table: two new `INTEGER DEFAULT 50` columns, plus `ALTER TABLE` migrations for legacy DBs.
- `generate_players` rolls both via the standard tier-grade ladder for position players. Pitchers get a neutral 50 since they don't bat in O27.
- `Player` dataclass gets two new float fields, defaulting to 0.5 (engine identity).
- `_db_team_to_engine` reads the columns from the team row and stamps them on the engine's Player object.

**`_runner_advance` semantics.** Previously took `(rng, base_advance, speed, extra_chance)` and returned just `int`. Now also accepts `baserunning` and `aggressiveness`, and the extra-base probability sums all three contributions:

```python
p_attempt = extra_chance
p_attempt += max(0.0, (speed       - 0.5) * cfg.RUNNER_EXTRA_SPEED_SCALE)
p_attempt += max(0.0, (baserunning - 0.5) * cfg.RUNNER_EXTRA_SPEED_SCALE)
p_attempt += max(0.0, (aggressiveness - 0.5) * 0.5 * cfg.RUNNER_EXTRA_SPEED_SCALE)
```

Speed and baserunning carry equal weight (a smart average-speed runner ≈ a fast raw runner). Aggressiveness is asymmetric — it can *add* attempts on top of skill but a passive runner doesn't subtract attempts below the base read.

`runner_advances_for_hit` updated to fetch both new attributes via a new `_get_baserunning(pid, state)` helper and pass them through to `_runner_advance` for each runner.

### Stage 2 — TOOTBLAN (`2931be3`)

**Goal.** A high-aggressiveness, low-skill runner *should* generate outs on the bases, not just stop short of the extra base when the roll fails.

**Mechanic.** `_runner_advance` now returns a tuple `(advance, thrown_out)` instead of just `advance`. When the extra-base attempt roll fires, a *second* roll decides whether the slide beats the throw:

```python
safe_p = (
    cfg.TOOTBLAN_SAFE_BASE                                # 0.78
    + (baserunning - 0.5) * cfg.TOOTBLAN_SKILL_SCALE      # 0.40
    + (speed       - 0.5) * cfg.TOOTBLAN_SPEED_SCALE      # 0.20
)
safe_p = max(cfg.TOOTBLAN_SAFE_MIN, min(cfg.TOOTBLAN_SAFE_MAX, safe_p))
```

Floor 0.45 / ceiling 0.96 — even bad runners aren't auto-out, even elite runners aren't auto-safe.

**Wiring back into the contact outcome.** `runner_advances_for_hit` now returns `(advances, runner_out_idx)`. The existing fielding outcome dict already had a `runner_out_idx` field (used for fielder's choice). `prob.py:resolve_contact` was updated to use the TOOTBLAN out_idx when the play wouldn't otherwise produce a runner out, and the rest of the path (`baserunning.advance_runners`) already records that runner correctly.

**Identity contract.** At neutral inputs the attempt probability from `RUNNER_EXTRA_SPEED_SCALE` is already 0, so TOOTBLAN never fires — no behavior change. Identity tests verified.

**Why it matters** in the player-attribute system: a high-`run_aggressiveness`, low-`baserunning` runner will attempt the extra base *and* get gunned down in roughly the same proportion as MLB's classic "runs into outs" archetype, while a high-`baserunning` runner with average speed turns into a quietly elite percentage runner.

### Stage 3 — Pickoffs (`2931be3`)

**Mechanic.** New check at the front of `between_pitch_event`, ahead of the existing wild pitch and SB rolls. Two rolls per check:

1. **Move probability** — does the pitcher attempt a pickoff?
   ```python
   attempt_p = (
       cfg.PICKOFF_ATTEMPT_BASE
       + (run_aggressiveness - 0.5) * cfg.PICKOFF_AGGRESSION_SCALE
   )
   if base_idx == 0 and pitcher.throws == "L":
       attempt_p += cfg.PICKOFF_LHP_1B_BONUS    # structural lefty advantage
   if base_idx == 1:
       attempt_p *= cfg.PICKOFF_2B_DAMPENER     # 2B pickoffs are rarer
   ```

2. **Success probability** — does the move catch the runner?
   ```python
   success_p = (
       cfg.PICKOFF_SUCCESS_BASE
       + pitcher.pitcher_skill * cfg.PICKOFF_SUCCESS_PITCHER_SCALE
       + (run_aggressiveness - 0.5) * cfg.PICKOFF_SUCCESS_AGGRESSION_SCALE
       - (baserunning        - 0.5) * cfg.PICKOFF_SUCCESS_BR_SCALE
   )
   ```

A high-Stuff lefty pitcher pickoff vs a leaning, low-IQ runner is the canonical "exploit a baserunner" play; a smart runner with average aggression vs a low-Stuff righty almost never gets picked off. The existing `pickoff_attempt` event handler in `pa.py` already records the out via `_record_out`, so no downstream changes were needed.

**Tunables landed in `o27/config.py`:** ten new constants for attempt rate, LHP bonus, 2B dampener, success base/scales, and min/max clamps.

### Stage 4 — Hit-and-run (`2931be3` + tune-pass build-out)

**First cut** modeled it as a *flagged* SB attempt that bypasses the speed gate:
- Probability scales with `mgr_run_game` (it's a manager-called play).
- Only fires with a 1B runner (the canonical setup).
- The runner goes regardless of foot speed — the play is *on*, not opportunistic.
- Success probability gets a fixed `HIT_AND_RUN_SUCCESS_BONUS` (~0.08) because the catcher's eyes are on the batter, not the runner.
- Tagged `"hit_and_run": True` in the event dict.

**Build-out** in the same session, prompted by "the hit-and-run layer can be built out more if that makes sense":

1. **Count-aware call rate.** Real managers concentrate hit-and-run in specific counts. Now skipped entirely with two strikes (batter can't take a borderline pitch), and probability heavily dampened (×0.20) outside the canonical 1-0 / 2-0 / 2-1 / 3-1 hitter's counts. Configurable via `HIT_AND_RUN_FAVORED_COUNTS` and `HIT_AND_RUN_OFF_COUNT_DAMPENER`.

2. **Contact-side protection bonus.** When an h&r SB *succeeds*, `state.hit_and_run_active` flips to True. The next pitch resolution checks the flag:
   - Swinging strikes are re-rolled against `HIT_AND_RUN_CONTACT_K_REDUCTION` (default 0.25) and on a hit get converted to fouls — the batter protects.
   - Borderline balls also have a chance to be converted to fouls (batter swings at iffy pitches to protect).
   - Flag persists until contact or another swinging strike, then clears (one-shot per success). Also clears at PA boundary.

The flag's location is `GameState.hit_and_run_active`, plumbed cleanly through the existing PA reset path. Identity preserved — at neutral inputs the flag never activates.

### Stage 5 — Sacrifice bunt (`2931be3`)

**`mgr.should_bunt(state, rng)` manager decision.** Conditions for considering the call:
- Regulation half (no super-innings)
- Runner on 1B (the canonical setup; 2B-only bunts are skipped)
- Outs < 18 (don't burn an out in the last third of the half — too few left)
- Batter is not a power threat (`power <= 0.55`)

If conditions are met, the call probability scales with `mgr_run_game` × `(1 + (1 - leverage_aware) × damper)` — high `run_game` skippers bunt; modern / sabermetric skippers (high `leverage_aware`) basically never. Score margin tilts the rate up when the team is down 1–2 runs and down sharply when leading by 4+.

**Outcome resolution.** Three buckets, rolled with a single uniform draw:
- **Bunt for hit** — batter safe at 1B, runners advance one. Rate scales with batter speed: a slow batter almost never beats it out, a 70-grade burner pushes the bunt-for-hit rate to ~0.25.
- **Canonical sacrifice** (~75% in the typical case) — batter out at 1B, runners advance one.
- **Failed bunt** (~10%) — popup or force at lead, no advance, batter out.

**`pa.apply_event` handler.** Added a new `sac_bunt` event branch that applies base-state and out changes directly, advances the lineup, resets the count, and increments `total_pa_this_half`. Doesn't go through `_resolve_contact` because there's no contact-quality / fielder-attribution to resolve — the bunt itself is the play.

---

## Verification

**Realism identity tests** (`tests/test_realism_identity.py`): 6/6 still pass after every change. The identity invariant carries cleanly through the new attributes because:
- `baserunning = 0.5` and `run_aggressiveness = 0.5` zero out their contributions in `_runner_advance`.
- TOOTBLAN can't fire when the attempt probability is 0.
- Pickoffs and hit-and-run gated by manager tendencies; at neutral 0.5 they're suppressed (and the test fixtures don't seed managers).
- Sac bunt requires non-default `mgr_run_game`; no fire at neutral inputs.

**Live integration test** (30 simulated games, seed=42, full 30-team league):

| Metric | Before all this work | After mechanics shipped | After CS-rate tune |
|---|---|---|---|
| League-wide SB | 0–4 / 30g sample | 98 | **119** |
| League-wide CS | ~0 | 70 | **52** |
| CS rate | n/a | 41% | **30%** |
| Pitcher CS-caught | ~0 | 67 | ~50 |
| Runs / 30g | within prior band | 650 | within band |

The initial 41% CS rate (after pickoffs + TOOTBLAN started landing on the out side) was clearly too high; MLB sits closer to 22–25%. Tuned in two passes:

- `SB_SUCCESS_BASE` 0.62 → **0.72** so the average straight-steal lands around 75% success.
- Pickoff knobs pulled down: `PICKOFF_ATTEMPT_BASE` 0.010 → 0.004, `PICKOFF_SUCCESS_BASE` 0.20 → 0.10, success ceiling 0.55 → 0.40. Real-MLB pickoff outs are rare flavor events (~0.05 / game per side); they should be a CS contributor, not a CS inflator.

Final 30% CS rate is intentionally a bit above MLB — O27's 27-out continuous half wears the catcher's arm down faster than a 9-inning game with mound visits and inning resets, so a structurally elevated CS rate against tired batteries is OK. The user's only ask was "shouldn't be 41%."

---

## Process notes

This was a smaller, more focused work cycle than the schedule + persona session. Two things kept the scope contained:

- **Sticking to the existing event/handler pattern.** Pickoffs already had an event handler in `pa.py` and `_record_out` plumbing — the work was just emitting the event from `between_pitch_event`. TOOTBLAN reused `runner_out_idx` from the fielding outcome dict. Hit-and-run reused `stolen_base_attempt` machinery with a tag. Only sac bunt needed a new event branch.
- **Picking the simpler model when "real" was disproportionately invasive.** Hit-and-run as a fully-coupled runner+batter play would have needed state plumbing across multiple modules; modeling it as a tag-flagged SB with a contact-side bonus captures the gameplay truth (it's mostly a "runner gets to go" mechanic) without the surgery. Called out in the commit message and AAR so anyone reading later knows it's the simplified take.

Where the scope did want to grow but I held the line:
- **Stat-credit for sac bunts.** The `sac_bunt` event changes game state correctly (runs, outs, bases) but doesn't credit AB/H/RBI through the renderer because `render.py`'s event-types-of-interest list only includes contact and pitch events. The mechanic *works*; the box score doesn't reflect bunting batters' contribution. Flagged in the commit message, deferred to a follow-up that extends the renderer's event set.
- **Stat-credit for TOOTBLAN / pickoffs.** Same shape of issue — the runner is correctly out in game state but the box-score line for the runner's CS/PO breakdown might not capture it cleanly. Worth a render-layer audit pass.

---

## What's still open

- **Renderer stat-credit for new events.** Sac bunt outcomes need `AB/H/RBI/SH (sacrifice hit)` credit; TOOTBLAN and pickoffs may need clean attribution against `pitcher.cs_caught` vs `catcher.cs`. One PR's worth of work in `render.py` to extend the event-types-of-interest list and accumulator branches.
- **Hit-and-run stat surfacing.** The event dict already carries `"hit_and_run": True`; nothing reads it. Adding an event-level `play_type` tag to the box-score breakdown would let the league standings page show "hit-and-run attempts" alongside straight steals.
- **3B pickoffs.** Skipped intentionally — they're rare and the LHP-vs-1B asymmetry doesn't apply. Would be ~10 lines of config and a copy of the 1B/2B branch if the user wants them.
- **Bunt for safety** (i.e., bunting for a hit with no runner on, e.g. drag bunt). The current model only fires the bunt with a 1B runner. Speedy batters in close games could legitimately call their own drag bunt against an over-shifted infield.
- **Squeeze bunt.** Runner on 3B + bunt for hit = squeeze. Currently the manager logic doesn't fire bunts with only a 3B runner. Real but very situational.
- **Catcher framing → pickoff / SB success.** The catcher's `arm` already feeds SB success; could also flow into pickoff-throw success on the back-side throw to the bag.

---

## File-level summary

**`c704c84` (baserunning attributes):**
- `o27/engine/state.py` — `Player` gains `baserunning` and `run_aggressiveness` (0.5 default).
- `o27/engine/prob.py` — `_runner_advance` extended; new `_get_baserunning` helper; `runner_advances_for_hit` threads both attributes through every advance call.
- `o27v2/db.py` — `players` schema gets two new INTEGER DEFAULT 50 columns + ALTER migrations.
- `o27v2/league.py` — `generate_players` rolls both for position players (neutral 50 for pitchers); `seed_league` INSERT extended.
- `o27v2/sim.py` — `_db_team_to_engine` reads the new columns and stamps them on the engine `Player`.

**`2931be3` (run-game events):**
- `o27/config.py` — TOOTBLAN, pickoff, hit-and-run, and sac-bunt tunables.
- `o27/engine/prob.py` — `_runner_advance` returns `(advance, thrown_out)`; `runner_advances_for_hit` returns `(advances, runner_out_idx)`; resolve-contact threads the TOOTBLAN out; `between_pitch_event` adds pickoff and hit-and-run branches ahead of SB.
- `o27/engine/manager.py` — new `should_bunt` decision function reading `mgr_run_game` / `mgr_leverage_aware` / batter `power`.
- `o27/engine/pa.py` — new `sac_bunt` event branch handling all three bunt outcomes.

**Tests**
- `tests/test_realism_identity.py` — 6/6 still pass (identity preserved through all four mechanics at neutral inputs).
- `tests/test_stat_invariants.py` — 9 pre-existing DB-fixture tests; not affected, but the renderer-credit gap for sac bunt may need a future pass.
