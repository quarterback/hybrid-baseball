# After-Action Report — Single-Division Schedule Fix + Snake-Draft Talent Dispersion

**Date completed:** 2026-05-07
**Branch:** `claude/fix-machine-limit-error-KtDSp`

---

## What was asked for

The user tried to create a new league with **14 teams in 1 subleague** (a
Finland setup) and got a 500 error. They mistakenly attributed it to
Fly's "Machines limit" notice in the dashboard chrome — actual cause was
a Python exception buried in the logs.

Then, separately, they sent the standings of a previously-seeded league:

```
NSH Stars        155- 7  .956
WSN Nationals    154- 8  .950
ARI Diamondbacks 155- 7  .956
…
NJC Canaries      26-136 .160
```

> "the win models are such that there's clearly not enough randomness
>  in this sport. teams should not win this much."

> "the talent distribution should not be automatic roster generation by
>  team quality, but rather a 'draft' style model so that players are
>  disbursed more evenly."

> "free agency and having players able to be released and signed would
>  work. rather than having money to worry about, it could just be
>  waivers and making sure that anyone useful that a team doesn't need
>  can go somewhere else. kind of like med school match day."

> "phase 3 is a must, i want everyone to have bad days possible
>  especially influenced by weather or other conditions along with
>  tired and talent."

Three threads, prioritised:

1. Unblock new-league creation (the 500).
2. Phase 1 — replace per-team org-strength multiplier with a snake draft.
3. Phase 2 — weekly Sunday match-day waivers (deferred).
4. Phase 3 — bad-day variance from weather + condition + fatigue (deferred).

---

## What was built

### 1. Schedule generator: handle single-division leagues

Symptom: `RuntimeError: Schedule imbalance after generation for team
IDs: [1..14] (expected 30 each)` from `o27v2/schedule.py:143`.

Trace: `_generate_pairings()` defaults to a 0.46 / 0.54 intra/inter
weight split. With 14 teams in one division, every pair is
intra-division, so `inter_pairs = []`. The generator still allocated
~16 of the 30 games to the inter bucket, the inter loop iterated over
an empty list, and those games silently disappeared. Each team ended
up with ~14 games instead of 30; the imbalance check then raised.

**Fix:** detect empty buckets and route the full target into the bucket
that has pairs:

```python
if not inter_pairs:
    intra_target = games_per_team
    inter_target = 0
elif not intra_pairs:
    intra_target = 0
    inter_target = games_per_team
else:
    intra_target = round(games_per_team * intra_w)
    inter_target = games_per_team - intra_target
```

The mirrored case (divisions of size 1, no intra pairs) also gets
handled even though `build_custom_config` doesn't currently allow it.

**Verification:** smoke-tested 5 configurations
(14×1, 30×6, 12×2, 8×1, 16×4 div counts) — every team gets
exactly `games_per_team` games in all cases.

### 2. Diagnostic — root-cause the 155-7 records

Spawned a sub-agent to read the engine cold and report on the per-PA
math, talent generation, and fatigue. Findings:

**Primary cause:** `o27v2/league.py:939` rolled a single
`org_strength` per team from the same 9-tier ladder players use, then
added `team_shift = org_strength - 50` to **every** attribute roll for
**every** player on the team. ~7% of teams rolled +30 to +45 on
everyone; ~23% rolled -21 to -30 on everyone. With 14 teams the league
reliably contained one Elite-shifted team and several cellar-shifted
teams. **No parity mechanism** existed.

**Secondary amplifier:** per-pitch math in `o27/engine/prob.py` is
linear-additive (no sigmoid), and the noise floor at lines 356-360 and
445-450 was deliberately dropped from 0.01 to 0.001 with a comment
explicitly saying "let transcendent talent actually transcend." Talent
gaps stack across `pitcher_skill + command + movement` vs
`skill + eye + contact + power`, compound across ~150 pitches/game,
and never get tempered by a probability floor.

**Fatigue:** threshold is `24 + stamina*40` BF, so elite-stamina
pitchers (threshold ~64) effectively never fatigue in 27 outs, while
sub-replacement pitchers cross it mid-game. Differential — but
secondary to the talent-distribution issue.

