# Feature Report — Power Play (the nickel fielder) and its stat rack

**Status:** complete, on branch `claude/power-play-optional-rule-HmsMs`, current with `main`.
**Scope of this report:** the optional Power Play rule, the three places it can be
toggled, and the full per-player stat family it produces. Complements the build-log
AARs (`docs/aar-power-play-nickel-fielder.md`, `docs/aar-power-play-ui-and-stats-segment.md`).

---

## 1. What it is

**Power Play** is an optional rule. While it is on, a fielding manager may, once per
at-bat (before the first pitch), deploy a **10th defender — the nickel fielder** — for a
short, **use-or-lose** window. While the window is open the extra glove cuts off
extra-base gaps and tightens the whole unit; when it closes it can't be re-used that
half.

Two perspectives, named deliberately:

- **Power Play** = the deploying defense **and the pitcher it backs** (the advantaged side).
- **Short-handed** = the **offense** facing a loaded defense a man down.

The pitcher with the nickel behind him is **not** "short-handed" — he is on the
advantaged side. This distinction drives the stat naming throughout.

Real-world parallel: it behaves like a per-competition rule rather than a universal one
— much as cricket's **Impact Player** (a substitute who can actively bat/bowl) is unique
to the IPL and absent from international cricket and other T20 leagues. And like NHL
power-play / penalty-kill splits, the resulting numbers are just **context stats** — no
"quality ranking" disclaimers needed.

### In-game effects (pre-existing, summarized)
- **Catch conversions:** some doubles/triples are held to singles; some shallow singles
  are run down for outs (`apply_nickel_defense`).
