# After-Action Report — Times-Through-the-Order Familiarity & the Softball/Underhand Arsenal

**Date completed:** 2026-05-27
**Branch:** `claude/sports-arbitrage-pitcher-mechanics-vVNgT`
**Commits:** `6274a12` (times-through-the-order familiarity), `7566137` (softball/underhand arsenal + archetypes), launch_angle_bias + foul_delta commit (this AAR)

---

## What was asked for

The designer opened with a structural argument about the sport's economics:

> "Any sport has arbitrage — guys eventually learn how to crack the code. The idea that everyone employs a bunch of flamethrowers or corner painters to navigate the strike zone and is content to have 9+ ERAs is unrealistic. There would be players who figure out a way around it, and those are knuckleballers, junkballers, and people who adapt the mechanics of softball pitching onto O27 baseball."

The follow-up, when offered a calibration pass to hold the run environment flat:

> "I do not want an offset tune. I don't care if runs scored are higher. I keep telling you this."

Then a second, detailed brief asked for a batch of softball-mechanics pitches and rare "elite stopper" archetypes — the pitching equivalent of an elite goalie — built around gravity manipulation, timing disruption, and fatigue immunity from a true underhand / ultra-low submarine slot.

The through-line: O27's fiction needs un-timeable deception arms to be a *mathematically viable* counter to a bat-dominant, 12-batter, second-chance offense — not just flavor text in the README.

---

## The gap we found

The engine modeled deception in several real ways already (movement suppresses hard contact at parity with batter power; submarine release reduces fatigue; low-arm-stress junk; count-aware usage; reverse-platoon O27-native pitches). But it was **missing the single lever that matters most in this sport**: a times-through-the-order effect.

There was *no* familiarity model. A pitcher faced the leadoff hitter for the 6th time in one 27-out arc with byte-identical effectiveness as the 1st. In MLB the times-through-the-order penalty is one of the largest known pitching effects and a starter only sees a hitter 2–3 times; in O27 the top of the order gets **5–7 PAs against the same arm in a single half-inning**, so familiarity *should* be the dominant pitching dynamic — and it was absent entirely. That absence is precisely what lets the sim flatten toward uniform high-ERA velocity: nothing punishes a pitcher for being figured out, so nothing rewards the arm that can't be figured out.

---

## What we did

### Part 1 — Times-through-the-order familiarity

The arbitrage made mechanical: each prior PA a batter has had against *this* pitcher *this game* tilts the matchup toward the hitter, attenuated by how un-timeable the pitcher's repertoire is.

**New per-pitch attribute: `timing_resistance` [0,1]** on all (now 24) catalog pitches. 1.0 = a hitter never times it up no matter the looks (knuckleball 0.95, sky_eephus 0.92, eephus 0.85); 0.0 = solved after one look (four_seam 0.20). ~0.5 neutral.

**New constants (`o27/config.py`):**

| Knob | Value | Role |
|---|---|---|
| `FAMILIARITY_PER_LOOK` | 0.18 | familiarity-dominance accrued per prior PA (pre-attenuation) |
| `FAMILIARITY_MAX_LOOKS` | 7 | cap on prior PAs counted |
| `FAMILIARITY_SWINGING` | −0.022 | fewer whiffs as the batter times the arm up |
| `FAMILIARITY_CALLED` | −0.009 | lays off borderline pitches he's now seen |
| `FAMILIARITY_CONTACT` | +0.020 | more balls in play |
| `FAMILIARITY_HARD_TILT` | +0.028 | contact-quality tilt toward hard per unit fam |
| `DEFAULT_TIMING_RESISTANCE` | 0.5 | repertoire-less fallback center |

**The shape:**

```
fam = FAMILIARITY_PER_LOOK * min(looks, MAX) * factor
factor = clamp((1 - timing_resistance) * 2, 0, 2)   # res 0.5→1.0, 1.0→0, 0.0→2.0
```

At `looks == 0` (first time facing this pitcher) `fam == 0` → every term collapses to the legacy probability surface. **This preserves the realism identity invariant** — `test_realism_identity` and all calibrated probabilities are untouched.

**Wiring:**
- `o27/engine/state.py` — `matchup_pa` dict keyed by `(pitcher_id, batter_id)` + `matchup_count()`. Keying on the pitcher means a fresh reliever resets familiarity to zero against everyone — *bringing in a new look is itself a manager lever.*
- `o27/engine/pa.py` — ticks the matchup counter at PA close, charged to whoever was on the mound.
- `o27/engine/prob.py` — `_pitcher_timing_resistance()` (usage-weighted repertoire mean; movement-derived fallback for legacy arms) and `_familiarity_dominance()`; the `familiarity` scalar threads into both `_pitch_probs` (whiff/called/contact split) and `contact_quality` (hard-contact tilt).

