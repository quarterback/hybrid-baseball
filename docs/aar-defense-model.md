# After-Action Report — Defense Model (Range, Errors, Per-Position Ratings, DRS/dWAR, PP-Pitching)

**Date completed:** 2026-05-03
**Branch:** `claude/improve-sim-realism-UHvKE`
**Commits:** `b376bcb`, `7e3c3f9`

---

## What was asked for

Defense was the largest open lever called out in the previous AAR (`aar-pitcher-tilt-and-elite-talent.md` § Known issues). The user picked **Errors + DRS** depth with **visible errors** (errors charge UER, credit ROE to the batter, and surface as discrete events).

Mid-task expansion:

> "Adding defense attributes to players would make this easier for each player, arm, fielding ability by position grouped by most likelies. Some guys can be legit utility guys since there's no real progression model — less necessary to have them growing over time, but the arm / defense / infield / outfield / catcher will work best. A strong-arm guy could in a blowout be used as a pitcher akin to real life, but keep those situations down to like an absolute blowout though — in this sport it'd make that worse, so you'd want to reserve those guys for the last few outs of a blowout, not 10 outs to go. Maybe 5–6 or even less before you'd bring a position player in with a strong arm to pitch if the score is so far out of hand with 6–9 outs to go that it's not likely they'll come back (think down 17+ with 6 outs to go or something)."

So three intertwined goals:
1. **Position-aware defense** — INF / OF / catcher specialist groups, plus utility players legit across groups.
2. **DRS / dWAR with positional value** — surfaces defensive value in WAR alongside batting/pitching.
3. **Emergency position-player pitcher in absolute blowouts** — strong-arm bench bat takes the mound when team is down 17+ with 6 or fewer outs left.

---

## What was built

### Stage 1 — Player attributes + schema (`b376bcb`)

New fields on `Player` (all default to 0.5 → identity):
- **`defense`** — general glove / surehandedness
- **`arm`** — throwing strength
- **`defense_infield`** — 1B / 2B / 3B / SS specialist rating
- **`defense_outfield`** — LF / CF / RF specialist rating
- **`defense_catcher`** — catcher framing / blocking

New columns on `players` table for all five, plus a new `roe` column on `game_batter_stats` (reached on error). ALTER TABLE migrations follow the existing pattern.

`BatterStats` dataclass gets `roe`. `GameState` gets `pitcher_errors_this_spell` (rolled into `_close_current_spell` via `SpellRecord`).

### Stage 2 — Engine wiring: range, errors, catcher arm (`b376bcb`)

**Team defense aggregator** (`sim._team_defense_rating`): positional-value-weighted mean across the 8 fielders. C and SS count more than 1B per Bill James positional-value table. Stamped on `Team.defense_rating` at game start.

**Catcher arm** stamped on `Team.catcher_arm` at game start.

**Range modifier in `prob.resolve_contact`**: after the contact-table draw produces an outcome, a fraction `range_shift = abs(team_def - 0.5) × 2 × DEFENSE_RANGE_SHIFT_SCALE` of plays flip:
- Better defense → some `single` outcomes flip to `ground_out`
- Worse defense → some `ground_out`/`fly_out`/`line_out` outcomes flip to `single`

**Error event in `resolve_contact`**: would-be-out plays trigger an error roll at `err_p = DEFENSE_ERROR_BASE − def_dev × DEFENSE_ERROR_SCALE` (default ~1.8% at neutral D). When fired, hit_type becomes `"error"`, batter is safe, advances like a single, and the path returns `is_error=True`.

**Error → UER**: `pa._score_run` charges any run scored in a spell after an error as unearned. Over-aggressive vs strict MLB scoring (which tries to determine which runs would have scored absent the error), but stable and produces visible UER counts that scale with team error rate.

**Catcher arm in SB success**: `prob.between_pitch_event` subtracts `(catcher_arm − 0.5) × SB_SUCCESS_CATCHER_ARM_SCALE` (= 0.20) from raw success probability. Elite-arm catcher (0.85) shuts down running game; noodle-arm (0.30) is exploited.

**Renderer**: `_update_stats` adds an `elif hit_type == "error"` branch that credits `s.roe += 1`, `s.ab += 1`, no hit, no out. `_stat_delta` extended with `sb / cs / fo / roe` so per-phase stat snapshotting includes them — this was the silent bug zeroing ROE in the DB even when the renderer credited it correctly.

