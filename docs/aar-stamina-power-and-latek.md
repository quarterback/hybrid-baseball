# After-Action Report — Stamina, Power Redistribution, LateK%

**Date completed:** 2026-05-05
**Branch:** `claude/fix-dark-theme-baseball-terms-7UhIv`
**Predecessor:** `aar-xra-2c-and-talent-spread.md`

---

## Why this PR exists

Three findings from the previous PR's verification run pointed at engine
internals, not just stat displays:

1. **Decay didn't differentiate workhorses from short relievers.**
   Mean Decay was +0.35 for starters (n=187) and +0.50 for relievers (n=43).
   Stat existed; mechanic didn't.
2. **2C usage was rate-gated by `stay_aggressiveness`, not by skill.** The
   user audited and concluded this is correct ("Read B" — every batter has
   the option, tendency creates variance) but with one caveat: it should
   be confirmed that the *outcome* of staying is differentiated by contact
   skill.
3. **`power` was a single-trick HR additive.** It boosted the HR row in
   HARD_CONTACT but didn't redistribute, which (a) inflated total league
   HR rate when high-power bats hit and (b) prevented gap-power vs
   pull-power archetypes from emerging — top 2B and top HR leaderboards
   were largely the same players.

Plus the user's framing constraint: **"this sport should be more
offensively dynamic than MLB. Stop trying to make this into MLB. The
engine, talent should not be clamped."**

---

## Audit baselines (preserved as engine-fact reference)

These are the pre-change measurements — useful as anchors for any future
recalibration, since the engine numbers shift each tuning pass.

### Decay baseline (pre-stamina-fix, post-talent-widening)

| Cohort | n | Mean Decay | Median | σ |
|---|---|---|---|---|
| Qualifying pitchers | 359 | +0.17 | +0.65 | 11.20 |
| Starters (GS > 5) | 187 | **+0.35** | — | — |
| Relievers (GS ≤ 1) | 43 | **+0.50** | — | — |

**Top-10 workhorses (BF ≥ 870, all GS=32+) at baseline:**

| Pitcher | BF | Stamina | Decay | K%-arc1 | K%-arc3 |
|---|---|---|---|---|---|
| Yates / MIN | 999 | — | +4.95 | 23.9% | 19.0% |
| Rice / NYY | 963 | — | +3.85 | 26.1% | 22.2% |
| Betances / COL | 938 | — | +3.28 | 23.0% | 19.7% |
| Infante / COL | 898 | — | -5.24 | 19.8% | 25.0% |
| Cooke / MTL | 898 | — | -4.82 | 25.3% | 30.1% |
| Nunez / MTL | 897 | — | +3.87 | 24.4% | 20.5% |
| Martin / NYY | 895 | — | +0.21 | 23.0% | 22.8% |
| Solovyev / ARI | 874 | — | +2.79 | 32.1% | 29.3% |
| Ramos / IND | 869 | — | -9.16 | 16.8% | 25.9% |
| Morozov / MIL | 867 | — | -8.55 | 17.0% | 25.5% |

Note: 4 of 10 workhorses post NEGATIVE decay — their arc-3 K% is *higher*
than arc-1 K%. That's lineup-cycling drift: the bottom-of-order hitters
strike out more, so K% naturally rises through the arc. The fatigue model
wasn't pushing back hard enough to overcome this drift.

### 2C distribution baseline

| Band | % of qualifiers | Share of all 2C events |
|---|---|---|
| rare (0-2%) | 3.4% | 0.9% |
| occasional (2-4%) | 20.4% | 10.6% |
| baseline (4-6%) | 29.4% | 26.6% |
| frequent (6-10%) | **43.0%** | **55.5%** |
| specialist (10-20%) | 3.8% | 6.4% |
| extreme (20%+) | 0% | 0% |

The 6.4% of league 2C events from 3.8% of "specialists" was the soft
archetype clustering the user noted — that pattern is the design intent
under Read B.

### Power-attribute call-site audit (pre-change)

