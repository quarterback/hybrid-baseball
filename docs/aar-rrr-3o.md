# After-Action Report — RRR/3O (cricket-style Required Run Rate)

**Date:** 2026-06-23
**Branch:** `claude/adoring-meitner-7wsd8p`
**Status:** Shipped to the live game page (box-score footer, Scoring Events
column, Chase panel). Validated against a freshly-simulated scratch DB via the
Flask test client; needs a smoke-check against a real `o27v2.db` before release.

---

## 1. Why this happened

The owner asked to "add RRR from cricket to O27." A T20-flavored spec followed,
asking for **Required Run Rate normalized to 3 outs (RRR/3O)** with three parts:
(1) compute it in the sim loop, (2) drive late-game manager AI off thresholds
(3.0 / 6.0 / 12.0), and (3) output a "Pressure Curve" to several surfaces.

Exploration turned up an important wrinkle: a form of RRR **already existed but
was stranded.** `o27/stats/team.py` has `required_run_rate` /
`required_run_rate_full` / `net_run_rate`, but they render only through the
*dead* legacy path (`o27/render/render.py` → `box_score.j2` → `o27/web/`). The
live `o27v2/` app surfaced none of it — exactly the "stranded feature" failure
mode CLAUDE.md warns about (cf. the visual-scorecard audit).

After laying out the tradeoffs, the owner **scoped this to analytics only**:
build the metric + display surfaces now, ship it, look at real Pressure Curves,
and decide on manager behavior later. When behavior is added it will **fold into
the existing leverage framework** (`manager.py`'s `_decisive_chase` /
`_desperation_rally` / blowout logic) behind a feature flag — *not* a parallel
threshold ladder. That AI work is explicitly **out of scope** here.

## 2. What got built

- **`o27/stats/team.py`** — the canonical formula, one source of truth:
  - module-level `required_run_rate_3o(target_runs, runs, outs, envelope=27)`:
    `(max(0, target − runs) / outs_remaining) × 3`. Returns `None` when there's
    no target or the out envelope is exhausted; a reached target yields `0.0`.
  - `TeamStats.required_run_rate_3o` property delegating to it (mirrors the
    existing `required_run_rate` / `required_run_rate_full` pattern).
- **`o27v2/web/box_score.py`**:
  - `compute_chase_rrr(rows, first_team_final)` — reconstructs the second-batting
    side's curve from its regulation `game_pa_log` rows. During a regulation
    chase the fielding side has finished batting, so its score is fixed and the
    chaser's running total is `score_diff_after + first_team_final`. Returns
    `{starting, peak, checkpoints, series}` or `None` on absent/legacy data.
  - `_rrr_note(rrr_summary)` — footer block (Starting / Peak faced + the
    27/18/9-outs-remaining checkpoints), spliced into `render_box_score` notes
    alongside the existing Powerplays/Walk-Back notes via a new
    `rrr_summary=None` kwarg.
- **`o27v2/web/app.py`** (`game_detail`) — determines the chaser from
  `home_bats_first` (it's whoever bats **second**, home *or* away), queries the
  chaser's phase-0 PA log, builds `rrr_summary`, stamps inline `rrr_3o` on each
  chaser-half scoring event, and passes both to the box score and template.
- **`o27v2/web/templates/game.html`** — an **RRR/3O** column on the Scoring
  Events table and a **Chase — Pressure Curve** panel (numeric checkpoints).
- **Docs/tests** — `docs/stats-reference.md` RRR/3O row; `o27/tests/test_rrr_3o.py`
  (DB-free formula + reconstruction unit tests); invariant #12 in
  `tests/test_stat_invariants.py`.

## 3. Design decisions / honesty

- **No engine-loop change, no migration.** RRR/3O is derived **at render time**
  from data already persisted in `game_pa_log` (`outs_after`,
  `score_diff_after`) plus the `games` row. This matches CLAUDE.md's "prefer
  reading persisted per-game data over re-simming" and keeps the change fully
  reversible. The spec's "compute in the loop" and "write to the JSON game-log
  payload" framing was based on two wrong assumptions: the hot loop doesn't need
  to carry it for an analytics-only build, and there **is no single JSON
  game-log payload** (only `?format=json` route dumps, which pick up the added
  context for free). The "entered_outs logging arrays" the spec warned about is
  a DB stat column on `game_batter_stats`, not a loop structure — untouched.
- **Keyed to the second-batting team, not the half.** The legacy `TeamStats`
  RRR hardcodes "home / 27 outs," which is wrong whenever the home side elects
  to bat first. Everything here keys off the actual chaser (`away` when
  `home_bats_first`, else `home`) and its half label (`top`/`bottom`).
- **Robust to PA-log ordering.** `game_pa_log` rows in `ab_seq` order are *not*
  monotonic in outs (stays, multi-swing ABs). Checkpoints use
  `max(runs where outs_after ≤ mark)` and peak takes the max over all points, so
  ordering quirks can't distort the displayed values; `series` is sorted by outs
  for any future chart.
- **Graceful degradation.** Legacy games with NULL SABR stamping, ties, and
  unknown bat order all yield `None`/`—`; the panel and column simply omit
  themselves rather than erroring.
- **Thresholds deliberately NOT shipped.** League run pace ≈ 0.43 R/out ≈ 1.3
  per 3 outs, so the spec's 3.0 / 6.0 / 12.0 map to "hard / brutal / hopeless" —
  a reasonable *shape*, but the exact numbers are guesses until measured against
  real chase distributions. Holding behavior until the Pressure Curves are
  visible is the honest sequencing.

## 4. Validation

- `pytest o27/tests/test_rrr_3o.py` — 8/8 pass (start = target/9; walk-off ⇒ 0;
  exhausted envelope / no target ⇒ None; hand-computed mid-chase; reconstruction
  + legacy degradation).
- Seeded a 30-team scratch DB (`initdb` + `sim 30`) and rendered games via the
  Flask test client: RRR/3O column, Chase panel, and footer all present
  (HTTP 200). Spot-checks held — e.g. game 1 (home bats first, wins 23–16): away
  chaser Starting = 24/9 = 2.67, RRR climbs as they fall short; game 3 (home
  chases 8, walks off): Starting 0.89 → 0.0 by 9 outs left.
- `pytest tests/test_stat_invariants.py` (incl. new #12) + `pytest o27/tests`:
  196 passed, 1 skipped against the scratch DB.

## 5. What I did NOT change / known limitations

- **No manager-AI behavior (this phase).** Deferred by decision and shipped
  separately — see `docs/aar-rrr-manager.md`, which folds RRR into the existing
  leverage framework (best-bat deployment, swing-for-fences, concession).
- **Regulation chase only (phase 0).** Super-innings and Declared-Seconds chase
  phases are not modeled in the curve yet.
- **Two pre-existing test failures are unrelated to this change.**
  `o27v2/tests/test_managers.py::test_roll_manager_shape_for_new_types` and
  `test_trades.py::test_gm_noise_can_be_lopsided` (a flaky statistical assertion
  over a single random trade) fail on the scratch DB; neither references RRR and
  this branch touches no manager/trade code.
