# After-Action Report — Motivation-driven trade engine

**Date completed:** 2026-05-17
**Branch:** `claude/add-trading-system-Drdve`
**Commit:** `8b3ac7d` (single commit; squash candidate on merge)

---

## Context

The user opened the session with an unpunctuated thesis statement:

> Trading in this game's main function ought to be a player
> distribution mechanic as well as a way to get talent blocked into
> roles th play as well as injuries a way to backfill injuries and
> other reasons trades should happe including irrratioonal ones

Parsed into four design goals:

1. **Player distribution** — trades as the league's main mechanism
   for moving talent around (above and beyond free agency / waivers).
2. **Talent unblocking** — a high-skill reserve sitting behind an
   active starter at the same position should be tradeable for
   value rather than rotting on the bench.
3. **Injury backfill** — long-IL holes at critical positions should
   prompt the front office to chase a healthy player via trade, not
   just promote a reserve in memory.
4. **Irrational reasons** — salary dumps, fire sales, GM noise,
   star demands. Trades should not be purely value-balanced math.

The existing `o27v2/trades.py` (~390 lines) was purely heuristic:
contenders bought from sellers at a 2/3-season deadline, plus 0–2
random in-season trades, total ~5–10 league-wide per 162-game season.
No FO identity, no injury sensitivity, no irrationality.

User decisions (via AskUserQuestion, four questions):

- **Scope**: rewrite `trades.py` from scratch.
- **Irrationality**: salary dumps + win-now overpays, rebuilds /
  fire sales, pure GM noise + star demands.
- **Volume**: high activity — 20–40 trades per team per season.
- **FO personality**: persisted columns on the `teams` table; drifts
  year-over-year.

---

## Design

### Motivation framework

Nine motivations evaluated per team per game-date. Each scores
`(value: 0..1, ctx: dict)`; the team's `fo_strategy` multiplies the
score; weighted-pick from top-3 above `MOTIVATION_FLOOR = 0.10`.

| Motivation | Trigger | Counterparty | Offer shape |
|---|---|---|---|
| `block_breaking` | Reserve `trade_value` within 0.10 of same-position starter | Team with weaker starter at that position | 1-for-1 |
| `injury_backfill` | Long-IL hole at C/SS/CF/P with > 14 days remaining | Team with surplus depth at the position | 1-for-1 |
| `deadline_buyer` | `phase ∈ {middle, late}` AND `win_pct > .500` | Seller | 2 mid for 1 star |
| `deadline_seller` | `phase ∈ {middle, late}` AND `win_pct < .500` | Buyer | 1 star for 2 mid |
| `salary_dump` | Payroll > 1.15× league median | Team with payroll < 0.85× median + `fo_aggression > 0.5` | 1-for-1 (high-$ out, cheap in) |
| `rebuild_fire_sale` | `fo_strategy = 'rebuild'` AND ≥ 30% roster age ≥ 30 | Contender / win-now | 1 vet (≥ 30) for 2–3 young (≤ 24) |
| `win_now_overpay` | `fo_strategy = 'win_now'` AND `fo_aggression > 0.5` | Rebuild / develop | 3 youngish for 1 star |
| `gm_noise` | Flat 0.11 floor for everyone | Random | 1-for-1 at relaxed threshold |
| `star_demand` | Losing streak ≥ 7 OR (games > 60 AND win_pct < .300) | Top-3 contender | 1 star for 2 mid-tier |

Acceptance is decided from the **partner's** perspective:

```
threshold = ACCEPTANCE_THRESHOLD[partner.fo_strategy]
  rebuild  0.85   win_now  0.80   develop  0.90
  contend  0.92   balanced 0.95
if motivation == 'gm_noise':        threshold *= 0.5
if motivation == 'win_now_overpay': threshold *= 0.7
if motivation == 'star_demand':     threshold *= 0.85
if motivation == 'injury_backfill': threshold *= 0.85   # added mid-session
# rebuild partners get a +30% incoming bonus per (avg_age_send - avg_age_recv)/10
# win-now partners get a +20% bonus when max_trade_value of incoming > outgoing
# archetype-bias partners get +15% on incoming when archetype matches
return sum(trade_value(p) for p in incoming) >= threshold * sum(trade_value(p) for p in outgoing)
```

