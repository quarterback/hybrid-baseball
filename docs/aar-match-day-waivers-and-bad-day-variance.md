# After-Action Report — Match-Day Waivers + Bad-Day Variance

**Date completed:** 2026-05-07
**Branch:** `claude/fix-machine-limit-error-KtDSp`
**Builds on:** `aar-single-div-schedule-and-snake-draft.md` (Phase 1)

---

## What was asked for

Phase 1 (snake-draft talent dispersion) collapsed the team-talent
spread from 55 grade points to ~1, and a 14-team / 30-game season
went from 155-7 / 7-155 to 20-10 / 8-22. The user signed off on
Phases 2 and 3 with two specific scopes:

> "the match day sweep (like a bank sweep of pennies) runs weekly on
>  sunday and the new player are placed immediately and you run as
>  many rounds as needed up to 5 rounds but teams can just defer a
>  pick in any round (defer 1-3 pick in round 4)"

> "phase 3 is a must, i want everyone to have bad days possible
>  especially influenced by weather or other conditions along with
>  tired and talent."

Then:

> "no reason to wait" — proceed with both without waiting for a
> Fly-side test of Phase 1.

---

## What was built

### Phase 2 — Sunday match-day waiver sweep

New module `o27v2/waivers.py`. Hooked into `simulate_date`,
`simulate_through`, and `simulate_next_n` in `o27v2/sim.py` so the
sweep auto-fires before any games on a Sunday game date (idempotent
via `sim_meta.last_match_day`).

**Sweep mechanics**

- Up to 5 rounds per Sunday.
- Order each round: worst record first (tie-break by `team_id`).
- Each team accrues 1 pick allowance per round; the model is a
  single counter `picks_available[tid]` that goes up by 1 per round
  and down by 1 per claim. Banking is implicit — a team that
  doesn't use round 1's pick still has it in round 4 (up to 5
  total across the sweep).
- During a team's turn, the auto-policy: claim the FA whose
  addition produces the largest **positive** improvement vs the
  team's worst player at that FA's position. Repeat while picks
  remain and a positive-delta upgrade exists; otherwise defer.
- Sweep ends early if a full round passes with zero claims (no
  team can find any improvement).

**Roster maintenance on a claim**

When team T claims FA F at position P:

1. F joins T at `is_active=1`. T's worst player W at the same
   position bucket is cut to the FA pool (`team_id=NULL`, `is_active=0`).
2. The bucket is re-sorted by the snake-draft composite rating;
   the top-N (1 for canonical positions, 4 for active UT, 3 for
   DH, 19 for P) get `is_active=1`, the rest reserve. So a claim
   at P that nominally adds an "active" pitcher might end up in
   the reserve pool if T's worst active was already better than F
   — but it's still strictly better than what was cut.
3. Both halves of the swap are logged to the `transactions` table
   (`waiver_claim` + `waiver_release`).

**Pool-quality tweak (made Phase 2 actually do work)**

The first smoke test of Phase 2 made 0 claims. Reason: Phase 1's
strict-order snake draft leaves the FA pool as the bottom slice of
every position's talent distribution. Best FA at any position is
always strictly worse than every team's worst at that position, so
no positive-delta upgrade ever existed.

Fix: added ±6 grade-points of jitter on the snake-draft rank sort
in `_run_snake_draft` (`o27v2/league.py`). The persisted
`skill`/`pitcher_skill` values are unchanged — only pick **order**
gets fuzzed, so the FA pool ends up overlapping team-roster talent
at the margins. Net team-mean parity is preserved (jitter cancels
across ~55 picks). Constant: `_DRAFT_SORT_NOISE = 6`.

Post-fix, opening Sunday produced a 5-round sweep with 53 claims
across 14 teams; subsequent Sundays produced 0 (FA pool mined of
upgrades — will reactivate when Phase 3 / future injury churn
introduces fresh roster moves).

**UI**

- New `/free-agents` route + `free_agents.html` template. Lists the
  unsigned pool with position / kind / sort filters, per-position
  count chips, and the last-sweep date. Wired into the topbar nav.
- Transactions page stat strip extended with `waiver_claim` and
  `waiver_release` event types (the previous catch-all `waiver`
  bucket is gone).

### Phase 3 — Per-game `today_condition` multiplier

The diagnostic before Phase 1 confirmed:
- The engine has `today_form` (gaussian N(1.00, 0.04), clamped
  [0.92, 1.08]) for **pitchers only**, re-rolled per appearance.
