# After-Action Report — Playoffs, Awards, Scouting, xRA v2 & Aging Drift

**Date completed:** 2026-05-11
**Branch:** `claude/identify-improvements-oUSUt`
**Commits:** `61ca192`, `8a73ddd`, `219daf0`, `3898f5a`, `ed7dc65`

---

## What was asked for

The session started open-ended ("what other improvements can I make?"). The user surfaced two concrete UI gaps from a phone screenshot of `/playoffs`:

1. The bracket showed series outcomes ("VCO wins 4-3") but had no way to drill into the individual games.
2. The Awards section showed one winner per category. The user wanted a realistic BBWAA-style voting display — top-5 with 1st / 2nd / 3rd-place vote totals.

After that landed, the user ordered the broader follow-up menu as **5 → 4 → 2 → 1**, then asked for aging drift on top, an AAR for all of it, and pushed back on the run-environment calibration premise.

---

## What was built

### 1. Clickable playoff series games (`61ca192`)

`o27v2/web/templates/playoffs.html` and the `/playoffs` route handler now attach a per-series list of games to each bracket tile. Each row links to the existing `/game/<id>` box score. No schema change — `games.series_id` was already populated, the data just wasn't surfaced.

- **Route**: `o27v2/web/app.py:4363-4445` — single bulk query against `games WHERE series_id IN (…)`, grouped in Python, with a `team_id → abbrev` map built once per request and reused.
- **Template**: inside each series tile, a tight `G1 / G2 / G3 …` list with away-vs-home abbrevs and the score.

### 2. BBWAA-style award voting (`61ca192`)

`o27v2/awards.py` previously chose one winner per category by `max()`/`min()` on a single underlying score and threw away the ranking. The replacement keeps every candidate's score, simulates 30 synthetic voters with deterministically-seeded gaussian jitter per `(season, category, voter, player)`, and persists each voter's top-10 ballot in a new `award_ballots` table.

- **New table** (`o27v2/db.py`): `award_ballots(id, season, category, voter_id, rank, player_id, player_name, team_abbrev, headline_stat)` with `UNIQUE(season, category, voter_id, rank)`. ALTER-table migration path for existing DBs.
- **Vote scoring**: BBWAA points (`1st=14, 2nd=9, 3rd=8, … 10th=1`) plus 1st/2nd/3rd-place vote counts. The winner row in `season_awards` is still written for backward compatibility.
- **Template**: replaced the single-card grid in `playoffs.html` with a per-category top-5 table showing rank, player, team, 1st/2nd/3rd votes, and total points. Old-season fallback path keeps the original cards for pre-voting data.
- **Reproducibility**: deterministic seeding means the same season produces the same vote distribution on every re-render. With 30 voters, a clear MVP will see 15-25 first-place votes; close races split the firsts more evenly (e.g. 11/8/6/3/2).

### 3. Dead `pitcher_role` branch removed (`8a73ddd`)

`o27v2/sim.py:_find_pitcher_id()` had a role-tagged fast path that never returned anything after Task #65 cleared all stored `pitcher_role` values. Collapsed into the single role-agnostic loop. Two lines deleted; safety-checked against every other `pitcher_role` consumer in the repo (`injuries.py`, `trades.py`, `batch.py`) — those use it for live derivations and aren't affected.

### 4. Scouting leaderboards (`219daf0`)

`/leaders` previously ranked players by raw 20-80 tools but only among the PA/outs-qualified set. HANDOFF noted that the talent census was structurally available but not surfaced. Added `Scouting · Pitchers` and `Scouting · Hitters` sections that rank **every signed player** by raw attribute, no playing-time filter.

- **Backend**: two extra `SELECT … FROM players JOIN teams …` queries (no game-stat join, so cheap and works on an empty DB).
- **Template**: same `card` macro, new datasets (`talent_hitters`, `talent_pitchers`). Surfaces the empty-DB landing too — the leaderboard is roster-state, not stat-state.
- **Pitchers**: Stuff, Command, Movement, Stamina.
- **Hitters**: Power, Contact, Eye, Speed.