### Front-office persona (`o27v2/front_office.py`)

Five strategies distributed per 30-team league:
`win_now: 4`, `contend: 5`, `balanced: 8`, `develop: 8`, `rebuild: 5`.

Per-strategy motivation weights live in `STRATEGY_MULT`. A win-now
team weights `win_now_overpay × 2.5` and `rebuild_fire_sale × 0`;
a rebuilder is the mirror image. Balanced teams are close to 1.0
across the board.

`fo_aggression` (Gaussian μ=0.5, σ=0.2, clamped to [0,1]) scales
the per-tick initiation probability — `p = BASE_INITIATE_PROB[phase] × (0.5 + fo_aggression)`.

`fo_archetype_bias` ('', 'power', 'speed', 'contact', weighted
6:1:1:1) feeds the +15% incoming bonus during acceptance.

`drift_fo_strategies()` runs once per offseason from
`development.run_offseason()`. Drift table:

- rebuild + good wp → develop
- develop + good wp → contend
- contend + good wp → win_now
- balanced + good wp → contend
- win_now + bad wp → balanced
- contend + bad wp → develop
- develop + bad wp → rebuild
- balanced + bad wp → rebuild

25% of would-be moves are blocked ("sticky GM") so the league doesn't
oscillate every season.

### Volume tuning

```
BASE_INITIATE_PROB = {
    'early':         0.06,
    'middle':        0.10,
    'late':          0.28,
    'post_deadline': 0.01,
}
MOTIVATION_FLOOR = 0.10
```

`late` is the final 25% of the pre-deadline calendar; it carries
~55% of the season's volume. Synthetic 183-day pre-deadline test
with 30 teams produces ~395 trades = ~26 per-team-involvement,
inside the user's 20–40 target band.

### Guardrails

Hard-coded in `_roster_floor_ok_after`:

- Every canonical hitter position (C, 1B, 2B, SS, 3B, LF, CF, RF)
  must keep ≥ 1 healthy non-joker on the post-trade roster.
- Team must keep ≥ 5 healthy non-joker pitchers post-trade.
- Joker players are filtered out of `_get_tradeable_players` entirely
  (matches `injuries.py` joker handling).
- Each team can complete at most one trade per `game_date` (per-date
  throttle via `fo_last_trade_date`; reinforces the existing
  `sim.py` "fire trades on last game of date" contract).

---

## Files touched

| File | Change |
|---|---|
| `o27v2/trades.py` | Full rewrite (390 → 643 lines). `trade_value` preserved byte-for-byte; `check_deadline_and_trades` signature preserved. |
| `o27v2/front_office.py` | NEW. `roll_fo`, `drift_fo_strategies`, strategy constants. |
| `o27v2/db.py` | `CREATE TABLE teams` extended with 5 FO columns; matching `ALTER TABLE` block appended to `init_db()` using the existing `try/except` pattern. |
| `o27v2/league.py` | `roll_fo(rng2)` call alongside `roll_manager`; teams INSERT extended. |
| `o27v2/development.py` | `drift_fo_strategies(rng)` called at the end of `run_offseason()`. |
| `o27v2/web/app.py` | `event_types` list at `/transactions` extended with the 9 new `trade_*` types; legacy `deadline_trade`/`inseason_trade` preserved for old rows. |
| `o27v2/tests/test_trades.py` | NEW. 11 tests. |

`trade_value` was deliberately not touched — `valuation.py:_BANDS`
maps its exact 0..1 output to salary tiers, and any drift here
would silently re-tier every player's salary in the league.

---

## Tests

All 11 in `o27v2/tests/test_trades.py` pass:

| # | Test | Asserts |
|---|---|---|
| 1 | `test_trade_value_signature_preserved` | Returns float in [0,1]. |
| 2 | `test_trade_value_snapshot` | 5 fixed cases reproduce exact prior outputs. |
| 3 | `test_check_deadline_and_trades_signature` | Returns list[dict] with keys `event_type`, `team_id`, `player_id`, `detail`. |
| 4 | `test_fo_schema_migration_idempotent` | Two consecutive `init_db()` calls leave each FO column present exactly once. |
| 5 | `test_injury_backfill_finds_same_position` | Long-IL SS on team A + season-late window → an SS lands on team A. |
| 6 | `test_rebuild_fire_sale_sends_vets` | ≥ 60% of rebuilder-shipped players are age ≥ 30. |
| 7 | `test_win_now_overpay_overshoots` | Win-now team sends more players than it receives in ≥ 60% of overpay trades. |
| 8 | `test_roster_floor_holds` | After 80 motivation passes across 18 distinct dates, every team has each canonical position covered and ≥ 5 healthy pitchers. |
| 9 | `test_gm_noise_can_be_lopsided` | ≥ 20% of `trade_gm_noise` events show side-imbalance > 0.15. |
| 10 | `test_drift_rebuild_to_develop` | Rebuild + 90W/72L drifts to develop/contend within 20 drift calls. |
| 11 | `test_roll_fo_strategy_keys` | 200 rolls cover all five strategies and produce all archetype-bias options. |

Two pre-existing test failures (`o27v2/tests/test_phase8_db_migration.py`
× 2, `tests/test_weather_calibration.py` × 1) were confirmed unrelated
via `git stash` baseline check.

---

## Friction encountered

### Per-date throttle starved volume tests

The first attempts at tests 5–7 ran multiple `run_motivation_pass`
calls against a single `game_date` and were surprised when only one
trade fired per team across 40 iterations. Root cause: the
`fo_last_trade_date` throttle is by date, not by call. Fix:
iterate across distinct ISO dates inside the 'late' window
(`date.fromisoformat('2024-07-14') + timedelta(days=i % 18)`).

This is also how the production sim works — `check_deadline_and_trades`
is called once per calendar date from `sim.py`, so the test loop now
mirrors reality.

### SQLite UPDATE … LIMIT

Initial fixture used `UPDATE players SET injured_until = ? WHERE … LIMIT 1`.
SQLite default builds reject the `LIMIT` clause on UPDATE. Replaced
with an explicit `SELECT id … LIMIT 1` followed by `UPDATE … WHERE id = ?`.

### `injury_backfill` partners rejecting fair trades

First green run of test 5 showed team A initiating injury_backfill,
building a valid offer, hitting `_validate_offer`, then bouncing on
`_evaluate_offer`. Partner threshold (default 0.95 for balanced)
rejected a fair 1-for-1 where the partner gave up a useful SS.

