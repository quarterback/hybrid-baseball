# Plan — Batted-ball hit-type vocabulary + Bunting

**Status:** in progress
**Branch:** `claude/baseball-hits-runs-variance-Cv0iW`
**Goal:** more "wasted" hits (target H/R ≈ 1.5, i.e. R/H ≈ 0.67) delivered
*honestly* via batted-ball texture, plus add bunting (currently absent).

---

## Why this approach

Current state on main: R/H ≈ 0.95 (runs ≈ hits). The user wants ~1.5× hits
per run, with the wastage **explained by groundball/flyball outcomes**, not a
flat penalty. The code map shows the engine's hit vocabulary is thin —
`single/double/triple/hr` + out types — and crucially **a hit carries no
groundball/flyball texture**: once "single" is selected, advancement is flat
regardless of how the ball was hit. There's a `batted_ball.py` layer that
samples EV/LA/spray, but it's *cosmetic* (runs after the outcome is decided,
only for spray charts).

So a weak dribbler and a screaming gap liner are both just "single" and advance
runners identically. That determinism is the problem. The fix is to give hits a
batted-ball texture rolled from the batter's profile, and make grounders convert
to runs worse (station-to-station, more force-outs) than liners.

## Critical constraint (from the code map)

Adding new `hit_type` strings (`"dribbler"`, `"gb_single"`, …) would break
stat-counting in ~12 places that switch on the closed set
(`pa.py:244`, `render.py:820/825/833`, `runner_advances_for_hit`, etc.).

**Therefore: texture rides as a SEPARATE field** `outcome_dict["batted_ball"]`,
NOT a new hit_type. A single stays `hit_type="single"` (counts as a hit
everywhere, unchanged) but carries `batted_ball="grounder"` which only
`runner_advances_for_hit` consumes for run-suppression. Zero risk to stat code.

---

## Part A — Batted-ball texture (the wasted-hits mechanism)

### A1. The texture vocabulary
`outcome_dict["batted_ball"]` ∈ {`dribbler`, `grounder`, `liner`, `flyball`}
set whenever a hit (single/double/triple) is finalized in `resolve_contact`
(before the `runner_advances_for_hit` call at prob.py:1931).

### A2. Rolled from the batter, not fixed
Probability of each texture depends on contact quality + batter.power +
batter.contact:
- **weak contact** single → mostly `dribbler` / `grounder` (the seeing-eye /
  beaten-out infield hit). Low-power slap hitters live here.
- **medium contact** single → `grounder` / `liner` mix, power shifts toward liner.
- **hard contact** single/double → `liner`; triples/doubles → `liner`/`flyball`
  gappers; hr → `flyball`.

So a low-power contact hitter sprays grounders (hits that clog the bases); a
slugger hits liners (hits that drive in runs). Player-differentiated by design.

### A3. The run suppression
`runner_advances_for_hit` gains a `batted_ball` param. A per-texture additive
`score_shift` (config `BATTED_BALL_SCORE_SHIFT`) folds into the existing
`_resolve_table(..., score_shift=…)` hook alongside `seq_shift`:
- `dribbler`: large negative — runners hold, nobody scores from 2nd, 1B→2B only.
- `grounder`: moderate negative.
- `liner`: ~neutral / slight positive (line-drive singles let runners run).
- `flyball`: neutral.

This is *additive on top of* the existing speed/arm/seq-form logic, so fast
runners still take extra bases — it just makes a grounder single a worse
run-producer than a liner single. Tunable; all-zero = identity (today's behavior).

### A4. Feeds the existing GIDP machinery
More grounders ⇒ naturally more force/double-play opportunities (the GIDP code
already keys off ground_out + runners). The same groundball tendency that makes
a hit non-productive also erases runners — coherent, one underlying cause.

### A5. Tuning
Push toward R/H ≈ 0.67 via `BATTED_BALL_SCORE_SHIFT` depths + the
weak/medium grounder rates. Measure with `scripts/measure_hr_coupling.py` and
report where it lands (the 27-out format has no inning-end to strand runners, so
0.67 may floor higher — report honestly). Disable with all shifts = 0.

---

## Part B — Bunting (new manager decision)

No steal/sacrifice/pre-pitch tactical decision exists today — bunting is the
first. Establishes the pattern.

### B1. Manager hook
New `ManagerAI.maybe_bunt(state, batter) -> Optional[str]` returning
`"sacrifice"` / `"bunt_for_hit"` / `None`, called in `pa.py` right after the
joker-insertion hook and before the pitch loop. Gated by a new archetype weight
`bunt_tendency` / `small_ball` (manager.py archetypes).

### B2. When it fires
- **Sacrifice**: runner on 1B (and/or 2B), < 2 outs, weak bat at plate, close/
  late game, manager small-ball lean. Advances the runner; batter normally out.
- **Bunt for hit**: fast batter, low outs, corners back; rolls success off
  `batter.speed` vs infield defense → `bunt_single` or out.

### B3. Resolution + stats
New `resolve_bunt(...)` short-circuits the pitch loop. Outcomes:
- successful sacrifice → runner advances, batter out, recorded as a **sacrifice**
  (SH — not an AB, like a sac fly). Needs a `sh` stat field (batter.py + schema)
  OR record as a plain out if we don't want a schema change in this pass.
- bunt-for-hit success → counts as a hit (`hit_type="single"`,
  `batted_ball="bunt"`, minimal advancement).
- failure → out (possibly force at lead base).

### B4. Stat schema decision
Adding a true `SH` column means a DB migration (game_batter_stats) like the
streak columns. If we want to avoid that in this pass, record a sacrifice as a
non-AB out with no hit (PA still counts) and surface bunts only in play-by-play.
Decide during implementation; lean toward the migration for correctness since
the project already has a clean migration pattern.

---

## Build order (staged, tested commits on this branch)
1. This plan (commit).
2. Part A batted-ball texture + tune to H/R target + tests (commit).
3. Part B bunting + tests (commit).
4. AAR.

## Risks
- Env shell I/O has been flaky this session; A/B sim tuning needs many runs.
- Pushing R/H to 0.67 may dent R/G realism (accepted per user) or floor higher
  than 0.67 due to the no-inning-end structure — will report actuals.
- Bunting touches the decision layer; must not regress the joker/stay hooks.