### 5. xRA v2 — 2B/3B apportionment (`219daf0`)

The v1 expected-runs-allowed formula collapsed every non-HR hit into a singles-equivalent coefficient (`_XRA_W_HIT = 0.45`) because the pitcher stats table doesn't break hits-allowed down by type. v2 splits the weight into three:

- `_XRA_W_1B = 0.45`
- `_XRA_W_2B = 0.78`
- `_XRA_W_3B = 1.05`

Each pitcher's non-HR hits are apportioned by the **league** share of 1B/2B/3B (sourced from `game_batter_stats` in `_league_werra_consts()`). With the post-reseed 120-game sample the blended per-non-HR weight came out at **0.568** vs the v1 flat 0.45 — meaningfully higher because O27 produces more XBH per non-HR hit than MLB. `xra_norm` (the multiplicative league anchor) absorbs the level shift, so league xRA still equals league wERA by construction.

**Honest limit**: with league shares applied uniformly to every pitcher, v2 only differentiates pitchers via their HR-allowed-vs-non-HR-allowed mix. True per-pitcher hit-type signal needs hit-type columns persisted on `game_pitcher_stats` — a v3. The v2 code already reads each pitcher's shares from a local variable, so v3 is a one-line callsite change once those columns exist.

### 6. Per-attribute aging curves (`3898f5a`)

Existing `o27v2/development.py` runs a multi-attribute offseason drift (HANDOFF Bug 4.2 flagged this as missing — actually already shipped in commit `e20e1cc`). But every attribute used the same `_mu_age` curve, so Power, Speed, Eye, and Command all drifted identically.

Added `_ATTR_AGE_PROFILE`: per-attribute multipliers on the growth side and decline side of `μ_age`. Reshapes the curve per attribute without changing the base `_mu_age`.

| Attribute | Growth × | Decline × | Reads as |
|-----------|----------|-----------|----------|
| Eye | 0.80 | **0.50** | Plate discipline holds longest |
| Contact | 0.90 | 0.70 | Sticky |
| Power | 1.20 | 1.30 | Peaks early, falls hard |
| Speed | 1.10 | **1.50** | Legs go first |
| Stamina | 0.90 | **0.65** | Workhorse moat per README |
| Command | 0.80 | 0.50 | Control persists |
| Stuff (pitcher_skill) | 1.10 | 1.10 | Velocity-like; declines first |
| Movement | 1.00 | 1.00 | Neutral |

Empirical verification (720 pitchers / 690 hitters force-aged from 35 → 36, base μ = -0.70):

```
Hitters     Eye -0.23   Contact -0.37   Power -0.70   Speed -0.71
Pitchers    Stamina -0.28   Command -0.34   Movement -0.58   Stuff -0.65
```

Stamina and Eye now bend the slowest; Speed and raw Stuff fall fastest. The base `_mu_age` curve (peak through 31, plateau 31-34, decline 34+) stays as-is — it already aligns with the README's "career arcs are longer because sidearm/submarine" theme. The MLB-style 27-29 peak the HANDOFF asked for is the wrong fit for O27.

### 7. Reseeded local DB (manual)

`python o27v2/manage.py resetdb` activated the Task #65 `stamina` and `is_active` columns on the live attributes. The 30-team / 47-player-per-team roster now has stamina distributed 20-80 across 1,008 pitchers (was uniformly defaulted at 50 before). The Fly.io deployment still needs `fly ssh console` + `manage.py resetdb` from the user.

---

## What was reverted

### Run-environment recalibration (`ed7dc65` reverts the change in `219daf0`)