### Stage 3A — Per-position fielding sub-ratings (`7e3c3f9`)

`league.py` rolls a "primary specialty" group at full tier and the two off-group ratings at attenuated rolls (mean ~ replacement-tier). Position determines primary:
- C → catcher group at full tier
- 1B/2B/3B/SS → infield group at full tier
- LF/CF/RF → outfield group at full tier

With **10% probability OR for UT-slot players**, all three rolls are at full tier — that's the legit utility archetype. The user explicitly called this out as needing a real "Ben Zobrist" bucket.

`sim._team_defense_rating` now uses `_position_defense_rating(player, pos)` which blends **60% sub-group + 40% general** defense. A guy playing out of his primary group is visibly weaker.

Verified in fresh seed: **Damian Cuevas (SS) IF=78 / OF=48** — pure SS specialist; **Akos Traore (UT) IF=56 / OF=40 / C=41** — utility profile with decent breadth; **Marcus Hunt (CF) OF=33** — possible per the tier ladder (5% of "primary" rolls land in sub-replacement).

### Stage 3B — Emergency PP-pitcher in extreme blowouts (`7e3c3f9`)

New config knobs:
- `PP_PITCH_DEFICIT_MIN = 17` — fielding team must trail by ≥ 17 runs
- `PP_PITCH_OUTS_LEFT_MAX = 6` — AND have ≤ 6 outs left to record
- `PP_PITCH_ARM_MIN = 0.55` — AND a position player with at least this arm rating

`manager.pick_new_pitcher` checks these gates BEFORE the regular pitcher-pick logic. When fired, the highest-arm position player takes the mound. Tightly gated per user direction — won't fire with 10+ outs to go even in 17-run games, won't fire with smaller deficits.

`prob._pitch_probs` recognizes when the current pitcher's `is_pitcher` is False and blends `0.55 × arm + 0.45 × pitcher_skill` into `raw_stuff`. A strong-arm bench bat throws meaningfully better than a noodle-arm one. Today_form still applies; spell-fatigue still applies. They're still amateurs but their arm rating gets to express.

### Stage 3C — DRS / dWAR + UI (`7e3c3f9`)

New `_POSITION_DRS_RANGE` table (runs / 162g range for elite-vs-replacement at each position):

| Position | Range (runs / 162G) |
|---|---|
| C | 15 |
| SS | 12 |
| 2B, CF | 8 |
| 3B | 7 |
| LF, RF | 5 |
| 1B | 4 |
| DH | 0 |
| UT | 6 |
| P | 2 |

`_aggregate_batter_rows` adds:
- **pos_def** — position-aware defense rating (60% sub + 40% general)
- **DRS** = `(pos_def − 0.5) × 2 × (games/162) × position_drs_range`
- **dWAR** = `DRS / runs_per_win`
- **WAR** is now total (offense + defense). The pure-batting WAR is preserved as `war_off` for callers who want offense-only.

Stats query updated to SELECT the per-position defense columns directly, so the aggregator can compute DRS without separate plumbing.

`/stats` batter template gets new columns: **Def, DRS, dWAR, ROE**, plus the WAR column updated to show the total.

---

## Calibration evidence

200-game fresh league:

**Defense distribution**:
- Top 5 DRS over the small sample: SS Fujita +0.6 (Def 0.717), SS Maehara +0.5, C Goodwin +0.5, C Holland +0.5, SS Osborne +0.4. Extrapolated: a +6 DRS season for an elite SS over 162G, ~+6 for elite C — matches MLB-style scaling.
- Bottom 5 DRS: Arriaga (C, Def 0.283) -0.5, Astudillo (3B) -0.3. Extrapolated: a -6 to -7 DRS season for replacement-tier defenders at premium positions.

**Total WAR leaders** (offense + defense combined):
- Marcus Hunt (CF): oVORP +14.5, DRS −0.2, WAR 0.67 in 18 games → ~6 WAR/162G pace = MVP-tier
- Oh-Seong Matsui (DH): oVORP +12.6, DRS −0.0 (DH = no defense)
- Bernardo Dominguez (2B): oVORP +11.1, DRS +0.2 — full-package All-Star

**Errors / SB / CS**:
- 200-game league averages: ROE per game ≈ 0.4, SB ≈ 2.5/game, CS ≈ 1.3/game
- UER attributable to errors: 227 over 80 games (≈ 2.8/game) — visible but not dominant