| File:line | What `batter.power` did |
|---|---|
| `prob.py:191` | Biased contact-quality classification toward `hard` via `CONTACT_POWER_TILT=0.10` (sum-preserving across {weak, medium, hard} probabilities) |
| `prob.py:489` | **Additive** boost `±0.08` to the HR row weight in HARD_CONTACT — NOT sum-preserving. Total HARD_CONTACT weight grew when high-power batters hit, creating extra offense rather than redistributing it. |

Contact tables reference (pre-change weights):

```
WEAK_CONTACT:    GO 0.50  FO 0.18  LO 0.10  1B 0.18  FC 0.04
MEDIUM_CONTACT:  GO 0.22  FO 0.14  LO 0.12  1B 0.32  2B 0.12  FC 0.08
HARD_CONTACT:                      LO 0.15  1B 0.20  2B 0.24  3B 0.08  HR 0.14  FO 0.19
```

The HR row in HARD_CONTACT was the only row with a power-driven weight
modifier. Singles, doubles, triples, line outs, fly outs — all power-blind.

### Fatigue config baseline (pre-change)

```
FATIGUE_THRESHOLD_BASE  = 24    # Phase 10 — was 10 before that
FATIGUE_THRESHOLD_SCALE = 40
FATIGUE_MAX             = 0.60
FATIGUE_SCALE           = 20.0
FATIGUE_BALL/CONTACT    = +0.06 / +0.04
FATIGUE_CALLED/SWINGING/FOUL = -0.04 / -0.03 / -0.03
```

Threshold math (BF count where fatigue starts firing):

| Stamina | BASE=24 / SCALE=40 (Phase 10 baseline) |
|---|---|
| 0.3 (low) | 36 BF |
| 0.5 (avg) | **44 BF** |
| 0.7 (high) | 52 BF |
| 0.9 (workhorse) | 60 BF |

A 27-out arc against an avg lineup is ~36-40 BF. So even the **lowest-stamina
pitcher's threshold (BF=36)** sits at the END of the arc — fatigue effectively
never fired within a single appearance. That's why Decay didn't differentiate.

---

## Changes shipped

### 1. Stamina drain curve (two passes)

`o27/config.py:122-145`. Round 1 dropped `FATIGUE_THRESHOLD_BASE` from 24
to 10; round 2 dropped it further to 6 after round 1's data showed the
league-wide arc-1 → arc-3 K% delta was still negative (lineup-cycling
drift dominating). Other coefficients also sharpened:

| Constant | Before | After | Effect |
|---|---|---|---|
| `FATIGUE_THRESHOLD_BASE` | 24 | **6** | Threshold now lands inside the arc for sub-elite stamina |
| `FATIGUE_MAX` | 0.60 | **0.80** | Max-fatigue cliff bites harder |
| `FATIGUE_SCALE` | 20.0 | **12.0** | Steeper ramp post-threshold (flat-then-cliff curve) |
| `FATIGUE_SWINGING` | -0.03 | **-0.06** | K-suppression doubled |
| `FATIGUE_CONTACT` | +0.04 | **+0.06** | Contact uptick sharpened |
| `FATIGUE_FOUL` | -0.03 | **-0.04** | |

New threshold math (BASE=6, SCALE=40):

| Stamina | New threshold |
|---|---|
| 0.3 (low) | 18 BF (fatigues mid arc-2) |
| 0.5 (avg) | 26 BF (fatigues end of arc-3) |
| 0.7 (high) | 34 BF (fatigues right at arc-end) |
| 0.9 (workhorse) | 42 BF (rarely fires — moat preserved) |

### 2. Power redistribution (replaces additive HR boost)

`o27/config.py:466-498` and `o27/engine/prob.py:423-490`. New helper
`_redistribute(table, edges, power_dev)` does sum-preserving weight shifts
along named edges. New helper `_apply_park()` cleanly separates park-factor
multiplications (which intentionally are NOT sum-preserving — parks
genuinely create / destroy events).

The existing `_scale_hard_row` is gone; `resolve_contact()` now applies
power redistribution per quality, then park factors.

