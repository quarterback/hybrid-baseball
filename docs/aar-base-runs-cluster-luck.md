# After-Action Report — BaseRuns / Cluster-Luck Decomposition

**Date completed:** 2026-05-07
**Branch:** `claude/sabr-baseball-analysis-uOD6J`
**Commits:** `e4ba41c` (initial BsR), `b94c774` (B-coefficient shape refit)

---

## What was asked for

Follow-up to the SABR analytics suite (see
`docs/aar-sabermetric-analytics-suite.md`). After framing how a complete
luck decomposition would look —

> "Together they give you a full luck decomposition: BABIP luck (xwOBA),
>  sequencing luck (BsR), and Pythag formula error (k* refit). Three
>  cleanly separated luck dimensions."

— the user asked the obvious question:

> "do we already have BsR? IF NOt, let's add it"

I shipped a BsR module with a multiplicative league-mean re-center and
flagged a B-coefficient *shape* refit as the obvious follow-up. The
user's response:

> "add that now and yeah don't leave it"

So this is two ships back-to-back: BsR with default coefficients, then
the shape refit on top.

---

## What was built

### 1. `o27v2/analytics/base_runs.py` — module + dual-pass output

Smyth standard form with HBP added (Tango variant):

```
A = H + BB + HBP − HR        baserunners
B = c1·TB + c2·H + c3·HR + c4·(BB+HBP)
C = AB − H                   batting outs
D = HR

BsR = A · B / (B + C) + D
```

Applied to **both** sides:

- **Offense:** sum each team's batting from `game_batter_stats` over
  played non-playoff regulation phase.
- **Defense:** sum the *opponent's* batting in each team's games
  (`b.team_id != t.id`) — same physics, different aggregation key.

The residual `actual − predicted` on each side is sequencing/cluster
luck. The net (off − def) is the cleanest "runs of luck" figure.

### 2. League-mean re-center

MLB-fit `c = (1.4, −0.6, −3.0, 0.1)` underpredicts in O27's ~22 R/G
environment by ~55%. After computing raw predictions, we apply a
multiplicative scale on each side so `SUM(pred) == SUM(actual)`. Live
values: `B_off ×1.553`, `B_def ×1.590`. This makes residuals mean-zero
without changing rank order.

This was the only pass shipped in commit `e4ba41c`. It's a 1-DoF fit
(scale only) so residuals still embed shape mis-fit on top of genuine
sequencing luck. That's what the second commit fixes.

### 3. Joint coordinate-descent shape refit

`_refit_coeffs()` minimises team-level SSE jointly across offense and
defense — 60 datapoints (30 teams × 2 sides), 4 parameters,
warm-started from MLB defaults. Cycles each `c_i` through 1-D ternary
search while holding the others fixed; 8 outer iterations × 4
coordinates × 50 inner iterations. Total fit time: ~0.4s.

Per-coordinate ternary search is well-behaved because BsR is monotone
in B (given B+C > 0) and B is linear in each `c_i`, so the SSE map per
axis is convex with the others fixed. The joint problem is
non-convex, but warm-starting from MLB defaults keeps us near a
sensible basin.

### 4. The bounds story (dev anecdote)

First refit attempt converged to `c1 = 2.977` against an upper bound
of `3.0` — clear sign the constraint was binding. Widened to `[0.5,
6.0]`, also widened the other three (`c2 ∈ [-3, 1]`, `c3 ∈ [-8, 2]`,
`c4 ∈ [-1, 1.5]`). Re-fit converged cleanly to `c1 = 3.27` with no
boundary contact. Conclusion: the canonical MLB bounds are too tight
for high-RPG environments — `c1` doubles, which is structurally what
we should expect.

### 5. Post-fit rescale (consistency fix)

Raw shape-fit predictions don't necessarily sum to league actual (the
fit minimises SSE, not bias). Fitted-pass residuals had ~700-run net
bias. To make the two SSE figures apples-to-apples and the fitted
residuals mean-zero like the default pass, applied the same
multiplicative rescale post-fit. Cost in SSE is negligible because the
4-coefficient family already absorbs scale; the rescale is essentially
a tiny final correction.

### 6. `/analytics` panel (dual-column layout)

New section appended to `analytics.html` after the Pythag panel.
Header shows fitted coefficients inline:

```
fitted B = (3.27·TB −0.582·H −3.858·HR +0.387·(BB+HBP))
        vs MLB (1.4·TB −0.6·H −3·HR +0.1·(BB+HBP))
        SSE cut 40.6%
```

Table is grouped under two top-level headers ("MLB-default coeffs" and
"Refit coeffs (O27)"), each spanning Off / Def / Net columns. Sorted
by `|Net (refit)|` — the cleaner sequencing read. Color cues: green at
+5 (Off) / −5 (Def), red at the inverse, mirroring the convention in
the Pythag panel.

---

## Numbers from the demo season

162-game season, 30 teams, 56,487 RS = 57,843 RA (~3% gap from playoff
exclusions / unfinished games).

### Fitted coefficients

| Coefficient    | MLB default | O27 refit | Δ      |
|----------------|-------------|-----------|--------|
| `c1` (TB)      | +1.400      | +3.271    | +1.87  |
| `c2` (H)       | −0.600      | −0.582    | +0.02  |
| `c3` (HR)      | −3.000      | −3.858    | −0.86  |
| `c4` (BB+HBP)  | +0.100      | +0.387    | +0.29  |

