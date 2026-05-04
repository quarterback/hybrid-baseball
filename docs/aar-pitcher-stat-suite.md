# After-Action Report — Pitcher Stat Suite (wERA / xFIP / Decay)

**Date completed:** 2026-05-04
**Branch:** `claude/fix-dark-theme-baseball-terms-7UhIv`

---

## What was asked for

Three iterations of the same complaint, each tightening the design:

1. > "ERA, WHIP and K/27 are just not good stats for this sport, there need to be other ways to make sense of stats for pitchers that make it easier to evaluate them, brainstorm them"

2. > "they're fine in the aggregated sense but not specially useful for game context at all. perhaps some kind of adjusted version that makes it closer to look like a what a real ERA tells you could help, but FIP and others are just not particularly revealing for this variant of baseball. it's a matter of figuring out what we actually want here and stop to tell."

3. > "aERA would be a way to simulate innings pitched by taking every 3 outs as an inning (probably what you were doing anyway) but idk what that would look like, same for FIP/WHIP, but idk what O27 necessitates in new stats we haven't tread that ground yet and this is time to do it for pitchers"

The third one was the unlock. The first two drafts of the plan were retrofits — re-skinning ERA / WHIP / K-27 to feel less broken. After the third message we threw the retrofit out and invented stats from O27's own mechanics: continuous 27-out innings, the stay rule, the 3-foul-out cap, no inter-innings rest.

The user then handed back a complete spec — three result-tier stats, a per-appearance Game Score, league-relative indices, full column layouts. The implementation is that spec.

---

## What was built

### Three result-tier stats

| Stat | Formula | Tells you |
|---|---|---|
| **wERA** | `(0.85·ER_arc1 + 1.00·ER_arc2 + 1.20·ER_arc3) × 27 / outs × C_w` (C_w refit to anchor league wERA = league raw ER/27) | Given *when* in the 27-out arc the runs scored, how much did this pitcher actually damage his team |
| **xFIP** (O27) | `(13·HR + 3·BB − 2·(K + FO)) × 27 / outs + C_x` (C_x refit so league xFIP = league wERA) | Stripping out batted-ball luck and defense, how good is this pitcher actually |
| **Decay** | `(K%_arc1 − K%_arc3) × 100`, gated on bf_arc1 > 0 AND bf_arc3 > 0; K% includes foul-outs | How does his stuff hold up across the continuous 27-out half (0 = durable, 30+ = significant late-arc fade) |

The point of all three: the same pitcher can post wERA 4.50, xFIP 5.80, Decay +30. He's been getting lucky AND his stuff falls off late. Three different stories the same season can tell — none redundant.

### Per-appearance Game Score (locked formula)

```
GSc = clamp(0, 100, 50 + outs + 2·max(0, K-3) - 2H - 4ER - 2UER - BB - 4HR + 1·fo_induced)
```

The `+1·fo_induced` bonus rewards the 3-foul-rule cheap outs that O27 invented. A 12-out / 5-K / 0-ER outing scores ~66; a 1-out / 3-ER outing ~39. Stable across short and long appearances — replaces the noise-amplifying ERA/WHIP/K-27 trio in the box score.

### Workload + PA-rate metrics

- **GSc avg** — mean per-appearance Game Score across the season.
- **GSc+** — league-relative Game Score (100 = league avg, higher = better). Replaces ERA+ as the headline index.
- **OS%** — per-game outs share (outs / 27).
- **OS+** — league-relative outs per appearance. Surfaces workhorse vs short-relief profile.
- **AOR** — Average Outs Reached, mean outs per appearance.
- **WS%** — Workhorse Start %: share of starts with outs ≥ 18 AND ER ≤ 6. O27's Quality Start equivalent. Computed per-row (not derivable from aggregates).
- **K%** — `(K + foul-outs) / BF`. Re-defined to count foul-outs as Ks because in O27 they're equivalent strikeout-class outs.
- **BB%, HR%** — unchanged formulas, keyed off BF.

### Dropped entirely

ERA, WHIP, K/27, BB/27, HR/27, FIP, RA/27, K/BB, ERA+, "league FIP within 0.05 of league ERA" invariant — out of every context (box score, player page, leaders, stats browse, league index, season detail). The user's verdict: the aggregates were fine in isolation, but they distract from the new metrics that actually answer O27 questions.

---

## Engine + schema changes

### New persistence (`o27v2/db.py`)

12 arc-bucketed INT columns + 1 boolean added to `game_pitcher_stats`:

```
er_arc1, er_arc2, er_arc3       -- earned runs by arc third
k_arc1,  k_arc2,  k_arc3        -- strikeouts by arc third
fo_arc1, fo_arc2, fo_arc3       -- foul-outs by arc third
bf_arc1, bf_arc2, bf_arc3       -- BF by arc third
is_starter                       -- 1 if this pitcher started the game
```

