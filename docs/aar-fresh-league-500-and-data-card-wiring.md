# After-Action Report — Fresh-League 500 (`derive_linear_weights` KeyError) + Wiring Salary / Manager / Sellback Cards

**Date completed:** 2026-05-09
**Branch:** `claude/wire-data-cards`
**Commits (in order):**
- `3a8092a` — Surface salary, manager archetype, and sellback trades in the UI
- (fix included in same commit) — `derive_linear_weights` defensive default for missing event types

---

## What was asked for

Two threads landed on top of each other in the same session:

1. **Audit the app for unwired features.** The user shipped a lot
   recently (tiered leagues, IPL auction, live-auction replay,
   currency overhaul, salary persistence, sellback trades) and
   wanted a sweep of "data exists, user can't see it" gaps.
2. **The user reported `i can't get a new league to load with the
   latest files on main`** — fresh league created post-restart was
   500'ing on every page that touched batter / pitcher stats.

Both turned out to be related. The audit surfaced three
template-level gaps that needed surfacing; the deploy bug was a
shared analytics helper that crashed when there was no game data
to derive linear weights from.

---

## What shipped

### A. The fresh-league 500

**Symptom:** Brand-new league, no games simmed yet, every page that
calls `_aggregate_batter_rows()` or `_aggregate_pitcher_rows()`
(team detail, player detail, standings, leaders, players list)
returns 500 with:

```
KeyError: '1B'
  File "o27v2/analytics/linear_weights.py", line 273, in derive_linear_weights
    raw = {et: rv[et] - rv_out for et in ("BB", "HBP", "1B", "2B", "3B", "HR")}
```

**Root cause:** `derive_linear_weights()` builds the `rv` dict from
events observed in `game_pa_log`. With zero games played, the
event loop produces no entries, so `rv_sum` is empty and the
comprehension that initialises `rv` produces only the keys it
later adds unconditionally (`BB`, `HBP`, `K_over_out`). The wOBA-
weight block then tries to subtract `rv["out"]` from `rv["1B"]`,
`rv["2B"]`, etc., none of which were populated.

The function had defensive guards on `pa > 0` (the OBP-scale block)
and `n_starters > 0` (the GSc-tuning block), but the dict-key
lookups for the raw event types were unguarded.

**Fix:** Defensively populate every event type the rest of the
function dereferences, defaulting to 0.0 when no plays produced
the type:

```python
rv = {et: (rv_sum[et] / rv_n[et]) if rv_n[et] else 0.0 for et in rv_sum}
for _et in ("out", "1B", "2B", "3B", "HR"):
    rv.setdefault(_et, 0.0)
rv["BB"]  = _walk_run_value(re_map, state_p)
rv["HBP"] = rv["BB"]
```

Semantically correct: no data → no signal → all derived weights
collapse to 0, the league wOBA = 0.0, and downstream consumers
get a no-op set of weights instead of a 500. Once games are
simmed, the function picks up real values on the next call (the
LRU-style cache invalidates per request via `_LINEAR_WEIGHTS_CACHE`,
which is reset at process boot).

**Verified:** All nine main pages now load on a fresh league:

```
/, /standings, /team/1, /player/1, /teams, /leaders,
/players, /youth, /auction → all 200
```

### B. Surfacing data that was silently persisted but never rendered

The audit (run via the `Explore` agent + my own verification pass)
found three gaps where the underlying data was present and correct,
but no template referenced it:

1. **Salaries on player detail + team roster.** `players.salary` is
   persisted at seed time via `o27v2/valuation.py::estimate_player_value()`.
   Already rendered on `/free-agents` and `/leaders`, but
   `/player/<id>` and `/team/<id>` left it off.
   - Add a "Salary" line in the player detail header strip,
     next to the existing "Est. value" derivation.
   - Add a "Salary" column to both batter and pitcher roster
     tables, with `data-sort-value` so sortable templates can
     order by the numeric value rather than the formatted label.
   Both render through the existing `money` Jinja filter so the
   guilder-toggle pill cycles ƒ → $ → € on these new cells too.

2. **Manager archetype + tactical axes on `/team/<id>`.**
   `teams.manager_archetype` plus the eight axis columns
   (`mgr_quick_hook`, `mgr_bullpen_aggression`, `mgr_leverage_aware`,
   `mgr_joker_aggression`, `mgr_pinch_hit_aggression`,
   `mgr_platoon_aggression`, `mgr_run_game`, `mgr_bench_usage`)
   are persisted at seed time and drive both in-game tactical
   decisions AND the auction-day personality (saber + joker_aggr
   feed `_team_auction_profile`). They were rendered nowhere.
   - Add a compact "Manager" strip on `/team/<id>` right under
     the existing header. Shows the archetype's friendly label
     (e.g. `mad_scientist` → "Mad Scientist") plus the eight
     axes as integer percentages, each with a hover-title
     explaining what that axis controls.
   - New `archetype_label` Jinja filter wraps
     `o27v2.managers.archetype_label()` so the snake-case keys
     resolve to human labels in the template.

