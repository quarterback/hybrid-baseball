# After-Action Report — Luck Ledger (estimated-vs-actual bases)

**Date:** 2026-06-21
**Branch:** `claude/vigilant-davinci-hn34xy`
**Status:** Shipped to the game page. Validated against a synthetic DB
(no live DB in the build sandbox); needs a smoke-check against a real
`o27v2.db` before release.

---

## 1. Why this happened

The owner pointed at [`dgrifka/baseball_game_simulator`](https://github.com/dgrifka/baseball_game_simulator)
— an MLB "deserve-to-win" simulator — and asked whether anything was worth
leveraging. The repo's *code* isn't: it trains a Gradient Boosting model on
~250K real Statcast batted balls and resamples actual games. O27 has the
opposite problem (a real engine, no real data), so the model doesn't
transfer. But its **output artifacts** do, and one in particular — the
"Luck Ledger" (estimated production vs. actual, plus the biggest per-player
luck swings) — maps directly onto data O27 already persists. The owner
picked it ("build #1").

The `game_pa_log` schema comment already *named* this use case:
*"Batted-ball physics hybrid layer … stamped here for downstream
visualization (spray charts, EV/LA-banded Luck Ledger, xwOBA
attribution)."* So the data substrate was anticipated; this just builds the
consumer.

## 2. What got built

- **`o27v2/analytics/luck_ledger.py`** — `build_game_ledger(game_id)`.
  - Reuses the exact (EV, LA)-bin surface the xwOBA analytics use
    (`_ev_la_bin_sql`, `_EV_EDGES`, `_LA_EDGES` from `expected_woba`), so
    the "estimated bases" baseline stays in sync with xwOBA rather than
    being a parallel binning.
  - `event_bases()` is the single scoring function used for **both** the
    actual tally and the league baseline: HR=4, 3B=3, 2B=2,
    single/**credited-stay**=1, out/error/uncredited-stay=0. Walks/HBP add
    one base each at the team level (mirroring the reference's "+walks").
  - Per team: estimated production (batted + walks), actual production,
    and `luck = actual − estimated`.
  - Per player: every BIP's `est → act`, ranked by `|luck|`, top 5 a side.
  - Deserve-to-Win: a Pythagorean win expectancy on estimated bases
    (exponent 2.0), as a single headline number per side.
- **`o27v2/web/app.py`** — `game_detail` builds `luck_ledger` (guarded;
  `None` on failure/legacy) and passes it to the template.
- **`o27v2/web/templates/game.html`** — a Luck Ledger panel below the
  spray charts: per-team est/actual/luck summary + a biggest-swings table,
  styled to match the existing spray-chart section.

## 3. Design decisions / honesty

- **The stay convention is the one real judgment call.** A credited stay
  is worth 1 base (matching `STAY ≈ 1B` in the wOBA linear weights). Because
  the (EV, LA) baseline is built with the *same* `event_bases` function over
  the whole league, the baseline already absorbs the league stay rate — so
  the estimated/actual comparison is unbiased in aggregate. An individual
  hard-hit *stay* can still read "unlucky" (0 actual bases vs a positive
  bin estimate), which is a known cosmetic wrinkle, not a scale bias.
- **Deserve-to-Win is deliberately lightweight.** It's a bases-Pythagorean
  proxy, *not* the resampled run distribution the reference shows (that's a
  separate, forward-looking feature — the 10K-sim histogram). Labeled as an
  estimate in the UI so it doesn't overclaim. The engine being
  seed-deterministic means a true distribution is buildable later by
  re-simming across seeds, which would supersede this number.

## 4. Validation

- `event_bases` / aggregation / SQL exercised end-to-end against a
  synthetic in-memory DB: per-team est/actual/luck and ranked swings all
  come out correct; the only "off-looking" numbers were an artifact of the
  random synthetic league (bins not representative), not the logic.
- `game.html` passes a Jinja parse; `luck_ledger.py` and `app.py` compile.
- **Not done:** run against a real `o27v2.db` (none in the sandbox), and a
  visual check of the panel. Both are the obvious next step before release.

## 4b. Recalibration — finer EV/LA grid (same day)

First live look at real games (#758, #791, #813 — all power blowouts)
showed the failure mode the AAR's §3 only half-anticipated: the 5×5
(EV, LA) grid borrowed from xwOBA lumps *every* 100–110 mph / 24–40°
barrel into ONE bin whose league-average bases (~2.0, dragged down by
caught flies) made genuine home runs read as "+2.0 lucky." A 9-HR, 39-run
demolition came out "+16.2 lucky," which is nonsense — crushing the ball
isn't luck.

Fix: `luck_ledger.py` now uses its **own** finer grid
(`_LL_EV_EDGES` 8 bands 70→112, `_LL_LA_EDGES` 8 bands −10→48), kept local
so the xwOBA calibration is untouched, with a sample-size fallback
(fine bin → EV-marginal → global, `_MIN_BIN_N = 20`) so sparse fine bins
don't inject noise. Validated on a dense synthetic league: a 100°/35°
barrel now estimates ~3.2 bases (was 2.0), so a HR there reads ~+0.8
instead of +2.0, and the per-player luck table surfaces real luck
(a hard liner caught, a flare that dropped) instead of "lucky home runs."

## 4c. Known limitation surfaced by real data — the currency is incomplete

Game #794 (Karaj 11, Nevelsk 8) is the important one: Karaj scored **11
runs on 6 singles, 0 XBH**, off walks + the stay/advancement game. The
estimated-bases model (batter total bases + 1/walk) can only see batter
contact, so it rated Karaj's offense weak (7.7 est batted) and even gave
the *opponent* the higher deserve-to-win. In O27 a large share of run
production is **runner advancement (RAD) and on-base/sequencing**, not the
batter's own total bases — so a batter-TB proxy systematically mis-ranks
games won by small-ball/walks. The contact-quality *luck table* is still
right per ball; it's **deserve-to-win** that needs a runs-based currency
(BaseRuns / linear weights over the full event set, incl. walks and
advancement) rather than a bases proxy. Flagged for the owner as the next
decision.

## 4d. Deserve-to-win moved to expected runs (owner decision)

Owner chose to fix §4c properly: deserve-to-win now predicts **expected
runs**, not a bases proxy. Each ball's (EV, LA) bin yields an expected
event mix (P[1B], P[2B], P[3B], P[HR]) alongside expected bases; summed
over a team's contact and combined with its *actual* walks/HBP/AB, that
line is run through the league-fitted **BaseRuns** estimator
(`o27v2/analytics/base_runs.py`), and a Pythagorean on the two expected-run
totals gives the win share. BaseRuns is already calibrated to O27's run
environment and values walks/singles with diminishing returns, so a
contact-light, walk-and-advance offense (game #794) is no longer undersold.

Changes:
- `base_runs.py`: `build_base_runs_table` now also returns
  `fitted_b_scale_off` / `fitted_b_scale_def` (the fitted-coeff run scale),
  which the ledger needs to put a single game's BaseRuns on the league run
  scale.
- `luck_ledger.py`: `_event_components` is the one event→(bases, hit, 2B,
  3B, HR) definition shared by both lenses; the estimator now returns the
  full expected vector per bin; `_run_model()` memoizes the fitted
  BaseRuns coeffs+scale on a DB-version key (count of played regulation
  games) so the refit runs once per sim state.
- Template leads with **deserve-to-win % + expected runs**, shows actual
  vs expected runs (sequencing luck), and keeps the per-ball **contact
  luck** table as the batted-ball lens.

The two lenses are now clearly separated: *contact luck* (batted-ball,
per ball) and *run luck* (sequencing, actual − expected runs). DTW is the
expected-runs comparison.

Validated on a synthetic 600-game league: a 9-HR power line gets ~91% DTW
(and reads sequencing-unlucky when it under-scores), while a 6-single +
7-walk line produces a sensible ~5 expected runs instead of being undersold.

## 4e. Running game folded into expected runs (owner ask)

Owner: "if the running game makes sense to add, add it." It does — stolen
bases and caught stealing are invisible to BaseRuns' event line (a CS is a
baserunning out, not an AB / not in H-2B-3B-HR-BB-HBP), so adding them
doesn't double-count.

- `linear_weights.py`: `_steal_run_values(re_map, state_p)` derives SB/CS
  run values from the same O27 run-expectancy map the wOBA weights use
  (steal-of-second model: SB advances 1B→2B, CS erases the runner and
  adds an out, each averaged over PA-start state occupation). Surfaced as
  `rv["SB"]` / `rv["CS"]` in `derive_linear_weights`. On synthetic RE data
  these come out SB ≈ +0.17, CS ≈ −0.38 (correct signs/magnitudes).
- `luck_ledger.py`: `_run_model` now also returns the SB/CS run values;
  `build_game_ledger` reads each team's actual SB/CS and adds
  `sb·rv_SB + cs·rv_CS` to BaseRuns expected runs. Pythagorean inputs are
  clamped ≥ 0 (a CS-heavy line can in principle net below zero).
- Template shows a `running: N SB · M CS (± runs)` line per team.

Scope note (owner explicitly fine with non-1:1 vs MLB): the model only
counts SB/CS, not first-to-third on a single or other extra-base taking —
that advancement is already inside the actual runs and partly inside
BaseRuns. Good enough; the goal was to display the running game's value,
not to fully decompose baserunning.

## 4f. Build/deploy state (for the record)

`main`'s live build `46f8e79` is the PR #264 merge that shipped **v1**
(coarse grid, bases-based DTW). The finer grid (§4b), expected-runs DTW
(§4d), and running game (§4e) are on branch
`claude/vigilant-davinci-hn34xy` ahead of that merge and need a fresh
merge to `main` to go live.

## 5. Follow-ups

- Smoke against a live DB; sanity-check league-wide that team luck sums to
  ~0 (it should, by construction of the baseline).
- Consider a RAD-graded "estimated bases" variant that credits a stay by
  the runner advancement it produced (derivable from `bases_before/after`
  + `runs_scored` on `game_pa_log`), which would make hard-hit stays read
  correctly instead of as "unlucky."
- The true resampled run-distribution / win-probability feature (the
  histogram artifact) as a separate, forward-looking matchup tool.
