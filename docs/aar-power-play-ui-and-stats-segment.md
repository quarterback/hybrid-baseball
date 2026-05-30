# AAR — Power Play, second segment: box score, presence effect, the toggle, and the stat rack

This follows `docs/aar-power-play-nickel-fielder.md` (which covered the base rule).
It records the work done *after* the base rule landed, the open design questions it
surfaced, and — importantly — **why the checkbox isn't showing up in the deployed
app**.

All work is on branch `claude/power-play-optional-rule-HmsMs`. Five commits:

| Commit | What |
|--------|------|
| `c3d5b8c` | Add optional Power Play rule (the nickel fielder) — *base rule (segment 1)* |
| `c98b4b1` | Add AAR for the base rule |
| `e50f968` | Name the nickel + his putouts in the Powerplays box-score note |
| `fed9749` | Add Power Play presence effect: banded team-wide defense + pitching lift |
| `0e0c0cc` | Group Power Play + IBB toggles under "Optional rules" on Engine Settings |

---

## 1. Box-score note — naming the nickel (`e50f968`)

The nickel never bats, so he has no batting row. Rather than force a 0-PA line into
the batting table (which would threaten the PA↔out reconciliation invariants), his
deployment and defensive line ride in the `Powerplays:` footer. It now names the
player (NF) and his putouts:

- single window: `Powerplays: New York — Reyes NF (O14-17, 2 PO)`
- two windows, same nickel: `Powerplays: Boston — Reyes NF (1: O11, 2: O25, 3 PO)`
- two windows, different nickels: `Powerplays: Boston — Reyes NF (1: O11), Ortiz NF (2: O25, 1 PO)`

PO is counted per deployment (`credit_nickel_putout`) at the fielder-attribution step;
the full PO/A/E still accrue to the player's fielding line via the renderer's
`_credit_fielder`, so a season's nickel work is a real defensive line under the hood.

## 2. Presence effect — the banded team-wide lift (`fed9749`)

The catch-conversions only cover balls hit *at* the nickel. A 10th defender taking the
field should also settle the whole unit, so while the window is open we apply a small
**banded multiplicative lift**, on the same per-PA stash-and-restore lifecycle as the
leadership flares:

- fielding team `defense_rating × (1 + presence)` — the "across the lineup" knob;
- active pitcher `command / pitcher_skill / movement / grit × (1 + presence)`.

`presence` is rolled once at window open by `_presence_for(nickel)`, scaling the
nickel's glove from the eligibility floor to a perfect glove across
`[POWER_PLAY_PRESENCE_MIN, POWER_PLAY_PRESENCE_MAX]` = **0.1%–4.4% per power play**.
Verified mechanistically (paired-CRN micro, N=300k): a top-of-band lift shaves ~0.27%
of hits through the fielding channel alone, fading to ~nil at the floor. Deliberately
not a magic pill.

**Terminology note (from review):** the side *with* the nickel is the "Power Play"
side. The pitcher backed by the nickel is **not** "short-handed" — he's on the
advantaged side. "Short-handed" refers to the **offense** facing a loaded defense a man
down. This distinction drives the stat design in §5.

## 3. The toggle (`0e0c0cc`) — and why it doesn't appear in the app

`POWER_PLAY_ENABLED` is a plain bool on `o27.config`, and `o27v2/engine_config.py`
auto-discovers every editable bool/int/float there, rendering bools as checkboxes
(`bool_keys()`). `0e0c0cc` added a curated **"Optional rules"** group so it shows as a
friendly **"Power Play"** checkbox on the **Engine Settings** page (`/engine/settings`)
instead of being buried under its raw key in "All other constants".

### Why the checkbox is missing in the deployed app

Two independent reasons, both real:

1. **The feature is unmerged.** `hybrid-baseball.fly.dev` deploys `origin/main`, which
   contains **no power-play code** — all five commits are branch-only. Until the branch
   is merged and the app redeployed, nothing PP-related (checkbox included) can appear.
   This is the immediate cause of "the checkbox does not appear."

2. **It's global, not per-league — but the expectation is per-league.** `engine_config`
   stores one override blob in `sim_meta` (key `engine_config`) that applies to **every**
   league in the DB. The toggle also lives **only** on the Engine Settings page — it is
   **not** on the new-league builder (`new_league.html` / `universe_new.html`) or the
   league-edit page (`league_edit.html`), which is where a "play with a nickel *for this
   league*" control is naturally expected. Confirmed: grep finds no `POWER_PLAY` /
   `nickel` reference in any league-creation or league-edit template.

### Recommended fix (deferred — needs a decision)

Make Power Play a **per-league rule flag**, surfaced where leagues are born:

- add a `power_play_enabled` column on the `leagues`/config row (or the per-league
  settings blob), defaulting off;