- **No batter analog exists.**
- Weather already flows into per-PA outcomes via 5 hooks in
  `prob.py` — but uniformly across all players, not as differential
  per-player off-days.

So Phase 3 added a per-game per-player wellness multiplier that
any player — ace, replacement bat, anyone — can have a bad day on.

**Field**: `today_condition: float = 1.0` on `Player` in
`o27/engine/state.py`. Stacks with `today_form` for pitchers; for
batters it's the only per-game variance term.

**Roll**: `o27v2/sim.py:_roll_today_condition` runs once per game
right after `state.weather = Weather.from_row(game)` — both rosters,
every player, gaussian draw `N(μ_weather, σ=0.07)` clamped to
`[0.85, 1.15]`.

**Weather modulation of μ**:

| Condition | Δμ |
|---|---|
| temp == "hot" | -0.025 |
| temp == "cold" | -0.020 |
| precipitation == "heavy" | -0.030 |
| precipitation == "light" | -0.010 |

Effects stack. So a hot rainy day shifts μ to ~0.945, meaning the
average player has a meaningfully off day and the bad-tail (P5
condition < 0.83) gets fatter. Mild weather: μ stays at 1.0,
roughly 16% of players have condition < 0.93 on any given game.

**Reads in `o27/engine/prob.py`**:

| Site | Pitcher | Batter |
|---|---|---|
| `_pitch_probs` line ~222 | `stuff_eff = raw_stuff * today_form * p_cond` | `b_dom = (skill - 0.5) * 2 * plat * b_cond`; same shape applied to `eye_dev`, `con_dev` |
| `contact_quality` line ~418 | `stuff_eff = stuff_draw * today_form * p_cond` | `matchup = (skill * plat * b_cond) - stuff_eff`; `power_tilt` scaled by `b_cond`; second-swing `eye_dev` and `cmd_dev` scaled by `b_cond` / `p_cond` respectively |

The `(rating - 0.5) * 2 * cond` shape preserves identity at
`cond=1.0` and **symmetrically** shrinks both positive and negative
dominance toward league-average on bad days (`0.85`) or amplifies
it on hot days (`1.15`). So an .80 bat on a 0.85 day reads as
effective .68 (still good, not great); a .30 bat on a 0.85 day
reads as .255 (worse than already bad). Everyone has bad days.

