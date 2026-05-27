# After-Action Report — Times-Through-the-Order Familiarity & the Softball/Underhand Arsenal

**Date completed:** 2026-05-27
**Branch:** `claude/sports-arbitrage-pitcher-mechanics-vVNgT`
**Commits:** `6274a12` (times-through-the-order familiarity), softball-arsenal commit (this AAR)

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

## Engine-modeling limitations (deliberately not faked)

The design brief described per-pitch trajectory physics the catalog **cannot currently represent**, and we chose to model honestly with the levers that exist rather than invent capabilities:

- **No per-pitch launch-angle field.** The batted-ball model derives launch angle from `hit_type`, and a pitch's only batted-ball lever is contact quality (weak/medium/hard → EV, via `hard_contact_shift`). So "induces grounders" (`peeled_drop`, `drop_knuck`) vs "induces popups" (`riseball`, `rise_knuck`) **both reduce to weak contact + EV suppression** — the grounder/popup distinction is flavor the engine can't yet separate.
- **No foul-out-specific target.** `k_delta` adds to swinging strikes; there's no foul-share delta, so "spikes foul_out%" is approximated as elevated whiff + weak contact.
- **No per-pitch movement variance** keyed to weather. The "knuckle-eephus catches every micro-current" idea has no hook; we folded that concept into the existing high-resistance / high-`bb_delta` knuckle variants rather than add a fake field.
- **Consolidation.** The brief's "underhand spinner" eephus is mechanically identical to `peeled_drop` (topspin → weak grounders) under the available levers, so it was not added as a separate redundant entry.

These are the natural next pieces of work if the designer wants the grounder/popup split to be real: a per-pitch `launch_angle_bias` feeding `sample_batted_ball`, and a foul-share delta in `_pitch_probs`.

---

## Calibration stance

Per explicit instruction, **no offsetting run-environment tune was applied.** The familiarity penalty raises late-arc offense and lowers effective wERA for deception arms; both are intended. The only gate retained is the stat-invariant suite, which checks for mathematically-impossible stats, not run levels.

The magnitudes (`FAMILIARITY_*`, the per-pitch `timing_resistance` values) were set by reasoning, not a season-scale calibration sweep, so the *size* of the junkball edge over a full 162 is an estimate. `FAMILIARITY_PER_LOOK` is the single dial to scale the whole effect up or down.

---

## Verification

- `python o27v2/manage.py smoke` — 10/10 games complete.
- `tests/test_realism_identity.py`, `o27/tests/`, `o27v2/tests/test_archetypes.py` — pass (identity preserved at first look).
- Full suite — 240 passed (1 pre-existing unrelated failure: `test_season_archive_writer` / `wrc_plus`, fails identically on the clean tree).
- Stat-invariant gate against a freshly-simmed 8-team DB — 10/10.
- Sim confirms the new pitches are thrown in real games (slither_knuck, drop_knuck, backhand_changeup, rise_knuck, peeled_drop, sky_eephus all present) and the new archetypes generate.

---

## Files touched

- `o27/config.py` — `timing_resistance` on all pitches; `FAMILIARITY_*` constants; 7 new catalog entries.
- `o27/engine/state.py` — `matchup_pa` + `matchup_count()`.
- `o27/engine/pa.py` — matchup counter tick at PA close.
- `o27/engine/prob.py` — repertoire→resistance, familiarity-dominance, threaded into both outcome models.
- `o27/data.py` — 3 new archetypes + `stamina_bonus` support.
- `o27v2/sim.py` — pitch-mix bucket classification for the new pitches.
- `o27v2/web/formatters.py` — display-name overrides.
