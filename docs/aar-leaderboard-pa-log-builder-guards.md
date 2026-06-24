# After-Action Report — Leaderboard 500s: rebase, then guard the pa_log builders

**Date completed:** 2026-06-24
**Branch:** `claude/vigilant-davinci-hn34xy`
**Commits:** `7736a01` (drop RRR from /leaders + guard — superseded), `52874df`
(restore all advanced stats, keep the guards)

---

## What was reported

> "leaderboard keeps either being empty or crashing the game in the last few
>  saves" … "https://hybrid-baseball.fly.dev/leaders 500 error"

Two symptoms — an empty leaderboard and a hard 500 — on recently created saves.

## The detour (and the real lesson)

I spent too long trying to reproduce the 500 against my **local branch**, which
returned 200 on every config I threw at it (12- and 30-team, full seasons,
cricket order, high-drama form, `?league=`, `?scale=xo`). I went hunting through
the route and the analytics builders for a data-dependent crash and found
nothing — because **the crash wasn't in my code yet.**

The owner said it plainly: *"if you rebased to main you'd see this is currently
updated."* `origin/main` had merged work I didn't have locally — the **RRR/3O +
PAI cricket-stats line** (PRs #273–#279) — which had wired several
`game_pa_log`-derived builders onto `/leaders`:

- `build_pressure_impact` (PAI / TRR+), `build_chase_split_table` (Chase BA),
- `build_hitter_dead_outs_table` (DOA%), `build_expected_outs_table` /
  `build_dead_outs_table` (pitcher xO / DO%), and `build_player_wpa` (WPA / LI).

I had been testing a stale branch. **Rebasing onto `origin/main` was the
prerequisite to even see the failing code.** Lesson: when a bug is "live" and the
local tree can't reproduce it, reconcile with the deployed ref *first*, not last.

## Root cause

All of those builders read `game_pa_log`. That table is **conditional**:

- **Empty / pruned** on lite/fast-sim saves (`sim … detail="lite"` skips
  `game_pbp` + `game_pa_log` + `game_scoring_events`), and `dbmaint` prunes pbp
  blobs to reclaim disk. → the builders return nothing and the advanced cards
  hide. **That is the "empty" symptom — not a bug.**
- When present-but-edge-shaped on a particular save, one builder threw, and since
  the `/leaders` route called them **unguarded**, a single failure 500'd the
  entire page. **That is the "crashing" symptom.**

I confirmed the empty path directly: a local save with **0 `game_pa_log` rows**
renders `/leaders` at 200 with every advanced card hidden; a full-detail save
(4132 pa_log rows) renders 200 **with** PAI, TRR+, Chase BA, DOA%, Expected Outs,
and WPA all present.

## What changed

First pass (`7736a01`) removed the RRR/PAI/Chase builders from `/leaders`
entirely — but the owner clarified the actual requirement:

> "i can use the PAI/TRR stuff if it can go somewhere without crashing
>  everything" … "Chase BA, TRR and PAI are not on the box score" … "xo and stuff
>  was genuinely interesting" … "pitcher xo and dead outs and wpa are all
>  interesting too."

So the deliverable is **keep every advanced stat, just make none of them able to
take down the page.** Final pass (`52874df`):

- **Restored** PAI / TRR+ / Chase BA builders and their cards on `/leaders`
  (they have no box-score home, so the leaderboard is where they live).
- **Wrapped every `game_pa_log`-reading builder in the leaders family in a
  guard** (`try/except` → `app.logger.exception(...)` → empty result):
  - `/leaders`: hitter pressure/chase/dead-outs, pitcher xO/dead-outs, WPA.
  - home dashboard stat panel: pressure/dead-out.
  - `/o27i` batter rows: xwOBA-EV, pressure, dead-outs, chase.

When the log is populated the stats render normally; when it's sparse/pruned, or
any builder errors, the affected columns degrade to blank and the page still
renders.

## Validation

- `/leaders`, `/`, `/o27i`, `/o27i/leaders` → **200** on both a full-detail save
  (all advanced stats shown) and an empty-`pa_log` save (cards hidden).
- Render + RRR-engine suites green (the two pre-existing failures —
  `wrc_plus` major-cat assertion and the season-archive writer — are unrelated to
  this work and predate it).

## Honest caveats / not done

- **I never reproduced the exact offending row.** The fix removes the failure
  *mode* (any one builder can no longer 500 the page) rather than a single
  identified bad record. If it recurs after deploy, the guards now log the real
  traceback to the fly logs, naming the exact builder for a targeted root-cause
  fix.
- **The guards are page-level resilience, not a data fix.** If a builder is
  genuinely buggy on certain states, it will silently blank its column (with a
  logged exception) rather than show wrong numbers — acceptable for a dashboard,
  but worth revisiting if a column starts disappearing for many players.
- **`detail="lite"` / pruned saves still show no advanced stats** by design —
  the pa_log isn't there to derive them. Not changed; that is the intended
  space/feature tradeoff.
