# After-Action Report — Pitch-Type Activation, Defense Depth, Game-Variance Widening

**Date completed:** 2026-05-13
**Branch:** `claude/review-and-improve-NbhPy`

---

## What was asked for

Open-ended session: "what else should I add/improve on this". After
surfacing the leftover work from the prior session's AAR (pitcher
dominance, xRA v3, aging UI), the user pushed in three concrete
directions:

1. **Pitching model needs considerably more depth** — currently no
   differentiation between pitch types. Hitting is already well-modeled.
2. **Defense / fielding could be improved** along with the stats that
   correspond.
3. **Run-environment variance is too narrow** — 22–26 R/G feels like
   "too many close games." User asked whether teams can bust out with a
   far wider band (22-33, 25-37, more variance).

Mid-session the user also dropped a link to `dgrifka/baseball_game_simulator`
as inspiration (spray charts, Monte Carlo, Luck Ledger). Asked to stay
focused on the approved plan, so those become a separate session.

The user also asked for the AAR after the work.

---

## What was built

### 1. Pitch-type activation — dormant infrastructure goes live

The biggest finding: the codebase had a **complete pitch-type
infrastructure already in place but never used**. Six pieces shipped end
to end to activate it.

| Piece | Where | What changed |
|---|---|---|
| Repertoire seeding | `o27v2/league.py` | New `_build_repertoire()` samples 3-5 pitches from `PITCH_CATALOG` (`o27/config.py:755+`), filtered by `release_angle` ↔ `release_optimal`/`max_release` fit. Always includes one fastball as primary. Stored as JSON on `players.repertoire`. |
| Per-PA pitch selection | `o27/engine/prob.py` | `_select_pitch()` already existed — call site already passed `pitch_type` and `pitch_quality` into `pitch_outcome()` and `contact_quality()`. The dormant path was wired but unfed. Now it draws from each pitcher's repertoire each PA, count-biased (`2strike` → put-away pitches; `behind` → fastballs). |
| Engine event surfacing | `o27/engine/prob.py` | `ball_in_play` and non-contact returns now carry `pitch_type` so the renderer can stamp it on the PA log. |
| PA-log persistence | `o27/render/render.py`, `o27v2/db.py`, `o27v2/sim.py` | New `pitch_type TEXT` column on `game_pa_log`. Renderer writes it; sim INSERT plumbs it through. |
| Per-game pitch-mix on pitcher stats | `o27v2/sim.py` | New `_decorate_pitcher_pitch_mix()` post-processes the PA log to compute `fastball_pct / breaking_pct / offspeed_pct / primary_pitch` per `(pitcher, phase)`, plus `singles_allowed / doubles_allowed / triples_allowed`. Stamped on `game_pitcher_stats`. |
| UI surfacing | `o27v2/web/templates/player.html`, `leaders.html` | New "Arsenal" panel on the pitcher page (FB%/BR%/OFF% + per-hit-type counts). New `Pitching · Arsenal mix` block on `/leaders`. |

The `PITCH_CATALOG` defines 16 pitch types with per-pitch K/BB/contact
deltas, hard/weak-contact shifts, platoon modes, `release_optimal` /
`max_release`, and `count_bias`. Sidearm-heavy O27 worlds skew toward
sinkers (most-thrown pitch at 17% of all typed PAs after the sample
sim) and away from 12-to-6 curveballs (`max_release=0.50` rules them
out for three-quarter slot pitchers).

**Verification — pitch-type differentiation is real:** After a 30-game
slice, gyroball allowed 1 HR in 160 PAs while four-seam allowed 15 HR
in 419 PAs. That's the `hard_contact_shift=-0.06` vs `+0.04` flowing
through correctly.

### 2. xRA v3 — per-pitcher hit-shape

`_aggregate_pitcher_rows()` in `o27v2/web/app.py` previously used a
league-share blended weight for non-HR hits (v2 limitation noted in
the prior AAR). v3 now reads `singles_allowed / doubles_allowed /
triples_allowed` per pitcher and computes the run-value from each
pitcher's actual hit shape. Falls back to v2's league shares when the
new columns are zero (legacy rows). The multiplicative `xra_norm`
anchor keeps the league mean stable; pitcher-level variation widens.

### 3. Defense depth — assists, DP chains, range factor

The earlier AAR was stale on the defense model — per-fielder PO/E
attribution was already shipped. The real gap was assists.

- **Assists column** `a` added to `game_batter_stats` (`o27v2/db.py`)
  and the `BatterStats` dataclass (`o27/stats/batter.py`).
