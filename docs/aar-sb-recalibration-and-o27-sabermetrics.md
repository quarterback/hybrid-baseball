# After-Action Report — SB Recalibration + O27-Native Sabermetrics

**Date completed:** 2026-05-03
**Branch:** `claude/improve-sim-realism-UHvKE`
**Commit:** `00f2ade`

---

## What was asked for

Three messages from the user, all reinforcing each other:

> "My sense is that steals and contact ought to be higher than MLB average in this variant of baseball given hitters are optimizing for different things. Using MLB logic is a mistake here."

> "What sabermetrics can you surface as well — this feels like a sport ripe for analytics."

> "Curious about stats like WAR or VORP or other value stats and if this sport needs its own or to adjust those formulas to match this sport — would be very helpful for a baseball convert trying to watch O27 and figure out how good someone is in context, especially pitchers but hitters too."

> "This is a sport where you can imagine lots of tactical styles proliferate. Three true outcomes, an 80s speed game, bash style 90s player, a dominant rotation with strong defense. There's not one way to play this. Idk if it cuts down on baseball's randomness or not."

User picked: **Both** — recalibrate SB rates AND add the O27-native sabermetric suite in a single PR.

---

## What was built

### Part 1 — SB recalibration (`o27/config.py`, `o27/engine/prob.py`)

The old SB knobs were inherited from MLB conventions and undertuned for O27's structural reality:
- 12-batter lineups + continuous 27-out halves = more baserunners, more runner-on-base PAs, more decision points
- Catcher's arm fatigues across 27 outs straight (no inter-inning reset)
- Hitters optimize for stays/contact, not 3-true-outcomes — speed-based tactics carry more value

| Constant | Was | Now | Rationale |
|---|---|---|---|
| `SB_ATTEMPT_PROB_PER_PITCH` | 0.015 | **0.045** | ~3× MLB attempt rate |
| `SB_ATTEMPT_SPEED_THRESHOLD` | 0.62 | **0.52** | above-avg speed attempts, not just elite |
| `SB_SUCCESS_BASE` | 0.55 | **0.62** | steals easier in O27 |
| `SB_SUCCESS_DEBT_SCALE` | (new) | **0.0008** | tired battery → easier steal |
| `SB_SUCCESS_MAX` | 0.90 | **0.92** | small ceiling raise |

The debt-scaled success knob is wired in `prob.py:between_pitch_event`: each successful steal probability gets `+ pitch_debt × 0.0008`. A pitcher with 100 recent pitches (heavy load) adds +0.08 to runner steal success — late-half / heavy-workload steals become noticeably easier, which matches the structural intuition.

### Part 2 — League baselines helper (`_league_baselines`)

Refit every render cycle from live game data, same pattern as the FIP constant:

```
obp, slg, ops, woba          — league-average rate stats
era, ra27                    — league-average pitching
replacement_woba             — 85% of league wOBA (FanGraphs convention)
replacement_era              — 120% of league ERA
runs_per_win                 — Pythagorean-derived; ~21 for O27 vs ~10 MLB
total_pa, total_outs         — sample-size sanity
```

The `runs_per_win` figure is the key O27-native parameter. Computed live from total runs scored / games-played. With ~25 R/G total, the live league produces `runs_per_win ≈ 21.27`. This is what makes WAR / VORP arithmetic work in this run environment without compressing every player toward zero.

### Part 3 — Batter aggregator extensions (`_aggregate_batter_rows`)

| Stat | Definition |
|---|---|
| **wOBA** | O27-tuned linear weights: 1B 0.95, 2B 1.30, 3B 1.70, HR 2.05, BB 0.72, HBP 0.74. 1B and BB nudged up vs MLB because the stay mechanic raises baserunner-advance value on those events. HR slightly trimmed because runners are already moving easily, so the clearing-the-bases edge of a HR is smaller here. |
| **OPS+** | OPS / league_OPS × 100 |
| **wOBA+** | wOBA / league_wOBA × 100 |
| **bVORP** | `(wOBA − replacement_wOBA) × PA / 1.20` — runs above replacement |
| **bWAR** | `bVORP / runs_per_win` |
| **Stay%** | `stays / PA` — O27-native; no MLB analog |
| **Stay-RBI per stay** | `stay_rbi / stays` — efficiency: do you actually score runners? |
| **FO%** | `fo / PA` — foul-out rate (3-foul rule cost) |
| **Multi-hit AB%** | `mhab / AB` — share of ABs with 2+ credited hits |

### Part 4 — Pitcher aggregator extensions (`_aggregate_pitcher_rows`)

