# Cricket-Stat Ports for O27

This note audits three cricket analytics concepts against the current O27 stat
surface and sketches the smallest useful ports where the game does not already
have an equivalent.

## Existing nearby O27 equivalents

| Cricket concept | O27 status | Current surface |
| --- | --- | --- |
| Match Impact / True Strike Rate | Partly exists | The v2 analytics stack already stores per-PA before/after state and exposes WPA/LI-style pressure context from the PA log. That is close to Match Impact, but it is win-probability framed rather than chase-requirement framed. |
| Dot Ball Percentage | Partly exists | Pitching already tracks strikeouts, foul-outs, outs recorded, double/triple-play outcomes, and fielding-independent pitching. It does not yet classify which outs are “dead” versus “productive” from the batting team’s perspective. |
| Expected Wickets | Partly exists | Expected wOBA already strips BABIP variance through contact-quality buckets. That is the right foundation for xO, but the current public stat is run-value/contact-quality based rather than out-probability based. |

## 1. Pressure-Adjusted Impact (PAI)

**Goal:** identify hitters whose plate appearances beat the expectation of the
live chase, not just hitters with strong raw slash lines.

**Recommended definition:**

```text
PAI = actual_run_value - expected_run_value_for_state
```

Where `expected_run_value_for_state` is keyed by:

- half/game phase,
- outs remaining in the 27-out envelope,
- bases occupied,
- score differential or chase target if batting second,
- required runs per three outs (`RRR/3O`) when a target is known.

**Display companion:** `True Run Rate+` or `TRR+`:

```text
TRR+ = 100 * actual_runs_created_per_PA / expected_runs_needed_per_PA
```

A value above 100 means the hitter beat the live chase requirement; below 100
means the PA lagged behind the target even if the raw event looked positive.

**Implementation path:**

1. Use `game_pa_log` as the event spine because it already persists before/after
   outs, bases, score differential, batter, pitcher, and contact information.
2. Build a run-expectancy table from historical O27 states.
3. Add a batting aggregation with `pai_sum`, `pai_per_pa`, and `trr_plus`.
4. Surface it on O27 Index/Savant player pages and a leaders table labelled
   “Pressure Hitters.”

## 2. Dead Out Percentage (DO%)

**Build status:** the first DO% pass is implemented in `build_dead_outs_table` and surfaced on O27 Index pitcher pages. Strikeouts and foul-outs are automatic dead outs. Contact dead outs require no run, no runner advancement, and no batter reach. Per design, a two-out double play contributes **two** dead outs and one dead-out PA.


**Goal:** credit pitchers for suffocating the opponent’s out envelope with outs
that do not materially improve the batting team’s state.

**Recommended definition:**

```text
DO% = dead_outs / outs_recorded
```

A **dead out** should be any pitcher-attributed out where all of the following
are true:

- at least one out was recorded,
- no run scored,
- no runner advanced to a higher base,
- the batter did not reach,
- the play was not a sacrifice or other explicitly productive advancement.

Strikeouts, foul-outs, pop-ups, routine flyouts with no advancement, and many
GIDP/GITP outcomes will qualify. A double play can be even more valuable than a
single dead out, so keep both. The production rule is that a two-out DP counts as two dead outs, not one:

```text
dead_outs      = out count on dead-out plays
dead_out_pas   = plate appearances producing at least one dead out
DO%            = dead_outs / outs_recorded
DO-PA%         = dead_out_pas / batters_faced
```

**Implementation path:**

1. Extend the PA/event log with enough outcome metadata to identify runner
   advancement, batter reach, and multi-out plays for every PA, not just BIP.
2. Add pitcher counters `dead_outs` and `dead_out_pas`.
3. Show DO% next to K%, FO%, WHIP, and FOP to describe pressure creation rather
   than only defense-independent dominance.

## 3. Expected Outs (xO)

**Build status:** the first xO pass is implemented in `build_expected_outs_table` and surfaced on O27 Index pitcher pages as `xO/27` plus `O − xO`. It uses the same EV/LA binning strategy as physics-native xwOBA against, treats strikeouts and foul-outs as automatic expected outs, and now includes power-play defensive support by adding nickel-converted hits saved behind the pitcher.


**Goal:** isolate how many outs a pitcher should have generated from contact
quality and pitch process, independent of defense and hit/no-hit luck.

**Recommended definition:**

```text
xO = Σ P(out | event features)
```

Start with the simple contact-quality version:

- strikeout / foul-out: `1.0 xO`,
- walk / HBP / HR: `0.0 xO`,
- ball in play: league out probability for its quality bucket, with later
  upgrades for exit velocity, launch angle, spray angle, pitch type, and park.

Useful derivatives:

```text
xO/27 = 27 * xO / batters_faced
O-xO  = actual_outs - xO
```

Interpretation:

- `actual_outs > xO`: defense/luck converted extra outs.
- `actual_outs < xO`: pitcher created out-shaped contact but did not get paid.

**Implementation path:**

1. Reuse the expected-wOBA quality table as the calibration precedent.
2. Build an out-probability table by contact-quality bucket from `game_pa_log`.
3. Aggregate xO by pitcher and batter.
4. Surface xO and O-xO on pitcher pages as the out-probability sibling of xRA
   and expected wOBA.

## Priority recommendation

1. **xO first** — implemented as the initial build because the existing expected-wOBA/contact-quality pipeline made it the lowest-risk port.
2. **DO% second** — high narrative value, but requires fuller PA outcome tagging
   to avoid misclassifying productive outs.
3. **PAI third** — most distinctive, but it needs a chase-aware expectancy model
   and more careful target-state calibration.

## Open design questions

- Should PAI use win probability, run expectancy, or chase probability as its
  baseline? The cricket analogy argues for chase probability when batting
  second and run expectancy otherwise.
- Should DO% count a two-out double play as two dead outs or one dead-out PA?
  The recommended answer is both counters, with DO% using outs and DO-PA% using
  PAs.
- xO includes power-play support in the production metric because O27 treats the nickel as part of the pitcher's actual environment; a later pure-pitcher split can subtract `defense_support_xouts` when needed.