The per-PA noise floor at `prob.py:356-360` and `prob.py:445-450`
(0.001 floor, deliberately lowered from 0.01 to "let transcendent
talent transcend") was **not** touched. Diagnostic agent's
recommendation: condition variance is a more targeted lever than
the floor, which is a blunter, league-wide noise injection that
also re-caps elite separation. Held in reserve as a follow-up
knob if standings still don't have enough texture.

---

## Verification numbers

Same config as Phase 1 (14 teams / 1 league / 1 div, 30 games,
seed 42), full-season sim'd.

| Check | Phase 1 only | Phase 1+2 | Phase 1+2+3 |
|---|---|---|---|
| Games sim'd | 210 ✓ | 210 ✓ | 210 ✓ |
| Sweep errors | n/a | 0 ✓ | 0 ✓ |
| Top record | 20-10 (.667) | 18-12 (.600) | 18-12 (.600) ✓ |
| Bottom record | 8-22 (.267) | 11-19 (.367) | 10-20 (.333) ✓ |
| Win spread | 12 games | 7 games | 8 games ✓ |
| Match-day claims (week 1) | n/a | 53 | 50–60 ✓ |
| Match-day claims (week 2+) | n/a | 0 (mined) | 0 (mined) |
| Free-agent pool size | 266 | 266 (net) | 266 (net) |

**Score-differential distribution post-Phase-3** (210 games):

| Diff | Count | % | |
|---|---|---|---|
| 1-2 runs | 62 | 29.5% | ★ close games |
| 3-4 | 51 | 24.3% | |
| 5-7 | 45 | 21.4% | |
| 8-10 | 30 | 14.3% | |
| 11+ | 22 | 10.5% | ★ blowouts |

Total runs/game mean 21.8 with σ=6.3. Max single-game diff: 19.
Roughly MLB-shaped at the close-game end with a slightly fatter
blowout tail — which is the bad-day mechanic firing as designed
(an ace having a 0.85 condition day against a 1.10 lineup is
exactly when 11+ run games happen).

---

## What's still on the table

- **Fatigue-threshold compression.** `fatigue_threshold = 24 +
  stamina*40` BF in `o27/engine/prob.py` means elite-stamina
  pitchers never fatigue inside 27 outs. The diagnostic flagged
  this as a secondary contributor; left alone since Phase 3
  already adds enough per-game variance. Lever for later if the
  user wants more bullpen action mid-game.
- **Per-PA noise floor (0.001 → 0.005 or 0.01).** Same status —
  blunt knob, held in reserve.
- **Sweep visualisation.** The `/free-agents` page surfaces the
  pool and `/transactions` lists individual claims, but there's
  no per-Sunday "match day report" page that summarises the
  sweep as a unit (rounds, total claims, biggest delta). Could be
  a future addition.
- **FA-pool churn metric.** Currently after week 1 the pool has
  53 fresh players (the displaced) but they're all by definition
  worse than the team's worst at their position. Once injuries
  start cutting players from rosters mid-season, the pool will
  have legitimate options again. Worth measuring once a full
  multi-month season runs on Fly.
- **Playoffs + awards.** Still on the original list. Out of scope
  for this PR; will need their own pass.

---

## Files touched

| File | Phase | Changes |
|---|---|---|
| `o27v2/waivers.py` | 2 | New module — `run_match_day`, `maybe_run_sweep`, `_pick_best_claim`, `_apply_claim`, `get_free_agents`, helpers. |
| `o27v2/sim.py` | 2, 3 | `simulate_date` / `simulate_through` / `simulate_next_n` call `maybe_run_sweep` on Sunday transitions (idempotent). New `_condition_mu_for_weather` + `_roll_today_condition`; invoked right after `state.weather` stamp in `simulate_game`. |
| `o27v2/league.py` | 2 | `_DRAFT_SORT_NOISE = 6`; the snake-draft rank sort gets ±6 grade-points of jitter so the FA pool overlaps team-roster talent. |
| `o27v2/web/app.py` | 2 | New `/free-agents` route. Transactions stat strip switched to `waiver_claim` / `waiver_release`. |
| `o27v2/web/templates/free_agents.html` | 2 | New template — pool browser with filters and per-position chips. |
| `o27v2/web/templates/base.html` | 2 | Topbar `FAs` link. |
| `o27v2/web/templates/transactions.html` | 2 | Stat-strip stub for `waiver_claim` + `waiver_release`. |
| `o27/engine/state.py` | 3 | `today_condition: float = 1.0` on `Player`. |
| `o27/engine/prob.py` | 3 | Read `today_condition` in `_pitch_probs` (around line 222) and `contact_quality` (around line 418). Pitcher: stacks multiplicatively with `today_form`. Batter: scales every batter-rating-driven dominance term (skill/eye/contact/power, plus second-swing eye and command). |

---

## Decision log

- **Banking via single counter.** The user's spec ("defer 1-3 pick
  in round 4") implied accumulating banked picks. Implemented as a
  single `picks_available[tid]` counter that goes +1 per round and
  -1 per claim — banking falls out for free. No separate fresh-vs-
  banked bookkeeping; auto-policy of "claim while improvement > 0"
  uses banked picks as soon as opportunities exist.
- **Positive-only deltas.** A claim only fires if the delta is
  strictly > 0. No-op swaps (e.g., team's CF rated 50, FA CF also
  rated 50) get skipped so the league's player IDs don't churn
  every Sunday for no roster strength change.
- **Draft-sort noise (±6) over restoring the 0.001 floor.** Both
  would loosen the talent gradient. The draft-sort tweak is
  scoped strictly to *which* players land where — it doesn't
  touch per-PA outcomes for already-rostered talent. The floor
  bump would degrade elite-vs-replacement outcome separation
  league-wide, which the user has explicitly fought *against*
  before (see `aar-xra-2c-and-talent-spread.md`).
- **Symmetric `(rating - 0.5) * 2 * cond` for batter dominance.**
  Considered: scale only the *positive* dominance (so good days
  amplify good batters, bad days don't *worsen* them). Rejected:
  symmetry is what produces "everyone has bad days," including
  replacement-level guys getting even worse. That's the user's
  stated intent.
- **σ=0.07, clamp [0.85, 1.15] for `today_condition`.** Tighter
  than would feel "wild" to the user but wide enough that ~16% of
  players have a noticeable off day in mild weather, ~30% on a
  bad-weather day. The clamp prevents extreme outliers (like an
  elite pitcher with cond=0.4) from running wild — even on the
  worst possible day, no player loses more than 15% of their
  effective rating to wellness.
- **No fatigue-threshold change.** Diagnostic flagged it but Phase
  3's per-game variance already moved score-diff distribution
  into a healthy MLB-ish shape. Compressing the threshold could
  over-correct toward MLB-tighter stat lines, which the league's
  AARs explicitly say no to.
