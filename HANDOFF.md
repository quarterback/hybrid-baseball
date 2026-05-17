# O27v2 Handoff Document

**Prepared:** 2026-05-17
**For:** Next agent picking up the simulator
**Supersedes:** `docs/HANDOFF-archive-2026-05-03.md` (Task #65 era; preserved for context)
**DB path (default):** `o27v2/o27v2.db`
**Active branch:** `claude/tune-scoring-simulator-fZr2I`

---

## 0. READ THIS FIRST

**O27 is variance-first. There is no R/G calibration target.**

The previous handoff treated 22-26 R/G as a target band. That band came from an earlier tuning pass and was never the designer's intent. As of the May 17 tuning pass:

- Run environment is whatever the mechanics produce. Currently ~33 R/G (200-game sample), min 15 / max 66, std 9.2. Wild variance is the design.
- Rate stats (K%, BB%, BA, SLG, HR/PA) are observed outputs of the mechanics, not knobs to tune toward. Do not "fix" them to land in an MLB-shaped band.
- The `TARGET_*` constants in `o27v2/config.py` are permissive sanity bounds for catching catastrophic regressions, not calibration targets. They are deliberately wide. Do not narrow them.
- Tooling reflects this: `o27/tune.py` reports values as "(observed)" and only flags genuine sanity failures.

If you find yourself thinking "the K% / BA / R/G is wrong, let me tune it back to MLB shape" — stop and confirm with the designer. The shape is deliberate.

---

## 1. CURRENT STATE

### What's working

- **Engine → stats pipeline** is real and end-to-end. Every stat row in `o27v2.db` traces back to a simulated PA via `o27/engine/prob.py` → `o27/render/render.py` → `o27v2/sim.py`. Nothing is generated outside gameplay.
- **30-team league + 162-game schedule** generation, standings, W/L attribution, FIP/ERA/WHIP/K-27/BB-27 in the web layer (`app.py:_aggregate_pitcher_rows`).
- **O27-specific stats** all persisted: `stays`, `stay_hits`, `stay_rbi`, `fo` / `fo_induced` (foul-outs), `multi_hit_abs`, `arc1/arc2/arc3` bucket splits, runner-advancement analytics (`rad_*`), batted-ball physics (EV / LA / spray).
- **Empirical analytics suite** (`o27v2/analytics/`): Pythagorean exponent (`pythag.py`), wOBA / Game Score weights (`linear_weights.py`), BaseRuns shape (`base_runs.py`) — all refit at runtime from league data via ternary search / coordinate descent. They auto-adapt to whatever run environment the config produces; no recalibration needed when the sim is retuned.
- **Joker insertions** uncapped (commit `87b7d0f`). Manager AI inserts based on leverage × persona, with no per-cycle or per-game cap.
- **Fatigue model** is the dominant pitcher axis:
  - Quadratic stamina scaling (`prob.py:307`): `threshold = FATIGUE_THRESHOLD_BASE + (stamina ** 2) * FATIGUE_THRESHOLD_SCALE`
  - Cocktail of stamina + grit + release_angle + pitch_debt + form modulates the slope
  - 81-stamina vs 73-stamina pitcher: ~8 batters of runway difference, not 3
- **Form variance** widened to give blowouts breathing room. Daily form clamped to [0.71, 1.84] with σ=0.25; the asymmetric upper clamp is rare-but-real (~3.4σ Gaussian event).

### What broke / what's still wonky

- **DB migration tests fail** (`o27v2/tests/test_phase8_db_migration.py::test_init_db_wipes_stale_and_reseeds`, `test_init_db_idempotent_on_phase8_db`). Players seeded into the DB get empty `role` strings instead of "workhorse" / etc. Pre-existing, not caused by the May 17 tuning pass. Investigate `o27v2/league.py` `_make_pitcher()` role assignment.
- **`game_pitcher_stats` duplicate rows** (from prior handoff, may still be live). 184/1,502 games (12.2%) had duplicate `(game_id, player_id, team_id)` pitcher rows. Mitigated by `_PSTATS_DEDUP_SQL` view but should be hard-fixed with a `UNIQUE` constraint + `INSERT OR IGNORE`. Verify against current DB before assuming present.
- **`game_batter_stats.outs_recorded = 0` with `ab > 0`** for some rows. From prior handoff. Renderer's out-attribution misses outs in complex multi-runner plays. Not surfaced in any UI currently.
- **Super-inning assertion** at `game.py:162` and `game.py:191` (`assert state.outs <= 5`) can still crash on extreme SI halves. From prior handoff. Replace with `if state.outs > 5: break` + log.
- **Stale 22-26 R/G references in docs** (`docs/aar-*.md`, README sections). Not load-bearing — they're historical AAR documents. Won't mislead the engine, may mislead a future reader. Update opportunistically if you're editing those files for other reasons.

---

## 2. MAY 17 TUNING PASS — WHAT CHANGED

**Commits:** `7512522`, `abfe793` on branch `claude/tune-scoring-simulator-fZr2I`.

See `docs/aar-scoring-variance-and-fatigue-dominance.md` for the full AAR.

### Offense (more contact, more damage)

`o27/config.py`:
- `CONTACT_WEAK_BASE`   0.38 → 0.18 (far fewer weak singles)
- `CONTACT_MEDIUM_BASE` 0.40 → 0.50
- `CONTACT_HARD_BASE`   0.22 → 0.32

### K-rate suppression

`o27/config.py`:
- `PITCHER_DOM_SWINGING`    0.06  → 0.025 (was the K-inflation driver)
- `PITCHER_DOM_CALLED`      0.03  → 0.015
- `BATTER_DOM_SWINGING`    -0.03  → -0.05
- `BATTER_CONTACT_SWINGING` -0.05 → -0.08

### Fatigue dominance (`o27/config.py` + `o27/engine/prob.py:307`)

- `FATIGUE_THRESHOLD_BASE`  24 → 10
- `FATIGUE_THRESHOLD_SCALE` 40 → 65
- Linear stamina → **quadratic stamina** (`stamina ** 2`)
- `FATIGUE_SCALE` 20.0 → 10.0 (steeper post-threshold cliff)
- All `FATIGUE_*` penalty coefficients ~1.5× stronger
- `GRIT_FATIGUE_RESIST` 0.60 → 0.30 (cocktail modifiers demoted)
- `RELEASE_FATIGUE_SCALE` 0.20 → 0.10

### Game variance (`o27/config.py`)

- `TODAY_FORM_SIGMA` 0.10 → 0.25 (required for wide clamps to be reachable)
- `TODAY_FORM_MIN`   0.82 → 0.71
- `TODAY_FORM_MAX`   1.18 → 1.84 (asymmetric — transcendent days are rare ~3.4σ events)

### Documentation hygiene

- `HANDOFF.md` (this file) replaces the May 3 handoff (archived to `docs/HANDOFF-archive-2026-05-03.md`)
- `o27v2/config.py`: `TARGET_RUNS_LO/HI` 22-26 → 20-50 as permissive sanity bounds with prominent "NOT a prescription" comment
- `o27/tune.py`: removed "target X" labels, replaced with "(observed)"; sanity bounds only
- Analytics docstrings (`pythag.py`, `linear_weights.py`, `base_runs.py`) updated to remove "~22 R/G" anchors; the formulas already auto-refit
- `o27v2/web/app.py`: RPW/WAR comments updated to reflect variance-first design

### 200-game tune output post-changes

```
Avg total runs/game              33.73  sanity 20–50 ✓
  Median / Std / Min / Max       33.0 / 9.3 / 15 / 66
Avg run rate (R/out)             0.6247   (observed)
Avg PAs/game (reg halves)        90.7     (observed)
Avg stays/game                   1.860    (observed)
Super-inning frequency           2.50%    sanity <10% ✓
League K%                        13.07%   (observed)
League BB%                       8.49%    (observed)
League BA                        .386     (observed)
League SLG                       .667     (observed)
League HR/PA                     4.18%    (observed)
```

---

## 3. WHAT NOT TO TOUCH

- **Engine architecture.** `o27/engine/` state machine is clean. Calibration values live in `o27/config.py`, not in the engine.
- **12-batter lineups** (8 fielders + SP + 3 DH). Structural to O27.
- **Stay mechanic.** Core rule. `stays`, `stay_hits`, `stay_rbi` columns in `game_batter_stats` are correct.
- **No stored `pitcher_role`.** Task #65 decision. Manager AI derives role live.
- **Joker insertions are uncapped.** Designer call (commit `87b7d0f`). Manager AI gates by leverage × persona.
- **Per-27-outs stat scale.** ERA, WHIP, K, BB are all per 27 outs (one O27 game). Do not convert to per-9-IP.
- **FIP constant calibration.** `_league_fip_const()` re-fits per render cycle. Do not hardcode.
- **`_PSTATS_DEDUP_SQL` view.** Keep until a `UNIQUE` constraint replaces it.
- **Empirical analytics refits.** `pythag.refit_pythag_exponent()`, `linear_weights.derive_linear_weights()`, `base_runs._refit_coeffs()` all run on every web request. Do not hardcode constants — they self-adjust to whatever R/G environment the sim produces.
- **`TARGET_*` constants in `o27v2/config.py`.** These are deliberately permissive. Do not narrow them to "tighter targets" — the comment at the top explains why.

---

## 4. OPEN THREADS

**3TO (third-time-through-order) tracking.**
Not currently a first-class concept in the engine. The arc1/arc2/arc3 bucketing is the closest proxy and works for a 12-man lineup with starters going the distance. A real per-(pitcher, batter) look-count tracker plus `era_tto1/2/3` stat columns would expose lineup-adaptation effects as proper splits. Discussed in the May 17 design conversation but not implemented.

**Pitcher evaluation framework (cricket / pesäpallo flavored).**
ERA is a context stat in O27, not the headline. The designer is interested in a composite — proposed name **PEFF** — weighting:
- OS+% (outs share normalized to league)
- CCR (Clean Contact Rate — weak / total contact)
- TTO Decay (OPS_against tto3 − tto1; needs 3TO tracking first)
- LDR (Leverage Damage Resistance — leverage-weighted ERA)
- Arsenal Index (repertoire depth × avg pitch quality)

All derivable from existing schema except TTO Decay. Web layer should lead pitcher tables with PEFF / wERA / CCR / OS+%; ERA becomes secondary. Not yet implemented.

**Arsenal depth in the fatigue cocktail.**
The fatigue model uses stamina + grit + release_angle + form + pitch_debt. It does NOT use repertoire depth. Pitchers with 5-pitch arsenals should resist third-time-through-order decay more than 2-pitch relievers. Proposed addition in `prob.py:307` area:

```python
arsenal_size = len(getattr(pitcher, "repertoire", []) or [])
arsenal_mult = 1.0 - max(0, arsenal_size - 2) * cfg.ARSENAL_FATIGUE_RESIST
fatigue *= arsenal_mult
```

Plus a new `ARSENAL_FATIGUE_RESIST` constant (~0.05). Discussed but not landed.

**Form variance symmetry.**
The current asymmetric clamp (0.71-1.84 around mean 1.0) intentionally makes deep off-days more common than transcendent days. Gaussian sampling means the 1.84 upper bound is a ~3.4σ event — rare but reachable. If the designer wants symmetric extreme upside (more common transcendent days), this requires a sampling-shape change (skewed dist), not just a clamp tweak.

**DB migration test failures.**
`test_init_db_wipes_stale_and_reseeds` and `test_init_db_idempotent_on_phase8_db` fail because seeded players get empty `role` strings. Pre-existing. Lives in `o27v2/league.py` `_make_pitcher()`.

**`docs/aar-*.md` references to 22-26 R/G.**
Multiple historical AARs reference the old target band. Not load-bearing but could mislead a future reader who treats AAR documents as canon. Update opportunistically.

---

## 5. DATABASE STATE

(Most of section 3 from the prior handoff still applies — schema is unchanged. The live DB may have been reseeded since; verify `players` row count and `stamina` / `is_active` column presence via `PRAGMA table_info(players)`.)

If the live DB is stale (still at the 12.21 R/G era), the new config will produce drastically different stat distributions. **Do a full reseed before evaluating any aggregated stats**: `db.init_db()` → `league.seed_league()` → `schedule.seed_schedule()` → `sim.simulate_next_n(...)`.

---

## 6. WHERE THINGS LIVE

| Concept | File |
|---|---|
| All tunable constants | `o27/config.py` |
| Pitch outcome resolution | `o27/engine/prob.py` |
| Fatigue model | `o27/engine/prob.py:300-335` |
| Daily form roll | `o27/engine/prob.py:1644-1692` |
| Stay mechanic | `o27/engine/stay.py` |
| Park effects (geometric) | `o27/engine/park_effects.py` |
| Manager AI (pitching, jokers) | `o27/engine/manager.py` |
| Single-game CLI | `o27/main.py` |
| Batch metrics runner | `o27/tune.py` |
| 30-team league wrapper | `o27v2/sim.py`, `o27v2/manage.py` |
| DB schema | `o27v2/db.py` |
| League seeding | `o27v2/league.py` |
| Empirical analytics | `o27v2/analytics/{pythag,linear_weights,base_runs}.py` |
| Web layer | `o27v2/web/app.py` |
| Invariant tests | `tests/test_stat_invariants.py` |
| Realism identity tests | `tests/test_realism_identity.py` |

---

**End of handoff. When in doubt, read the AAR for the most recent tuning pass and confirm design intent with the designer before retuning anything.**
