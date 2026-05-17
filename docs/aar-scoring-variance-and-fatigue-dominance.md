# After-Action Report — Scoring Boost, Variance Widening, Fatigue Dominance

**Date completed:** 2026-05-17
**Branch:** `claude/tune-scoring-simulator-fZr2I`
**Commits:** `7512522` (offense + K% + fatigue restructure), `abfe793` (form variance widening + stale-target cleanup)

---

## What was asked for

The designer opened with a 21-12 box score from a single sim and asked for ways to "boost scoring while keeping pitchers relevant." The conversation evolved through several explicit refinements:

1. **Lower K% to ~16-18%** (current sim was producing 25.5%)
2. **Drop weak contact, raise medium and hard contact** — specifically: `WEAK 0.38 → 0.18`, with medium and hard both moving up
3. **Fatigue should be the biggest impact on decay**, steep, with the biggest difference between an 81-stamina pitcher and a 73-stamina pitcher made dramatic — implied via a **cocktail of personal attributes** rather than stamina alone
4. **More wild variance in game outcomes** — too many close games, the sport's fiction calls for a wider spread
5. **No R/G prescription** — explicit pushback: *"i never set the 22-24 r/g threshold you did originally and i dont want to keep sticking to that"*
6. **Fix the documentation** so stale targets don't mislead future agents
7. **Fix the formulas** for the new run environment, archive the old handoff, and write an AAR

There was a recurring theme: the designer wants O27's stat lines to feel like a coherent fictional sport (cricket and pesäpallo were referenced as the right mental model — high-scoring environments where bowling/pitching still matters but ERA is a context stat, not the headline metric).

---

## What we did

### Calibration

| Knob | Old | New | Why |
|---|---|---|---|
| `PITCHER_DOM_SWINGING` | +0.06 | +0.025 | A recent tuning pass bumped this from 0.03 → 0.06; was the single biggest K-inflation driver per `config.py:64` |
| `PITCHER_DOM_CALLED` | +0.03 | +0.015 | Same reasoning, smaller magnitude |
| `BATTER_DOM_SWINGING` | -0.03 | -0.05 | High-skill batters now whiff materially less |
| `BATTER_CONTACT_SWINGING` | -0.05 | -0.08 | High-contact batters now rarely whiff |
| `CONTACT_WEAK_BASE` | 0.38 | 0.18 | Halved per designer call |
| `CONTACT_MEDIUM_BASE` | 0.40 | 0.50 | Bumped per designer call |
| `CONTACT_HARD_BASE` | 0.22 | 0.32 | Bumped per designer call (sums to 1.0) |

`CONTACT_MATCHUP_SHIFT` (0.25) was left alone — it preserves the differentiation that lets a high-Stuff, high-Movement pitcher still pull contact toward weak even in the new offensive baseline.

### Fatigue restructure

The big structural change. Before: a linear stamina-to-threshold mapping that left elite-stamina pitchers (threshold 56 BF) and average pitchers (threshold 53 BF) functionally identical — both above the ~35-45 batters a typical 27-out solo would face. Fatigue rarely fired.

After: quadratic stamina, lower floor, wider spread:

```python
# o27/engine/prob.py:307
fatigue_threshold = max(
    cfg.FATIGUE_THRESHOLD_BASE,
    cfg.FATIGUE_THRESHOLD_BASE + round((pitcher.stamina ** 2) * cfg.FATIGUE_THRESHOLD_SCALE),
)
```

With `FATIGUE_THRESHOLD_BASE=10` and `FATIGUE_THRESHOLD_SCALE=65`:

| Stamina | Threshold (BF) | Reaches fatigue at |
|---|---|---|
| 0.81 | 53 | Still goes deep |
| 0.73 | 45 | Cracks in arc3 |
| 0.50 | 26 | Cracks in arc2 |
| 0.30 | 16 | Opener / one-time-through |

The 81-vs-73 gap is now ~8 batters (roughly one full time through the order), not 3. That's the dramatic stamina moat the designer wanted.

Additional fatigue changes:
- `FATIGUE_SCALE` 20.0 → 10.0: steeper post-threshold cliff
- All `FATIGUE_*` penalty coefficients ~1.5× stronger (BALL 0.06→0.09, CONTACT 0.06→0.10, etc.)
- `GRIT_FATIGUE_RESIST` 0.60 → 0.30: grit now modulates ±15% instead of ±30%
- `RELEASE_FATIGUE_SCALE` 0.20 → 0.10: submarine bonus halved

The latter two demote the cocktail modifiers so stamina drives 70-80% of the variance and grit / arm-slot / form / workload-debt produce the remaining differentiation — matching the designer's framing that fatigue should dominate, with other attributes as flavor.

### Game-to-game variance

Daily form clamps and sigma:

| Knob | Pre-pass | Pass 1 | Pass 2 (final) |
|---|---|---|---|
| `TODAY_FORM_SIGMA` | 0.04 | 0.10 | 0.25 |
| `TODAY_FORM_MIN` | 0.92 | 0.82 | 0.71 |
| `TODAY_FORM_MAX` | 1.08 | 1.18 | 1.84 |

The two-pass evolution mattered. Pass 1 widened the band but the designer reported games were still too contained. Pass 2 went bolder: 0.71-1.84 clamps with σ=0.25. Critical reasoning: at σ=0.10 the upper clamp at 1.84 is 8.4σ above mean — theoretical, never reachable. Bumping σ to 0.25 makes the lower clamp a ~1.2σ event (frequent) and the upper a ~3.4σ event (rare but real).

The asymmetric clamp (0.29 below mean vs 0.84 above mean) is intentional and a Gaussian-sampling property: deep off-days will always be more common than transcendent days under symmetric sampling. If the designer wants symmetric extreme upside, that needs a skewed sampling distribution, which is a bigger change than a clamp tweak.

### Documentation hygiene — the "future agents will believe it" problem

The designer flagged that the 22-26 R/G band was baked into multiple places and future agents would treat it as canon. Cleaned:

- **`o27v2/config.py:191-220`**: `TARGET_RUNS_LO/HI` widened from 22-26 to 20-50 as permissive sanity bounds. Similar widening for stays / jokers / pitcher changes. Added a prominent comment block: *"These are NOT calibration targets. O27 is intentionally a high-variance, high-scoring fictional sport… Do NOT re-tune the simulator to hit any specific R/G or rate stat unless the designer explicitly asks for it."*
- **`o27/tune.py`**: removed all hardcoded `target X` strings from the metrics table. Each stat now reports as `(observed)`. Removed the `targets_met` assertion that flagged 22-24 R/G specifically. Kept sanity bounds only for catching catastrophic regressions (R/G under 20 or over 50, super-inning rate over 10%, etc.). Updated module docstring to explain the variance-first philosophy.
- **`o27v2/analytics/{pythag,linear_weights,base_runs}.py`**: removed "~22 R/G" anchors from docstrings. Clarified that the empirical-refit logic (ternary search, coordinate descent) already auto-adapts to whatever run environment the sim produces — no hardcoded coefficients to drift out of calibration.
- **`o27v2/web/app.py`**: RPW formula comment updated (still uses the dynamic `9 + sqrt(R/G/4) × 3.5` calculation, but the comments no longer mislead the reader with stale "25 R/G" / "24 R/G" examples).
- **`HANDOFF.md`**: archived prior version to `docs/HANDOFF-archive-2026-05-03.md`. Wrote a fresh handoff that leads with "O27 is variance-first. There is no R/G calibration target." The first thing a future agent reads.

### What we did NOT change

- **Park geometry** (`o27/engine/park_effects.py`). Park multipliers (`PARK_HR_MIN/MAX = 0.85/1.20`, `PARK_HITS_MIN/MAX = 0.93/1.08`) untouched. Geometric park effects already provide per-game variance and don't need widening to deliver the wild outcomes the designer wanted — daily form does that work.
- **Weather effects** (`o27/engine/weather.py`). Could be amplified for more variance but the form-sigma widening covers it.
- **Stay mechanic**. Designer accepted higher stays (~2/game vs old 0.3-1.0 target band) as part of the new normal.
- **Joker insertions**. Cap already removed in commit `87b7d0f` before this work began.
- **Analytics formula coefficients**. They self-refit at runtime; no recalibration needed.
- **K% pullback**. Sim landed at 13.07% post-pass-2, below the designer's 16-18% range. Designer's response: *"I'm fine with lower K% don't adjust that"* — left at 13.07%.

---

## Results

200-game tune output after both commits:

```
SCORING
  Avg total runs/game            33.73  (sanity 20–50)
  Median / Std / Min / Max       33.0 / 9.3 / 15 / 66

RUN RATE
  Avg run rate (R/out)           0.6247

PLATE APPEARANCES
  Avg PAs/game (reg halves)      90.7
  Min / Max PAs                  68 / 124

STAY MECHANIC
  Avg stays/game                 1.860
  Avg multi-hit ABs/game         0.560 (0.62% of PAs)

SUPER-INNING
  Frequency                      2.50%  (5/200 games)

RATE STATS
  League K%                      13.07%
  League BB%                     8.49%
  League BA                      .386
  League SLG                     .667
  League HR/PA                   4.18%

MANAGER ACTIVITY
  Avg joker insertions/game      3.64
  Avg pitcher spell length (BF)  45.36 (median 44)
```

