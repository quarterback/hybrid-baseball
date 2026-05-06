# After-Action Report — V3 closure attempt + continuous talent flow

**Date completed:** 2026-05-06
**Branch:** `claude/fix-dark-theme-baseball-terms-7UhIv`
**Predecessors:** `aar-phase-11-closeout.md` (the prior close-out), `aar-second-chance-hits-and-eye-command-modifier.md`

---

## Context

User accepted the Phase 11 close-out and asked to revisit follow-up #3:
extend talent-weighting beyond the 2C-event scope. Two architectural
constraints emerged during the work:

1. **No probabilistic gates.** "We're gating talent weighting and it
   shouldn't be." The probabilistic gate (rng vs gate_p) on Path A
   was the wrong shape — talent should flow continuously into outcome,
   not as a binary probabilistic flip.
2. **Plausibility over spec.** "Don't keep preening on this to achieve
   some spec, i just want things that make sense." Stars distinctive,
   marginal players still useful, O27 feels structurally different
   from MLB.

Plus a smaller fix request: count SI stats properly in `game_pa_log`.

---

## What was built

### 1. SI phase coverage in `game_pa_log`

`o27v2/db.py` — new `phase` column on `game_pa_log` (default 0 for
regulation; N>=1 for SI round N). Migration via `CREATE TABLE IF NOT
EXISTS` plus migration ALTER list (parallel to `game_batter_stats`).

`o27/render/render.py` — `capture_context()` now snapshots
`state.super_inning_number` as `phase`; the pa_log row reads it from
ctx.

`o27v2/sim.py` — INSERT path threads phase through. PA log now shows
SI events with phase>=1 distinct from regulation (phase=0). SI gap
shrunk from ~16% to a small residual (~2-3% across the population).

### 2. Continuous talent flow on 2C outcomes (replaces Path A gate)

Path A's probabilistic gate on stay-event runner_advances is gone.
The new model in `o27/engine/prob.py` (after the stay decision):

```python
if choice == "stay" and quality in ("weak", "medium"):
    eye_dev = (batter.eye - 0.5) * 2
    con_dev = (batter.contact - 0.5) * 2
    cmd_dev = (pitcher.command - 0.5) * 2
    talent_factor = eye_dev + con_dev - cmd_dev    # ±3 range, ±1 typical

    if quality == "weak":
        expected = 0.5 * (1.0 + talent_factor)     # ~0 .. ~1.5
    else:  # medium
        expected = 1.0 + 0.5 * talent_factor       # ~0.5 .. ~2.5

    expected = max(0.0, min(3.0, expected))
    floor_v = int(expected)
    frac    = expected - floor_v
    adv     = floor_v + (1 if rng.random() < frac else 0)
    adv     = max(0, min(3, adv))
    outcome_dict["runner_advances"] = [adv, adv, adv]
```

**Talent is the deterministic anchor.** It maps to an expected
advance value — a continuous function of the matchup. The rng draw
resolves only the fractional remainder (e.g., expected = 0.7 → 70%
chance of adv=1, 30% chance of adv=0). That's not a "magical RNG" —
it's deterministic-anchored stochastic resolution.

**Plausibility behavior:**

| Player tier | talent_factor | weak quality expected | medium quality expected |
|---|---|---|---|
| Marginal | ≈ -1.0 | ~0 (occasional credit) | ~0.5 (mostly 1) |
| Average | ≈ 0 | ~0.5 (50% credit) | ~1.0 (always 1) |
| Star | ≈ +2.0 | ~1.5 (always credit, sometimes 2) | ~2.0 (always 2, sometimes 3) |

