# After-Action Report — Post-game card decisions + team records, and the "(L, 4-0)" box-score bug

**Date completed:** 2026-06-03
**Branch:** `claude/postgame-scorecard-display-dMhTC`

---

## TL;DR

Three tangled complaints about how finished games present pitcher decisions and
records:

1. **Box scores showed impossible decision records** — e.g. `Hlavacka (L, 4-0)`:
   a pitcher tagged with the loss whose season record didn't include a loss.
   And "sometimes the record is there, sometimes it isn't."
2. **The expanded (schedule) cards still showed the SP matchup post-game**,
   while the small (dashboard) cards already switched to W/L pitchers with
   records "like real MLB."
3. **No team records on the cards** — the user wanted the W-L line on each side,
   pre-game and updated after the game resolves.

The root cause of #1 was **two different W/L attribution algorithms**: the box
score *labeled* the losing pitcher by most **runs allowed** (one heuristic),
but the **record** printed beside the label came from the season W-L map, which
charges the loss by most **earned runs** (a different heuristic). When they
disagreed — **30 of 120** simulated finals — the labeled pitcher's record
didn't include that game's decision, so it read `(L, 4-0)` or, when the labeled
pitcher had no other decisions, `(L)` with no record at all (**26 of those 30**).

Fix: collapse all attribution onto **one canonical decision** in
`box_score.py` (`credit_win` / `charge_loss` / `decide_pitchers`), used by the
season W-L map, the per-game box-score label, the dashboard/schedule cards, and
the youth/world-cup boxes alike. After the change, **0 / 120** finals show a
loser without a loss or a winner without a win.

Then #2 and #3 are straightforward presentation: the schedule cards now render
W/L decisions (with records) for finals and keep the SP matchup only pre-game,
and both the dashboard and schedule cards carry each club's W-L on the side.

---

## What was wrong

### The decision/record split

- `box_score.render_pitching_table` prints the decision *tag* from a
  `decisions` map and the *record* from a `season_wl` map.
- `app.game_detail` built `decisions` inline: **W = last winning pitcher who
  recorded an out; L = losing pitcher with the most `runs_allowed`.**
- `app._pitcher_wl_map` (which *is* `season_wl`) charged: **W = SP if ≥12 outs
  else most-effective reliever; L = pitcher with the most `er`.**

`runs_allowed` vs `er` (and "last out" vs "starter") routinely pick different
pitchers. The label and the record were computed by different rules over the
same game, so they could point at different arms — and the record beside the
label was then simply *the wrong pitcher's season line*.

A second, smaller cause of "no record sometimes": a long-named decision pitcher
(`Gymnastikforening (L, 0-1)`) overran the fixed-width name column and the
record got **truncated off the end**.

---

## The fix

### 1. One source of truth (`o27v2/web/box_score.py`)

New pure helpers, no DB or app dependency:

- `credit_win(rows)` — SP keeps the win with `>= SP_OUTS_THRESHOLD` (12) outs,
  else the most effective reliever `max(outs - ER)`, tiebreak outs then earliest.
- `charge_loss(rows)` — most earned runs, tiebreak earliest appearance; always
  returns a pitcher when rows exist (so the L the label shows is the L the
  season map counted).
- `decide_pitchers(win_rows, lose_rows)` — the pair.

`SP_OUTS_THRESHOLD` now lives here; `app._SP_OUTS_THRESHOLD` re-exports it.

### 2. Everyone routes through it (`o27v2/web/app.py`)

- `_pitcher_wl_map` and `_attach_decisions` now **aggregate pitcher rows per
  player across phases** (a starter who also threw a super-inning is judged on
  his whole line) and call the shared helpers.
- New `_game_decision_pids(game)` returns `(w_pid, l_pid)` for one final from
  `game_pitcher_stats` with the *same* grouping + helpers, so a box score's
  label is guaranteed to be the pitcher whose season record it prints.
- `game_detail` (and the youth / world-cup box views) now derive decisions from
  the canonical helpers instead of ad-hoc inline logic.
- `box_text._pick_decisions` (the markdown export path) delegates to
  `decide_pitchers`.

### 3. Don't truncate the record

`render_pitching_table` now reserves room for the ` (W, x-y)` suffix and
truncates the **name** instead, so the decision/record never gets chopped.

### 4. Cards: decisions + team records

- New `_attach_team_records(games, as_of_date)` attaches `away_record` /
  `home_record` ("W-L"). Finals show the record **through** their date
  (standings after the game); unplayed games show what the clubs **bring in**
  (through the prior day). One query covers both cutoffs.
- Wired into the dashboard (`index`) and `schedule` routes.
- `index.html`: a muted record chip beside each team.
- `schedule.html`: a `gc-rec` chip on each side; finals render a `gc-dec`
  block (`W Atal (2-1)` / `L Schoenfield (0-1)`) and the SP matchup is shown
  **only pre-game**.

---

## Validation

- Reproduced the bug on a seeded 30-team DB (120 finals): **30** label/record
  mismatches, **26** labels with no matching season record. After the fix:
  **0** losers without a loss, **0** winners without a win; season totals stay
  balanced (`total_W == total_L == finals`).
- Long-name case now renders `Gymnastikfor (L, 0-1)` (name truncated, record
  intact) instead of `Gymnastikforening (L,` (record chopped).
- `pytest o27/tests o27v2/tests` → **235 passed, 1 skipped**.
- Rendered `/`, `/schedule`, and `/game/<id>` against the seeded DB: records on
  both card styles pre/post game, W/L decisions on the expanded finals, SP
  matchup retained for upcoming games.

---

## What I did *not* change

- **Saves are still not computed** — they need lead-state tracking the schema
  doesn't capture (unchanged from prior decisions).
- The *heuristics themselves* (12-out SP win, most-ER loss) are unchanged in
  spirit — the win was making every surface agree on one of them, not inventing
  a new rule.
- Team records on a card are "as of the card's anchor date." On the
  dashboard/schedule every game in a list shares that date, so the figure is
  exact; it is not a per-game historical replay for arbitrary mixed-date lists.