- add the checkbox to the new-league builder and league-edit form, next to the other
  league-shaping options;
- at sim time, read the per-league flag into `state.power_play_enabled` (the engine
  already honors that per-game override first — see `power_play_on`, which checks the
  per-game override *before* the global config), so no engine change is needed.

The Engine Settings global checkbox can stay as a league-agnostic default / quick A-B
switch, but the per-league flag is what matches the product's mental model.

---

## 4. Test + regression status (this segment)

- `o27/tests/test_power_play.py`: **30 cases** (box-score formats, presence band
  scaling, apply/restore, idempotency, inert-when-closed / non-deploying-team, window
  open/clear).
- Power-play + declared-seconds + realism-identity suites green; the rule-off
  seed-replay identity test still passes (off = byte-for-byte unchanged).
- Pre-existing unrelated failure noted: `tests/test_template_renders.py::
  test_season_archive_writer_runs_end_to_end` (a `wrc_plus` category assertion) fails
  identically with our changes stashed — not caused by this work.

---

## 5. The stat rack — design (investigated, NOT yet built)

The base rule tracks power-play activity **only in game state**
(`pp_xbh_held`, `pp_hits_converted`, `power_play_deployments`, nickel putouts), and
**none of it survives into the league DB** — `game_batter_stats` / `game_pitcher_stats`
have zero PP columns, so nothing aggregates into season totals, leaderboards, or player
pages. A full end-to-end stat family is needed in leagues where the rule is on.

### Scope (decided with the user)

- **Power Play defense** (the deploying team + the nickel): PPD (power plays deployed),
  PPO (outs while a window is active), nickel PO/A/E under position NF, XBHH (extra-base
  hits held to singles), HC (hits converted to outs).
- **Short-handed offense** (hitters facing an active window against them): SH-PA, SH-AB,
  SH-H, SH-AVG, plus XBH-lost.
- **Leaderboards + glossary**: ranked season leaders + a "Power Play" glossary section.
- **Pitching: deferred.** The pitcher backed by the nickel is on the advantaged side,
  not short-handed; what (if anything) to measure there is an open question we chose not
  to guess at in this pass.

### Storage (decided): a dedicated table

A new `game_power_play_stats` table (+ a season aggregate), rather than bolting always-
NULL `pp_*` columns onto `game_batter_stats`/`game_pitcher_stats` for the 99% of leagues
that never enable the rule. Cleaner to gate to rule-on leagues and easy to drop.

### End-to-end extension points (mapped, ready to implement)

| Layer | File:anchor | Work |
|-------|-------------|------|
| Engine accumulation | `o27/engine/power_play.py`, `state.py:441-442,642` | Already tracks XBHH/HC/PO/deployments; add a per-PA "window active vs this batting team" flag for short-handed offense (PA-start hook at `prob.py:2156`, reset at `prob.py:2102`, finalize at `pa.py:290`) |
| Carrier | `o27/stats/batter.py` | Add `sh_pa/sh_ab/sh_h/sh_xbh_lost` fields (nickel defense already flows via `po/a/e` + team counters) |
| Render credit | `o27/render/render.py:_update_stats` (1401), `_credit_fielder` (853) | Increment short-handed batting fields when the window was active against the batter |
| Per-game DB | `o27v2/db.py` (new `CREATE TABLE game_power_play_stats`) | New table keyed by (game_id, team_id, player_id) |
| Sim write | `o27v2/sim.py:_extract_batter_stats` (733), insert block (1691) | Extract + INSERT, **only when `power_play_on` for that game** |
| Season aggregate | `o27v2/season_archive.py:_snapshot_leaders` (140) | `SUM()` the new table; new `season_power_play_leaders` rows |
| Leaderboards | `o27v2/web/app.py` `/leaders` (4215) | New category cards (rule-on leagues only) |
| Glossary | `o27v2/web/glossary.py` | New "Power Play / Short-handed" section |

### Build order (when greenlit)

1. Engine: short-handed per-PA flag + counters; box-score already done.
2. Carrier + render increments + unit tests.
3. DB table + sim extract/insert (gated on `power_play_on`).
4. Season aggregation + leaders + glossary.

Gating principle throughout: write/aggregate/display PP stats **only for games/leagues
where the rule was on**, so rule-off leagues are untouched.

---

## 6. Open decisions for the user

1. **Merge** the branch so the (global) checkbox + rule actually ship to the deployed
   app — this alone resolves "the checkbox does not appear."
2. **Per-league vs global**: should Power Play become a per-league flag on the new-league
   builder (recommended, matches "by league"), with the global Engine Settings toggle
   demoted to a default? This is prerequisite framing for the stat rack's rule-gating.
3. **Build the stat rack** per §5 once 1–2 are settled.
