# After-Action Report — DEF Subs / Pinch Runners / Joker-to-Field / GIDP Annotations

**Date completed:** 2026-05-06
**Branch:** `claude/decouple-hits-runs-rGteJ`
**Commit:** `ca29e4a`

---

## What was asked for

Direct list from the user, scoped from the "still rough" section of the
prior box-score AAR:

> "Split defensive subs, emit pinch runners, surface gidp and double plays if you havent and joker in the field"

Four asks, all in the same shape: events that the engine wasn't emitting
(or that the render layer was conflating with another event) need to
flow through to the box score so they show up correctly.

---

## What was built

### 1. Defensive sub split

Mostly a render-side fix. The engine *already* emitted a `defensive_sub`
event (`o27/engine/manager.py:777` — `manager.defensive_sub` writes
`{"type": "defensive_sub", "team_id", "out_id", "in_id"}` to
`state.events`). The renderer's `_build_disp` had no `defensive_sub`
arm, so by default the substitute landed in `BatterStats` with
`entry_type="starter"`, indistinguishable from anyone else.

New handler in `o27/render/render.py`:

```python
elif etype == "defensive_sub":
    in_id  = event.get("in_id")
    out_id = event.get("out_id")
    in_name = event.get("in_name", "")
    if in_id is not None:
        stats_obj = self._batter_stats.get(in_id) or BatterStats(
            player_id=str(in_id), name=in_name
        )
        stats_obj.entry_type = "DEF"
        if out_id is not None:
            stats_obj.replaced_player_id = str(out_id)
        self._batter_stats[in_id] = stats_obj
```

`_NON_PA_EVENTS` extended to include `defensive_sub`,
`tactical_def_swap`, `pinch_runner`, and `joker_to_field` so they don't
falsely trip the new-PA detector at the top of `render_event`.

Box-score side: `o27v2/web/box_score.py:_ordered_rows_with_indent`
extended its "indented under starter" classifier to `("PH", "PR", "DEF",
"sub", "joker_field")`. The position column for a `DEF` row reads the
fielding slot the substitute took (from `box_position`), not "ph" — a
defensive sub is taking the field, not pinch hitting.

### 2. Pinch runner — new event end-to-end

The engine had no concept of a pinch runner. Added the full chain:

**`manager.pinch_run(state, base_idx, runner_in)`** — replaces the
runner at `bases[base_idx]` with `runner_in`. The PR also takes the
outgoing runner's lineup slot (standard MLB rules: the original is out
of the game). Emits a `pinch_runner` event with `in_id`, `out_id`,
`base_idx`.

**`manager.should_pinch_run(state, rng)`** — conservative gate. Returns
`{base_idx, runner_in}` or `None`. Conditions:

- Not super-inning.
- ≥18 outs into the half (last third of the 27-out half).
- `|score_diff| ≤ 1` — game is close.
- Slowest runner on base has `speed < 0.40`.
- A non-pitcher bench bat with `speed > slowest_speed + 0.20` exists
  (worth burning a roster slot for).
- Probabilistic fire: `0.20 + (mgr_run_game − 0.5) × 0.30`. So an
  aggressive run-game manager fires more often, a passive one barely.

The `score_diff_for_batting()` / `_for_fielding()` helpers used by
existing manager code didn't actually exist on `GameState`, so the new
helpers compute the diff inline:

```python
bat_role = "visitors" if state.half in ("top", "super_top") else "home"
fld_role = "home" if bat_role == "visitors" else "visitors"
score_diff = state.score.get(bat_role, 0) - state.score.get(fld_role, 0)
```

**Wiring**: `prob.between_pitch_event` calls `should_pinch_run` after
`should_defensive_sub` and emits `{"type": "pinch_runner", "base_idx",
"runner_in"}`. `pa.py` dispatches to `mgr.pinch_run`.

**Renderer**: a new `pinch_runner` arm in `_build_disp` marks the
substitute `entry_type="PR"`. Box score uses position label `pr`.

### 3. Joker-to-field — new event end-to-end

By the user's rules (clarified earlier in the project), every team
carries exactly 3 jokers in the DH-pool. Jokers normally bat as
tactical pinch-hitters (one PA, then return to bench). The new event
covers the rare case where a joker is moved into a fielding slot to
*replace a fielder*, which:

- Reduces `team.jokers_available` by 1 (so 3 → 2 → 1 → 0).
- Pins the joker to that fielding slot in the box score.
- Is functionally never seen in real MLB (DH players don't usually
  swap into the field), so the trigger is deliberately tiny.

**`manager.joker_to_field(state, joker, player_out)`**:

```python
fielding.lineup[idx] = joker
fielding.jokers_available = [
    j for j in fielding.jokers_available if j.player_id != joker.player_id
]
out_pos = getattr(player_out, "game_position", "") or getattr(player_out, "position", "")
joker.game_position = f"J→{out_pos}" if out_pos else "J"
```

The `J→<pos>` format is the typographic signal the user specified — a
box-score reader sees `Joker     j→cf ....` and immediately knows what
happened.

**`manager.should_joker_to_field(state, rng)`** — gates:

- Not super-inning.
- ≥24 outs into the half (very late).
- Fielding team has at least one joker available.
- Fielding team is trailing by ≥3 (a defensive downgrade is justified
  only when the game is nearly lost — saves the offensive joker option
  if the game is still close).
- Random fire at 0.005 base rate.
- Joker must be ≥0.10 better than the current weakest-glove fielder
  at that fielder's position group (`defense_infield`,
  `defense_outfield`, or `defense_catcher`).

So in a typical game, this fires zero times. In an extended series of
blowouts, it might fire once. That's the intended cadence.

**Wiring** identical to pinch runner — between-pitch event, pa.py
dispatch, render handler.

### 4. GIDP / GITP annotations

`BatterStats` got two new counters:

```python
gidp: int = 0
gitp: int = 0
```

The earlier hits/runs decoupling work (commits `f1ac800` / `9145b77`)
already produced `hit_type="double_play"` and `hit_type="triple_play"`
outcomes from `prob._generate_pitch`. The render layer's run-path arm
in `_update_stats` now picks them up:

```python
elif hit_type == "double_play":
    s.gidp += 1
elif hit_type == "triple_play":
    s.gitp += 1
```

Persisted via two new `game_batter_stats` columns (`gidp INTEGER`,
`gitp INTEGER`) with `ALTER TABLE` migrations. The `_BAT_NUM` tuple in
`web/app.py` (used by `_dedup_by_player_phase` and
`_consolidate_per_player`) extended so dedup/consolidation include the
new fields.

**Box-score renderer** appends two annotation lines (right after `HBP`):

```python
pairs = _pick("gidp")
if pairs:
    lines.append(f"  GIDP: {_items(pairs)}.")
pairs = _pick("gitp")
if pairs:
    lines.append(f"  GITP: {_items(pairs)}.")
```

Producing newspaper-correct output:

```
  HR: Payne (8).
  GIDP: Lopez.
  GITP: Bates.
```

Multi-DP days collapse to `Smith 2.` (same convention as 2B/3B/HR
annotations). Real-MLB DP fielding chains (`6-4-3`) aren't surfaced
yet — we don't track per-fielder participation in DPs, only the batter
who hit into one.

---

## Sample box score (all four mechanisms in one team)

Smoke test with synthesized rows covering every entry type:

```
RED SOX
                        AB   R   H  2B  3B  HR RBI  BB   K  2C   H/AB
Biggio      ss ......    5   2   3   0   0   0   1   0   1   0  0.600
  Smith     ss ......    1   1   0   0   0   0   1   0   1   0  0.000
Lopez       2b ......    3   1   1   0   0   0   1   0   1   0  0.333
  Speed     pr ......    0   1   0   0   0   0   1   0   1   0   .000
Payne       1b ......    4   1   3   0   0   1   3   0   1   0  0.750
  Joker     j→cf ....    2   1   1   0   0   0   1   0   1   0  0.500
Bates       3b ......    4   1   0   0   0   0   1   0   1   0  0.000
McJoke      j  ......    1   0   0   0   0   0   1   0   1   0  0.000
Totals                  20   8   8   0   0   1  10   0   8   0  0.400
  HR: Payne (8).
  GIDP: Lopez.
  GITP: Bates.
```

