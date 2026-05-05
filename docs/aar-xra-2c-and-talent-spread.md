# After-Action Report — xRA, Second-Chance AB, IA Reorg, Talent Spread

**Date completed:** 2026-05-05
**Branch:** `claude/fix-dark-theme-baseball-terms-7UhIv`

---

## What was asked for

After the wERA / xFIP / Decay / GSc trio shipped (the prior AAR), the user
played with the data and came back with three concrete bugs and a layout
complaint:

1. > "WAR magnitudes are inflated. Top hitters in the 12-15 range over
>    162 games. That's not right."

2. > "Stay% reads at 1.6% league-wide — that's not a percentage, that's a
>    rounding error. Also you've been calling it 'Stay'; my name for it is
>    Second-Chance AB. Use 2C everywhere I can see it."

3. > "Tom Martin's xFIP is **negative 8.81**. xFIP is broken."

Plus an information-architecture pass (cut Standard views, chunk columns,
move League's distribution panels off the headline page) and a footer
removal.

Mid-PR the user pushed back on a separate dimension entirely:

> "the wider distribution talent bump should happen, that's table stakes…
>  this sport should be more offensively dynamic than MLB, stop trying to
>  make this into MLB. the dynamism of offense is a feature not a bug…
>  i don't want to artificially inflate things, but i'm saying the engine,
>  talent should not be clamped"

Which became its own work item.

---

## What was built

### 1. xRA replaces xFIP — the negative-value bug

xFIP's coefficient set is `13·HR + 3·BB − 2·(K + FO)`. In MLB, the
strikeout term is small relative to HR and BB. In O27, with high K-rate
arms over short samples, `2·(K+FO)` regularly overwhelms `13·HR + 3·BB`
and the formula drops below zero — which is mathematically meaningless
for an "expected runs allowed" rate stat.

**Replacement: xRA — Expected Runs Allowed, non-negative by construction.**

```
raw_xRA = (1.4·HR + 0.45·non_HR_hits + 0.32·BB + 0.32·HBP) × 27 / outs
xRA     = raw_xRA × xra_norm
```

- All coefficients are sabermetric-traditional linear-weight run values.
- All non-negative — no event in the formula *subtracts* runs allowed,
  so xRA is bounded ≥ 0 mathematically. No high-K small-sample edge case
  can make the number flip negative.
- `non_HR_hits = max(0, hits - HR)` — singles-equivalent approximation
  since we don't persist 2B/3B at the pitcher level. Acceptable v1; a
  fairer pass would apportion 2B/3B allowed using league rates.
- `xra_norm = league_werra / league_raw_xra` — multiplicative scaler
  refit each render call so league xRA matches league wERA, the same
  anchor xFIP had. **Multiplicative**, not additive `+ C_x`, so the output
  stays non-negative even at extreme small samples.

`_league_werra_consts()` now returns `(c_w, xra_norm, league_outs_per_g)`.
The old `c_x` xFIP constant is gone.

**Verification:**
- League-wide MIN(xra) = 6.43 across 363 qualifying pitchers. Zero
  negative values.
- Outs-weighted league xRA = 11.72 vs league wERA = 11.67 — calibration
  delta 0.046, inside the 0.05 tolerance.
- Tom Martin (the user's reported case) renders as a positive,
  proportional xRA.

A new column **K-BB% = (K - BB) / BF** rides alongside as an
uncalibrated quick-read. Surfaces in the Standard pitching view next
to wERA / xRA — three columns that read together:
- wERA = how he's actually pitched
- xRA  = how he should have pitched given the events
- K-BB% = the underlying rate that drives both

### 2. WAR baselines audit — the doubled-WAR bug

Top hitters were posting 12-15 WAR over 162 games. Trace:

`_aggregate_batter_rows()` and `_aggregate_pitcher_rows()` accept an
optional `baselines` kwarg. Inside, WAR uses `baselines["runs_per_win"]`
to convert run-creation into wins. The fitted RPW for O27's ~25-R/G
environment is ~21. **If `baselines` is `None`**, the code falls through
to a hard-coded MLB default of `runs_per_win = 10`.

Two callsites in `app.py` were calling `_aggregate_batter_rows([row])`
and `_aggregate_batter_rows(batting)` without passing `baselines=`. They
hit the MLB-10 fallback. Result: WAR roughly doubled (21/10 ≈ 2.1×).

**Fix:** hoist `baselines = _league_baselines()` to top of the route
and thread `baselines=baselines` into every aggregator call. Two
callsites patched (lines 971 and 1934).

The RPW formula itself is unchanged — `9 + (R/G_total / 4)^0.5 × 3.5,
min 8`. For ~50 R/G two-team total it lands at ~21.4, which matches
the new measured value.

### 3. Stay → 2C rename, with the percentage reading dropped

User's word for the second-chance AB mechanic is "2C." Engine column
`stays` stays as-is internally; only user-visible labels move.

| Was | Now |
|---|---|
| `Stays` (count column) | `2C` |
| `Stay%` (rate column) | dropped |
| `Stay aggressiveness` (player attribute) | `2C aggressiveness` |
| `STY` (game-log abbrev) | `2C` |
| `Stay rule` (methodology copy) | `Second-chance AB rule` |
| `League Stay%` (Pulse cell) | `League 2C/G` (per-game count) |

The `Stay%` rate display was hitting ~1.6% league-wide, which reads as
a rounding error, not a percentage. Counting `2C` (and the per-game
`2C/G` Pulse cell) communicate the mechanic at the right magnitude.

### 4. Information-architecture pass

**(a) Standard views cut to 10 columns each.** Locked column lists:

```
Standard Batting:  G  PA  H  HR  RBI  BB  SO  PAVG  OPS  OPS+
Standard Pitching: G  GS  W  L  Outs K  BB  wERA  xRA  K-BB%
```

WAR moves to Advanced (it's a derived value metric, not a counting/rate
stat). 2C / FO / SB / CS / HBP / UER move to Advanced and All. The
result is a Standard view that reads at-a-glance for "is this player
producing or not" without the wide-table-on-mobile problem.

**(b) Visual chunking: bold key columns + extra padding between groups.**
A new `.col-group-end` CSS class adds `padding-right: 16px` and a soft
right-border on the last column of each conceptual group: counting →
rate → value. Applied in stats / leaders / players / team / league /
player tables. PAVG / OPS / OPS+ for batting; wERA / xRA / pWAR for
pitching are consistently `fw-bold` so the eye lands on them first.

**(c) League page split: distributions move to /distributions?scope=teams.**
The League page tried to be both headline-summary AND detail-explorer.
Split it: League page keeps Pulse + Standings + link buttons. The
Distribution-quartile table and Outlier panels move to a new
`?scope=teams` view on `/distributions`. The Distributions page gets
a Players/Teams pill toggle at top (Players default, Teams opt-in via
the pill). On the Teams scope the player-highlight form is hidden,
since highlights are only meaningful for individuals.

**(d) Footer line removed.** The "single 27-out innings · pesäpallo-
influenced stay rule · 3-foul out cap" editorial line is gone, replaced
with a minimal nav strip (League / Teams / Stats / Leaders /
Distributions). The Stay→2C rename also covered the footer text in one
sweep.

### 5. Engine tuning bump — stay-aggressiveness

The 2C mechanic was firing at ~1.6% of PAs, target 4-8%. Two knobs in
`o27v2/league.py`:

| Attribute | Position players | Pitchers-as-batters |
|---|---|---|
| `stay_aggressiveness` | gauss(0.10, 0.05) → **gauss(0.30, 0.10)** | gauss(0.05, 0.03) → **gauss(0.20, 0.06)** |
| `contact_quality_threshold` | gauss(0.28, 0.06) → **gauss(0.50, 0.10)** | gauss(0.20, 0.05) → **gauss(0.40, 0.08)** |

Post-resim measured rate: **5.65%** of PAs trigger a 2C, dead in the
target band. The mechanic now reads as a real feature in the box score
and the per-game arc instead of a rounding-error footnote.

### 6. Wider talent distribution — the user's pushback

After the WAR fix, the league's top hitter landed at OPS+ 125 / WAR 4.7.
Plan target was 6-9 WAR. The arithmetic was correct — the league's top
end of *talent* was just compressed.

The user's response settled the framing:

> "I don't want talent compression. I've said from the start that I want
>  more talent. Increase the elite tier to 2%, the next highest tier to
>  8%, and the good-to-average tier to 25%."

> "This sport should be more offensively dynamic than MLB. Stop trying to
>  make this into MLB."

The 10-tier `_TALENT_TIERS` table in `o27v2/league.py` is rewritten:

| Tier | Was | Now | Grade range |
|---|---|---|---|
| Elite+        | 0.5% | 0.5% | 81-95 |
| Elite         | 2.0% | 1.5% | 75-80  *— Elite+/Elite combined = 2%* |
| Excellent     | 5.0% | **8.0%** | 65-74  *— "next highest" = 8%* |
| Very Good     | 10.0% | 12.0% | 60-64 |
| Good          | 15.0% | 12.0% | 55-59  ┐ |
| Above Avg     | 18.0% | 8.0%  | 50-54  ├ good-to-average = 25% |
| Average       | 19.5% | 5.0%  | 45-49  ┘ |
| Below Avg     | 15.0% | 18.0% | 40-44 |
| Replacement   | 10.0% | 20.0% | 30-39 |
| Sub-Replacement | 5.0% | 15.0% | 20-29 |

Both tails widen — top *and* bottom — while the soft-middle "good to
average" band tightens. This produces the offensive-dynamism shape O27
wants: elite contact bats stack their independent attribute rolls into
genuine outliers, and the long bottom tail supplies the below-replacement
arms those bats feast on. **Top batter post-resim: Yannick Achebe, 35
HR / 290 RBI / 1.071 OPS / OPS+ 132 / WAR 6.21.** Top pitcher: Roger
Rice, pWAR 6.16.

The 290 RBI is the right number for this sport. In MLB the leader sits
~140. O27's 27-out continuous innings pile up RBI opportunities for the
elite bats sitting in front of the long talent tail — that's the
feature, not a bug.

---

## What's still on the table (out of scope for this PR)

- **PA-by-lineup-slot view.** User flagged that leadoff hitters should
  be racking up ~5+ PA/game and 9-spot ~3. We don't surface this
  anywhere yet. Future addition: a per-team-page panel showing each
  lineup-slot's PA/G average.
- **Decay distribution shape sanity-check.** If the histogram on
  `/distributions` shows everyone clustered at 0-10, pitcher fatigue
  isn't producing meaningful late-arc degradation. Self-checkable now
  that the page exists.
- **2B/3B-allowed apportionment in xRA.** v1 collapses extra-base hits
  into singles-equivalent. Fairer to apportion using league rates for
  doubles and triples allowed.
- **WAR-formula re-tune for offensive dynamism.** WAR with RPW=21 still
  compresses elite hitters into a 4-7 range even with the wider talent
  spread, because the high R/G environment converts run-creation into
  wins at a discount. If the user wants top WAR back at 8-12 range, the
  RPW formula needs its own pass — separate from talent shape, separate
  from the baselines audit that fixed the MLB-fallback bug.

---

## Files touched

| File | Changes |
|---|---|
| `o27v2/web/app.py` | xRA formula + xra_norm in `_league_werra_consts`; K-BB% in pitcher aggregator; baselines hoisted in `players()`; two missing-baselines callsites patched; `_league_team_aggregate()` extracted; `/distributions` extended with `?scope=teams` branch |
| `o27v2/league.py` | `_TALENT_TIERS` rewritten (wider tails, tighter middle); `stay_aggressiveness` and `contact_quality_threshold` defaults bumped for batters and pitchers-as-batters |
| `o27v2/web/templates/base.html` | `.col-group-end` CSS; footer line replaced with nav strip |
| `o27v2/web/templates/stats_browse.html` | Standard cut to 10 cols; xfip→xra; Stay→2C; chunking applied |
| `o27v2/web/templates/leaders.html` | xFIP card → xRA; Stay% card → 2C; methodology rewrite |
| `o27v2/web/templates/league.html` | Pulse Stay% → 2C/G; team-table column rename; Distributions/Outliers blocks → link buttons |
| `o27v2/web/templates/distributions.html` | Players/Teams pill toggle; team-scope quartile + outlier panels |
| `o27v2/web/templates/player.html` | Stay → 2C across bio + advanced + splits + game log; xFIP → xRA in Advanced + tooltips |
| `o27v2/web/templates/players.html` | xFIP → xRA in pitcher list |
| `o27v2/web/templates/compare.html` | Stays → 2C; xFIP → xRA |
| `o27v2/web/templates/team.html` | xFIP → xRA |
| `o27v2/web/templates/season_detail.html` | xFIP → xRA in archived season block |
| `o27v2/web/templates/game.html` | STY column → 2C in box-score batting tables |
| `o27v2/web/text_export.py` | Stays → 2C; xfip → xra; K-BB% added in player-card pitching block |

---

## Verification numbers (post resim + backfill)

| Check | Target | Actual |
|---|---|---|
| Games played | 2430 | 2430 ✓ |
| 2C rate (stays / PA, league-wide) | 0.04 - 0.08 | **0.0565** ✓ |
| xRA min across qualifying pitchers | ≥ 0 | **6.43** ✓ |
| xRA calibration (outs-weighted xRA - wERA) | ≤ 0.05 | **0.046** ✓ |
| RPW (fitted) | ~17-21 for ~25 R/G | **21.41** ✓ |
| Standard batting columns | 10 | 10 ✓ |
| Standard pitching columns | 10 | 10 ✓ |
| All routes return 200 | yes | 12/12 routes 200 ✓ |
| League page Distribution + Outlier blocks | removed | gone ✓ |
| Footer editorial line | removed | gone ✓ |
| Talent: elite combined band (≥75 grade) | 2% | **1.7%** ✓ |
| Talent: excellent band (65-74) | 8% | **8.5%** ✓ |
| Talent: good-to-average (45-59) | 25% | **24.1%** ✓ |
| Top batter line | offense-dynamic | **Achebe: 35 HR / 290 RBI / 1.071 OPS** ✓ |