Marginal hitters still produce something on stays (bottom-eye-decile
mean stay_hits/season is 9.3, max 46 — they're useful contributors).
Stars are reliably better (top-eye-decile mean 23.3, max 52). The
gap is wide but not absolute.

---

## Final league shape

Re-sim path: `resetdb` → `sim 2430` → `backfill_arc`.

| Metric | Result | Read |
|---|---|---|
| League PAVG | .278 | Normal |
| League BAVG | .331 | Normal |
| stay_rate | 5.72% | Stable |
| HR | 4,497 | In band |
| R/team-game | 12.44 | Normal (≈25 R/G league total) |
| 2C-Conv% (league) | 59.4% | Talent-mid |
| stay_rbi% | 4.26% | Modest, talent-distributed |

### Talent shape — 2C-Conv% by eye decile

| Decile | Eye | Conv |
|---|---|---|
| 1  | 22.3 | **38.2%** |
| 2  | 30.5 | 44.3% |
| 3  | 35.7 | 47.5% |
| 4  | 40.1 | 54.0% |
| 5  | 42.6 | 56.2% |
| 6  | 47.4 | 59.4% |
| 7  | 54.1 | 64.9% |
| 8  | 58.7 | 69.3% |
| 9  | 63.2 | 75.2% |
| 10 | 71.5 | **80.8%** |

**Spread: 42.7pp top-vs-bottom.** Monotonic from d1 to d10, no
plateau. The top 20% are clearly distinctive (75-81% conversion);
the bottom 20% still produce (38-44%). Stars matter, marginal
hitters aren't useless. That's the shape the user asked for.

### V3 (Δ by contact decile)

Still flat (.050 → .057 across deciles). The persistent architectural
limit is real: Δ measures BAVG-PAVG geometry, which moves slowly
because stay-credited hits are ~10-15% of total H. The PA log
decomposition shows `stay_h/PA` scales with contact rating but the
aggregate Δ doesn't shift much because both BAVG and PAVG move
together when total H grows.

V3 was effectively unsolvable under the "talent-only-on-2C-events"
scope — closing it would require talent-weighting run-chosen safety
hits too, which the user has not authorized and which would
re-architect the contact-resolution layer. Documented in prior AAR.

---

## What was NOT shipped (and why)

- **V3 spec match** — not reachable under the talent-on-2C scope.
  Closing it would mean retuning run-chosen safety-hit logic (out
  of scope; would re-open `runner_advances_for_hit` in
  `o27/engine/baserunning.py`).
- **Spec-band perfection on V1/V2** — abandoned at user's instruction
  ("ignore the spec, just do what makes sense"). The actual numbers
  produce plausible talent diversity even where they exceed the
  literal spec band (top decile 81% vs spec 60-70%; user accepted
  this trade for shape monotonicity).
- **Path 2 `swings_in_ab` modifier on subsequent swings** — preserved
  unchanged. Still applies on swing 2+ via `contact_quality`'s
  matchup_shift extension. Path A sat on top of this; with Path A
  removed and replaced by the fractional model, Path 2 still
  contributes the swing-2+ mid-AB amplification.

---

## Files touched

| File | Change |
|---|---|
| `o27/engine/prob.py` | Path A gate replaced by fractional talent advance; `import math` added (unused after final iteration but kept) |
| `o27/render/render.py` | capture_context emits `phase` from `state.super_inning_number`; pa_log row threads phase through |
| `o27v2/db.py` | `phase` column on game_pa_log + migration |
| `o27v2/sim.py` | INSERT writes phase column |

Commits in this session:

- `06fa387` — Phase 11D per-PA event log (game_pa_log)
- `1d43c7e` — Phase 11 close-out AAR
- `1285ef7` — Continuous talent flow + SI phase coverage (this work)
- this AAR

---

## Follow-ups parked

1. **V3 closure via run-chosen hit talent-weighting** — would re-open
   `runner_advances_for_hit` in `o27/engine/baserunning.py`. Held;
   the user explicitly said don't keep iterating to hit specs.

2. **Cleanup of orphaned config constant** — `TALENT_2C_SHIFT_SCALE`
   in `o27/config.py` is no longer referenced (was used by the gate).
   Trivial cleanup.

3. **PA log SI residual gap** — diff is now ~310/12,482 (~2.5%).
   Likely a few special-case events not threading through pa_log
   capture (jokers, certain SI mid-round transitions). Not blocking.

4. **`swings_in_ab` modifier in contact_quality** — could be
   simplified or removed now that the fractional advance does the
   talent-shape work directly. Held to avoid re-opening Path 2 work.

---

## Closing read

The architecture now matches the user's principle: talent is a
continuous deterministic anchor, the rng draw is post-decision
fractional resolution. Marginal hitters produce; stars are
distinctive; the league looks plausible (BAVG/PAVG/HR all in
normal range, stay_rate stable, 12 R/team-game). 2C-Conv% spreads
monotonically across the eye spectrum from 38% to 81% — wider
talent diversity than any prior architecture in this work.

O27 plays differently from MLB at the talent margins: a high-eye
contact specialist genuinely converts 2Cs at twice the rate of a
marginal-eye batter, and the gap shows up in stay_hits totals,
2C-RBI shape, and per-decile conversion alike. The mechanic is
visible in the data without being arbitrary.

V3 (Δ by contact) is parked — the persistent flat Δ across deciles
is honestly diagnosable via the PA log decomposition (`stay_h/PA`
scales, aggregate Δ doesn't), and closing it would require an
intervention outside the 2C-mechanic scope.