- **Renderer crediting** (`o27/render/render.py`): on `ground_out` /
  `fielders_choice` / `double_play` / `triple_play` / `infield_out`,
  credit the play's `fielder_id` with both PO and A. On DPs/TPs,
  pull a derived pivot infielder from the fielding team's lineup
  (2B/SS/3B/1B priority) and credit them with an additional PO + A.
  Approximate but produces realistic 6-4-3 chains.
- **Phase delta** (`o27/render/render.py:_stat_delta`) extended to
  carry the `a` column across super-inning splits. Without this the
  increments would have been wiped between phases (and they were — the
  first verification pass showed PO=636 / A=0 until this fix).
- **Range factor leaderboard tile** (`o27v2/web/app.py`,
  `leaders.html`): `(PO + A) × 27 / outs_team_played`. Surfaces high-
  involvement gloves regardless of the team's defensive workload.
- **Fielding% updated everywhere** to `(PO + A) / (PO + A + E)`.

**`should_defensive_sub` was already wired** — the older defense AAR
listed it as a gap, but `o27/engine/prob.py:1354` already calls it on
every PA, and it's gated by `state.outs >= 6` + manager
`bench_usage`. Removed from the punch list with no change needed.

### 4. Game-to-game run variance — three independent dials

User pushback: the 22-26 R/G band is too tight. Asked whether teams
can bust out into a 22-33 or 25-37 range — more slugfests and more
pitchers' duels both, around the same mean.

Three knobs added, all identity-at-defaults so legacy DBs reproduce:

1. **Per-pitcher form widening** (`o27/engine/prob.py:_maybe_roll_form`):
   `today_form` sigma and bounds now scale with each pitcher's
   `pitch_variance` and inversely with their `grit`. A high-variance
   low-grit arm sees `[0.6, 1.4]` bounds; a gritty consistent arm
   stays near `[0.92, 1.08]`. Default (pitch_variance=0) is identity.

2. **Per-team-per-game offense hot factor** (`o27v2/sim.py:_roll_today_condition`):
   each team rolls a `today_hot_factor ~ N(1.0, 0.10)` once per game,
   bounded `[0.78, 1.22]`. Multiplies onto every non-pitcher's
   `today_condition` for that game only. Both teams roll independently,
   so two-team-cold games are duels and two-team-hot games are
   slugfests. Stashed on `Team.today_hot_factor` for diagnostics.

3. **`pitch_variance` rolled at seed time** (`o27v2/league.py:_make_pitcher`):
   uniform `[0.02, 0.14]`. Combined with new `grit` (uniform `[0.25, 0.75]`)
   and `release_angle`, each new pitcher gets a distinct day-to-day
   behavior fingerprint. Persisted on `players` via three new columns
   with migrations + SCHEMA additions.

**Verification — variance:**
Pre-change typical 60-game slice: σ ≈ 3 R/G total, all games landed
roughly in the 14-30 band.
Post-change 90-game slice:

```
Per-team R/G: min=1  max=26  mean=10.9  stdev=4.69
Total R/G:    min=10 max=41  mean=21.8  stdev=7.14
```

