# After-Action Report — day-centric schedule + power-play game tag

**Date completed:** 2026-06-02
**Branch:** `claude/mobile-responsiveness-overflow-5dumJ`

---

## Context

Two requests from a phone-first user:

1. **Power-play visibility.** No way to tell, from a game list, whether a
   power play was deployed in a game — you had to open each box score. Wanted
   a small tag on the score so games where either/both teams used it are
   scannable.
2. **Schedule redesign.** The `/schedule` page dumped up to 200 games for the
   whole season into one long sortable table — "really not a great design."
   Asked for the model real sports sites (NBA/NFL/MLS/MLB) use: a calendar,
   click a day, see that day's games — never the whole season at once.

## How power-play usage is stored

There is no per-game flag on `games`. Deployment lives in
`game_power_play_stats` — one row per (game, team, player), written only when
the rule was on. A team **deployed** the nickel when its row has
`pp_deploys > 0` (the same table also carries short-handed *offense* rows for
the team batting against a nickel, so the `pp_deploys > 0` filter is what
isolates "this team used power play"). So per game we derive `pp_home` /
`pp_away` from the distinct deploying team_ids.

## What changed

**Route — `schedule()` in `o27v2/web/app.py` (rewritten):**
- Day-centric. `?date=YYYY-MM-DD` selects the day; `?month=YYYY-MM` flips the
  calendar without picking a day. The league/team/status filters scope every
  query (date bounds, per-day counts, the day list).
- Default day: the current sim date if it has games under the filters, else
  the most recent prior day with games, else the earliest scheduled day —
  so opening the page lands on "today's slate" the way MLB's app does.
- Builds a Sunday-first month grid (via `calendar.monthdatescalendar`) with a
  game-count per day, plus prev/next month, prev/next *game day* steppers, and
  a per-game power-play tag (`pp_home`/`pp_away` from `game_power_play_stats`
  where `pp_deploys > 0`).

**Template — `schedule.html` (rewritten, v2 after reference review):**
- The first cut used a month-grid calendar. The user supplied ESPN / NBA /
  NHL / MLB / FOX schedule screenshots — *none* use a month grid; they all use
  a **horizontal day strip + ‹ › steppers + a date jump**. Pivoted to match:
  a centered rolling **7-day strip** (view_date−3 … +3, the ESPN model), each
  pill showing weekday · M/D · game count, the selected day in accent and the
  sim clock outlined. The `‹ ›` arrows step the window a week; a native
  `<input type=date>` (bounded to the season) jumps anywhere, and a **Today**
  button snaps to the sim clock. The strip auto-scrolls the selected pill to
  center on load and is horizontally swipeable on a phone.
- Below the strip: a day banner ("Friday, April 3, 2026" · N games) then the
  day's games as **cards** (`minmax(280px,1fr)`, single-column on a phone).
- Each card: league chip · first pitch · `SI`/`PP` badges; away and home rows
  (abbrev, name, score; winner bold/green); a per-side `PP` tick; a footer with
  the **venue** (park · city, the ESPN/FOX touch) and a Box-score link or Sim
  button.
- Power-play tag: a `PP` badge in the card meta (tooltip naming the deploying
  side), upgraded to `PP ×2` when both teams deployed.

**`base.html`:** added a reusable `.pp-badge` (and `.pp-badge.pp-both`) next
to `.si-badge`.

## Validation

Installed Flask in the sandbox, seeded a 30-team / 2430-game DB
(`manage.py initdb` + `sim 30`), and exercised the live server against the
redesigned strip:
- `/schedule` → 200; defaults to the sim clock's day, the 7-day strip renders
  centered on it (weekday · M/D · game count), venue lines populate.
- Inserted synthetic `game_power_play_stats` rows (one home-only game, one
  both-team game) → verified `PP` badge with the correct team tooltip,
  `PP ×2` for the both-team game, and per-side `PP` ticks on the right day.
- `?date=` (mid-month, far-future empty day, malformed `bogus`), the league
  filter, and `status=unplayed` all return 200 with no traceback; the empty
  day shows the "No games on this day" state. Prev/next-week arrows, the date
  picker, and the Today button render with the season-bounded min/max.

## Follow-up: projected starters for upcoming games

First pass only showed actual starters on played games, on the reasoning that
O27 commits no rotation so there's no "probable." The user pushed back — MLB
probables shift constantly too (rest, scratches), so a projected-then-changed
starter is *more* true to life, not less. Implemented:

- Factored the live SP pick (rest-tiered, Helms/Stamina/slot/debt) out of
  `_db_team_to_engine` into a shared `_pick_steering_arm()` in `sim.py`, plus a
  public `projected_starter(team_id, game_date)` that feeds it the same
  candidate set (`_get_active_players` + `_pitcher_workload_state`). Because
  both paths call the *same* function, the projection equals what the sim will
  actually throw when no games run in between — verified 8/8 against
  `simulate_game` on a freshly seeded DB.
- The schedule now shows a `PROJ` (amber, italic) starter line on unplayed
  games and the confirmed `SP` line on played ones; the projected tooltip says
  it may change as games are played.

This is honest about the model: the projection moves as intervening games
shift the rest picture, exactly like a real probable.

## Not done / follow-ups

- The `PP` tag is on the schedule (the dedicated game-browsing surface). The
  home page's recent-games strip and the game-detail header could carry the
  same `.pp-badge` later — the style is already shared, so it's a small add.
- Not verified on a physical phone; the layout was checked via the rendered
  HTML and the CSS breakpoints, not a device.
- The old whole-season sortable table is gone by design. If a flat
  "everything" list is ever wanted again, it would be a separate view.