**It does what the argument predicted.** Two identical arms, same lineup, across one game:

| Look | Flamethrower whiff% / contact% | Knuckleballer whiff% / contact% |
|---|---|---|
| 1st | 10.1% / 23.4% | 10.1% / 23.4% |
| 4th | 8.1% / 25.6% | 9.6% / 23.9% |
| 6th | **7.0% / 26.7%** | **9.3% / 24.2%** |

(repertoire resistance: flamethrower 0.34, knuckleballer 0.83). The velocity arm gets cracked over the marathon; the junk arm survives.

### Part 2 — The softball / underhand arsenal

Seven new catalog pitches (17 → 24), all hard-gated to a low release slot (`max_release` ~0.30–0.45 — they don't exist above sidearm), extremely arm-friendly (`arm_stress` 0.45–0.55), and high `timing_resistance`:

| Pitch | Profile | Role |
|---|---|---|
| `riseball` | pure backspin, high K, weak fly, EV suppression | anti-power "ladder" pitch |
| `peeled_drop` | pure topspin, extreme weak contact, big EV kill | grounder / runner-freeze, counters the stay |
| `backhand_changeup` | velocity mismatch, reverse platoon, weak contact | punishes early triggers on second-chance contact |
| `sky_eephus` | ~50 mph vertical parabola, max K + walk risk | extreme timing-disruption put-away |
| `slither_knuck` | horizontal break, high K, command challenge | strikeout knuck vs high-eye hitters |
| `drop_knuck` | dead-stall tumble, extreme weak contact, low K | groundball knuck that neutralizes the stay |
| `rise_knuck` | hovers at the letters, K + weak popups | burns the AB's 3-contact budget |

Three rare "elite stopper" archetypes (`o27/data.py`), slot-locked and given a `stamina_bonus` (new optional archetype field) to encode fatigue immunity — they hold movement deep into the 27-out arc where a velocity arm decays:

- **`knuckleball_ace`** ("Chaos Elite", weight 0.03, slot 0.10–0.32, +0.18 stamina) — pure 3-pitch knuckleballer: slither/drop/rise. Repertoire resistance ≈ 0.93.
- **`softball_riseballer`** (weight 0.04, slot 0.05–0.25, +0.15 stamina) — riseball / peeled_drop / backhand_changeup / sinker. ≈ 0.69.
- **`underhand_eephus_artist`** (weight 0.03, slot 0.08–0.30, +0.15 stamina) — sky_eephus / peeled_drop / backhand_changeup / drop_knuck. ≈ 0.84.

Plus plumbing: pitch-mix bucket classifier (`o27v2/sim.py`) updated so the new pitches count toward off-speed%/breaking% (percentages stay normalized), and display-name overrides (`o27v2/web/formatters.py`).

---

### Part 3 — Making the grounder/popup split a real outcome (launch_angle_bias + foul_delta)

The first pass shipped the seven pitches but flagged that the engine had no way to separate "grounder pitch" from "popup pitch" — both collapsed to weak contact. The designer's response: *"then yes let's do that now. Feels like a good evolution in the game's modeling."* So we added the two missing levers.

**`launch_angle_bias` [−1, +1]** (per-pitch catalog field). In `resolve_contact`, after the existing sum-preserving power redistribution, the bias rolls `ground_out ↔ fly_out` weight inside the contact-quality table via the same `_redistribute` mechanism (new constant `LAUNCH_REDIST_GO2FO = 0.35`):

- bias < 0 (grounder): `fly_out → ground_out` — `sinker` −0.45, `spitter` −0.55, `curve_10_to_2` −0.50, `peeled_drop` −0.70, `drop_knuck` −0.60, `splitter` −0.25.
- bias > 0 (fly/popup): `ground_out → fly_out` — `four_seam` +0.30, `eephus` +0.20, `sky_eephus` +0.30, `riseball` +0.70, `rise_knuck` +0.50.
- Identity at 0.0 (every pitch without the field). Sum-preserving, so total outs-vs-hits is unchanged — only the *type* of out shifts. The bias also nudges the synthetic `sample_batted_ball` launch angle (±8° at full bias) so spray charts stay consistent with the categorical outcome.

**`foul_delta`** (per-pitch catalog field) added to the foul component in `_pitch_probs`. In O27 every foul spends one of the batter's 3 contact events, so a positive `foul_delta` literally burns the AB toward a foul-out — the mechanism behind the "rise/letters" pitches: `riseball` +0.04, `sky_eephus` +0.03, `rise_knuck` +0.03, `eephus` +0.02, `four_seam` +0.01. Identity at 0.0.

**Proof it's now a real outcome split** (8-team, 224-game sim — clearest on the knuckle trio that shares every other parameter):

| Pitch | bias | grounder% | flyout% |
|---|---|---|---|
| `drop_knuck` | −0.60 | 25.3% | 13.1% |
| `slither_knuck` | 0.00 | 19.4% | 13.9% |
| `rise_knuck` | +0.50 | 23.4% | **17.0%** |
| `four_seam` | +0.30 | 16.8% | 14.2% |
| `sinker` | −0.45 | 22.7% | 12.0% |

The three knuckle variants are no longer cosmetic — they produce distinct batted-ball profiles. (Rare pitches like `riseball`/`peeled_drop`/`sky_eephus` have tiny samples in an 8-team pool; the mechanism is identical and verified on the unit redistribution.)

### Remaining modeling gaps (still deliberately not faked)

- **No per-pitch movement variance keyed to weather.** The "knuckle-eephus catches every micro-current" idea has no hook; folded into the high-resistance / high-`bb_delta` knuckle variants rather than add a fake field.
- **Consolidation.** The brief's "underhand spinner" eephus is mechanically identical to `peeled_drop` (topspin → weak grounders), so it was not added as a separate redundant entry.

---

## Calibration stance

Per explicit instruction, **no offsetting run-environment tune was applied.** The familiarity penalty raises late-arc offense and lowers effective wERA for deception arms; both are intended. The only gate retained is the stat-invariant suite, which checks for mathematically-impossible stats, not run levels.

The magnitudes (`FAMILIARITY_*`, the per-pitch `timing_resistance` values) were set by reasoning, not a season-scale calibration sweep, so the *size* of the junkball edge over a full 162 is an estimate. `FAMILIARITY_PER_LOOK` is the single dial to scale the whole effect up or down.

**One downstream effect surfaced and was re-baselined.** The familiarity model raises *late-arc* offense (more looks at the same arm = more runs), which lifts run-expectancy at the end of the arc and compressed the `test_re_curve_overall_decreasing` start:end ratio from ≥4× to ~3.71× — confirmed on the 30-team default config, not an 8-team artifact. Since the higher-run environment is intentional and no offset tune is wanted, the heuristic was re-baselined 4× → 3.5× (with a comment), keeping it a guard against gross curve inversions while matching the new baseline. This is a SABR sanity heuristic, not a correctness invariant; the `make test-invariants` stat-invariant gate (mathematically-impossible-stat checks) was unaffected throughout.

---

## Verification

- `python o27v2/manage.py smoke` — 10/10 games complete (re-run after each part).
- `tests/test_realism_identity.py`, `o27/tests/` (incl. `test_power_redistribute.py`), `o27v2/tests/test_archetypes.py` — pass. Identity preserved at first look (familiarity) and at bias/delta 0.0 (launch/foul).
- `tests/test_analytics_invariants.py` — 7/7 after the RE-curve re-baseline.
- Stat-invariant gate (`make test-invariants`) against freshly-simmed DBs — 10/10 throughout.
- Sim confirms the new pitches are thrown in real games and produce distinct grounder/flyout profiles (see Part 3 table); new archetypes generate.

---

## Files touched

- `o27/config.py` — `timing_resistance` on all pitches; `FAMILIARITY_*` constants; 7 new catalog entries; `launch_angle_bias` + `foul_delta` fields and `LAUNCH_REDIST_GO2FO`.
- `o27/engine/state.py` — `matchup_pa` + `matchup_count()`.
- `o27/engine/pa.py` — matchup counter tick at PA close.
- `o27/engine/prob.py` — repertoire→resistance, familiarity-dominance, threaded into both outcome models; `foul_delta` in `_pitch_probs`; `launch_angle_bias` redistribution in `resolve_contact`.
- `o27/engine/batted_ball.py` — `pitch_launch_bias` shifts synthetic launch angle.
- `o27/data.py` — 3 new archetypes + `stamina_bonus` support.
- `o27v2/sim.py` — pitch-mix bucket classification for the new pitches.
- `o27v2/web/formatters.py` — display-name overrides.
- `tests/test_analytics_invariants.py` — RE-curve start:end heuristic re-baselined 4× → 3.5×.
