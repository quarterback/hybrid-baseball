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

## 5. Follow-ups

- Smoke against a live DB; sanity-check league-wide that team luck sums to
  ~0 (it should, by construction of the baseline).
- Consider a RAD-graded "estimated bases" variant that credits a stay by
  the runner advancement it produced (derivable from `bases_before/after`
  + `runs_scored` on `game_pa_log`), which would make hard-hit stays read
  correctly instead of as "unlucky."
- The true resampled run-distribution / win-probability feature (the
  histogram artifact) as a separate, forward-looking matchup tool.