The user agreed Phase 1 (talent dispersion) addresses the dominant
cause and per-PA / floor changes can wait for measurement.

### 3. Snake-draft seeding (Phase 1)

Replaces the per-team `org_strength` multiplier with a flat, team-blind
draft.

**Pipeline:**

1. **Pool generation** — `_generate_draft_pool()` builds position-keyed
   pools at 1.4× the league's roster slots. Each player is rolled from
   the same 9-tier `_TALENT_TIERS` distribution; no team bias enters
   the rolls.

2. **Snake draft** — `_run_snake_draft()` walks each slot type
   (`CF, SS, 2B, 3B, RF, LF, 1B, C, UT, DH, P`) and runs (active +
   reserve) rounds per type. Direction alternates **across the entire
   draft**, not per-slot — so a team picking first in round 1 picks
   last in round 2 regardless of which position the round was for.
   Cumulative draft-position equity stays tight.

3. **Active vs reserve** — within each slot type, the first
   `n_active` picks per team are flagged `is_active=1`; the remainder
   `is_active=0` (reserve depth, promoted on injury). Surplus players
   keep `is_active=0` and become free agents.

4. **Free-agent pool** — players with no team land in `players` with
   `team_id IS NULL`. `players.team_id` is now nullable; the schema
   change is documented in-line. Ready for Phase 2's match-day sweep.

5. **`org_strength` recompute** — `_team_org_strength_from_roster()`
   averages each team's active roster's composite rating (skill +
   contact + power + eye for hitters, pitcher_skill + command +
   movement for pitchers) and writes it back to `teams.org_strength`.
   The persisted value now *reflects* roster talent (so the team-page
   sortable badge still works) instead of *biasing* it.

**Slot definition** (`_DRAFT_SLOTS`, mirrors the old per-team
composition exactly so the post-draft roster shape is identical):

```python
("CF", 1, 0), ("SS", 1, 0), ("2B", 1, 0), ("3B", 1, 0),
("RF", 1, 0), ("LF", 1, 0), ("1B", 1, 0), ("C",  1, 0),
("UT", 4, 8),  # 4 active bench + 8 reserve depth
("DH", 3, 0),
("P", 19, 5),  # 19 active staff + 5 reserve arms
```

**`generate_players()` and `_roll_tier_grade()` keep their old
signatures** — `org_strength` and `team_shift` parameters are no-ops
on the league path but still honoured by non-league callers
(`smoke_test.py`, `batch.py`) so those scripts work unchanged.

---

## Verification numbers

