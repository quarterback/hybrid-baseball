# Feature Report — Pitching Stats Second Pass

**Date:** 2026-06-01
**Branch:** `claude/sharp-carson-oLd4e`
**Scope:** `o27v2/web/app.py`, `o27v2/web/glossary.py`, `docs/stats-reference.md`

---

## Summary

A second look at the per-game pitching suite produced three changes: a fix to
**wERA** so short outings stop lying, a new fielding-independent per-game
index — **FOP (Fielding-Omitted Pitching)** — on a 0–100 scale, and an
Out-Share-native workload number (**Game Equivalents**). Together they close a
real gap — O27 had no single, bounded, defense-independent number for "how well
did this arm pitch, game by game" — and they remove a long-standing small-sample
distortion in the headline run-prevention stat.

| Stat | Status | Scale | One-liner |
|---|---|---|---|
| **wERA** | Changed | ERA scale | Arc-weighted ERA, now shrunk toward league for short outings |
| **FOP** | New | 0–100, 50 = avg | Fielding-Omitted Pitching — fielding-independent per-game pitching index (the DIPS sibling of Game Score) |
| **DIPS-ERA** | New | ERA scale | FO-inclusive fielding-independent ERA that FOP is built from |
| **Game Equivalents (GE)** | New | count | Total workload in complete-game-worths (`outs / 27`) |

---

## 1. wERA — normalized so partial outings don't lie

### Problem
wERA was an arc-weighted ER total projected to a per-27-out rate:

```
wERA = (0.85·ER_arc1 + 1.00·ER_arc2 + 1.20·ER_arc3) × 27 / outs × C_w
```

The `× 27 / outs` term extrapolates any outing to a full game. For starters
that's fine, but for short relief it explodes: a closer who allows one arc-3
earned run in 3 outs projects to a **~10.8 wERA** — and the arc-3 weight
(1.20) compounds it. The headline run-prevention stat, plus everything
derived from it (wERA+, VORP, WAR, the XO z-anchor), inherited that noise.

### Fix
Regress each pitcher's weighted-ER rate toward the league mean with an
empirical-Bayes prior worth **one arc (9 outs) of league-average ball**:

```
wERA = (adj_wER + (league_wERA / 27)·9) × 27 / (outs + 9)
```

where `adj_wER = weighted_er × C_w`.

### Why it's safe
- A pitcher performing at exactly the league rate returns **exactly league
  wERA at any out-count**, so the league anchor — and therefore `wERA+ = 100`
  meaning "league average" — is preserved by construction.
- Full starter lines barely move (the 9-out prior is small next to 24–27
  outs); short outings regress toward the mean instead of blowing up.
- `prior == 0` (empty DB, or callers that don't pass baselines) falls back to
  the exact legacy formula, so nothing regresses in those paths.

### Verified behavior (league wERA = 4.00, C_w = 1)
| Outing | Old wERA | New wERA |
|---|---|---|
| Closer: 3 outs, 1 arc-3 ER | 10.80 | **5.70** |
| Starter: 27 outs at league rate | 4.00 | **4.00** (anchor held) |
| SP: 24 outs, 2 mid-arc ER | 2.25 | **2.73** |
| Reliever: 6 outs, 0 ER | 0.00 | **2.40** (regressed toward league) |

> **Note:** This is a behavior change to an existing stat. wERA, wERA+, VORP,
> WAR, and XO-anchored values for short-outing pitchers will shift (toward
> sanity). League means and full-season starter values are effectively
> unchanged.

---

## 2. FOP (Fielding-Omitted Pitching) — the per-game pitching index we lacked

### Gap it fills
None of the existing stats was simultaneously **per-game**, **bounded**,
**fielding-independent**, and **normalized to a fixed scale**:

- **Game Score** is bounded and per-game but **results-based** (uses hits and
  runs), so defense and BABIP luck leak in.
- **FIP** is fielding-independent but **unbounded** and on the open-ended ERA
  scale.
- **wERA / xRA** are rates, not single-number per-game indices.

FOP is the missing quadrant: the DIPS philosophy of FIP delivered on a fixed
0–100 scale. The name reads as a blunt classic-baseball acronym, and the **O**
explicitly nods to O27's native foul-outs, which FOP counts as true-outcome
strikeout equivalents.

### Definition
```
DIPS-ERA = (13·HR + 3·(BB+HBP) − 2·(K+FO)) / IP + C_dips
FOP      = 100 / (1 + (DIPS-ERA / league_ERA) ^ 2.2)      # clamped to [0, 100]
```

- **Inputs are true outcomes only** — strikeouts + foul-outs, walks + HBP,
  home runs. Defense and ball-in-play luck never touch it. Foul-outs are
  credited as strikeouts, consistent with O27's K% convention (plain MLB FIP
  ignores them).