---

## Key decisions and trade-offs

### Specialist vs utility model

The 60/40 sub/general blend is intentionally aggressive. A SS specialist with IF=78 / OF=48 / general=46 effectively plays at `0.6 × 78 + 0.4 × 46` ≈ 65 at SS, but `0.6 × 48 + 0.4 × 46` ≈ 47 at OF — visibly worse out of group. A utility player with IF=72 / OF=74 plays at ~64 at either, which is real breadth. The numbers map to the tactical archetypes the user wanted.

### UT-slot players force-rolled as utility

Without this, a UT-positioned player gets `primary="if"` (the default branch) and rolls as an INF specialist. That's wrong — the UT slot exists explicitly to be multi-position. Forcing utility-style rolls on UT slots makes the position name accurate.

### Errors charge UER over the rest of the spell

Real MLB scoring tries to determine which runs would have scored absent the error. That requires re-running the half hypothetically, which is genuinely complex. My implementation charges every run after an error in the same spell as UER. Over-aggressive but stable — and the resulting UER counts (~2.8/game) are believable for a sport with this much offense.

### PP-pitcher gating: 17-run deficit AND 6 outs left

The user's exact framing. Anything looser would have position-player pitching firing too often (PP appearances should be a once-per-50-games event, not a routine occurrence). Tested empirically — over 200 games on the test seed, PP-pitcher conditions almost never fired, which matches the intent.

### Catcher arm via team-stamped `catcher_arm`

The engine doesn't carry per-player position info on Player objects (positions live in the DB roster row). Stashing `catcher_arm` on the Team object at game start is cleaner than threading position lookups through the steal handler. Single source of truth, single point of update on lineup changes.

### POSITION_DRS_RANGE values

These are heuristic Bill James-flavored numbers. C at 15 reflects the position's massive impact (framing, throwing, blocking, calling games); SS at 12 reflects the volume of plays; 1B at 4 reflects the very limited play diversity. UT at 6 reflects "average of positions a utility guy plays" — they don't get the C/SS premium because they're spread across positions. The values can be tuned with multi-season data.

### Engine-level vs DB-level error attribution

I did NOT attribute errors to the responsible fielder. That would require modeling spray angle (the play type — grounder to SS vs fly to LF — determines which fielder makes the play). Without spray angle, every error would be attributed arbitrarily, which is worse than blaming the team collectively. Per-fielder error attribution waits for spray-angle modeling.

### The renderer `_stat_delta` bug

The most expensive bug in this PR: I added `roe` to BatterStats, the renderer credited it correctly, the extract function read it correctly, the INSERT wrote it correctly — but the **per-phase stat-delta computation** had a hardcoded list of fields it diffed, and `roe` wasn't in that list. So every phase boundary zeroed `roe` before extract ran. Discovered via patching the extract step to dump raw renderer stats and seeing `roe=1` in memory while the DB had 0. Fix was a one-line addition. The same bug had been silently zeroing `sb / cs / fo` since their addition; this fix benefits those too.

---

## What was verified

- **6/6 identity tests pass** — at all neutral inputs the engine still produces pre-realism / pre-defense output.
- **`o27v2/smoke_test.py`** — 10/10 games complete cleanly.
- **All key web routes return 200** on a fresh-seeded 200-game league: `/stats`, `/stats?side=pit`, `/standings`, `/leaders`, `/team/<id>`.
- **Defense profile distribution** verified: SS specialists (IF >> OF), CF specialists (OF primary), legit utility players (IF + OF + C all ≥ 50), and the occasional sub-replacement-primary as expected from tier ladder.
- **DRS scaling** verified: top-end SS gets ~+0.6 in 18 games (~+5.4 / 162G), bottom-end C gets ~−0.5 in 12 games (~−6.7 / 162G). Total WAR leaders show offense-driven value with defensive contributions hovering near zero in small samples.
- **Errors persist correctly** post-_stat_delta-fix.

---

## Files changed

