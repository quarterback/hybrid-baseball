# AAR — CapSpace fantasy games: Go Streaking, Sluggers, Pilots, DFS rebalance

## What this was

CapSpace shipped with one live game (Daily Slate DFS) and a shelf of
non-functional format teasers. This work turned three of those teasers into
real, playable games and rebalanced the existing DFS scoring, all under one
design principle that emerged mid-build:

> **Counting stats are the backbone; O27-specific mechanics are seasoning, not
> the thesis.** People care about hits, runs, HR, RBI, strikeouts — the
> familiar stuff — and O27 can *add* to that. Nobody wants to draft around
> "2C-AB usage rate."

Everything here settles from **persisted** per-game stats
(`game_batter_stats` / `game_pitcher_stats`, `phase = 0`) and never re-sims,
per the determinism note in CLAUDE.md.

## What shipped

- **Go Streaking** (`streak.py`, `voyage` format → `streak` view). Hit-streak
  survivor: pick one hitter a slate, a hit (`hits > 0`) extends your streak, a
  hitless day resets it to zero. The most normal stat there is. Endpoints
  `GET /api/streak`, `POST /api/streak/pick`.

- **Sluggers** (`sluggers.py`, `walkback` format → `sluggers` view). A home-run
  counting game with the Walk-Back twist. Pick up to 3 sluggers a slate; score
  `HR×4 + RBI×2 + Run×1` — the homer plus the runs/RBI that bring the
  walk-back runners home. Season-running total + per-slate field-average /
  ceiling benchmark. Endpoints `GET /api/sluggers`, `POST /api/sluggers/{pick,remove}`.

- **Pilots** (`pitching.py`, `pilot` format → `pilots` view). The pitching
  game, built so O27's hitter-dominant world can be learned from the mound.
  Standard pitching counting stats lead — `K×3 + Out×1 − ER×2 + QS(+6)` — and
  the **new finisher stats from main** season it: Quality Finish (+6) and
  Terminal Out (×0.5). Same per-slate structure as Sluggers. Endpoints
  `GET /api/pilots`, `POST /api/pilots/{pick,remove}`.

- **Category leagues** (`categories.py`, `stay` format → `categories` view).
  A season-long **Roto engine** — one engine, four formats as config:
  - **Standard 5×5** — R/HR/RBI/SB/**OBP** + **K/QS/ERA/WHIP/QF** (OBP over
    AVG, QS over wins, and **Quality Finish** standing in for the nonexistent
    save).
  - **Razz (anti-league)** — every category direction inverts (worst real
    production wins) with **AB/out floors** so you must roster players who
    actually play, not bench dust.
  - **HR Derby** — single category (HR). **Pitchers Only** — the five pitching
    categories.

  You draft a roster; season aggregates (summed from persisted stats) feed each
  category; you're ranked Roto-style against a **computed field** of 48
  synthetic rosters spanning a skill spread. Endpoints `GET /api/categories`,
  `GET /api/categories/pool`, `POST /api/categories/draft`. The draft pool
  surfaces worst-players-first in Razz so the bad bats you want are on top.

- **Sportsbook** (`sportsbook.py`, `skipper` format → `sportsbook` view). A
  solo play-money book. The house posts a **moneyline** and **run total** for
  each slate game, priced from team form — regressed Pythagorean win% (log5 +
  home edge, with a vig) and regressed runs-for/against for the total — never
  from the game's predetermined seed, so the book can be beaten. Stake units
  from a persistent bankroll; bets settle off the final score. Endpoints
  `GET /api/sportsbook`, `POST /api/sportsbook/bet`. Verified the odds math,
  line generation, bankroll deduction/validation, and win/loss/push grading
  (a controlled +150 winner credited 100u→250u payout).

- **DFS scoring rebalance** (`data.py` `_W`, `_batter_fp`, `_pitcher_fp`; UI
  `SCORING`). Added **Stolen Base (+4)** and **HBP (+2)** (standard counting
  stats that were missing), added a **quality-start bonus** (+6, a starter
  going ≥18 outs with ≤3 ER) as the stand-in for O27's nonexistent pitcher
  win, and **demoted the O27 `stay` bonuses** from +3/+4 to small +0.5/+1
  flavor so a multi-stay game no longer rivals a homer. The UI legend was
  synced and now lists standard stats first, O27 stays last and labelled.

## Why "Pilots" leans on the new finisher stats

Main landed relief/finisher pitching stats (`terminal_outs`,
`quality_finish`, `lead_entries`/`lead_held`, `ir_inherited`/`ir_scored`) —
O27's structure-agnostic answer to having no innings and **no save rule**.
Terminal Outs ("outs that protected a never-relinquished lead") and Quality
Finish ("sealed 9+ final outs never trailing") are legible, counting-stat-like
measures of who closes games, so they fit the seasoning principle: familiar K
/ QS lead, finisher work adds the O27 texture. Saves/holds deliberately do not
exist, so no SV+HLD category was attempted.

## Validation (what was actually checked)

All three games were exercised end-to-end through the real Flask
`test_client`, plus direct module tests on fully-played slates:

- **Go Streaking:** settle returns `hit`/`miss` correctly; streak accumulates
  on hits and **resets on a miss** (hit-miss-hit → current 1, best 1).
- **Sluggers:** picked the slate's top-3 producers → score 69.0 matched the
  hand-computed `HR×4+RBI×2+Run×1`; season total accumulates; benchmark
  (field-avg / ceiling) bracketed the score correctly; 3-pick max + game-start
  lock enforced.
- **Pilots:** on a fresh DB (new schema) simmed 60 games — finisher stats
  populated (258 terminal outs, 11 quality finishes). Top-3-by-K pick scored
  141.5, matching `_score_row`; finisher contributions (a 27-out
  finisher-starter) flow through; benchmark and locks correct.
- **DFS rebalance:** batter fp 30.5 and pitcher QS fp 46.0 matched by hand;
  the DFS computed-field path still scores without error.

## What was NOT changed / known gaps

- **Walk-back runs are approximated.** Today's schema records a HR's own run
  and the walk-back run lives inside `runs`/`rbi`; there is no dedicated
  per-batter walk-back-run column. When one lands, Sluggers should swap the
  `Run` term for the explicit walk-back component.
- No salary cap / draft economy on the three new games — they are
  pick-from-the-slate, like Go Streaking, not cap-constrained like DFS.
- `pytest` is absent in the sandbox; validation was via `test_client` and
  direct module calls, not the suite.

## Categories engine — validation

Exercised through the real app and direct module tests on the 60-game DB:

- **Roto / all four formats** scored without error; drafting the top-skill
  roster in std5×5 → roto 480.5, rank 1/49, with per-category rank + points.
- **Razz inversion** proven: drafting the *best* players finished **49/49**
  (every category ranked worst). Drafting **bad-but-playing** players won
  **1/49**, and **bench-dust** (below the AB/out floor) was **DQ'd to last** —
  exactly the "worst players who actually play" design.
- **Draft slot validation** rejects wrong hitter/pitcher counts; the Razz pool
  correctly lists the weakest hitters first.

## Still queued

**Best Ball** — draft once, the best valid lineup auto-scores each slate; the
category engine already supplies most of the roster/field machinery it needs.