Fix: added `motivation == 'injury_backfill': threshold *= 0.85`
to the partner-side discounts (mirrors `star_demand`'s logic).
Rationale: a partner sending from their depth surplus is shedding
genuine surplus and should be more willing to deal, same as in MLB.

### `gm_noise` baseline dominated synthetic environments

First volume sweep: `trade_gm_noise` was ~49% of all trades —
because in a freshly-seeded league with no games played, no
injuries, and no losing streaks, most motivations score 0 and
`gm_noise` is the only thing above the floor. Lowered `gm_noise`
from `0.15` to `0.11` (just above `MOTIVATION_FLOOR = 0.10`) so
it still fires when nothing else does, but yields when real signals
exist. In a real simulated season with accumulated injuries and
diverged records, the share drops naturally.

### Phase8 + weather test failures

Pre-existing. `test_phase8_db_migration.py` expects pitchers seeded
with `pitcher_role='workhorse'` but Task #65 cleared that column.
`test_weather_calibration::test_extreme_weather_within_calibration_envelope`
is a known sampling-noise flake. Confirmed unrelated and left as-is.

---

## What's deferred

1. **Offseason trade window.** The plan flagged this as an open
   question; the user chose "high activity, concentrated in-season,"
   so no offseason trade pass was added. `run_offseason()` is the
   natural place to add `run_motivation_pass(offseason_date, ...)`
   if a midwinter hot-stove feel is wanted later.

2. **Volume invariant test.** The plan listed a `test_volume_in_band`
   that runs a full 162-game season and asserts 300 ≤ trade events ≤ 700.
   Not included — too RNG-sensitive for a unit test, and the realistic
   way to measure is the existing post-sim SQL spot-check listed in
   the verification plan below.

3. **Legacy DB backfill CLI.** Existing seeds get `fo_strategy='balanced'`
   defaults on first `init_db()` after this change. Trade behavior on
   a long-running save will be blander than on a fresh seed until a
   one-shot `python o27v2/manage.py backfill_fo` is added. Out of
   scope for this commit; trivial to add.

4. **In-game FO surfacing.** The team detail page does not yet show
   `fo_strategy` / `fo_aggression` / `fo_archetype_bias`. The columns
   are queryable but invisible. Adding a "Front office" row alongside
   the manager block is a small Flask template change.

5. **Star-demand narrative.** `star_demand` fires when a team's best
   player wants out, but the transactions log just shows it as a
   regular `trade_star_demand`. No "Player X has demanded a trade"
   pre-event headline. Would need a separate pre-trade event_type and
   transactions UI work.

6. **Counter-counter-tactics.** No support yet for vetoed trades
   (e.g., the player has a no-trade clause), three-team deals, or
   conditional / future-considerations trades. Single-counterparty
   1-for-1 / 1-for-2 / 1-for-3 covers the design goals.

7. **Trade-deadline event UI.** The `/transactions` page now lists all
   nine new `trade_*` types but doesn't visually group "deadline week"
   trades vs. routine ones. A `phase` column on `transactions` would
   help; out of scope here.

---

## Verification plan

1. **Unit tests** — `pytest o27v2/tests/test_trades.py -v` → 11/11 green.
2. **Reseed + season sim** — run the project's seed CLI on a fresh
   DB, simulate one 162-game season, then verify:

   | Check | SQL |
   |---|---|
   | Trade volume in band | `SELECT COUNT(*) FROM transactions WHERE event_type LIKE 'trade_%';` → expect 600–1500 rows (≈ 300–600 trades for a 30-team league). |
   | No team starved | `SELECT team_id, COUNT(*) FROM transactions WHERE event_type LIKE 'trade_%' GROUP BY team_id;` → no team below 10 events or above 80. |
   | No orphaned players | `SELECT id FROM players WHERE team_id IS NULL AND id NOT IN (<initial FA pool>);` → empty. |
   | Per-position floor | For each team: `SELECT position, COUNT(*) FROM players WHERE team_id = ? AND is_active = 1 GROUP BY position` — every canonical position present. |
   | Motivation mix | `SELECT event_type, COUNT(*) FROM transactions WHERE event_type LIKE 'trade_%' GROUP BY event_type;` — expect `gm_noise` < 35% of total once real signals (injuries, records) are present. |

3. **Idempotent migration** — call `init_db()` twice on the same DB
   file. No exceptions; `fo_*` columns present exactly once.
4. **Web UI smoke** — visit `/transactions?type=trade_rebuild_fire_sale`.
   Page renders; only rebuild trades shown.
5. **Offseason drift** — run `development.run_offseason()` on a sim'd
   season. Inspect `fo_strategy` deltas in the returned `fo_moves` list.

---

## Commit trail

```
8b3ac7d  Add motivation-driven trade engine with FO personalities
```

Single commit by design — the plan was approved before any code
was written, and the rewrite is one atomic unit (the old `trades.py`
shares only `trade_value` with the new file). On merge this could be
squashed further; alternatively it could be left as the single record
of "the trade rewrite."

---

## Connecting design — distribution vs. realism

The user's intent reads as a balance: trades should be **realistic
enough** that contenders chase stars and rebuilders ship vets, but
**irrational enough** that a power-hungry GM occasionally pays too
much for a slugger or a sub-.300 team's ace forces his way out.

The motivation framework expresses both:

- **Realism** lives in the `STRATEGY_MULT` table — each strategy
  has a coherent "voice" that biases which motivations fire.
- **Irrationality** lives in the relaxed acceptance thresholds
  (`gm_noise × 0.5`, `win_now_overpay × 0.7`) and the flat `gm_noise`
  baseline. Even a balanced FO will, given enough chances, make a
  clearly lopsided deal.

The two interlock through `fo_aggression`: high-aggression teams
both initiate more often AND are more willing to be the counterparty
in salary dumps. The result, on a fresh seed, is a league where some
GMs are aggressive idiots, some are patient developers, and most are
boring — exactly the dynamic the user described.
