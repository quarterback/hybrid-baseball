# After-Action Report — Cricket-Idiom Scorecard + TRI (Total Runner Influence)

**Date completed:** 2026-06-26
**Branch:** `claude/mlb-stadium-dimensions-7icja0`

---

## What was asked for

The owner shared a photo of a BBC Test-match scorecard (England v New Zealand,
Trent Bridge) — *"I'd never seen cricket result like that on screen"* — and,
after some back-and-forth, *"it'd be fascinating if you could do it for O27
mostly as an exercise."* Then *"Yes wire it"* (into the game page). Two
corrections and a design provocation followed:

1. **Innings framing.** *"the bottom half isn't a 2nd innings, it's bottom first
   and 2nd is a super inning and so forth"* — O27 has no innings; each side bats
   one 27-out half (top then bottom), and a *second* bat only happens in a super
   inning.
2. **The batter's "innings" number.** Total bases undersells the sport: *"the
   ability to move runners to bases using multiple strikes is the differentiator
   … RBI as part of that but … bases batted forward."* This became **TRI**.
3. **Promote TRI** to the player page + a leaderboard (tiers 1+2; no backfill).

## What was built

### 1. The cricket card — `o27v2/web/cricket_card.py`

Renders a finished O27 game in the BBC idiom from the persisted tables
(`game_batter_stats`, `game_pitcher_stats`, `games`, `players`). The mapping:

| Cricket | O27 |
| --- | --- |
| out = **wicket**; innings closes "all out" | a 27-out half → headline `R/27` |
| batter's innings score | **TRI** (see §3); balls faced = PA; `*` = never made an out |
| bowler figures `9–3` ("nine for three") | outs recorded – runs allowed |
| the chase | the **bottom** side chases the **top**'s total |
| 2nd innings / Super Over | **super innings** (the only second bat) |

Wired into the game page (`game_detail` builds `cricket_card_text`;
`game.html` gets a **Box score / Cricket card** toggle via a tiny vanilla-JS
switch, no Bootstrap-JS dependency).

### 2. Getting the structure right (two wrong turns, logged honestly)

- First cut labelled away = "1st innings", home = "2nd innings (chasing)". The
  owner corrected it: those are two halves of ONE regulation innings; a super
  inning is the real second innings. Relabelled to **top · batting first** /
  **bottom · chasing N** under a "Regulation — 27 outs a side" heading, with a
  separate super-innings block and an "in super innings" result line.
- I briefly suspected the `game_batter_stats.phase` column meant super-inning
  round (a route comment says so) and that I was merging super-inning ABs into
  the totals. Checked the play-by-play (`game_scoring_events.half`): the games
  only had `top`/`bottom`, no `super_*` — `phase` is internal arc bucketing, so
  summing across phases for a team total is correct. No merge bug; it was purely
  labelling.
- A completed half always bats to 27 outs by rule, so the card shows `/27`. The
  per-batter `outs_recorded` ledger undercounts (baserunning outs aren't charged
  to a batter), which had produced a misleading `/26`; the rule fixes it.

### 3. TRI — Total Runner Influence

The owner's provocation: total bases misses O27's signature skill (moving
runners with the stay). Investigation found the engine **already tracks the
pieces** — `RAD` (`rad_1b/2b/3b`, "graded bases each runner gained", the
runner-movement analogue of total bases) and the TB components. The owner named
the combination and its rationale:

> **TRI = TB + RAD** — own total bases plus the bases of runner movement caused.
> Unlike RBI it credits the advance *even when a teammate strands the runner*,
> so it isolates the bat's own contribution. RBI is TRI's scoring tail. No
> re-weighting — TB already grades 1B/2B/3B/HR and RAD grades runner bases the
> same way.

Surfaced on three surfaces, all derived from already-persisted columns (so
historical games are covered through aggregation — no backfill job):

- **Cricket card** — the batter's headline "innings" number is TRI (the
  runner-mover who'd sat mid-card on TB jumps to the top).
- **Glossary** (`o27v2/web/glossary.py`) — canonical definition next to RAD.
- **Player page** (`player.html`) — a TRI cell in the Runner Advancement table;
  the route computes `tb + rad_total` post-aggregate, mirroring `rad_total`.
- **Leaders** (`/leaders`, `leaders.html`) — a sortable TRI Top-10 card under
  Batting · Runner Advancement, same min-PA qualifier as the rest.

## Verification

- `tests/test_cricket_card.py` (7) — TB / RAD / TRI math, the top/bottom +
  chase labels (asserting the old "1st/2nd innings" wording is gone), not-out
  `*`, and the result line (by runs; level → super innings).
- End-to-end via the Flask test client: `/game/<id>` renders the toggle + JS +
  corrected labels; `/player/<id>` shows the TRI header; `/leaders` renders the
  TRI card and its top value (`Yamauchi 33, Baloy 28, Viscarra 24`) matches a
  hand `SUM(TB + RAD)`, sorted descending.
- 53 tests pass across the cricket/real-parks/template suites. Two
  `test_template_renders` failures (`gmli`, `wrc_plus`) are **pre-existing** —
  confirmed identical on the clean tree via `git stash`, unrelated to this work.

## Honest gaps / what's still open

- **Super-innings path is logic-tested, not eyeballed.** No sampled game went to
  extras, so the super-innings render + "won in super innings" line are covered
  by unit logic over a synthetic card, not a real tied game. If a live DB has
  one, it's worth a look.
- **TRI is not on the box-score batting lines or the team page** — only the
  cricket card, player page, and leaderboard. Extending the standard box score
  (`box_score.py`) is a follow-up.
- **No historical backfill job** — deliberately skipped (owner: tiers 1+2). None
  is needed since TRI aggregates from persisted columns, but a materialized
  season-stat cache, if one exists elsewhere, would not include TRI until
  recomputed.
- **`stays` read 0 while RAD was high** in the sampled game — advances are being
  credited through the general `adv_*`/RAD path regardless of stay-vs-run-chosen.
  Worth confirming the stay mechanic tags its share before building any
  stay-only cut on top of TRI.
- **TRI isn't a partition of team runs** (neither was TB) — it's a per-bat
  contribution metric, not reconcilable to the line score. Accepted by design.

## Process notes

- Scope arrived as a photo and a vibe, not a spec; the build→correct rhythm
  (ship a card, owner corrects the framing, reship) is how the innings model and
  TRI both landed. Worth surfacing my assumptions earlier — the "1st/2nd
  innings" mislabel would have been caught by asking how O27 frames innings
  before rendering.
- Two `AskUserQuestion` calls failed mid-session (permission stream closed); I
  fell back to picking the lowest-risk increment (glossary definition, then
  player + leaderboard once the owner chose tiers 1+2) rather than guessing the
  full rollout.