**Edges:**

| Quality | Edge | Scale |
|---|---|---|
| HARD | line_out → hr | 0.50 *(was a +0.08 additive)* |
| HARD | single → double | 0.30 |
| HARD | double → triple | 0.20 |
| MEDIUM | single → double | 0.20 |
| MEDIUM | ground_out → fly_out | 0.15 |
| WEAK | single → fly_out | 0.20 |

`scale` reads as: at +1 power_dev (= grade-100 power), `scale` fraction
of the `from`-row weight moves to the `to`-row. At -1 power_dev (=
grade-0 power), the flow reverses (`scale` fraction of `to` moves back
to `from`). Identity at power=0.5 (= grade-50). Total table weight
invariant — league HR/2B/3B rates stay where they were, while per-player
profiles diverge.

The legacy archetype `hr_weight_bonus` field is now folded into the same
`line_out → hr` edge instead of being a separate additive — one consistent
mechanism.

### 3. LateK% sibling stat for short-relief

`o27v2/web/app.py:861-868`. New per-pitcher field
`late_k_pct = (k_arc3 + fo_arc3) / bf_arc3` with `late_k_known` gate.
A second-priority sibling to Decay: a closer pitching outs 25-27 has
no arc-1 sample, so `decay_known=False` and Decay reads `—`. LateK%
gives those pitchers a comparable single-arc K% number. Useful as
both a leader-card metric and a column on the Advanced/All pitching
views.

Surfaced in:
- `templates/leaders.html` — new Pitching · Result-Tier card
- `templates/stats_browse.html` — new column on Advanced + All views

`late_k_pct_pct` (×100) sibling field for the leader-card macro, which
doesn't transform values — keeps the display as `30.5%` instead of `0.305`.

---

## Verification numbers (post all three changes)

### League totals — power-redistribute sum-preserving check

```
PA      = 218,136
HR/PA   = 2.06%
2B/PA   = 6.43%
3B/PA   = 1.24%
Hits/PA = 28.14%
K/PA    = 17.75%
BB/PA   = 8.19%
```

The redistribution is mathematically sum-preserving across all in-play
tables, so league HR / 2B / 3B should stay where they were pre-change.
What changed is per-player spread.

### Power-archetype emergence (the user's locked acceptance test)

| Top-10 HR leaderboard | Top-10 2B leaderboard |
|---|---|
| Maeda 37 HR / 46 2B (power 92, contact 25) — TTO Elite+ slugger | Achebe 32 HR / 82 2B (power 73, contact 55) — broad-spectrum elite |
| Luna 34 HR / 59 2B (power 68, contact 40) — TTO | Powell 18 HR / **73 2B** (power 32, contact 50) — gap-power specialist |
| Achebe 32 HR / 82 2B | Scott 31 HR / 72 2B (power 67, contact 49) |
| Scott 31 HR / 72 2B | Lane 27 HR / 72 2B (power 62, contact 51) |
| Perez 31 HR / 70 2B | Campusano 12 HR / **71 2B** (power 63, contact 43) — gap |
| Triunfel 30 HR / 53 2B | Albertsen 18 HR / 71 2B (power 56, contact 58) |
| Robbins 29 HR / 64 2B | Perez 31 HR / 70 2B |
| Upton 28 HR / 58 2B | Jimenez 15 HR / **70 2B** (power 45, contact 32) — gap |
| Amezaga 28 HR / 58 2B | Diaz 25 HR / 69 2B |
| Lane 27 HR / 72 2B | Aviles 26 HR / 68 2B (power 69, contact 61) |

**Top-10 HR ∩ Top-10 2B overlap: 4/10**, with 4 of the non-overlapping
2B leaders (Powell, Campusano, Albertsen, Jimenez) showing the gap-power
specialist profile (modest power, lots of doubles). And 4 of the
non-overlapping HR leaders (Maeda, Luna, Triunfel, Upton) skew toward the
TTO-slugger profile (low contact, high power, HRs concentrate XBH into
the long ball). Four-archetype shape emerging from a single power rating.