Joint SSE: 345,426 (default) → 205,226 (refit) — **40.6% reduction**.

The dominant change is `c1`: total bases are ~2.3× more
advancement-valuable in O27 than in MLB. Reading the formula
intuitively: in a hit-rich league there are more runners on base when
extra-base hits land, so each TB advances more total runners. The
HR coefficient gets sharper (`−3.86` vs `−3.0`) because home runs
"already include the batter scoring," which double-counts more in a
denser environment. BB/HBP rises (`+0.39` vs `+0.10`) for the same
reason that hits do — walks in front of hits convert.

### Top sequencing-luck movers

| Tm  | Default-net | Refit-net | Compression |
|-----|-------------|-----------|-------------|
| PHI | −334.5      | −235.8    | ~30%        |
| MIN | +235.7      | +191.8    | ~19%        |
| OAK | +195.1      | +121.5    | ~38%        |
| NJC | −168.9      | −107.0    | ~37%        |
| CIN | −151.3      | −155.9    |  +3% (grew) |
| COL | −124.9      | −121.6    |   ~3%       |
| CHW | +120.2      | +130.8    |  +9% (grew) |

Most teams compress ~30% — that's the chunk of apparent default-coeff
"luck" that was actually environment-shape mis-fit, not sequencing.
Two teams (CIN, CHW) actually grow under the refit, which is the
expected signature of *real* sequencing variance — it doesn't
disappear when shape mis-fit goes away.

### Sanity checks (both passes)

- `SUM(off_luck) ≈ 0` to within ±0.2 R (rounding only)
- `SUM(def_luck) ≈ 0` to within ±0.2 R
- `league_actual_rs ≈ league_actual_ra` (must hold; symmetry across
  the league: every run scored is a run allowed by the opponent)

---

## What this unlocks

The original SABR-suite AAR sketched cluster-luck as the "natural pair
for the Pythag-refit luck column." That goal is now hit. The full
three-axis luck decomposition is:

| Axis                  | Source                   | Captures                                  |
|-----------------------|--------------------------|-------------------------------------------|
| Event-level (BABIP)   | xwOBA residual           | Hits-vs-quality, contact ball-in-play luck |
| Game-level (sequence) | BsR residual (refit)     | RISP timing, runner distribution, GIDP    |
| Season-level (curve)  | Pythag k* residual       | W%-from-RD curvature, leverage spread     |

These are *almost* orthogonal but not perfectly so — see the next
section.

A natural follow-on (left for later) is a single combined panel that
shows each team's W% surplus split into the three buckets as a stacked
bar, with the unattributed remainder shown explicitly. That would let
a SABR reader point at one figure and say "this team's 8 wins above
Pythag breaks down as: 3W from BABIP, 4W from clustering, 1W from
running close games."

---

## Caveats / known issues

- **BsR ↔ Pythag residuals are partially correlated.** A team that
  sequences well scores more runs *and* tends to score them in higher-
  leverage spots, which inflates Pythag overperformance too. Fitting
  them independently double-counts some sequencing luck. The fix is
  to fit Pythag on **BaseRuns-predicted** RS/RA rather than actual,
  so the k* residual captures only distribution-shape luck on top of
  the sequencing residual. Not done here; flagged for the combined-
  panel ship.
- **30 teams × 2 sides = 60 points fitting 4 parameters** is 15:1
  DoF. Fine for a stable point estimate, but the fitted coefficients
  will jitter season-to-season. The structural fit (multiple seasons
  pooled) is the more durable answer; this single-season refit is
  good enough for in-season "where is the luck right now" reads.
- **No B+C > 0 guard during ternary search.** Bounds are wide enough
  that pathological combinations are possible in principle (e.g.
  large negative `c2` and `c3` with small `c1`), and `_bsr` falls
  back to `D` (HR-only) if `B+C ≤ 0`. In practice the fit doesn't
  visit that region from the warm start, but a hard clip on B would
  be cleaner.
- **Coordinate descent isn't gradient descent.** It can stall in
  saddle regions if two parameters are highly correlated. Verified
  this isn't happening here by perturbing the initial point ±20% on
  each axis and confirming convergence to within ±0.05 on every
  coefficient. Could add a final Nelder-Mead polish if precision
  matters more later.
- **Default-coefficient pass is now somewhat redundant** given the
  refit pass is strictly better. Kept it because the *gap* between
  the two columns is itself the headline finding ("here is how much
  of MLB-default 'luck' was actually environment shape mis-fit"), and
  removing it would lose that diagnostic.

---

## Files touched

```
o27v2/analytics/base_runs.py          # NEW — Smyth BsR + 4-param shape refit
o27v2/analytics/__init__.py           # export build_base_runs_table
o27v2/web/app.py                      # /analytics route: pass base_runs to template
o27v2/web/templates/analytics.html    # NEW BsR panel (dual-column)
docs/aar-base-runs-cluster-luck.md    # this doc
```

No engine changes. No schema changes. Pure analytics layer on top of
existing `game_batter_stats` + `games`.

---

## Closing thought

The interaction shape on this one was nice: I shipped the v1 with
default coefficients and called out the shape-refit as the obvious
next step in the same response. User said "add that now and don't
leave it." That's the cleanest version of the "ship working, surface
the next step, finish it" loop — no scope ambiguity, no rework, both
ships clean. Worth noting because it's the pattern I should default
to when an analytics module has a "good / better" form: ship the
good as a checkpoint, surface the upgrade path, finish the better in
the same session.