Smoke-tested with the user's exact config: `team_count=14,
leagues_count=1, divisions_per_league=1, games_per_team=30,
rng_seed=42`.

| Check | Before | After |
|---|---|---|
| Schedule generation | RuntimeError | 210 games ✓ |
| Total players generated | 14 × 47 = 658 | 924 (658 rostered + 266 FA) ✓ |
| All 14 teams have exactly 1 of each starter position | yes | yes ✓ |
| `org_strength` spread across teams | 20-95 grade-points (rolled) | 1 grade-point (recomputed mean) ✓ |
| Mean active hitter rating spread (best vs worst team) | wide | 51.3 → 51.8 (0.5pt) ✓ |
| Mean active pitcher rating spread | wide | 52.5 → 52.8 (0.3pt) ✓ |
| 14-team / 30-game season top record | 155-7 (.956) reported | **20-10 (.667)** simulated ✓ |
| Same season bottom record | 7-155 (.043) reported | **8-22 (.267)** simulated ✓ |
| Win spread | ~148 games | **12 games** ✓ |

Scaled to a 162-game season: top ~108 wins / bottom ~43 — inside
realistic MLB territory (104-58 has been reached; 116-46 is the modern
ceiling). Phase 3's per-game variance from weather/fatigue/condition
will add texture *on top of* this floor.

---

## What's still on the table (out of scope for this PR)

- **Phase 2 — Sunday match-day waiver sweep.** Specs locked in: runs
  weekly on Sundays, claims placed immediately as each round resolves,
  up to 5 rounds per sweep, teams can defer picks across rounds (banked
  picks from rounds 1-3 usable in 4-5). Worst-record-first ordering,
  each team auto-claims best available FA at its weakest position.
  No money, no contracts.

- **Phase 3 — bad-day variance.** User wants every player capable of
  having a bad day driven by weather, condition, fatigue, and talent
  floor. The engine already has weather hooks (`tests/test_weather_calibration.py`
  exists) and a per-pitcher fatigue model — Phase 3 is about widening
  the per-game noise envelope so the standings have texture beyond pure
  parity. Likely levers: compress `fatigue_threshold = 24 + stamina*40`
  so even good arms sometimes degrade; introduce a per-game player
  "form" multiplier driven by weather + rest + recent workload.

- **Per-PA math review (deferred).** The diagnostic flagged
  `prob.py:356-360` and `prob.py:445-450` — the deliberately-lowered
  noise floor (0.01 → 0.001) — as the secondary amplifier. With Phase 1
  bringing the league back to realistic spread, restoring the 0.01
  floor may not be needed. **Decision criterion:** if post-Phase-3
  standings still show any team above .700 over a 162-game season, do
  the floor restoration. Otherwise leave the explicit
  "let transcendent talent transcend" comment intact.

- **FA-pool UI surfaces.** Free agents now exist in the `players`
  table with `team_id IS NULL` but nothing in the web UI lists them
  yet. Phase 2 will need at minimum a `/free-agents` page and a
  match-day sweep results page.

- **Playoffs + awards.** User asked about both as part of the same
  conversation. Out of scope for this PR but on the radar — playoffs
  need bracket generation, sim entry, results storage, and standings
  tab; awards need MVP / Cy Young / RoY selection rules and a tab.

---

## Files touched

| File | Changes |
|---|---|
| `o27v2/schedule.py` | `_generate_pairings`: detect empty intra/inter bucket and route the full target into the bucket that has pairs (fixes the 14-team / 1-div RuntimeError). |
| `o27v2/db.py` | `players.team_id` no longer `NOT NULL` — supports the FA pool sitting in the same table without a sentinel team row. Comment in SCHEMA explaining the convention. |
| `o27v2/league.py` | New `_DRAFT_SLOTS`, `_DRAFT_OVERSAMPLE`, `_player_overall`, `_generate_draft_pool`, `_run_snake_draft`, `_team_org_strength_from_roster`. `seed_league` rewritten in three phases: insert teams with placeholder org_strength → generate + draft pool → persist rosters and FAs, recompute org_strength. `_roll_tier_grade` and `generate_players` keep their old signatures but document that `team_shift` / `org_strength` are no-ops on the league path. |

---

## Decision log

- **FA pool storage:** chose `team_id IS NULL` over a "Free Agents"
  sentinel team because it keeps queries that JOIN players to teams
  naturally exclusive of FAs (a sentinel would have shown up in
  league-wide standings / leaderboards by accident).

- **Single 1.4× oversample** (instead of position-by-position tuning):
  uniform multiplier is simpler and the FA pool composition naturally
  reflects the slot-type proportions of the league. Pitchers dominate
  the FA pool (134 of 266) which matches the pitcher-heavy roster
  shape.

- **Snake direction alternates globally, not per slot type:** per-slot
  alternation would let a team that picks first in CF also pick first
  in SS, then 2B, etc. — small per-position edges stack into a
  meaningful total. Global alternation forces draft-equity to balance
  over the whole 55-round draft.

- **`_player_overall` composite for sort order:** uses skill / contact
  / power / eye for hitters and pitcher_skill / command / movement for
  pitchers — same attributes the engine actually rewards per-PA. Using
  raw `skill` alone would have ignored the realism-layer attributes
  the talent-spread AAR added.

- **Don't touch `prob.py` floor yet:** the explicit user comment
  "let transcendent talent transcend" (and the Aug 2025 task that
  lowered the floor) is a deliberate design choice. Phase 1
  parity-fix may be sufficient on its own.