The HANDOFF document flagged "Bug 1: 24.42 R/G is not defensible, target 14-18" and named contact-mass redistribution as the lever. Following that, the calibration step shifted `CONTACT_HARD_BASE` 0.22 → 0.10 and `CONTACT_MEDIUM_BASE` 0.40 → 0.34, with the mass going to `CONTACT_WEAK_BASE`. Two reseed-and-resim passes landed at **18.43 R/G total**, just inside the HANDOFF target band.

The user pushed back: **why lower runs at all?** The answer is that there's no good reason, and the change was wrong. Two reasons to revert:

1. **README is canonical**, not HANDOFF. The README's `What This Does to the Sport` section explicitly states "League R/G runs around 22-26 vs MLB's ~9." The pre-tune ~24 R/G sat right inside that band. HANDOFF was an in-progress maintainer note that disagreed with the canonical design doc.

2. **The high run env IS the design**. The whole reason O27 exists is to give baseball T20-cricket-style constant action — soccer-like moments of always-something-happening instead of MLB's ebb-and-flow. Suppressing offense to land at an MLB-shaped run env contravenes that design intent.

The contact bases are back to `WEAK 0.38 / MEDIUM 0.40 / HARD 0.22`. The right way to make aces look like aces in this run env is not to flatten the run env — it's to push the pitcher-tilt knobs that already exist (`PITCHER_DOM_*`, `FATIGUE_THRESHOLD_*`, contact-quality talent gates) so elite pitching dominates inside a high-action context.

---

## What was confirmed unnecessary

Three of the HANDOFF follow-ups turned out to already be done:

- **Bug 5** (super-inning assert crash) — already replaced with a logged soft-cap at `o27/engine/game.py:164-168, 195-199`.
- **Bug 3** (negative FIP for individual pitchers) — obsolete. FIP and xFIP were already replaced by xRA in the pitcher aggregator, whose multiplicative anchor at `app.py:1086-1088` keeps it ≥ 0 by construction. No FIP rendered, nothing to floor.
- **Bug 2** (duplicate `game_pitcher_stats` rows) — `UNIQUE(player_id, game_id, phase)` constraint already in place at `db.py:273`, plus an ALTER-TABLE migration for legacy DBs.

These three are now removed from the open-work list. HANDOFF.md should be updated; that's a paper exercise, not code.

---

## What's still open

1. **Production reseed.** The Fly.io DB still predates Task #65 — `stamina` and `is_active` columns are missing live. Needs `fly ssh console` + `manage.py resetdb` from the user. Local dev is reseeded.
2. **Pitcher dominance in the high-run env.** With the run-env recalibration reverted, the next pass on "make aces look like aces" needs to go through `PITCHER_DOM_*` magnitudes, the contact-quality talent gates, and possibly the fatigue threshold scale — not the base contact distribution.
3. **xRA v3.** Per-pitcher hit-type breakdown on `game_pitcher_stats` (columns `singles_allowed / doubles_allowed / triples_allowed`) would let xRA differentiate pitchers by the *shape* of hits they allow, not just total count. v2 is ready to consume this with a one-line change.
4. **Aging drift UI.** The drift runs every offseason but the user has no way to see it. A `/player/<id>/history` panel showing year-over-year attribute deltas would close the loop. Out of scope for this session.

---

## Process notes

- The plan-mode plan written for this session (`/root/.claude/plans/what-other-inprogmeents-can-deep-moonbeam.md`) survived contact with reality. The two-part plan (playoffs drill-down + BBWAA awards) was the right scope to commit upfront; the follow-up menu at the bottom was the right deferred list.
- The biggest accuracy failure was leaning on HANDOFF.md as if it were canonical. It's a maintainer's working note from a previous agent and disagrees with README in at least one important place (the run env). README is the design doc. Read README first, then check HANDOFF for status; not the reverse.
- Several "bugs" in HANDOFF.md were already fixed by the time this session started. Always verify before patching.
- The aging-drift system was a similar story — HANDOFF said it was missing, the actual code had it working. The improvement that landed (per-attribute curves) was a genuine gap, not a fix.