Reading the rows:

- **Biggio** (SS) is the starter. **Smith** is a defensive sub (`ss` —
  not `ph`) who took Biggio's lineup slot in the field, indented.
- **Lopez** (2B) is the starter. **Speed** is a pinch runner (`pr`)
  who entered for Lopez on the basepaths, indented; he scored a run
  but never picked up a bat (AB=0, H=0).
- **Payne** (1B) is the starter. **Joker** is a joker pulled into the
  field (`j→cf`) who took Payne's lineup slot — got 2 ABs because the
  game ran long enough for the slot to come up to bat again.
- **Bates** (3B) is the starter; he hit into a triple play, picked up
  in the GITP annotation.
- **McJoke** is a bench joker (DH-pool) who took one tactical PH; he
  trails the lineup at the bottom with the un-translated `j` tag.

Every entry type renders with the right position label, indents
correctly under the player they replaced, and contributes to totals.

---

## What's still rough

- **DP fielding chains** (`DP: 6-4-3`) require tracking per-fielder
  participation in each double play. The existing engine knows the
  *primary* fielder credited with the play (`fielder_id` on the
  outcome dict, used for PO attribution) but not the chain. A first
  cut could just print the primary fielder's position; full
  chain-rendering would need a new event field listing all fielders
  who touched the ball.
- **Pinch-runner stat persistence**: a PR who never bats picks up R
  and the GIDP/GITP fields default to 0, but they don't currently
  show their PA/AB as "0". The `0.000` H/AB rendering reads as
  ".000" with a leading space-eating slash because the `_rate` helper
  formats it as `f"{'.000':>{STAT_W + 3}}"` rather than computing
  zero-over-zero. Cosmetic.
- **DEF sub of a DEF sub**: if a DEF sub is later replaced by another
  DEF sub, the second sub's `replaced_player_id` points at the first
  sub, not the original starter. The indent ordering walks one level
  deep — chains of subs get flattened to the starter. Realistic
  multi-level subbing is rare enough to defer.
- **Joker-to-field test coverage**: the trigger is so rare that
  `_generate_pitch` smoke runs don't produce one in 5k samples.
  Verified by direct call to `manager.joker_to_field`; end-to-end fire
  through `between_pitch_event` would need a constructed
  blowout-with-bench-glove-mismatch state.

---

## Identity / regression check

`tests/test_realism_identity.py` still passes 6/6. None of the new
manager functions or render handlers touch the realism axes that the
identity contract pins. The new `gidp` / `gitp` counters and the four
new event types are additive over the existing event stream.

---

## Files touched

- `o27/engine/manager.py` — `pinch_run` + `should_pinch_run`,
  `joker_to_field` + `should_joker_to_field`, plus an inline
  `_local_rng` fallback.
- `o27/engine/prob.py` — two new arms in `between_pitch_event` after
  `should_defensive_sub`.
- `o27/engine/pa.py` — dispatch arms for `pinch_runner` and
  `joker_to_field` events.
- `o27/render/render.py` — `_NON_PA_EVENTS` extended with the new
  event types; `_build_disp` arms for `defensive_sub`,
  `pinch_runner`, `joker_to_field`; `_update_stats` increments
  `gidp`/`gitp` on the run-path.
- `o27/stats/batter.py` — `gidp` and `gitp` fields; `entry_type`
  docstring updated to enumerate all six valid values
  (`starter` / `PH` / `PR` / `DEF` / `joker` / `joker_field`).
- `o27v2/db.py` — schema CREATE additions + `ALTER TABLE` migrations
  for `gidp` and `gitp`.
- `o27v2/sim.py` — `_extract_batter_stats` reads `gidp`/`gitp` off
  `BatterStats`; INSERT path updated.
- `o27v2/web/app.py` — `_BAT_NUM` extended to dedup/consolidate the
  new counters.
- `o27v2/web/box_score.py` — `_ordered_rows_with_indent` recognizes
  `PR` / `DEF` / `joker_field`; position-label switch handles `PH`
  ("ph") and `PR` ("pr") explicitly; `render_batting_annotations`
  appends `GIDP:` and `GITP:` lines.

Total diff: 339 insertions, 12 deletions across 9 files.