League mean is preserved (21.8 vs README's 22-26 target band). The
tails are real — 3-7 duels and 21-20 / 17-19 / 14-22 slugfests both
showed up in the same 90 games. σ roughly doubled.

---

## What was reused (not built)

- `PITCH_CATALOG` (`o27/config.py:755`) — full 16-pitch catalog with
  per-pitch deltas. No new pitch-type definitions.
- `_select_pitch()`, `_release_quality()`, `_apply_pitch_platoon()` in
  `o27/engine/prob.py` — all already wired to consume `pitch_type` and
  `pitch_quality`.
- `_credit_fielder()` in `o27/render/render.py` — generic per-fielder
  stat incrementer.
- `_roll_tier_grade()` and the 20-80 talent ladder in
  `o27v2/league.py` — reused for pitch quality rolls so arsenal grades
  match the rest of the talent system.
- `manager.defensive_sub()` and `entry_type="DEF"` — already plumbed
  end-to-end.

---

## Schema changes (additive only)

**`players`**:
- `repertoire    TEXT    DEFAULT NULL` — JSON-encoded list of pitches
- `release_angle REAL    DEFAULT 0.5`
- `pitch_variance REAL   DEFAULT 0.0`
- `grit          REAL    DEFAULT 0.5`

**`game_pa_log`**:
- `pitch_type    TEXT    DEFAULT NULL`

**`game_pitcher_stats`**:
- `singles_allowed INTEGER DEFAULT 0`
- `doubles_allowed INTEGER DEFAULT 0`
- `triples_allowed INTEGER DEFAULT 0`
- `fastball_pct  REAL    DEFAULT 0.0`
- `breaking_pct  REAL    DEFAULT 0.0`
- `offspeed_pct  REAL    DEFAULT 0.0`
- `primary_pitch TEXT    DEFAULT ''`

**`game_batter_stats`**:
- `a             INTEGER DEFAULT 0` — assists

All ALTER TABLE migrations are idempotent and silent on existing-
column errors. SCHEMA blocks for fresh DBs got the same column
definitions. Verified on a fresh `manage.py resetdb`.

---

## Files changed

```
 o27/engine/prob.py               |  +21 lines (form roll widening,
                                              pitch_type on event dict)
 o27/render/render.py             |  +55 lines (assist crediting,
                                              DP-chain pivot, pitch_type
                                              on pa_log, `a` in delta)
 o27/stats/batter.py              |   +1 line  (a: int = 0)
 o27v2/db.py                      |  +82 lines (migrations + SCHEMA)
 o27v2/league.py                  | +162 lines (repertoire builder +
                                              variance rolls)
 o27v2/sim.py                     | +158 lines (PitchEntry loading,
                                              pitch-mix decorator,
                                              hot factor)
 o27v2/web/app.py                 | +127 lines (xRA v3, assists/RF
                                              queries, new selects)
 o27v2/web/templates/leaders.html |  +18 lines (Arsenal mix + A + RF)
 o27v2/web/templates/player.html  |  +42 lines (Arsenal panel + A col)
 o27v2/youth.py                   |   +5 lines (carry rep + variance
                                              fields on graduation)
```

---

## Honest gaps / what's still open

1. **Per-pitch resolution.** Currently sampling one pitch per PA. A
   future iteration could sample per-pitch within the PA, but the
   marginal value vs implementation cost is small and the per-PA path
   is enough to differentiate arsenals.

2. **DP chain attribution is approximate.** Pivot fielder is picked by
   position priority (2B → SS → 3B → 1B), not by ground-side spray.
   Real 6-4-3 attribution needs a spray angle on the BIP outcome.

3. **Manager AI doesn't read pitch types.** `pick_new_pitcher()` still
   scores on Stuff + Stamina only — no "bring in a slider arm vs a
   high-Power lineup" matchup logic. The repertoire is there; the
   manager doesn't consult it yet.

4. **Variance dials are tunable, not calibrated.** The `[0.02, 0.14]`
   `pitch_variance` band and the `σ=0.10` `today_hot_factor` were
   picked by feel. They produce the right shape; whether the σ ≈ 7
   total-R/G is the right target is a design decision.

5. **`baseball_game_simulator` Monte Carlo / Luck Ledger / per-player
   contribution charts** — user shared screenshots mid-session. Deferred
   to a separate ticket.

6. **Batted-ball physics (EV / LA / spray) — hybrid layer.** Deferred
   to backlog. The cheap path is NOT to rewrite contact resolution. It
   is: keep the existing categorical model (`weak`/`medium`/`hard` →
   `hit_type`) as the canonical engine output, and on top of it sample
   a synthetic (exit_velocity, launch_angle, spray_angle) per BIP from
   the bucket + pitch_type + power/movement inputs. Persist on
   `game_pa_log`. That unlocks spray charts, EV/LA-banded Luck Ledger,
   and xwOBA-style per-player attribution without touching the engine
   math the run env is calibrated against. Full physics rewrite (where
   EV/LA *drive* the fielding outcome) would be phase-scale, would
   invalidate the existing run-env / Stay / defense calibrations, and
   would require building an O27-specific probability surface (MLB
   Statcast surfaces are wrong for the 12-batter / 27-out / sidearm
   structure). User wants to tackle this after more low-hanging
   improvements.

7. **HANDOFF.md is still stale** (multiple bugs marked open are fixed).
   Not touched this session.

---

## Process notes

- The plan-mode plan survived basically intact except where
  exploration showed the work was already done (`should_defensive_sub`,
  most of `_select_pitch`). Both were correctly identified during the
  exploration phase as already-wired, and the plan adjusted before
  implementation.
- The `_stat_delta` whitelist bug (assists getting wiped between
  phases) was caught only because I traced `_credit_fielder` after
  seeing A=0 in the verification DB. Lesson: when adding a new field
  to `BatterStats`, also update `_stat_delta` AND `_has_activity` if
  the new field can be the only activity a player has.
- SCHEMA vs ALTER ordering: fresh DBs use `executescript(SCHEMA)`
  AFTER the migrations, so new columns must be added to BOTH the
  migration path AND the SCHEMA string. Initially missed this for
  `pitch_type` on `game_pa_log` and the pitcher-stats pitch-mix
  columns — caught when the first sim crashed with "no such column."