3. **Sellback trades on the static `/auction` page.**
   `auction_results.traded_to_team_id` + `trade_price` were
   persisted by the trade mechanic (PR #35) and rendered as
   "⇄ Traded to" callouts in the live-auction replay, but the
   static `/auction` page only showed the original Vickrey
   winner. A user who skipped the live replay never learned
   that any sellbacks had happened.
   - Add a "Final owner" column to the top-30-sales table.
     When `traded_to_abbrev` is non-null, render
     `⇄ <buyer> <trade_price>` in the accent color; otherwise
     em-dash.
   - `get_auction()` now joins to the traded-to team and
     returns the trade columns in the shape the template
     consumes.

---

## End-to-end smoke (rng_seed=42, 56-team tiered)

| Page | Status | Notes |
|---|---|---|
| `/` | 200 | Homepage clean |
| `/standings` | 200 | Tiered standings render |
| `/team/1` | 200 | Manager card + salary column visible |
| `/player/1` | 200 | Salary line in header strip |
| `/auction` | 200 | "Final owner" column with `⇄` arrows on the 7 trades |
| `/auction/live` | 200 | Unchanged from PR #36 |
| `/youth` | 200 | Unchanged from PR #36 |
| `/leaders` | 200 | Pre-existing salary leaderboard works |
| `/players` | 200 | Player list renders |

---

## What I'd do differently

**The fresh-league 500 should have been caught by an integration
smoke test long ago.** The function had been live for weeks and
worked on every existing save (because all those saves had real
game data). The failure mode is exactly the one a smoke test
should catch — load every nav route on a freshly-seeded DB,
assert all return 200 — but no such test exists. Filed as
follow-up #1 below.

**The fix is correct but it papers over a structural issue.**
`derive_linear_weights` mixes "summarise the existing run-expectancy
matrix" with "derive league wOBA scaling". The first half should
return whatever the data supports, including empty results; the
second half should be optional / gated on `pa > 0`. The
current shape — one big function with implicit empty-data
contracts on dict keys — is a footgun. A future refactor should
split the empirical-RV pass from the wOBA-scale pass and have the
latter return `None` when there's no league data, with consumers
defaulting to MLB-fit weights as a fallback.

**Should have run the audit before responding to the user's first
"is anything missing" prompt rather than after.** The agent
caught most of the gaps but also produced a few false positives
(claimed player flags weren't rendered when they actually were)
that I had to manually verify. Rule for next time: trust-but-verify
on a sub-agent's findings before quoting them — grep takes 5 seconds.

**Should have batched the audit-fix and the bug-fix into separate
PRs.** I shipped them in a single branch (`claude/wire-data-cards`)
because the bug-fix was discovered while smoke-testing the audit
fixes. Separate PRs would have made the bug fix easier to cherry-
pick if a deploy needed only the 500-fix and not the new UI cards.
For low-stakes work it's fine; in a more careful environment
that's a habit to break.

**Manager card layout is functional but ugly on narrow viewports.**
The eight axes fit on one row at desktop width, but mobile wraps
the row mid-axis. A grid layout with explicit columns would be
cleaner. Punting on this until the rest of the responsive
behaviour gets a pass.

---

## Pointers for follow-up work

1. **Fresh-league smoke test.** Add `tests/test_fresh_league.py`
   that creates an in-memory DB, runs `seed_league` for each
   preset config (`12teams`, `16teams`, `24teams`, `30teams`,
   `36teams`, `8teams`, `56teams_tiered`), and asserts every
   route reachable from the top nav returns 200 before any games
   are simmed. ~30 lines and would have caught the linear-weights
   bug at PR time.

2. **`derive_linear_weights` structural cleanup.** Split the
   function into `_empirical_rv_per_event_type()` (returns
   whatever data supports) and `_woba_scale_factor()` (gated on
   league PA > 0, returns `None` for empty leagues). The current
   single-function shape is a known footgun.

3. **Persisted MLB-fit fallback weights.** When the league has
   no game data, `derive_linear_weights` now returns all-zeros.
   That's safe but unhelpful — `/leaders` and `/standings` will
   show 0.000 for wOBA across the board. A better default would
   be MLB-fit static weights (BB=0.69, HBP=0.72, 1B=0.88, 2B=1.24,
   3B=1.56, HR=2.00) until the league has accumulated enough
   PAs to derive its own. ~5 lines in `derive_linear_weights`'s
   empty-data branch.

4. **Manager card on `/teams` index.** The detail page now shows
   the archetype, but the teams-list page still shows only org
   strength. Adding a small archetype label in the team-list row
   would let users browse "which teams have which skipper style"
   at a glance.

5. **Sellback trade column polish.** Currently shows just `⇄ ABBR
   <price>`. Could add the original-winner abbrev too:
   `KCR → ⇄ NYM ƒ47 cr` so the flip is legible without a hover
   tooltip. Minor.

6. **Joker filter on `/youth`.** The audit flagged
   `is_joker / joker_archetype` columns are persisted and shown
   on individual youth-team pages, but the `/youth` browse page
   only filters by `bat / arm / stars`. Adding a `jokers` filter
   would let users browse the unusual-archetype prospects
   directly.

7. **Live auction discoverability.** The "▶ Watch the auction"
   button only appears on `/auction` and only after an auction
   has run. First-time users who haven't reached the off-season
   never see the live page exists. Either a nav entry, or a
   homepage tile when an auction has just landed, would help.

8. **`org_strength` tooltip explaining its full effect.** The
   badge tier names ("Elite / Good / Replacement") read as
   purely aesthetic; the tooltip should mention the three
   things it actually drives in the engine: per-attribute roll
   bonuses at seed time, multi-season player development rates,
   AND auction-day discipline (tighter noise on bid valuations).