- **`C_dips`** is a new league constant (`_league_dips_constant()`) chosen so
  league DIPS-ERA equals league ERA — which makes a league-average pitcher
  map to **exactly FOP 50**.
- **The logistic squash** is what bounds it: a dominant 2-out cameo caps near
  100 instead of extrapolating off the chart, and a disaster floors near 0.
  Small samples are bounded by design rather than projected.
- **Steepness `2.2`** sets the spread: half the league DIPS-ERA ≈ 99, double
  ≈ 14.

### Verified behavior (league ERA = 4.00)
| Game | FOP |
|---|---|
| League-average full game | **50.0** (anchor held) |
| Half the league DIPS-ERA (dominant) | 98.7 |
| Double the league DIPS-ERA (rough) | 14.4 |
| Clean 3-out cameo (3 K, nothing else) | 100.0 |
| Zero outs (no data) | 50.0 (neutral) |

### Where it's computed
Single-sourced through `_pitcher_fop()` so the **season aggregate**
(`_aggregate_pitcher_rows`) and the **box-score game log**
(`_top_pitcher_outings`) produce identical values from the same inputs. The
underlying `dips_era` is exposed alongside it as the ERA-scale companion.

---

## 3. Game Equivalents — Out-Share-native synthetic workload

### Context
Out Share already serves as O27's innings-pitched replacement. The ask was a
"synthetic IP" derived from it. Worth noting: **`outs / 3` already equals IP**,
because a complete O27 game is 27 outs = 9 IP — so a per-appearance synthetic
IP would be mathematically identical to the existing `ip` column and add
nothing.

### What was added instead
**Game Equivalents (GE) = `total_outs / 27`** — season workload expressed in
complete-game-worths. A starter who threw 240 outs logged ~8.9 game
equivalents. This is the number that actually adds information on top of raw
outs, and synthetic IP, if ever surfaced, is simply `GE × 9`.

---

## Documentation & surfaces

- **Glossary** (`o27v2/web/glossary.py`): entries for `fop`, `dips_era`, and
  `game_equiv`.
- **Stats reference** (`docs/stats-reference.md`): wERA formula row updated;
  FOP, DIPS-ERA, and Game Equivalents rows added to the per-game and workload
  sections.
- **Distributions view** (`/distributions?scope=players`): FOP and GE added to
  the pitcher distribution panels.

---

## Validation

- Syntax + import checks pass; the `tests/test_new_stats.py` suite is green.
- Both anchors verified numerically: a league-average pitcher maps to wERA =
  league wERA and FOP = 50.0 exactly.
- Two failures visible in CI (`wrc_plus` batting-leader assertion; sqlite
  "no such table" invariant errors) **pre-exist on the base commit** — they
  require a seeded database and are unrelated to this change (confirmed by
  stashing the edits and re-running).

---

## Not in scope (candidates for follow-up)

- **Leaderboard archive** — registering FOP as a saved/sortable category in
  `season_pitching_leaders` needs a DB column and migration.
- **Per-appearance FOP average** — an outs-weighted mean across games (the way
  `gsc_avg` is computed via a per-row path) rather than the from-aggregate
  value.
- **Dial tuning** — the FOP steepness (`2.2`) and the wERA shrinkage prior
  (`9.0` outs) are deliberate, tunable choices; easy to adjust if a flatter/
  steeper FOP or harder/softer wERA regression is wanted.