The variance evidence:
- **Range 15-66 runs/game** on the same league with the same talent. A 51-run spread between min and max.
- Pass 1 produced max=57; pass 2 produced max=66 — the form-clamp widening directly expanded the upper tail.
- Std dev of 9.3 over a 33.7 mean (CV ≈ 0.28). That's a real spread; for context, MLB game totals have CV around 0.45-0.5 (mean ~9 R/G total, std ~4-5).

The K% landed lower than the designer's stated 16-18% range. They accepted it.

---

## Design intent captured (for future agents)

The conversation that drove this work clarified several non-obvious principles that should propagate forward:

**1. O27 is not MLB-shaped. Stop trying to make it MLB-shaped.**
The previous tuning pass anchored everything to "feels like 1990s-2000s MLB" — K% in the high teens, BA around .290, R/G under 25. The designer rejected this framing. O27 is a fictional sport with its own internal logic. Stat distributions should follow the mechanics, not target an external reference league.

**2. Variance is a feature.**
The 15-runs-to-66-runs game spread is not a calibration miss. It's the design. A pitcher having a 0.71-form day against a hot batting lineup *should* produce a 60-run game. A high-form ace against a slumping team *should* produce a 15-run game. The sport's fiction requires that breadth.

**3. ERA is a context stat in O27, not the headline.**
Discussed at length but not yet implemented. In a high-scoring fictional league, ERA is misleading; a 6.00-ERA pitcher might be elite if their CCR (Clean Contact Rate), OS+% (Outs Share normalized), TTO Decay, and LDR (Leverage Damage Resistance) are league-leading. The cricket bowling / pesäpallo lukkari analogy is the right mental model. A proper PEFF (Pitcher Effectiveness) composite is on the open-threads list.

**4. Pitcher individuality should emerge from a cocktail of attributes.**
Fatigue is the biggest axis (stamina dominates), but grit, arm-slot, arsenal depth (not yet wired), form, and workload-debt should all flavor it. Two pitchers with identical stamina but different grit / arsenal / form profiles should produce distinguishable arc3 stat lines.

**5. Future agents will read documentation and act on it.**
Stale targets in `config.py` comments, `tune.py` flags, docstrings, and `HANDOFF.md` mislead. They get treated as canon by any agent picking up the work cold. Aggressive cleanup of stale prescriptions is part of any tuning pass, not optional cleanup.

---

## Open threads inherited or surfaced

These came up in the conversation but were not landed in code:

- **3TO (third time through order) tracking.** Not currently first-class in the engine; arc bucketing is the proxy. Proper per-(pitcher, batter) look-count with `era_tto1/2/3` splits is on the list.
- **Arsenal depth as a fatigue cocktail factor.** The repertoire is exposed on `pitcher.repertoire` but not used in the fatigue model. A 5-pitch starter should resist third-time-through decay more than a 2-pitch reliever. Proposed:
  ```python
  arsenal_mult = 1.0 - max(0, arsenal_size - 2) * cfg.ARSENAL_FATIGUE_RESIST
  fatigue *= arsenal_mult
  ```
  with new `ARSENAL_FATIGUE_RESIST ≈ 0.05`.
- **PEFF composite and pesäpallo-style splits.** Pitcher table in the web UI should lead with effectiveness composite, not ERA. CCR / OS+% / TTO Decay / LDR all derivable from existing schema except TTO Decay.
- **Form sampling shape.** Currently Gaussian, which makes the asymmetric clamp produce asymmetric reachability. If the designer wants common transcendent days, a skewed sampling distribution would be needed.
- **DB migration tests** (`test_init_db_wipes_stale_and_reseeds`, `test_init_db_idempotent_on_phase8_db`) fail because seeded players get empty `role` strings. Pre-existing, unrelated to this pass, but should be fixed.
- **AAR documents in `docs/`** reference the old 22-26 R/G target. Not load-bearing but worth opportunistic cleanup.

---

## Lessons

- **Don't anchor to numbers the designer didn't set.** I anchored to 22-24 R/G from the prior tuning log on the first proposal and had to be corrected explicitly. The lesson: when a target appears in code or docs, verify with the designer that it's still the intent before treating it as canon.
- **Surface uncertainty about magnitudes.** I had to walk back the K% pullback estimate (proposed +3-4% gain from `PITCHER_DOM_SWINGING` 0.025→0.04) once the sim ran — the relationship isn't linear and my estimate was rough. Owning that "this is a directional estimate, not a precise prediction" upfront would have set better expectations.
- **Sigma and clamps interact.** Proposing wide clamps (0.71-1.84) without also bumping sigma would have produced theoretical bounds that never fire. Caught this before edits, but only because I checked the sampling code at `prob.py:1672-1677`. Reading the math before proposing knob changes matters.
- **Documentation cleanup is part of the work, not separate.** The designer's "future agents will believe it!" message landed harder than expected. The lesson: treat stale comments / docstrings / target constants as a first-class deliverable, not as "we'll clean up later."