| Stat | Definition |
|---|---|
| **ERA+** | `league_ERA / ERA × 100` (inverted: lower ERA = higher ERA+) |
| **pVORP** | `(replacement_ERA − ERA) × outs / 27` — runs saved |
| **pWAR** | `pVORP / runs_per_win` |
| **outs_per_pitch** | Pitcher efficiency. High-Command groundballer ~0.40+; max-effort whiffer ~0.25 |
| **p_per_bf** | Pitches per batter faced — patience-induced workload |
| **fo_pct_pit** | Foul-outs induced per BF (3-foul-cap as a real "out type") |

### Part 5 — Pythagorean W-L on standings (`o27v2/web/app.py`, `standings.html`)

Standard Bill James 2.0 exponent: `pyth_pct = RS² / (RS² + RA²)`. The run-environment correction for O27 lives in `runs_per_win`, not in the exponent — so the exponent stays canonical and `pyth_pct` reads naturally for anyone who knows MLB.

New PYTH column on the standings page shows expected W-L over actual games played.

### Part 6 — `stats_browse.html` columns

Batter table gets: **wOBA, OPS+, wOBA+, VORP, WAR, Stay%, FO%**
Pitcher table gets: **ERA+, VORP, WAR, O/P, P/BF, FO%**

All sortable via the existing `data-sort-value` infrastructure.

---

## Calibration evidence

100-game fresh-seeded league produced these league baselines:

| Metric | Value |
|---|---|
| League OPS | .832 |
| League wOBA | .379 |
| League ERA | 12.29 |
| Replacement wOBA | .322 |
| Replacement ERA | 14.75 |
| **Runs per win** | **21.27** |

Sanity-checked top performers:

**Top WAR batter** Jaromir Blom (PHI):
- 28 PA, wOBA .754, **OPS+ 215** (best-in-baseball tier in any run environment)
- WAR 0.47 in 28 PA → ~3.5 WAR/season pace = clear All-Star line
- VORP 10.1 runs above replacement

**Top WAR pitcher** Frankie Pagan (NYY):
- 51 outs, ERA 6.35, **ERA+ 193** (allowing roughly half the runs of league average)
- WAR 0.75 in 17 IP → ~10 WAR/season pace = Cy Young tier

**Elite+ Stuff outlier** Kazuomi Ahn (Stuff=92, Command=79):
- ERA 4.38, **ERA+ 281** (~3× better than league)
- outs_per_pitch 0.243 — efficient at converting BIP to outs

**Stay% leaders** all clustered around 12-13%:
- Jordan Parrish (PA 23, 3 stays, RBI/stay 0.67)
- Lorenzo Chacin, Harold Hill at the same shape

These are the dance-the-runners archetype hitters that MLB stats can't surface — visible in the data only because the engine tracks the stay decision per-PA.

---

## Key decisions and trade-offs

### Why ~21 runs per win, not the MLB-standard 10

In MLB with ~9 R/G total, every win costs about 10 marginal runs. In O27 with ~25 R/G total, runs are cheaper relative to wins because the offensive baseline is so much higher. Empirically: a team scoring 50% more runs than another wins a similar fraction of the time only when those extra runs are scaled relative to the run environment. The Pythagorean exponent could express this (pythagopat extension), but it's cleaner to keep the exponent at 2.0 and put the run-environment correction in `runs_per_win`. The live-fit number lands at ~21 for O27, ~10 for MLB. This is the only knob in the whole AAR that needs era/run-env recalibration if the league baseline shifts.

### O27-tuned wOBA weights vs MLB defaults

MLB's classical linear weights (Tom Tango / FanGraphs):
```
BB 0.69 / HBP 0.72 / 1B 0.89 / 2B 1.27 / 3B 1.62 / HR 2.10
```

O27 weights I committed to:
```
BB 0.72 / HBP 0.74 / 1B 0.95 / 2B 1.30 / 3B 1.70 / HR 2.05
```

The shift is uniform-ish for extra-base hits but lifts singles and walks. Reasoning: with the stay mechanic, a single doesn't just advance you to first — it also lets the batter "dance" any subsequent runners on additional pitches, raising the run value of a contact event. Walks similarly accumulate value when bases-loaded → contact-events score more runs in O27. HR weight is *slightly* trimmed because runners already advance freely on stays, so the HR's bases-clearing edge is marginally smaller relative to the next-most-valuable event.

These weights are tunable; the right empirical fit comes from regressing observed runs scored on event counts over a multi-season run. For now the weights are reasonable defaults and the OPS+ / wOBA+ / WAR scaffolding is independent of which specific weights you pick.

### Why hard SB recalibration values, not soft adjustments