- **Presence lift:** a small banded multiplicative boost to the fielding team's
  `defense_rating` and the active pitcher's effectiveness while the window is open
  (0.1 %–4.4 % per power play, rolled from the nickel's glove).
- **Box score:** the nickel and his putouts ride in the `Powerplays:` footer (he never
  bats, so he has no batting row).

---

## 2. How to turn it on — three controls that compose

| Control | Where | Scope | Storage |
|---|---|---|---|
| **Global default** | Engine Settings → "Optional rules" → Power Play | every league in the save | `sim_meta` (`engine_config`) → `cfg.POWER_PLAY_ENABLED` |
| **Per-league (single)** | New-league builder → "Optional rules" checkbox | the league being created | `teams.power_play_enabled` |
| **Per-league (universe)** | Peer-league universe builder → per-league "Power Play" Off/On select | each league independently | `teams.power_play_enabled` |

**Composition rule (in `o27/engine/power_play.py:power_play_on`):** a game reads its
per-game override first, then falls back to the global default. `sim.py` sets the
override to `True` only when the league opted in; when the league did **not** opt in it
leaves the override unset (`None`) so the global toggle still applies. Net effect:

> Power Play is on for a game if **the league opted in OR the global default is on.**

A per-league choice can never silently dead-disable the global switch.

### Why a `<select>`, not a checkbox, in the universe builder
The universe builder reads its repeating per-league fields as parallel `getlist()`
arrays aligned by index. An unchecked checkbox doesn't submit, which would misalign each
league's flag from its name. An Off/On `<select>` always submits exactly one value per
row, preserving the alignment — and matches the builder's dropdown-heavy UI. League
names pass through to `teams.league` verbatim, so each opted-in league is stamped with
`UPDATE teams SET power_play_enabled = 1 WHERE league = ?`.

---

## 3. The stat rack

All Power Play stats live in a dedicated table, **`game_power_play_stats`**, written
**only for games where the rule was on**. Leagues that never enable it keep an empty
table, and the leaderboards/glossary surface nothing — zero footprint.

### 3a. Power Play · Defense (the nickel)
| Stat | Meaning |
|---|---|
| **PPD** | power plays deployed (windows this player started as the nickel) |
| **PPO** | outs the deployment windows covered |
| **XBHH** | extra-base hits held to singles |
| **HC** | hits converted to outs (shallow singles run down) |
| **NF-PO** | putouts recorded as the nickel |

### 3b. Power Play · Short-handed Offense (the hitter facing it)
| Stat | Meaning |
|---|---|
| **SH-PA / SH-AB / SH-H** | plate appearances / at-bats / hits taken while the opposing nickel was deployed |
| **SH-AVG** | SH-H / SH-AB |

### 3c. Power Play · Pitching (the pitcher with the nickel behind him)
The nickel only touches **balls in play**, so a pitcher's window line splits cleanly into
his own work (K/BB) and the defense behind him (BABIP, saves).

| Stat | Meaning |
|---|---|
| **PP-BABIP** | BABIP-against with the nickel deployed |
| **BABIP Δ** | PP-BABIP minus his BABIP without the nickel (negative = the extra fielder lowered it) |
| **PP-K% / PP-BB%** | strikeout / walk rate in windows — defense-independent (never reach the extra fielder) |
| **PP-Cov%** | share of his total outs taken with the nickel behind him (usage/protection) |
| **PP-BF** | batters faced while protected |
| **H-Saved / XBH-Saved** | singles run down / extra-base hits held behind him |

> Pitching was deliberately scoped as a context family (it appears in its own section,
> not folded into the ERA/wERA leaders) so that defense-aided outs don't distort
> pitcher-quality rankings — while still being presented as normal stats.

---

## 4. End-to-end plumbing

| Layer | File(s) | Role |
|---|---|---|
| Rule + effects | `o27/engine/power_play.py` | deploy/close window, catch conversions, presence lift, per-nickel save attribution to the fielding pitcher |
| Per-PA short-handed flag | `o27/engine/prob.py`, `o27/engine/state.py` | `power_play_sh_active` snapshotted at PA start; `pp_pitcher_support` dict |
| Carrier (offense) | `o27/stats/batter.py` | `BatterStats.sh_pa/sh_ab/sh_hits` |
| Accumulation | `o27/render/render.py` | before/after delta around `_update_stats` credits short-handed batting; `_credit_pp_pitcher` accumulates window + total pitcher counters |
| Storage | `o27v2/db.py` | `teams.power_play_enabled` column; `game_power_play_stats` table (defense + offense + `ppp_*` pitching columns); idempotent migrations |
| Per-game gating + write | `o27v2/sim.py` | sets `state.power_play_enabled` from the league flag; `_extract_power_play_stats` + INSERT, gated on `power_play_on(final_state)` |
| Toggles | `o27v2/web/app.py`, `templates/new_league.html`, `templates/universe_new.html` | the three controls in §2 |
| Surfacing | `o27v2/web/app.py` (`/leaders`), `templates/leaders.html`, `web/glossary.py` | three league-scoped leader sections, rendered only when non-empty; glossary "Power Play / Short-handed" section with deep-linkable entries |

**Gating principle throughout:** write / aggregate / display Power Play stats **only**
for games and leagues where the rule was on, so rule-off play is byte-for-byte unchanged
and rule-off leagues show nothing.

---

## 5. Verification

- **Engine/identity:** rule-off games are unchanged; the per-game override beats the
  global config; `short_handed_for_batting` snapshot is stable across a PA.
- **Data:** rule-on leagues populate defense + short-handed + pitching rows; rule-off
  leagues write **zero** rows. Invariants hold — `SH-AB ≤ SH-PA`, pitcher
  `bip_hits ≤ bip` (BABIP ≤ 1.0), `pp_bip ≤ tot_bip`, `K+BB ≤ BF`. (`SH-H > SH-AB` and
  `bip > bf` can occur — that is the engine's Second-Chance multi-contact property, the
  same reason the app leads with PA-denominated rates; not a bug.)
- **Per-league isolation (universe):** a 2-league universe with MLB on / NPB off stamped
  6/6 and 0/6 teams; simming 60 games produced 312 power-play stat rows for MLB and zero
  for NPB in the same save.
- **Surfacing:** all three leader sections render with data, every card deep-links to a
  resolvable glossary entry, and the sections are absent in rule-off leagues.
- **Tests:** `o27/tests/test_power_play.py` (30, engine) + `tests/test_power_play_stats.py`
  (7, DB→leaders→glossary) — **37 green**. Main's suites (streaks, template renders) pass
  on the merged tree.

### Known, unrelated
- `tests/test_template_renders.py::test_season_archive_writer_runs_end_to_end` fails on a
  `wrc_plus` assertion — **pre-existing on `main`**, fails identically without any of this
  work. Not introduced here.
- **No backfill:** stats populate only for games simmed after the rule is on; already-
  played games are not retroactively credited (they would need a re-sim).

---

## 6. Commits (this branch, after `main` @ `0be80a2`)

```
Add optional Power Play rule (the nickel fielder)            [segment 1]
Name the nickel + his putouts in the Powerplays note         [segment 1]
Add Power Play presence effect (banded defense + pitching)   [segment 1]
Group Power Play + IBB toggles under "Optional rules"        [segment 1]
Add per-league Power Play opt-in on the new-league builder
Add the Power Play / short-handed stat rack
Add Power Play pitching stats (the protected side)
Drop the over-caveating on Power Play pitching stats
Add per-league Power Play toggle to the universe builder
(+ AAR docs, + a merge of main to stay current)
```

Auto-migrates on boot (new column + table + `ppp_*` columns added idempotently in
`init_db()`), so existing saves upgrade with no manual step.