Arc thirds key off the **defending team's running 27-out count** (1-9 → arc 1, 10-18 → arc 2, 19-27 → arc 3). Super-innings outs roll into arc 3.

`outs_arc*` is intentionally not stored — wERA's denominator is total outs, and Decay needs only K/FO/BF per arc.

### Engine instrumentation (`o27/engine/`)

- `_arc_index(outs)` helper in `o27/engine/pa.py`.
- `pa_start_outs` snapshotted in `_end_at_bat` so K/BB/FO/BF for an at-bat all charge to the arc the AB *started* in (not the arc the resulting out crossed into). One subtle correctness call: ER is bucketed at *score time* per the spec, not start-of-AB, so wERA's late-arc weighting captures when damage actually happens.
- Per-spell arc accumulators (`pitcher_*_arc_this_spell`) added to `GameState`. Reset on every spell change.
- `SpellRecord` extended with arc fields. Both `_close_current_spell` (in `o27/engine/game.py`) and `pitching_change` (in `o27/engine/manager.py`) pass them through. **The manager.py path also picked up a pre-existing bug fix as a side effect**: it was building `SpellRecord` without `sb_allowed`, `cs_caught`, `fo_induced`, leaking those counters across pitchers when a reliever came in mid-half. Plumbed alongside the arc fields.

### Sim integration (`o27v2/sim.py`)

`_extract_pitcher_stats` sums arc fields across spells (a pitcher may pitch discontiguous spells if pulled and re-used). Stamps `is_starter = 1` if the first-by-`start_batter_num` spell entered at PA #1.

### Aggregator rewrite (`o27v2/web/app.py`)

- `_pitcher_game_score(outs, k, h, er, uer, bb, hr, fo)` — pure function, used by both per-row box-score rendering (via the `_decorate_pitchers` decorator) and `_aggregate_pitcher_rows` (via per-game means for season averages).
- `_league_werra_consts()` returns `(c_w, c_x, league_outs_per_g)` refit per call. Solves `c_w` so league wERA = league raw ER/27, then `c_x` so league xFIP = league wERA. Drops the old `_league_fip_const`.
- `_aggregate_pitcher_rows` rebuilt: drops every per-27-outs derivation; adds wERA, xFIP, Decay, GSc avg, GSc+, OS+, AOR, K%/BB%/HR% (with foul-outs in K%), oOBP/oSLG/oOPS, K-BB%, VORP rebased to wERA.
- `_PSTATS_DEDUP_SQL` extended to project the 13 new columns.
- All six pitcher-SELECT call sites (index top-5, /game/<id>, /players, /stats, /leaders, /player/<id>, /team/<id>) updated to `SUM` the new arc/is_starter columns.

---

## UI changes

| Surface | Was | Now |
|---|---|---|
| **Box score** (`game.html`) | Pitcher \| BF \| OS% \| OUT \| H \| R \| ER \| BB \| K \| HR \| HBP \| UER \| SB \| CS \| FO \| P \| ERA \| WHIP \| K/27 | Pitcher \| **G** \| **GSc** \| OUT \| BF \| P \| OS% \| H \| R \| ER \| BB \| K \| HR \| FO |
| **Player page header** | … ERA · ERA+ · K · pWAR | … **wERA** · **xFIP** · K · pWAR |
| **Player page Pitching tab** | Standard / Rate-per-27 / Per-Batter / Value | Standard (+ GS) / **Result-Tier (wERA/xFIP/Decay)** / **Workload (GSc avg/OS%/OS+/AOR/WS%)** / Per-Batter / Value (GSc+/VORP/WAR) |
| **Player game log** | BF \| Outs \| H \| R \| ER \| BB \| SO \| HR \| P | **GSc** \| Outs \| BF \| H \| R \| ER \| BB \| SO \| HR \| FO \| P |
| **/players** pitcher index | ERA, WHIP | wERA, xFIP, K%, BB%, OS+, GS |
| **/leaders** | 4 cards on per-27 rates + ERA+/FIP | 4 cards on result-tier (wERA/xFIP/Decay/GSc+) + 4 on workload (GSc avg/OS+/AOR/W) + 4 on PA rates (K%/BB%/HR%/oAVG) |
| **/stats** pitching browse | per-27 column block + ERA+/FIP/WHIP | new column block: wERA \| xFIP \| Decay \| GSc \| GSc+ \| OS+ \| AOR (per-27 dropped) |
| **/team/<id>** pitching panel | ERA \| FIP \| WHIP | wERA \| xFIP \| K% |
| **Index top-5 cards** | ERA · Top 5 | **wERA · Top 5** |