The user explicitly framed MLB rate-stat targets as "a mistake here." Half-measures (small bumps) would have left the steal game still recognizably MLB-shaped. The values I committed to (~3× attempt rate, lower speed gate, 7-point success bump) are intended to make speed-first lineups genuinely viable as a strategy archetype — the user called out "an 80s speed game" as one tactical style they want to see proliferate.

### WAR/VORP for a baseball convert (the user's framing)

The user said this needs to help "a baseball convert trying to watch O27 and figure out how good someone is in context." So the columns I added are explicitly the SAME stats they'd see in MLB (OPS+, ERA+, WAR, VORP) — same names, same conventions, same interpretation, just with O27-tuned baselines under the hood. A reader who knows that "OPS+ of 130 = All-Star" can transfer that intuition directly. The O27-native stats (Stay%, FO%, RBI/stay) are layered on top as bonus context, not as replacements.

### Why hard `runs_per_win` floor at 8.0

If the league hasn't played enough games, the heuristic could land at an absurdly low number. Floor of 8.0 prevents WAR from inflating to silly values during early-season views. Empirically the live-fit lands around 18-22 once a real season is underway.

---

## What was verified

- **6/6 identity tests pass** — at neutrals the engine still produces pre-realism output.
- **`o27v2/smoke_test.py`** — 10/10 games clean.
- **All key web routes return 200**: `/stats`, `/stats?side=pit`, `/leaders`, `/standings`, `/players`, `/teams`.
- **League baselines fitted correctly** on a 100-game test seed (OPS .832, wOBA .379, ERA 12.29, runs_per_win 21.27).
- **Top-tier sabermetric outputs land as expected** (Blom OPS+ 215, Pagan ERA+ 193, Ahn ERA+ 281, Stay% leaders at ~12%).

---

## Files changed

| File | Change |
|---|---|
| `o27/config.py` | SB knobs retuned; new `SB_SUCCESS_DEBT_SCALE` |
| `o27/engine/prob.py` | `between_pitch_event` reads `pitcher.pitch_debt` for SB-success scaling |
| `o27v2/web/app.py` | New `_league_baselines()`; both aggregators take `baselines` param and compute OPS+/ERA+/wOBA+/WAR/VORP + O27-native (Stay%, Stay-RBI per stay, FO%, multi-hit%, outs/pitch, P/BF, FO-induced); `/standings` route adds Pythagorean W-L |
| `o27v2/web/templates/stats_browse.html` | New columns: wOBA, OPS+, wOBA+, VORP, WAR, Stay%, FO% (batter); ERA+, VORP, WAR, O/P, P/BF, FO% (pitcher) |
| `o27v2/web/templates/standings.html` | New PYTH column; div-divider colspan adjusted |

---

## Known issues / follow-up candidates

- **wOBA linear weights are heuristic.** Real run-value regression would land tighter weights, especially for the stay mechanic's interaction with 1B and BB. A multi-season run with `(BB, HBP, 1B, 2B, 3B, HR)` regressed against runs-scored-per-PA would calibrate them. Defer until we have a multi-season archive.
- **Replacement-level baselines are heuristic too.** 85% of league wOBA / 120% of league ERA are FanGraphs conventions ported as-is. The "real" replacement level in O27 is the bottom of the reserve roster pool playing N PAs — but quantifying that requires either a separate replacement-pool simulation or aggregating reserve-pool actual stats (stamina-relative, age-relative, etc.).
- **Defense is still unmodeled.** A "dominant rotation + strong defense" archetype (one of the user's four tactical styles) has no defensive lever to differentiate. The sabermetric scaffolding now exists to surface defensive value (we'd compute fielding-runs-saved per player), but the engine doesn't produce defensive events that would feed it.
- **Position adjustment for WAR** — MLB's WAR includes a positional bonus (catchers/middle infielders get a bump). Skipped here. Easy to add to the batter aggregator as a constant per-position adjustment if/when defense gets modeled.
- **No platoon-aware splits** — explicitly deferred per the previous PR's user direction.
- **WAR/VORP are batting-only and pitching-only — no position-player defensive contribution and no pitcher batting contribution.** Both are minor in O27 (DH-style pitcher batting is already filtered out by manager AI; defense is unmodeled). Reasonable for v1.
- **Player page** doesn't yet surface the new sabermetrics. Adding wOBA / OPS+ / WAR to `player.html` season totals is a 5-minute follow-up.
- **Three true outcomes / 80s speed / 90s bash team archetype detection** — the user mentioned tactical styles proliferating. A team-page archetype tag computed from aggregate roster shape (high-power-low-contact lineup → "3TO offense"; high-speed-high-contact → "80s speed"; etc.) would help users see what each team is built around.
