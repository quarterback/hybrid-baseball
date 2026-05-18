# After-Action Report — Engine Fairness Audit, 2C Calibration, FIP Rebuild

**Branch:** `claude/add-situational-stats-mCE0A`
**Commits:** `41f2734` → `54436db` → `8d902e8` → `d61f93a` → `b9b14ea`

---

## Setting

This segment ran on top of the baserunning-friction pass (covered in
`aar-baserunning-friction-and-game-ending-fixes.md`). At session start
the open thread was a stubborn 4 R/g home-team advantage in 500-game
smokes — items 1/2/3 (target pressure, fielding fatigue, rebuttal
offense) had been implemented and were firing, the bat-first edge cap
was at ±1%, the `should_swap_offensive_for_defense` asymmetry had been
fixed, and *still* home was winning ~73%.

User direction: trace it.

---

## What the user asked for

1. **Trace the 4 R/g residual home advantage** with per-PA instrumentation split by batting team.
2. **Production league balance audit** — once the engine was diagnosed, check whether the deployed app's home-team-wins-more pattern was a rotation-deployment artifact, an engine bias, or something else.
3. **Apply the advancement dial drop** the user had proposed earlier (with current values as the baseline, not the brief's original).
4. **The 2C / stay mechanic** — after the 8-team analytics suite surfaced that STAY RV was −0.308 (~as bad as an out), the user invited a touch on the mechanic. Asked for a proposal first, then approved.
5. **FIP is "broken"** per another chat reviewing the user's stats output. Investigate and fix.

---

## What landed

### 1. The home-advantage trace and the fixture-artifact reveal

Built a per-PA instrumentation harness via monkey-patches around `contact_quality`, `resolve_contact`, and `runner_advances_for_hit`, grouping every call's inputs and outputs by `state.batting_team.team_id`. First pass over 200 games surfaced this:

```
PITCHER STUFF facing each batting team:
  home      avg pitcher_stuff faced: 0.5268   (= make_foxes' pitcher)
  visitors  avg pitcher_stuff faced: 0.6264   (= make_bears' pitcher)
```

Bears had +0.10 `pitcher_skill` over foxes. That gap alone — visitor batters facing the harder pitcher every PA — explained the contact-quality split (home 36% hard / visitor 27%), the H/PA split (home 0.49 / visitor 0.44), and the R/PA split (home 0.49 / visitor 0.37) in lockstep.

The "swap test" I'd been running for hours to verify whether the bias was team-strength or home-seat was silently a no-op. `make_foxes()` hardcodes `team_id="visitors"`, `make_bears()` hardcodes `team_id="home"`, and the engine routes batting/fielding by the `team_id` *string*, not by which slot the team object occupies on `GameState`. So `state = GameState(visitors=make_bears(), home=make_foxes())` left bears mechanically-home (its `team_id` string was still `"home"`) and foxes mechanically-visitor (its `team_id` was `"visitors"`). The swap was nominal only.

Confirming with two genuinely identical inline teams (same pitcher_skill, same speed, same everything, `team_id` matched to the slot):

```
=== 300 BALANCED games ===
  W-L:  home 152 (50.7%) / visitor 148 (49.3%)
  R/g:  home 14.94  visitor 14.79  gap +0.16
  HBF:  51.7%
```

Engine is fair. The 4 R/g "home advantage" I'd been hunting for hours was just the bears-vs-foxes team-strength gap, masked as a structural bias by the team_id-routing quirk.

### 2. Production league balance audit

User asked for a real audit on the deployed app's database state (`o27v2/o27v2.db`, 30 teams, 1974 players, 2430-game schedule, fresh). Two scans:

**Schedule balance:**
```
home games:  mean=81.0  stdev=5.20  range=73-91
away games:  mean=81.0  stdev=5.20  range=71-89
```
Each team plays ~81 home / 81 away. Schedule is symmetric to within sampling noise. Not a scheduling artifact.

**Pitching strength scan** turned up the actual production home advantage in `o27v2/sim.py:_db_team_to_engine`:

```python
home_bonus = (
    v2cfg.HOME_ADVANTAGE_SKILL
    if team_role == "home"
    else 0.0
)
...
skill = _scout.to_unit(p["skill"]) + home_bonus
```

`HOME_ADVANTAGE_SKILL = 0.08` in `o27v2/config.py`. Every non-joker home batter's `skill` rating got bumped by an absolute +0.08 on the 0-1 normalized scale, every game, in production. That single constant — not engine logic, not the bat-order decision, not any of the items 1/2/3 I had added — was the actual source of the deployed-app home-team-wins-more pattern. Same +0.08 magnitude as the foxes/bears fixture gap that masqueraded as engine bias in the earlier diagnostic.

Per user direction, set to 0.0. The o27 engine itself was fair throughout; the o27v2 production sim layer was injecting the asymmetry.

### 3. Advancement dial drop

`ADVANCE_2B_ON_1B_SCORE` 0.54 → 0.49 (hold_3B 0.32 → 0.37; out stays 0.14).
`ADVANCE_1B_ON_2B_SCORE` 0.43 → 0.40 (to_3B 0.34 → 0.37; hold_2B 0.16, out 0.07 unchanged).

User's earlier suggestion applied with the now-current baseline. Effect was visible in the H/R ratio — combined with `HOME_ADVANTAGE_SKILL=0.0`, the balanced 300-game smoke produced H/R = 1.30, meaning hits exceed runs by ~30% (real LOB texture, not target-chasing).

### 4. 2C mechanic tweaks toward break-even RV

User ran a complete 8-team season and shared the analytics-suite output. The standout finding: STAY RV = −0.308, just barely better than the −0.380 RV of taking an out. A credited 2C stay was empirically nearly as bad as just being retired. Optimal play under that economics is to never stay, which kills the cricket-style 2C as the sport's distinguishing mechanic.

Proposed two nudges toward neutral EV without flipping the mechanic positive:

- `STAY_DEFENSE_READ_BASE` 0.10 → 0.07 (fewer defense-read break-ups; each prevented one worth ~+0.5 RV).
- Talent-weighted expected-advance floors: weak 0.55 → 0.70, medium 1.05 → 1.20. Successful stays now move runners further on average, recovering some of the lost RV.

User approved. Applied. Balanced 300-game smoke: stay-credit rate moved from ~70% to ~80%, engine fairness preserved (W-L 51.3% / 48.7%, R/g gap +0.25). The mechanic stays high-variance and still costs you sometimes; the average is meaningfully less bad.

### 5. FIP rebuild

User flagged that another chat called FIP "broken" while reviewing the stats output. Investigation found three separate implementations across the codebase, each broken differently:

| File | Constant | Formula bug |
| --- | --- | --- |
| `o27/almanac/compute.py` | 3.10 (MLB) | Constant wrong for O27's ~11 R/G environment |
| `o27/v2_bridge.py` | 11.50 (O27) | **HR term missing entirely** — FIP = `(3*BB − 2*K)/IP + 11.50` |
| `o27/data.py` | 11.50 (O27) | HBP missing from `BB + HBP` term |

For the almanac (the calibrated, season-aware path), replaced the hardcoded 3.10 with a constant computed dynamically from this season's league totals:

```python
fip_kernel_league = (13 * p_hr + 3 * (p_bb + p_hbp) - 2 * p_k) / p_ip
out["fip_const"]  = out["era"] - fip_kernel_league
out["fip"]        = out["era"]    # by construction
```

This makes `league_FIP == league_ERA` per the canonical FIP definition, then carries `fip_const` through the league dict so per-pitcher FIPs in `_augment_pitchers` are calibrated to the live environment.

For the two simpler paths (`v2_bridge.py`, `data.py`) that don't have league dict access, kept the 11.50 baseline but fixed both formula bugs (added missing HR term in v2_bridge, added missing HBP in data.py).

All three paths now share the canonical `(13*HR + 3*(BB+HBP) − 2*K)/IP + C` shape.

---

## What was decided NOT to do

- **Reverse any of the symmetric-by-role mechanics** added earlier (target pressure, fielding fatigue, rebuttal offense, edge cap, swap-fn symmetry). They're good engineering on their own terms and weren't fixing a fake bug — they were tightening real strategic asymmetries.
- **Push the 2C tweaks to positive EV.** The mechanic should remain a tactical trade with real cost. Neutral-to-slightly-negative average with positive variance is the intent.
- **Centralize the FIP computation into one helper.** Tempting refactor but the three call sites have different data shapes (almanac has the league dict; v2_bridge has DB rows; data.py has computed dicts). Standardizing the formula across all three was the immediate fix; a shared helper is worth doing later but would have been scope creep here.

---

## Files changed

- `o27v2/config.py` — `HOME_ADVANTAGE_SKILL: 0.08 → 0.0` with rationale comment
- `o27/config.py` — `ADVANCE_2B_ON_1B_SCORE`, `ADVANCE_1B_ON_2B_SCORE` dial drops + redistribution; `STAY_DEFENSE_READ_BASE 0.10 → 0.07`
- `o27/engine/prob.py` — talent-weighted 2C expected-advance floors lifted (weak 0.55→0.70, medium 1.05→1.20); `should_swap_offensive_for_defense` gate rewired to fire for `first_batting_team` rather than `state.half == "top"`
- `o27/engine/manager.py` — `BAT_FIRST_HOME_EDGE_CAP` applied to bat-first probability; `should_swap_offensive_for_defense` rewired to the first-batting-team test
- `o27/almanac/compute.py` — dynamic FIP constant; xFIP uses same constant
- `o27/v2_bridge.py` — FIP gained missing HR + HBP terms; SELECT extended to pull the columns
- `o27/data.py` — FIP gained missing HBP term in both occurrences
- `tests/test_declared_seconds.py` — `test_should_bat_first_biased_above_50pct` replaced with `test_should_bat_first_near_coin_flip` reflecting the new design intent

---

## Lessons that carry forward

- **`state.batting_team` and `state.fielding_team` look up by GameState *slot* (`top`/`bottom`/`super_top`/etc), but `state.score` is keyed by the intrinsic `team_id` *string*.** Test fixtures that hardcode `team_id="home"` or `team_id="visitors"` can't be swapped at the GameState level — they have to be rebuilt with the slot-matching `team_id`. Burning hours on a fake "structural home advantage" that was actually a fixture-strength gap is the cautionary tale.
- **When a piece of game logic feels asymmetric and you've ruled out the engine, look one layer up.** The o27 engine was fair. The +0.08 home-batter bump was in the o27v2/ production sim layer, not the engine. Always check the DB-to-engine conversion path for hidden constants.
- **Empirical run-value analysis surfaces design questions the engine alone can't.** The STAY RV of −0.308 wasn't a bug — it was the mechanic doing what it's defined to do, with the cost now finally measurable. The fix (modest nudges) was informed by the empirical decomposition; without the analytics suite producing that number, the right tuning direction wouldn't have been obvious.
- **FIP's constant is *defined* such that league_FIP == league_ERA.** Hardcoding the MLB constant in a non-MLB environment doesn't just produce noisy values — it produces values that are meaningless relative to ERA, which is the entire comparison point of FIP. Always derive the constant from the league actually being measured.

---

## Open items

1. **Almanac leaderboard headline refactor.** The almanac currently leads pitching leaderboards with ERA / FIP. The 8-team analytics suite already uses the empirically-refit wERA / xRA / Game Score weights and produces meaningful values; the almanac just hasn't been updated to surface those as the primary stats. Next on the user's list.
2. **Production data backfill.** With `HOME_ADVANTAGE_SKILL=0.0`, historical games played at the old value carry the ~+0.08 home-batter bump in their stats. Re-sim or treat the prior data as a different rule set.
3. **Centralize the FIP helper.** Three call sites currently share a hand-copied formula. Worth a small helper module that takes raw counters + an optional league dict and returns FIP with the right constant for the context.
4. **`make_foxes()` / `make_bears()` hardcode `team_id`.** Test-only ergonomics issue. Should be parameterized so the `team_id` follows the slot the fixture is placed in.

---

## Things to remember (carried forward)

- The user does not want MLB-target chasing. Knobs should be tuned by what plays right in the engine on its own terms.
- The user can't watch games on the deployed app directly — diagnostic numbers need to be surfaced via smoke tests or commit messages.
- The user is willing to commit fuzzy off-round numbers in config when the alternative reads "designed" too obviously.
- The user wants the brief / spec to be authoritative *only* if they wrote it. AI-suggested specs are treated as suggestions; the user's instinct beats the spec.
- **Always verify test fixtures before running a long diagnostic on them** (`team_id` lesson above).
- **When investigating asymmetric behavior, look at the conversion layer, not just the engine** (HOME_ADVANTAGE_SKILL lesson above).