Everything that referenced `era`/`fip`/`whip`/`k27` was either dropped or aliased to the new keys. The season archive's `season_pitching_leaders` table reuses its three numeric slots (`era`/`fip`/`whip`) to store wERA/xFIP/GSc-avg for go-forward seasons; old archived seasons keep their original ERA/FIP/WHIP values undisturbed.

---

## Backfill

The arc columns are only populated for games sim'd after the schema change. Existing seasons need a one-shot replay — `o27v2/manage.py backfill_arc`:

1. Wipes `game_pitcher_stats` / `game_batter_stats` / `team_phase_outs` / `transactions`.
2. Resets `played=0`, `home_score`/`away_score`/`winner_id` on all games (keeps `seed`).
3. Restores active rosters (clears `injured_until`, `il_tier`).
4. Re-runs `simulate_next_n` from day 1 — `simulate_game` reads the persisted seed, so outcomes match the original sim exactly.
5. New instrumentation populates arc fields as games re-run.

Engine output is fully seed-deterministic given roster state, and the games table persists each game's seed (the trade-deferral fix from the previous PR ensured trade decisions are also seed-deterministic given games-played-so-far). So the replay reproduces the original season faithfully — same scores, same standings, same individual stats.

---

## Verification

- **Unit-level invariant** — per-pitcher arc sums equal totals on every game (`k_arc1+k_arc2+k_arc3 == k`, same for ER/FO/BF). Verified after instrumenting pitching_change to also reset/snapshot the arc accumulators (the first smoke-sim pass had ER/BF arc counters leaking across pitchers when relievers came in; fix landed alongside the related sb_allowed/cs_caught/fo_induced leak that pre-dated this PR).
- **League calibration** — `_league_werra_consts` refits each call so league wERA ≈ league raw ER/27 and league xFIP ≈ league wERA within 0.05. Re-asserted in `tests/test_stat_invariants.py::test_invariant_8_fip_anchored_to_era` (renamed in spirit, kept the function name for git history).
- **Click-through** — every route under `python o27v2/manage.py runserver` returns 200: index, standings, schedule, players, leaders, stats (batting + pitching tabs), teams, team detail, transactions, seasons, player detail, game detail.
- **Stat sanity** on a 50-game smoke sim: a pitcher with 35 outs / 11 ER scattered between arcs posted wERA 7.94 (well below league 11.40) but xFIP 1.77 — a defense-independent talent isolation that flagged him as elite even though his runs-allowed line wasn't. Decay +27.3 said his stuff faded late. Three signals, three different stories — exactly the design payoff.

---

## Lessons learned

### The retrofit reflex

The first two drafts of the plan asked "how do we re-skin ERA / WHIP / K-27 so they don't blow up at small samples." The third draft, after the user explicitly pushed back, asked "what does O27 *do* that no MLB stat tracks." That re-framing was the entire unlock — every metric in this PR (wERA, xFIP-with-foul-outs, Decay) keys off mechanics that don't exist in MLB.

When a stat looks broken in a variant sport, the first instinct is to fix the formula. The second-pass instinct is to ask whether the *thing the stat is measuring* is even what you care about in the new sport.

### Per-spell counters need symmetric resets

The pre-existing bug in `manager.py:pitching_change` (sb_allowed/cs_caught/fo_induced not reset between pitchers) had been latent because it only manifests when relievers enter mid-half and tools rarely sum SB/CS/FO at high enough resolution to catch the leak. Adding 12 more arc-bucketed counters that go through the same path made the bug visible immediately. Whenever the engine maintains state across multiple "spell" structures, the close-spell flow and the open-new-spell flow have to be symmetric — every counter that's accumulated needs to be both snapshotted *and* reset.

The fix added a 4-line block to both `_close_current_spell` and `pitching_change` to keep them in lockstep. A future refactor that extracts the snapshot+reset into a single helper would prevent this class of drift entirely.

### Decay needs a sentinel, not a None

The first cut left `decay = None` for pitchers without enough cross-arc sample. Jinja's `sort` blew up the leaders page (`'<' not supported between instances of 'float' and 'NoneType'`). Switched to a sentinel value (999.9) plus a `decay_known` boolean for display. Lower-is-better leaderboards still sort correctly, and templates check the boolean to render "—" for missing data.

The lesson generalizes: any aggregate metric that can legitimately be undefined needs a sortable default, not a None — even when it would mean "unknown" semantically. Sentinel + paired-known-flag pattern is cheaper than carrying nullable floats through every sort/format path.

### One stat per slot is enough

Earlier drafts of this plan tried to ship rERA + GSc+ + ERA+ together — three different "league-relative pitcher quality" metrics, all explaining the same thing. The final spec drops to one (GSc+) plus the result-tier trio. The trio carries different information (wERA is what happened, xFIP is what should have, Decay is the trajectory); GSc+ is the single league anchor. Less is more — three metrics that each tell one story beats six metrics that overlap.
