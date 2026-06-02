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

**Template — `schedule.html` (rewritten):**
- Two-column layout: a sticky month **calendar** (game-count dot per day,
  selected day in accent, the sim clock outlined) beside the selected day's
  games as **cards**. Collapses to one column ≤768px; cards are
  `minmax(260px,1fr)` so they go single-column on a phone.
- Each card: league chip · first pitch · `SI` / `PP` badges, an away row and a
  home row (abbrev, name, score; winner bold/green), a per-side `PP` tick
  showing *which* team deployed, and a Box-score link or Sim button.
- Power-play tag: a `PP` badge in the card meta (with a tooltip naming the
  deploying side), upgraded to `PP ×2` when both teams deployed.

**`base.html`:** added a reusable `.pp-badge` (and `.pp-badge.pp-both`) next
to `.si-badge`.

## Validation

Installed Flask in the sandbox, seeded a 30-team / 2430-game DB
(`manage.py initdb` + `sim 12`), and exercised the live server:
- `/schedule` → 200; defaults to the first game day (15 games), calendar shows
  30 active days, cards render with scores.
- Inserted synthetic `game_power_play_stats` rows (one home-only game, one
  both-team game) → verified `PP` badge with the correct team tooltip,
  `PP ×2` for the both-team game, and per-side `PP` ticks.
- Month nav (`?month=2026-05` / `2026-03`), league filter, `status=unplayed`,
  a team filter, an empty future day, and a malformed `?date=not-a-date` all
  return 200 with no traceback; the empty day shows the "No games on this day"
  state.

## Not done / follow-ups

- The `PP` tag is on the schedule (the dedicated game-browsing surface). The
  home page's recent-games strip and the game-detail header could carry the
  same `.pp-badge` later — the style is already shared, so it's a small add.
- Not verified on a physical phone; the layout was checked via the rendered
  HTML and the CSS breakpoints, not a device.
- The old whole-season sortable table is gone by design. If a flat
  "everything" list is ever wanted again, it would be a separate view.
