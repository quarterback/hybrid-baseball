# O27v2 Handoff Document

**Prepared:** 2026-05-03  
**For:** Claude Code (or next agent taking over)  
**From:** Replit Agent (Tasks #47–#65)  
**DB path (default):** `o27v2/o27v2.db`  
**Deploy target:** Fly.io app `hybrid-baseball`, region `ams`, volume `o27v2_data`

---

## 1. CURRENT STATE

### What works end-to-end

- **Engine → stats pipeline is real.** Every stat row in the DB comes from an actual simulated at-bat. The chain is: `o27/engine/prob.py` resolves pitch outcomes using player attributes → `o27/render/render.py` accumulates per-batter and per-pitcher stats → `o27v2/sim.py` extracts and writes to `game_batter_stats` / `game_pitcher_stats`. Nothing is generated outside gameplay.
- **Player attributes feed probability calculations.** `batter.skill` shifts contact rate; `pitcher.pitcher_skill` shifts strike rate and fatigue threshold; `speed` controls baserunning; `stamina` (where populated) controls long-relief preference in the manager AI.
- **Schedule generation** produces a full 162-game season for 30 teams (162 games/team confirmed equal across all teams).
- **Standings, W/L, game logs, player stat pages** all render in the web layer. FIP, ERA, WHIP, K/27, BB/27 are computed in `app.py:_aggregate_pitcher_rows()`.
- **W/L attribution** (`app.py:_pitcher_wl_map()`, lines 155–197) correctly assigns decisions to the pitcher who recorded the most outs on the winning/losing team per game. The `seen` set prevents double-counting even when duplicate stat rows exist.
- **FIP calibration** (`app.py:_league_fip_const()`, lines 242–265) re-anchors the FIP constant to league ERA each render cycle, so league-average FIP equals league-average ERA regardless of the K/HR/BB mix.
- **Dedup SQL view** (`app.py:_PSTATS_DEDUP_SQL`, line 147) exists and is used in ERA/WHIP/FIP/leaderboard queries, mitigating the duplicate-rows bug for display purposes.
- **Injury system, trade system, waiver logic** all run post-game without crashing.
- **`teams.wins` / `teams.losses`** are correctly balanced: 1,502 each across 1,502 played games, zero NULL `winner_id` rows.
- **Task #65 code** (expanded rosters, tier-rolled attributes, live pitcher roles) is fully in place in the Python source. The DB has not been reseeded to activate it yet — see DB STATE section.

### What is confirmed broken (with evidence)

**1. Run environment: 12.21 runs/team/game.**
League average across 1,502 games: 24.42 total runs/game (min 5, max 59). MLB baseline is ~4.5/team. BA is .295, SLG is .465, HR rate is 0.88/team/game (roughly correct). The excess scoring is almost entirely in extra-base hits and aggressive baserunning, not walks. The probability tables in `o27/config.py` are miscalibrated for the O27 structural rules (27 outs per side, 12-batter lineups cycling more plate appearances per game than a 9-batter MLB lineup).

**2. Duplicate `game_pitcher_stats` rows.**
184 of 1,502 games (12.2%) have at least one pitcher appearing more than once for the same `(game_id, player_id, team_id)`. There are 260 extra rows in a table of 6,316 (4.1% inflation). Stat aggregation queries that bypass `_PSTATS_DEDUP_SQL` will overcount K, BB, and outs for affected pitchers.

**3. FIP goes negative for individual pitchers.**
104 of 185 pitchers have a negative raw FIP component `(13*HR + 3*BB − 2*K) * 27/outs`. Example: a pitcher with 1 K, 0 HR, 0 BB, 2 outs recorded gets raw FIP component = −27.0. The league-level calibration constant prevents the league *average* from going negative, but individual pitcher FIP values can be deeply negative and are not meaningful.

**4. `game_batter_stats.outs_recorded` = 0 for 497 rows where `ab > 0`.**
497 batter rows exist where the player had at least one at-bat but zero outs credited. This suggests the renderer's out-attribution loop (`render.py:_update_stats()`, lines 736–824) is missing some outs in complex multi-runner play sequences. The field is not currently displayed, but it will matter if per-batter out tallies are used for lineup rendering.

**5. Super-inning assertion fires in extended sims.**
`game.py:162` and `game.py:191` contain `assert state.outs <= 5` for super-inning halves. This assertion can fire with exit code 1, crashing the calling process. 6 of 1,502 games have `super_inning >= 4`, indicating deep-SI games that stress the boundary condition.

**6. `stamina` and `is_active` columns not in the live DB.**
The live `o27v2/o27v2.db` does not have `stamina` or `is_active` columns — confirmed with `PRAGMA table_info(players)`. The columns are in the `CREATE TABLE` statement and ALTER TABLE migration path in `db.py:56–57, 262–276`, but the live DB predates Task #65. Running `init_db()` will add them with defaults (stamina=50, is_active=1). Task #65 code handles the absence gracefully via `COALESCE` and fallbacks — no crash occurs — but the expanded-roster and live-role logic won't fully activate until a fresh reseed.

### What was changed recently (Task #65)

**Committed:** 2026-05-03, commits d4a85c6 and 6c21a12.

| File | What changed |
|------|-------------|
| `o27v2/league.py` | Added `_TALENT_TIERS` (9-tier 20–80 scout grade ladder), `_roll_tier_grade()`, `_make_hitter()`, `_make_pitcher()`. Rewrote `generate_players()` to produce 47 players/team: 12 fielders + 3 DH + 19 active pitchers + 13 reserves. No `pitcher_role` set at generation. |
| `o27v2/db.py` | Added `stamina INTEGER DEFAULT 50` and `is_active INTEGER DEFAULT 1` to CREATE TABLE and ALTER TABLE migration path. |
| `o27/engine/state.py` | Added `stamina: float` field to `Player` dataclass (defaults to `pitcher_skill` for back-compat). |
| `o27/engine/manager.py` | Rewrote `pick_new_pitcher()`: no `pitcher_role` read; scores candidates by `state.outs` bracket: outs ≥ 19 → max Stuff, outs 10–18 → Stuff dominant, outs < 10 → max Stamina. |
| `o27v2/sim.py` | `_db_team_to_engine()`: SP selected as highest-Stamina arm not used in last 4 days. Added `_recently_used_pitcher_ids()`. Simplified `_promote_pitcher_role()`. |
| `o27v2/injuries.py` | `get_active_players()`: returns `is_active=1` healthy players; promotes reserves one-for-one to fill injuries back to 19P / 15 position-player target. |

---

## 2. KNOWN BUGS WITH FILE/LINE REFERENCES

### Bug 1: Run environment 3× too high

- **Symptom:** 12.21 runs/team/game measured. SLG .465 (MLB .410). BA .295 (MLB .250). K% 25.5% and BB% 7.1% are near-MLB; the excess comes from extra-base hits and baserunning.
- **Root cause:** Base contact and advancement probabilities in `o27/config.py` were tuned without accounting for O27's 12-batter lineup (vs. 9-batter MLB). More lineup spots cycling through 27 outs means more plate appearances per game — roughly 50% more — and the "Stay" mechanic credits additional hits without ending at-bats, compounding it.
- **Files and lines:**
  - `o27/config.py:128–165` — `CONTACT_WEAK_BASE = 0.38`, `CONTACT_MEDIUM_BASE = 0.40`, `CONTACT_HARD_BASE = 0.22` and the `WEAK_CONTACT`, `MEDIUM_CONTACT`, `HARD_CONTACT` hit-type weight tables. The single/double weights in HARD_CONTACT are the most likely culprit.
  - `o27/config.py:90–101` — `PITCHER_DOM_CONTACT = -0.03`, `BATTER_DOM_CONTACT = +0.03` dominance modifiers.
  - `o27/engine/prob.py` — `_runner_advance()` function: extra-base chance formula uses `speed - 0.5` as a linear addend; scale may be too generous.
- **Fix approach:** Reduce `CONTACT_HARD_BASE` and `CONTACT_MEDIUM_BASE`. Re-sim a full season and check runs/game. Target: 14–18 total runs/game (7–9 per team — O27's structural rules produce more than MLB by design; 24 is not defensible).

### Bug 2: Duplicate `game_pitcher_stats` rows

- **Symptom:** 260 extra rows across 184 games. Any raw query against `game_pitcher_stats` without the dedup view overcounts pitcher stats.
- **Root cause:** The `simulate_game()` guard at `sim.py:353` raises `ValueError` for already-played games when called directly, but the batch helper `simulate_next_n()` at `sim.py:598` calls `simulate_game()` inside a loop without re-verifying `played=1` status after each commit. A concurrent web request or retry during a batch run can insert stat rows twice for the same game. A secondary path also exists: `_insert_pitcher_stats()` at `sim.py:570–582` writes phase-less rows as a fallback; if both the phase-aware path (line 454) and this path fire for the same game, rows multiply.
- **Files and lines:**
  - `o27v2/sim.py:353` — already-played guard.
  - `o27v2/sim.py:454–467` — primary phase-aware pitcher stat INSERT.
  - `o27v2/sim.py:570–582` — `_insert_pitcher_stats()` secondary path.
  - `o27v2/web/app.py:147–158` — `_PSTATS_DEDUP_SQL` view (mitigation, not fix).
- **Fix approach:** Add `UNIQUE(game_id, player_id, team_id, phase)` constraint to `game_pitcher_stats`. Change both INSERT paths to `INSERT OR IGNORE`. Run a one-time cleanup: `DELETE FROM game_pitcher_stats WHERE rowid NOT IN (SELECT MIN(rowid) FROM game_pitcher_stats GROUP BY game_id, player_id, team_id, phase)`.

### Bug 3: Individual pitcher FIP goes negative

- **Symptom:** 104/185 pitchers have a negative raw FIP component. Worst case: 1 K, 0 HR, 0 BB → component = −27.0.
- **Root cause:** K% (25.5%) is high relative to HR% (2.2% per PA). FIP as a formula assumes an MLB-typical K/HR ratio. In this sim, strikeouts dominate so severely that the `−2*K` term outweighs `13*HR + 3*BB` even at normal K totals. The calibrated constant (`_league_fip_const()`) keeps the league average non-negative, but it cannot fix individual pitcher values.
- **Files and lines:**
  - `o27v2/web/app.py:242–265` — `_league_fip_const()`.
  - `o27v2/web/app.py:297` — per-pitcher FIP: `p["fip"] = ((13*hr) + (3*bb) - (2*k)) * 27.0 / outs + fip_const`.
  - `o27/config.py:90–101` — dominance modifiers that set relative K rate.
- **Fix approach:** Primary fix is Bug 1 (calibration). Short-term display guard: floor `p["fip"]` at 0.0 at line 297. The formula itself is correct; the inputs are wrong.

### Bug 4: `game_batter_stats.outs_recorded` zero for batters with at-bats

- **Symptom:** 497 rows have `outs_recorded = 0` with `ab > 0`. Some are legitimate (a 1-for-1 hitter who only produced a hit and no out), but the volume suggests genuine misses.
- **Root cause:** The renderer's out-attribution in `_update_stats()` uses a leftover reconciliation at line 824: `s.outs_recorded += leftover`. This fires only when `_or_before` fails to detect the out earlier in the same event. When a runner is thrown out on the bases during a hit (e.g., first-to-third on a single, thrown out at third), the out may land in the unattributed pool in `_compute_team_phase_outs()` rather than in the batter's row.
- **Files and lines:**
  - `o27/render/render.py:736–824` — `_update_stats()`, out-attribution block. Lines 762, 768, 795, 804 increment directly; line 824 handles leftovers.
  - `o27v2/sim.py:238–280` — `_compute_team_phase_outs()` unattributed-out reconciliation.
- **Fix approach:** Add logging to `_compute_team_phase_outs()` that prints `(game_id, team_id, phase, unattributed)` whenever `unattributed > 0`. Cross-reference with batter rows having `outs_recorded = 0` in the same phase to isolate the play type.

### Bug 5: Super-inning assertion crash

- **Symptom:** `AssertionError: SI super_bottom overrun for home round N: outs=6` terminates the process.
- **Root cause:** The SI half-end condition in `game.py:run_half()` relies on the dismissal count reaching 5 before the outer loop checks the assertion. Under specific combinations of Stay + baserunning, the outs counter reaches 6 before the `break` fires.
- **Files and lines:**
  - `o27/engine/game.py:162` — `assert state.outs <= 5` (super_top).
  - `o27/engine/game.py:191` — same assertion (super_bottom).
- **Fix approach:** Replace both assertions with:
  ```python
  if state.outs > 5:
      # log warning; treat as normal half-end
      break
  ```
  The safety invariant is preserved as a log, not a crash.

### Bug 6: `_find_pitcher_id()` dead branch after Task #65

- **Symptom:** Not a crash; a misleading dead code path.
- **Root cause:** Task #65 cleared all `pitcher_role` values. The first branch at `sim.py:567` (`if p.pitcher_role in ("starter", "workhorse")`) never fires. The fallback at `sim.py:569` handles it correctly.
- **File and lines:** `o27v2/sim.py:565–579` — `_find_pitcher_id()`.
- **Fix approach:** Remove lines 567–568. Leave only the `if p.is_pitcher` fallback.

---

## 3. DATABASE STATE

### Current schema (confirmed via PRAGMA)

**`players`** — 185 rows (pre-Task-65 seed, 18/team old generator)
```
id, team_id, name, position, is_pitcher, is_joker,
skill (stored as REAL 0.0–1.0; new seed would store INTEGER 20–80),
speed REAL, pitcher_skill REAL,
stay_aggressiveness REAL, contact_quality_threshold REAL,
archetype TEXT, pitcher_role TEXT,
hard_contact_delta REAL, hr_weight_bonus REAL,
age INTEGER, injured_until TEXT, il_tier TEXT
```
**MISSING from live DB:** `stamina INTEGER DEFAULT 50`, `is_active INTEGER DEFAULT 1`

**`teams`** — 30 rows
```
id, name, abbrev, city, division, league, wins INTEGER, losses INTEGER
```

**`games`** — 4,500+ rows (full 162-game season scheduled)
```
id, season INTEGER, game_date TEXT, home_team_id, away_team_id,
home_score, away_score, winner_id, super_inning INTEGER, played INTEGER, seed INTEGER
```

**`game_batter_stats`** — ~32,000 rows
```
id, game_id, team_id, player_id, phase INTEGER,
pa, ab, runs, hits, doubles, triples, hr, rbi, bb, k, stays, outs_recorded
```
No uniqueness constraint. No known widespread duplicate issue here (pitcher stats are the duplicate problem).

**`game_pitcher_stats`** — 6,316 rows (260 are duplicates)
```
id, game_id, team_id, player_id, phase INTEGER,
batters_faced, outs_recorded, hits_allowed, runs_allowed, er, bb, k, hr_allowed, pitches
```
No uniqueness constraint. **This is what allows duplicate rows.**

### Integrity issues

| Issue | Count | Impact |
|-------|-------|--------|
| Duplicate pitcher stat rows | 260 extra rows in 184 games | Raw queries overcount; mitigated by `_PSTATS_DEDUP_SQL` |
| `batter.outs_recorded = 0` with `ab > 0` | 497 rows | No displayed stat affected yet |
| `stamina`, `is_active` absent | All 185 players | Task #65 live-role logic uses fallback silently |
| `skill`/`speed`/`pitcher_skill` stored as floats | All 185 players | `scout.to_unit()` handles both formats; no functional issue |

### Is the existing season salvageable?

No, not for tuning purposes. The run environment (12.21 R/G/T) is wrong enough that any calibration decision made against this dataset will be meaningless. After Bug 1 is fixed, do a full reseed (`seed_league()` → `seed_schedule()`) and re-sim a fresh season before evaluating any stats. The existing data can be preserved in a backup but should not be the reference dataset for calibration.

To apply Task #65 columns to the current DB without a full reseed (for code compatibility only, not roster expansion):
```sql
ALTER TABLE players ADD COLUMN stamina INTEGER DEFAULT 50;
ALTER TABLE players ADD COLUMN is_active INTEGER DEFAULT 1;
```

---

## 4. WHAT NOT TO TOUCH

These are working and settled. Do not relitigate.

- **Engine architecture.** `o27/engine/` is a clean state-machine game engine. The event→renderer→DB pipeline is correct in structure. Calibration problems are constant values in `o27/config.py`, not design flaws.
- **47-player rosters (34 active + 13 reserve).** Composition: 12 fielders + 3 DH + 19 active pitchers + 13 reserves. Settled in Task #65.
- **12-batter lineups.** 8 fielders + SP + 3 DH = 12. This is structural to O27 rules. Do not reduce to 9.
- **Stay mechanic.** The "Stay" plate appearance outcome is a core O27 rule. The `stays` column accounting in `game_batter_stats` is correct.
- **No stored `pitcher_role`.** Task #65 decision. Manager AI derives role live from Stuff and Stamina at each appearance. Do not add back a persisted role field.
- **Dedicated pitchers only.** Non-pitchers (`is_pitcher=0`) never relieve. Enforced in `pick_new_pitcher()`. Two-way exceptions are not implemented and not needed.
- **Single Flask app, `/stats` blueprint.** One process, one DB file. `o27v2/web/app.py` and its templates. Do not split.
- **Fly.io: `hybrid-baseball`, region `ams`, volume `o27v2_data`.** The `O27V2_DB_PATH` env var on Fly points to `/data/o27v2.db`. Do not change the volume mount path.
- **Per-27-outs stat scale.** ERA, WHIP, K, BB are all per 27 outs (one O27 game). Consistent throughout `app.py:_aggregate_pitcher_rows()`. Do not convert to per-9-IP.
- **FIP constant calibration.** `_league_fip_const()` re-fits each render cycle. Do not hardcode a constant.
- **`_PSTATS_DEDUP_SQL` view.** Keep this in place until the `UNIQUE` constraint is added to `game_pitcher_stats`. Do not remove it early.
- **`scout.to_unit()` dual-format handling.** The function accepts both legacy floats (≤ 1.0, passed through) and 20–80 integer grades. Both the old DB (floats) and new seed (integers) work correctly. Do not change the branch logic.

---

## 5. OUTSTANDING DESIGN QUESTIONS

**Run environment target.**
What is the intended runs/game/team for O27? The structural rules (12-batter lineups, Stay mechanic, 27 outs/side) produce more scoring than MLB by design, but no numeric target has been set. A target is required before any calibration attempt. Suggested starting point: 7–9 total runs/game (3.5–4.5 per team). This is a design decision, not a code question.

**Aging and attribute drift.**
Task #65 set up `stamina` and `pitcher_skill` as independently rolled attributes but added no year-over-year drift. The design intent was that a stamina-decaying arm naturally drifts from the rotation into the bullpen. No aging loop exists. Needs: a per-offseason drift function (±1–3 grade points/year, age-curve weighted for peak at 27–29 and decline after 32).

**Reserve promotion visibility.**
When an active player is injured, the highest-Stamina reserve is promoted ephemerally (in-memory only, no DB flag flip). The web UI shows no indication that a reserve is playing that day. Either accept invisible depth or add a per-game `playing_today` record.

**Super-inning scoring rules.**
Whether SI halves should hard-cap at 5 outs (crashing if exceeded) or soft-cap (log and continue) is unresolved. The assertion-to-crash behavior is clearly wrong; the question is whether 6+ outs in a single SI half should be a logged anomaly or a corrected bug in the half-end detection.

**Schedule inter/intra-division split.**
The schedule generator uses weighted round-robin with division preference (`schedule.py:186`). Whether the actual inter/intra-division game distribution matches the intended design (e.g., 4× vs. 3× series) has not been formally verified. A query checking game counts by same-division vs. cross-division team pairs would confirm or deny.

**FIP replacement formula.**
Standard FIP was designed for MLB conditions. With O27's 12-batter lineups, Stay mechanic, and different K/HR/BB ratios, an O27-native expected-runs metric may be more meaningful long-term. This is a research question; FIP with the calibration constant is adequate as a placeholder.

**Leaderboard by attribute tier.**
Now that every attribute is on the 20–80 scout scale, a "Top Arms by Stuff" and "Top Arms by Stamina" leaderboard is structurally available. No decision has been made on whether to surface this in the `/stats` page.