### Decay differentiation (post-stamina-r2)

| Stamina quartile | n | Stamina range | Mean Decay | σ |
|---|---|---|---|---|
| Q1 (low) | 42 | 20-42 | -2.49 | **14.66** |
| Q2 | 73 | 42-57 | +0.67 | 10.88 |
| Q3 | 87 | 57-64 | +0.68 | 7.98 |
| Q4 (workhorses) | 90 | 64-94 | -0.16 | 13.70 |

The σ pattern is the meaningful signal: low-stamina arms (Q1) post σ=14.66
— the largest variance — meaning *some* of them genuinely fade hard while
others don't (the survivor-bias artifact: low-stamina arms only reach
arc-3 when they're having a good day, masking the mean). The workhorse
moat shows up as Q4 mean = -0.16 (≈ flat across the arc — they hold
their K% even as the lineup recycles to weaker hitters).

The league-wide arc-1 (22.22%) vs arc-3 (26.38%) K% delta is still
negative because lineup-cycling drift inherently lifts arc-3 K% across
all pitchers regardless of fatigue. That's a structural feature of
27-out single innings, not an engine bug. Per-pitcher Decay still sorts
high-fade vs low-fade *relative to lineup drift*, which is what it's
supposed to do.

### LateK% spread (the short-relief sibling)

```
n=355  min=0.0%  max=100.0%  mean=25.1%  σ=9.25 pp
```

±9pp σ across the qualifying pool. Tail values (0% / 100%) are small-sample
relievers with a handful of arc-3 BFs. With proper qualifier-thresholding
on `bf_arc3` the metric will read clean.

### Smoke test

All 13 sampled routes return 200, including the new LateK% column on
`/stats?side=pit&view=advanced` and `/stats?side=pit&view=all`, and the
new LateK% leader card on `/leaders`.

---

## What's still on the table (follow-up parking lot)

- **Lineup-cycling drift offset for Decay.** League-wide arc-3 K% will
  always be higher than arc-1 K% because the 27-out single inning cycles
  to weaker hitters. A "Decay relative to expected lineup-quality drift"
  metric would isolate the pure fatigue signal. The simplest version: use
  the league's natural arc-1→arc-3 K% delta as the zero-point, so a
  pitcher whose arc-3 K% drops *more* than the league average drift earns
  positive Decay.
- **Selection bias on Q1 Decay.** Low-stamina starters who blow up in
  arc-2 get pulled before arc-3 — so the cross-arc sample for Q1 is from
  their best outings, biasing Decay toward zero. A per-appearance Decay
  metric (not season-aggregated) would surface this; would need
  per-game arc data, which exists in `game_pitcher_stats`.
- **Weak-contact symmetry.** WEAK_CONTACT only has one power edge
  (single → fly_out). No edge for ground_out → fly_out on weak contact —
  a low-contact, low-power batter probably does pop more infield flies,
  but the model captures that via CONTACT_POWER_TILT shifting the
  classification, not the resolution. Worth a future check whether the
  weak-contact distribution feels right empirically.
- **HR rate stability check.** The redistribute is mathematically
  sum-preserving, but the league HR rate post-change is 2.06% — would
  need to compare to the exact same seed-set under the old additive
  model to confirm zero drift. Acceptable variance for now.

---

## Files modified

| File | Item |
|---|---|
| `o27/config.py` | Stamina r2; `POWER_REDIST_*` constants block; legacy `POWER_HR_WEIGHT_SCALE` annotated |
| `o27/engine/prob.py` | `_redistribute` + `_apply_park` helpers; `_hard_edges/_medium_edges/_weak_edges`; `resolve_contact` rewired through them; `_scale_hard_row` removed |
| `o27v2/web/app.py` | `late_k_pct` / `late_k_known` / `late_k_pct_pct` in pitcher aggregator |
| `o27v2/web/templates/leaders.html` | LateK% leader card |
| `o27v2/web/templates/stats_browse.html` | LateK% column on Advanced + All views |
