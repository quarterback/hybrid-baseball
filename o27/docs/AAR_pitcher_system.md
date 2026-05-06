# AAR — Pitcher System Retune (Phase 11: Talent-Dictates-Performance)

**Branch**: `claude/improve-pitcher-system-UiM9b`
**Date**: 2026-05-06
**Goal stated by user**:
> Make the pitching system more competitive for pitchers — let their talent dictate performance more than any kind of artificial weights. Remove all talent gates and let talent proliferate. Add a pitch-quality range model (stuff/movement/control as a static range per guy, not a single number) and a "grit" stat (25–75) that boosts effectiveness when stamina is low.

## Mission

Before this pass, the per-pitch and contact-quality models were quietly **batter-tilted**, with several artificial floors and caps muting elite pitcher talent. Walks favored pitcher control, but strikeouts and hard-contact suppression favored the batter ~2×, and pitchers had a single offensive talent dimension (Stuff) versus the batter's three (skill / contact / power / eye). Several "soft levers" (probability floors, fatigue caps, daily-form noise) actively pushed outcomes toward the league mean regardless of talent.

## What Was Done

### 1. Rebalanced per-pitch dominance toward the pitcher
`o27/config.py:101–104`
- `PITCHER_DOM_BALL`:     −0.06 → **−0.07**
- `PITCHER_DOM_SWINGING`: +0.03 → **+0.06** (now matches `BATTER_CONTACT_SWINGING`)
- `PITCHER_DOM_CONTACT`:  −0.04 → **−0.06** (now exceeds batter's promotion)

### 2. Widened movement tilt to parity with power tilt
`o27/config.py:454`
- `CONTACT_MOVEMENT_TILT`: 0.06 → **0.10**

### 3. Removed talent gates
- **Probability floors**: `prob.py:170` and `prob.py:235–248` — all `max(0.01, …)` lowered to `max(0.001, …)`. The 0.01 floor was the single largest "push to the middle" lever.
- **Fatigue cap**: `config.py:129` — `FATIGUE_MAX` 0.60 → **1.00** (low-stamina arms can now truly collapse).
- **Pitch-debt cap**: `config.py:487` — `FATIGUE_DEBT_MAX_PENALTY` 0.20 → **0.40** (overworked arms suffer 2× more).
- **Daily-form noise**: `config.py:475–477` — `TODAY_FORM_SIGMA` 0.10 → **0.04**, bounds tightened from [0.80, 1.20] to [0.92, 1.08]. RNG no longer overrides talent.

### 4. Pitch-quality range model (per-guy "static range")
- New `Player.pitch_variance: float = 0.0` (`engine/state.py`).
- New helper `_sample_quality(rng, central, variance)` in `engine/prob.py` — every pitch draws stuff / command / movement uniformly in `[rating ± pitch_variance]`, clamped to [0, 1]. Identity at variance = 0.0.
- Threaded `rng` through `_pitch_probs` and used in both `pitch_outcome` and `contact_quality`.
- Each pitcher now has their own consistency profile: a low-variance arm repeats his stuff every pitch; a high-variance arm lives on the edges of his distribution.

### 5. Grit — stamina-fatigue compensator
- New `Player.grit: float = 0.5` (`engine/state.py`), bounded 0.25–0.75 by config (`GRIT_BOUND_MIN/MAX`).
- Applied as a multiplicative dampener on the fatigue ramp (`engine/prob.py`):
  ```python
  grit_mult = max(0.0, 1.0 - (grit - 0.5) * 2.0 * GRIT_FATIGUE_RESIST)
  fatigue *= grit_mult
  ```
- At grit = 0.50 → multiplier 1.0 (identity). At 0.75 → fatigue dampened 30%. At 0.25 → fatigue amplified 30%.
- High-grit veterans grind through tired innings; low-grit kids unravel.

### 6. Demo roster + helper updates
- Extended `o27/main.py` `_player()` helper to accept `command`, `movement`, `pitch_variance`, `grit`.
- Set non-default values on F9 Okafor and B9 Lindqvist so the new code paths exercise in tune runs.

## Validation

### Identity invariance
At all-neutral inputs (`pitcher_skill = command = movement = grit = 0.5`, `pitch_variance = 0.0`, `today_form = 1.0`, neutral weather), `_pitch_probs` returns the league base table to within 1e-6. **Confirmed** via spot check at counts (0, 0), (1, 1), (3, 2).

### League means (500-game tune.py batch)
| Metric | Pre-pass (baseline) | Post-pass | Target |
|---|---|---|---|
| Avg total runs/game | 23.99 | **23.52** | 22–24 ✓ |
| Avg run rate (R/out) | 0.444 | **0.436** | ~0.43 ✓ |
| Avg PAs/game | 80.4 | **80.2** | ~79 ✓ |
| Avg stays/game | 0.660 | **0.730** | 0.3–1.0 ✓ |
| Super-inning freq | 4.60% | 5.20% | <5% ! |
| League K% | n/a | **18.38%** | 17–19% ✓ |
| League BA | n/a | **.292** | .280–.305 ✓ |
| League SLG | n/a | **.462** | .440–.480 ✓ |
| League HR/PA | n/a | **2.08%** | 2.0–2.6% ✓ |
| League BB% | n/a | 7.45% | 9–10% ! |

### Talent dispersion (the actual goal)
Spot check, ace (Stuff/Cmd/Mov 0.85/0.85/0.80, grit 0.70) vs replacement (0.30/0.30/0.30, grit 0.30) facing a league-average bat:

| Outcome (0-0 count) | Ace | Replacement | Delta |
|---|---|---|---|
| Ball | 26.0% | 39.4% | **−13.4 pp** |
| Called strike | 23.3% | 14.4% | **+8.9 pp** |
| Swinging strike | 16.2% | 7.7% | **+8.5 pp** (2.1× ratio) |
| Contact | 19.7% | 24.9% | **−5.2 pp** |

Hard-contact suppression (10k-pitch sample):
- Ace: **6.8% hard contact** (base 22% → 70% reduction)
- Replacement: **30.3% hard contact** (38% increase over base)

Late-game (50 BF, fatigue zone):
- Ace's profile barely shifts (high Stamina × high Grit shields him)
- Replacement decays further: ball rate 39.4% → 43.5%, called strikes 14.4% → 11.7%

## Lessons Learned

- **The 0.01 floor was the single biggest gate.** Dropping it to 0.001 unlocked transcendent talent without breaking league means.
- **Pitcher-side dominance scalars were the obvious imbalance.** Bringing `PITCHER_DOM_SWINGING` from +0.03 to +0.06 (matching `BATTER_CONTACT_SWINGING`) was the cleanest single-line lever for K%.
- **Grit + Stamina compose cleanly.** Stamina sets the threshold; Grit scales the post-threshold ramp. They mean different things and can be rolled independently in roster generation.
- **Per-pitch variance gives pitchers their own consistency identity** without adding a new RNG branch — the helper falls back to identity at variance = 0 so legacy callers get bit-exact behavior.

## Phase 11b — Pitch Catalog + Release-Angle System

Implemented in the same branch after user approval.

### What was added

**17-pitch catalog** in `o27/config.py → PITCH_CATALOG`:
- Fastballs: four_seam, sinker, cutter, palmball
- Breaking: slider, sisko_slider, walking_slider, curveball, curve_10_to_2
- Off-speed: changeup, vulcan_changeup, splitter
- Specialty: knuckleball, spitter, eephus, screwball, gyroball

Each pitch type defines k_delta, bb_delta, contact_delta, hard/weak_contact_shift, platoon_mode/scale, release_optimal/window, arm_stress, max_release, count_bias.

**`PitchEntry` dataclass** (`engine/state.py`): `pitch_type`, `quality`, `usage_weight`.

**`Player.release_angle`** (0.0=submarine / 0.5=sidearm / 1.0=three-quarter sidearm). Drives:
- Release-angle platoon amplifier: submarine pitchers have stronger platoon effects (RELEASE_PLATOON_AMP_SCALE=0.60, so sub adds 30% more).
- Arm-ease fatigue reduction for submarine deliveries (RELEASE_FATIGUE_SCALE=0.20).
- Per-pitch release_quality multiplier [0.5, 1.0] that scales how well a pitch works from a given slot.

**`Player.repertoire`** (list[PitchEntry]) — per-pitcher pitch selection. Legacy pitchers (empty list) retain full identity.

**`_select_pitch`** — count-aware weighted selection. 2-strike counts boost put-away pitches 2.2×; behind boosts fastballs 1.6×. Hard release gate excludes pitches with `max_release < pitcher.release_angle`.

**Pitch selection threaded through `_generate_pitch`** — one `_select_pitch` call, same pitch drives both `pitch_outcome` (K/BB/contact) and `contact_quality` (hard/weak/GB shifts).

**Platoon per pitch type** via `_apply_pitch_platoon` — five modes:
- neutral / standard / reverse / same_heavy (Sisko slider) / opposite_heavy (Vulcan changeup, cutter)

**O27 sidearm/submarine structural fact**: treated as lore-level, not enforced mechanically. `max_release` on individual pitches (e.g. curve_10_to_2 ≤ 0.50, sisko_slider ≤ 0.70) encodes which pitches simply don't work from higher slots — the restriction emerges from pitch viability, not an enforcement gate.

**Demo pitchers given archetypes:**
- F9 S. Okafor (release_angle=0.45): sidearm K-specialist — four_seam + sisko_slider + curve_10_to_2 + changeup
- B9 C. Lindqvist (release_angle=0.15): submarine groundball monster — sinker + walking_slider + spitter + curve_10_to_2

### Validation (500-game tune, full pitch system)
| Metric | Post 11b | Target |
|---|---|---|
| Avg total runs/game | 22.51 | 22–24 ✓ |
| Avg run rate (R/out) | 0.4169 | ~0.43 ✓ |
| Super-inning freq | 5.00% | <5% ≈ |
| League K% | 18.54% | 17–19% ✓ |
| League BA | .280 | .280–.305 ✓ |

Identity invariant preserved (all-neutral inputs: max_diff < 1e-6 vs base table).

---

## Open Items / Follow-ups

- **BB% is at 7.45%** (target 9–10%). Pitcher Command and Stuff-Ball are now both pulling hard. If the league-mean BB% target stands, trim `PITCHER_COMMAND_BALL` from −0.07 → −0.05 OR raise `PITCHER_DOM_BALL` ceiling. But the user's mandate was "talent dictates performance, not the artificial mean," so the stat-line drift may be acceptable.
- **Super-inning frequency 5.20%** (target <5%) — barely over. Likely a downstream effect of pitcher dominance widening the spread of close vs blowout games. Worth a 1–2 pass tune-pass if the target is hard-binding.
- **Roster generation has no per-pitcher rolls for `pitch_variance` or `grit` yet.** The two demo pitchers in `main.py` got values; any procedural league generator (`o27v2.league.py` etc.) needs to roll these from `PITCH_VARIANCE_MEAN` / `GRIT_BOUND_MIN..MAX`.
- **Optional decomposition of Stuff into K-Stuff and Weak-Stuff** still on the table (called out in the original plan as Phase 2). Pitch_variance + grit may make this unnecessary; revisit after a season of stat-line review.

## Files Changed

- `o27/config.py` — dominance scalars, fatigue caps, form noise, new pitch_variance + grit constants
- `o27/engine/prob.py` — `_sample_quality` helper, threaded rng, per-pitch sampling in `_pitch_probs` and `contact_quality`, grit fatigue dampener, lowered floors
- `o27/engine/state.py` — added `pitch_variance` and `grit` fields to `Player`
- `o27/main.py` — extended `_player()` helper, set values on F9 / B9 demo pitchers
- `o27/docs/AAR_pitcher_system.md` — this document