| File | Change |
|---|---|
| `o27/engine/state.py` | Player gets `defense`, `arm`, `defense_infield/outfield/catcher`; Team gets `defense_rating` and `catcher_arm`; SpellRecord gets `pitcher_errors_this_spell` |
| `o27/engine/prob.py` | `resolve_contact` adds range modifier + error event; `between_pitch_event` reads `team.catcher_arm` for SB success; `_pitch_probs` blends `arm` into raw_stuff for position-player pitchers |
| `o27/engine/pa.py` | `_resolve_contact` detects hit_type=="error" and increments `pitcher_errors_this_spell`; `_score_run` charges UER for any run after an error |
| `o27/engine/manager.py` | `pick_new_pitcher` adds blowout PP-pitcher gate (deficit ≥ 17, outs_left ≤ 6, arm ≥ 0.55) |
| `o27/engine/game.py` | Both spell-reset blocks zero `pitcher_errors_this_spell` on new spell |
| `o27/render/render.py` | `_update_stats` credits `roe` on hit_type=="error"; `_stat_delta` includes `sb / cs / fo / roe` |
| `o27/stats/batter.py` | BatterStats gets `roe` |
| `o27/config.py` | New defense knobs: `DEFENSE_RANGE_SHIFT_SCALE`, `DEFENSE_ERROR_BASE/SCALE/MIN/MAX`, `SB_SUCCESS_CATCHER_ARM_SCALE`, `PP_PITCH_*` blowout thresholds |
| `o27v2/db.py` | Schema adds five player defense columns + `roe` on game_batter_stats; ALTER TABLE migrations |
| `o27v2/league.py` | `_make_hitter` rolls per-position sub-ratings with specialist/utility logic; `_make_pitcher` rolls `defense + arm` + neutral sub-groups; INSERT extended |
| `o27v2/sim.py` | `POSITIONAL_VALUE` table; `_position_defense_rating` helper; `_team_defense_rating` is position-aware; `_db_team_to_engine` plumbs new attrs and stamps `defense_rating` + `catcher_arm` on Team; batter extract / INSERT include `roe` |
| `o27v2/web/app.py` | `_POSITION_DRS_RANGE` table; `_position_defense_for_row` helper; batter aggregator computes `pos_def`, `drs`, `dwar`, total `war`; stats query SELECTs new defense columns |
| `o27v2/web/templates/stats_browse.html` | New columns: Def, DRS, dWAR, ROE |

---

## Known issues / follow-up candidates

- **Errors are team-attributed, not fielder-attributed.** A real DRS would credit/debit individual fielders for plays they did or didn't make. Without spray-angle modeling we can't say "the SS booted a grounder" — we just know the team made an error. Per-fielder error attribution waits for spray-angle.
- **PP-pitcher rarely fires with current thresholds.** With 17-run / 6-out gating on a fresh league, PP appearances are extremely rare (could be once per several hundred games). That's intentional but means the feature mostly exists as a safety valve — verify after a multi-season run that it fires occasionally.
- **dWAR is small in small samples.** 18 games of defense produces DRS in the ±1 range, dWAR in the ±0.05 range. Full-season totals are where defense becomes WAR-significant. Should look credible in a 162G run.
- **No defensive substitution / late-inning defensive replacement.** A real manager pulls a slugger for a defensive specialist in the bottom of the half with a 1-run lead. Not modeled.
- **Catcher arm uses a single rating per team-game.** If a team carries two catchers and rotates them, the rating reflects only the starter. Acceptable but could be plumbed per-spell if catcher rotation becomes a feature.
- **Per-position ratings unused for batting.** A C's `defense_catcher` rating doesn't affect their batting stats. Correct — it's a defensive rating only — but worth noting for anyone surprised that a +SS doesn't hit better.
- **Range modifier is symmetric.** Equal magnitude shifts at both ends (0.85 D = +0.07 out flip, 0.15 D = +0.07 hit flip). MLB has more ceiling on terrible defense than excellent (a 50-grade fielder vs 80-grade is much smaller spread than 50-grade vs 20-grade in actual defensive efficiency). Not modeled — symmetric is simpler and the per-position DRS table already encodes asymmetric position scarcity.
- **Position-player pitcher uses `pitcher.command/movement` defaults of 0.5.** A PP-pitcher's effective Stuff blends with arm, but their command and movement come from the position-player Player object's defaults. Not threading through arm-derived command would be a small additional accuracy gain.
- **Renderer `_stat_delta` field list still hardcoded.** Adding a new BatterStats field requires updating that list — the bug pattern that bit ROE could bite the next field too. A `dataclasses.fields()` iteration would be safer.
