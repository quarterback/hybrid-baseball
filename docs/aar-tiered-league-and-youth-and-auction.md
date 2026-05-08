# After-Action Report — Tiered league + Youth league + IPL auction

**Date completed:** 2026-05-08
**Branch:** `claude/new-league-structure-tqk8H`
**Commits (in order):**
  - `d6d10ac` Tiered 56-team league config + promotion/relegation engine
  - `10a2b62` UI surface for the tiered league
  - `293153d` O27 Youth League (rosters + season-end aging + UI)
  - `4d187fb` Phase 2: Youth tournament + IPL-style auction

This session built three structural additions to O27 in one branch:

1. **A new league structure** — 4-tier promotion/relegation (Galactic /
   Premier / National / Association), 14 teams per tier, 80 games per
   team. Replaces nothing; sits next to the existing 30-team config.
2. **The O27 Youth League** — 32 national teams of ages 14–19, modelled
   on the U19 cricket/UEFA-youth feel, auto-attached to every save.
   Includes a per-season group-stage + knockout tournament, and a
   season-end aging pass that graduates 20-year-olds into the pro
   free-agent pool.
3. **An IPL-style auction with Vickrey resolution** — runs at season
   rollover for tiered configs, redistributes every non-keeper player
   to the highest valuer.

Everything is wired into the existing season-rollover flow and surfaced
in the UI via dedicated pages.

---

## Part 1 — Tiered 56-team league with promotion/relegation

### Format

Four tiers, top to bottom:

```
O27 Galactic       — 14 teams, top tier
O27 Premier        — 14 teams
O27 National       — 14 teams
O27 Association    — 14 teams, bottom tier
```

Each team plays 80 games per season:
- 52 in-tier games (4 games × 13 in-tier opponents)
- 28 cross-tier games against the paired tier (2 games × 14 opponents)

**Cross-tier pairing.** The user's first proposal was "play every
adjacent tier" (i.e. middle tiers play both above and below). That
arrangement is mathematically inconsistent with 80 games for everyone:
if Galactic plays each Premier team 2x, then Premier plays each
Galactic team 2x — leaving zero schedule space for the Premier–National
edge. The only configuration that lands at exactly 80 games for all
56 teams with symmetric per-pair counts is **paired tiers**:

- **Galactic ↔ Premier** play
- **National ↔ Association** play
- **Premier ↔ National** do not play

The promotion/relegation cascade still runs through every adjacent
boundary regardless of who plays whom — schedule pairing is purely
about generating 80 balanced games per season.

### Promotion / relegation rules

At season end, per tier-edge:

- **Top 2** of the lower tier auto-promote.
- **Bottom 1** of the upper tier auto-relegates.
- **Seeds 11/12/13** of the upper tier enter a relegation playoff:
  11 plays 12, the loser of that match plays 13, and the loser of
  the second match also relegates.

That puts 2 teams up and 2 teams down per edge — a balanced exchange.
End tiers (Galactic, Association) only see one direction each.

The relegation playoff is decided by a win-pct-weighted Bernoulli
draw with a 0.4–0.6 floor/ceiling so a worse team has live odds —
NOT a real game sim. That's deliberate: the playoff happens at
off-season time when the engine isn't running, and the user explicitly
said the auction part isn't important right now (so the playoff
infrastructure shouldn't be either). The hook in `_decide_playoff_match`
is one function — swap it for a real game sim later if desired.

### Files / where the work lives

| File | What it does |
|------|--------------|
| `o27v2/data/league_configs/56teams_tiered.json` | New config. `schedule_mode: tiered`, four tier names in `tier_order`, `tier_schedule_pairs` lists the only two cross-tier edges that play, `promotion_relegation` block carries the cascade rules. Untouched: every other config under `data/league_configs/`. |
| `o27v2/schedule.py` | New `_generate_tiered_pairings()` builds the directed game list directly from `tier_order` + `tier_schedule_pairs`. Routed in `generate_schedule()` via a `schedule_mode == "tiered"` check. The MLB-shape `_generate_pairings()` is unchanged — every other config still flows through it. |
| `o27v2/league.py` | New `_assign_tiered_divisions()` deals teams into tiers via random shuffle (talent-blind per the demo cut), 14 per tier. `seed_league()` routes through it when the config opts in. |
| `o27v2/promotion.py` | New module. `apply_promotion_relegation(config, *, rng_seed, dry_run)` reads live standings, computes the move list, optionally writes to `teams.league` + `teams.division`. Returns a structured report (moves + per-tier playoff log) for the UI. |

### UI surface

| Surface | Behaviour |
|---------|-----------|
| `/new-league` preset card | Tiered configs get a "Tiered · P/R" badge. Subtitle switches from "X divisions / Y teams-per-div" to "X tiers / Y teams-per-tier". Tier names render as little chips below the card. |
| `/standings` | When the active config is tiered, tiers render top → bottom. Each row gets a seed number + a coloured gutter (green = promotion zone, yellow = relegation playoff, red = auto-relegation). A Status column makes the zone explicit. Top tier shows no promotion zone; bottom tier shows no relegation. |
| `/standings` action buttons | "Preview P/R" runs a dry-run; "Run Promotion / Relegation" applies it and reloads. Both call `POST /api/season/promote-relegate`. |
| `POST /api/season/promote-relegate` | New endpoint. Returns the moves + playoff log. Refuses on non-tiered configs. |
| `/api/season/advance` | Now also calls `apply_promotion_relegation` between the off-season development pass and the wins/losses wipe (gated on tiered config). |

### What got verified

End-to-end:
- 2240 games scheduled for 56 teams; every team at exactly 80.
- 364 in-tier games per tier, 392 cross-tier games on each scheduled
  edge, 0 on the unscheduled P–N edge.
- 12 moves on every dry-run (2 per tier-edge × 3 edges × 2 sides).
- After apply, every tier still has exactly 14 teams.
- The dry-run does not mutate `teams.league`. The apply writes are
  observable in the DB immediately.
- 30teams config is unchanged — the new code paths are only entered
  when `schedule_mode == "tiered"`.

### Trade-offs accepted

- **Talent-blind initial tier draw.** The user opted out of stratified
  talent at seed time, so the inaugural Galactic isn't actually
  better than Association on day 1. Promotion/relegation will
  produce competitive separation over multiple seasons.
- **Playoff is a Bernoulli draw, not simulated games.** Cheap to ship,
  swap-able later.
- **No "rivals" mechanic.** The user mentioned rivalries in the
  abstract but didn't ask for them — the schedule doesn't designate
  any specific cross-tier teams as recurring rivals.

---

## Part 2 — O27 Youth League

### Format

Thirty-two national teams (a deliberately broader field than the WBC,
mixing baseball powers and cricket nations à la ICC U19):

```
USA, Canada, Mexico, Dominican Republic, Puerto Rico, Cuba,
Venezuela, Colombia, Brazil, Argentina, Suriname, Guyana,
Jamaica, Trinidad & Tobago, United Kingdom, Ireland, Netherlands,
Italy, Czech Republic, Finland, South Africa, Zimbabwe, India,
Pakistan, Malaysia, Philippines, Japan, South Korea, Taiwan,
Australia, New Zealand, Fiji
```

Each carries 12 players ages 14–19 (8 hitters at canonical positions
+ 4 pitchers). 384 youth players league-wide. Names are drawn
region-biased per team (Japan rosters get east-Asian names, etc.) via
the existing `make_name_picker` from `league.py`.

### Auto-attached

`seed_league()` calls `youth.seed_youth_league()` after pro seeding.
Default-on across every existing config; opt-out is one config flag
(`attach_youth_league: false`). Older saves that pre-date this can
attach the league via `POST /api/youth/seed` from the empty-state
button on `/youth`.

### Year-end aging + graduation

`youth.advance_youth_year()` runs every season rollover:

1. **Develop**: each player gets `o27v2.development._develop_player`
   applied to them with `org_strength=50` (neutral). The 14–19 band
   sits entirely in `_mu_age = 2.5` territory, the strongest growth
   tier — exactly the prospect-development arc the feature exists to
   produce.
2. **Age +1**.
3. **Graduate**: any player who would now turn 20 is inserted into the
   pro `players` table with `team_id NULL, is_active 0` (i.e. unsigned
   free agent), then deleted from `youth_players`. This puts the new
   pro-eligible cohort into the existing FA pool — no new pickup
   logic required; the existing waiver / signing flow finds them.
4. **Refill**: new 14-year-olds backfill missing positions back to
   `ROSTER_SIZE = 12`.

In testing, a fresh league produced 66 graduations after one rollover
(roughly 17% turnover, as expected for a 6-year age band at the top).

### Tournament (Phase 2)

Per season, after the pro season ends and before aging runs:

```
Group stage
  8 groups of 4 (random draw — no pots / seeding)
  Round-robin within group → 3 games per team → 48 games

Knockout (single elim)
  Top 2 per group → R16 (8 games)
  → QF (4) → SF (2) → Final (1)

Total: 63 games per tournament
```

Game simulation is **heuristic, not the full O27 PA-by-PA engine**.
Each team's overall composite (averaged across the roster, hitters
weighted by `(skill+contact+power+eye)/4`, pitchers by
`(stuff+command+movement)/3`) feeds a logistic win-prob; the score is
drawn around an expected total scaled to ratings. Sudden-death
extra-run breaks ties.

This is intentional. The youth tournament is a watching-flavour layer,
not a deep-stat competition. Mirroring `game_batter_stats` /
`game_pitcher_stats` for youth would have at least doubled the schema
surface for marginal narrative gain. The development engine — which
**is** the substance of the youth feature — does not depend on per-game
youth stats.

The bracket builder uses standard A1-vs-B2 / B1-vs-A2 alternating
pairings to avoid first-round group-mate rematches, then propagates
winners through QF/SF/Final via `_advance_knockout_round`.

### Files

| File | What it does |
|------|--------------|
| `o27v2/youth.py` | Single module: schema (`youth_teams`, `youth_players`, `youth_groups`, `youth_group_membership`, `youth_games`), seeder, `advance_youth_year`, `run_youth_tournament`, `reset_youth_tournament`, `get_tournament`, plus read helpers `youth_teams()` / `youth_roster()` / `top_prospects()`. |
| `o27v2/league.py` | `seed_league()` now auto-attaches youth (default-on, gated by `attach_youth_league` config flag). |
| `o27v2/web/app.py` | Routes: `/youth`, `/youth/team/<id>`, `/youth/tournament`, `POST /api/youth/seed`, `POST /api/youth/tournament/run`. Season-advance now runs the tournament BEFORE aging so each tournament happens with that season's rosters, then graduates / refills. |
| `o27v2/web/templates/youth.html` | League index: 32 nations sortable by avg age, bat grade, arm grade. Top-25 prospects table, archetype-switchable (overall / bat / arm / speed). |
| `o27v2/web/templates/youth_team.html` | Per-team roster: full attribute grid. Age-19 cells highlight green to flag the upcoming graduates. |
| `o27v2/web/templates/youth_tournament.html` | 8 group cards (top 2 of each highlighted) + every knockout round + champion banner. |

### What got verified

- 32 teams seeded, 384 youth players total, age range 14..19 across
  the league.
- One rollover produced 66 graduations → 66 new pro-side FAs at age
  20 → 66 new 14-year-olds backfilled. Roster size restored to 384.
- 63 games per tournament: 48 group + 15 knockout. R16 contains
  exactly 16 distinct teams (top 2 of each group). One champion.
- `/youth`, `/youth/team/<id>`, `/youth/tournament` all render.
- 30teams (non-tiered) save still gets the youth league; auction
  surface correctly reports tiered-only.

### Trade-offs accepted

- **No per-PA youth stats.** Heuristic game results only.
- **Random group draw, no pots.** Real ICC U19 uses pot seeding. For
  v1 the random draw is fine — every season produces a different
  bracket shape, and a pot system is a small follow-up.
- **No carry-over performance feedback.** A youth player who tears up
  the tournament does not get a development bonus. The aging draw is
  identical for everyone. This was the simplest defensible cut for
  the demo.

---

## Part 3 — IPL-style auction with Vickrey resolution

### Format

For tiered configs only. Runs at season rollover, **after**
promotion/relegation (so the auction pool is sized against the final
tier slots).

Pipeline:

1. **Keepers**: each team retains its top-N players by overall (config
   knob, default 3). 56 × 3 = 168 keepers retained on a full tiered
   league. Reserves are eligible to be kept — a high-grade reserve
   beats a middling active.
2. **Pool**: every non-keeper non-FA player is cut from their team
   (`team_id = NULL`, `is_active = 0`) and joins the auction pool.
   The pool is sorted by overall desc with small jitter — best
   players go to auction first.
3. **Per-player Vickrey draw**: every team computes a private
   valuation. Highest valuer wins, pays max(second-highest + 1,
   min_bid). Bid ties broken by team id desc.
4. **Unsold**: when no team will pay min_bid (because purses are
   spent or rosters are full), the player stays a free agent. In
   testing this is a long tail of ~700 replacement-level players —
   the IPL "released to auction, nobody bid" outcome.
5. **Re-rostering**: each team's purchased players are sorted by
   overall and the first `(34 - n_keepers)` get `is_active = 1`; the
   rest land in reserves.

### Bid valuation

```
base_value      = overall × 5            ($250–400 for elite grades)
need_multiplier = 1.0 + 0.15 × position_need (capped at 1.5)
noise           = uniform(0.85, 1.15)
max_bid         = floor(base × need_mult × noise)
purse_cap       = max(10, purse_remaining - 10 × (slots_left - 1))
final_bid       = min(max_bid, purse_cap)
```

`position_need` counts how many slots a team still needs at the
player's position (target = 4 for high-rotation positions CF/SS/2B/C,
2 elsewhere; target = 24 for pitchers). A team with full pitching
won't outbid a team with arm needs. The purse cap reserves $10 per
remaining slot so a team can't buy a single $1000 superstar and
brick the rest of its roster.

### Why Vickrey

Two reasons:

1. **No need to model bidding rounds.** A single private bid per
   player resolves cleanly to a winner.
2. **Honest "second-best willing to pay X" log entries.** When the
   UI shows `Juan Trudel → OMA for $594 (bid $599, 2nd $593)`, the
   second-bid number is meaningful — it's the price the next-best
   suitor was actually willing to pay. With a winner-pays-bid model,
   the bid itself is gameable; with Vickrey it isn't, so the numbers
   are interpretable as actual valuations.

### Files

| File | What it does |
|------|--------------|
| `o27v2/auction.py` | Whole module. Schema (`auction_keepers`, `auction_results`), `_select_keepers`, `_team_bid`, `apply_auction`, `get_auction`. ~370 lines. |
| `o27v2/data/league_configs/56teams_tiered.json` | New `auction` block: `enabled: true`, `keepers_per_team: 3`, `team_purse: 1000`, `min_bid: 10`. |
| `o27v2/web/app.py` | Routes: `/auction`, `POST /api/auction/run`. Season-advance hooks the auction in immediately after promotion/relegation. |
| `o27v2/web/templates/auction.html` | Top-30 sales table, summary bar (keepers / sold / unsold / total), keepers-by-team grid. Empty-state messaging when no auction has run, or non-tiered-config messaging on the wrong save. |
| `o27v2/web/templates/base.html` | Auction nav link added before Youth. |

### What got verified

End-to-end on the 56-team tiered config:
- 168 keepers retained (56 × 3) — matches config exactly.
- 2464-player pool → 1736 sold, 728 unsold.
- Top sales hit ~$594 for grade-70 talent (Juan Trudel, P, Omaha) →
  expected based on the formula: 70 × 5 × ~1.5 noise × position_need
  = ~$525 + auction-up.
- Vickrey gap visible in the log (`bid $599, 2nd $593` → price $594).
- `/auction` renders all sections; non-tiered config shows the
  "tiered-only" message instead of erroring; `30teams` save unaffected.

### Trade-offs accepted

- **No human bidding.** This is a sim, not a UI auction room. AI
  valuations resolve everything.
- **One bid per player per team.** No multi-round escalation. The
  noise term is what produces variance between teams, not back-and-forth
  bidding.
- **Keepers chosen by raw overall.** No "they had a great year" bonus,
  no contract-status-based decisions, no "this kid is too young to
  protect" reasoning. The ranked top-N is plenty for the demo.
- **Long unsold tail.** ~30% of the pool goes unsold. That's a
  feature, not a bug — it produces an actual FA pool of replacement
  players for the league's signing/waivers loop to work against.

---

## Architectural decisions that propagated through everything

### Separate tables for the youth league

The choice of separate `youth_teams` / `youth_players` / `youth_*`
tables — instead of `is_youth` flags on the existing `teams` /
`players` / `games` tables — was the load-bearing call for the entire
youth feature. Reasons it paid off:

- The Flask app has 50+ queries against `teams` / `players` / `games`.
  Adding a flag would have required threading `WHERE is_youth = 0`
  through every leaderboard, schedule view, free-agent page, etc.
  Easy to miss one and end up with youth players polluting pro-side
  outputs.
- Schema differences are real. Youth players don't have
  `injured_until`, `il_tier`, `archetype`, `pitcher_role`,
  `stay_aggressiveness`. Forcing the same row shape would have meant
  carrying nullable columns everywhere or making youth rows lie.
- Graduation is a clean schema-to-schema insert (`youth_players` → 
  `players` with `team_id NULL`), preserving exactly the attributes
  the dev engine uses.

The cost is real: every read endpoint that wants to show youth data
has to query both tables. That's been kept localised — `youth.py`
owns all the youth reads, the rest of the app sees no churn.

### The tiered config is a new config, not a fork of the existing schedule logic

Adding a new `schedule_mode` discriminator (rather than parameterising
the existing MLB-shape generator) keeps the generator clean. The MLB
path is unchanged, the tiered path is a sibling function, and the
config carries enough metadata (`tier_order`, `tier_schedule_pairs`)
that the schedule shape is fully declarative.

This made the UI work easy too: the standings template branches on a
single `is_tiered` flag from the route, and falls through to the
existing layout otherwise. No risk of regressing existing standings.

### Auction runs on the *teams* schema, not a separate "auction roster" schema

Players move between teams via `team_id` updates and `is_active` flag
flips, exactly as the existing trade / waiver code does. No
intermediate "in transit" state. Means the auction's effects are
visible immediately on the team page, the FA page, and every player
search — no follow-up sync is needed.

---

## Phase 4 — Youth roster overhaul: jokers, YPI governor, recruiting stars, hidden ratings

### Why this happened

The Phase 3 youth-sim commit shipped with `jokers_available=[]` and
`mgr_joker_aggression=0.0`. Reviewer correctly pointed out that this
isn't O27. Jokers are structural — `README.md:19` lists them as a
load-bearing rule of the sport, and `README.md:68` describes joker
deployment as the reason intentional walks are rare in O27. A youth
tournament with no jokers is a different game.

So Phase 4 fixes that, and while we were re-shaping the youth roster
anyway, also addresses two other regressions / requests the user
flagged:

1. Roster was 12 (8 hitters + 4 pitchers) — too thin. No backups, no
   real bullpen, no jokers. Bumped to **28 per team**: 8 starters +
   8 backups + 9 pitchers + 3 jokers.
2. The numerical attribute grid (skill=72, contact=68, …) was being
   surfaced directly in the UI. Per the user's design ask: youth
   ratings should work like US college recruiting stars (★ to ★★★★★)
   and the underlying numbers should stay hidden. Stats are the only
   in-period signal you get on a kid; stars are a sticky one-time
   projection from age-14 evaluation.
3. Even-talent-everywhere produced too-predictable youth tournaments.
   Solved with the **Youth Potential Index** governor.

### The Youth Potential Index (YPI)

Per-player access factor in `[0.22, 0.81]`, rolled once at creation
and stored on the `youth_players` row. Stored attribute grades are
the player's **TRUE potential**; the engine multiplies each unit-
space rating by YPI before resolving plate appearances. Effect:

```
true potential          → engine sees → produces stats like
─────────────────────────────────────────────────────────────
80 grade × 0.81 YPI     →   65 unit  → looks like a 4★ in box scores
80 grade × 0.30 YPI     →   24 unit  → looks like a 1★ in box scores
40 grade × 0.79 YPI     →   32 unit  → looks like a 2★
40 grade × 0.22 YPI     →    9 unit  → looks borderline-replacement
```

YPI never persists into the pro `players` table. When a 19-year-old
graduates at season rollover, `_graduate_to_pro_fa` inserts their
TRUE attribute grades into the pro pool — the governor lifts. So the
flameout / hidden-gem narrative emerges naturally:

- 5★ recruit, true 80 attrs, YPI 0.30 → posted .150 in tournaments
  → enters pro pool with full 80-attr grades → reveals himself as a
  star. **The hidden gem.**
- 1★ recruit, true 35 attrs, YPI 0.79 → posted .380 in tournaments,
  user thought "wow, this kid's special" → enters pro pool with
  actual 35 grades → washes out. **The phenom who didn't pan out.**

### Recruiting stars (★ to ★★★★★)

Public-facing rating. Computed once at age 14 from the composite of
TRUE attributes (hitters: skill+contact+power+eye / 4; pitchers:
pitcher_skill+command+movement+stamina / 4) and stored on the row.
**Sticky** — does NOT update as the player develops. So a kid rated
3★ at 14 keeps that label even if his attributes climb to 4★-grade
levels by 19. Empirical distribution at the calibrated thresholds:

```
5★ : 0.7%  — true elite (composite ≥ 68)
4★ : 9%    — solid blue-chip (composite ≥ 58)
3★ : 30%   — most starting-caliber kids (composite ≥ 48)
2★ : 47%   — back-end depth (composite ≥ 36)
1★ : 13%   — walk-on tier
```

Stars don't move with development AND aren't the same signal as
stats — that's the whole design point.

### What the user sees

- **Stars** (sticky, true-potential-derived): 1-5 stars on every
  player.
- **Stats**: PA / AB / H / HR / RBI / BB / K / AVG for hitters,
  G / GS / OUT / K / BB / H / R / ER for pitchers. Aggregated from
  `game_youth_batter_stats` / `game_youth_pitcher_stats` over the
  season's tournament.
- **Names, ages, country, position, B/T**.

What the user does NOT see:
- Numerical attribute grades (skill, contact, power, eye, etc.).
- The Youth Potential Index access factor.

The AVG ladder by stars in the calibration sample tournament:

```
5★ .333   — small sample, masked by YPI variance
4★ .340   — solid; flips the script vs 5★ on individual kids
3★ .303
2★ .273
1★ .228
```

The closeness of 5★/4★ and the wide gap to 1★/2★ is the YPI doing
exactly what it should — diffusion at the top, more deterministic at
the bottom.

### Roster shape

```
8 starting fielders (CF, SS, 2B, 3B, RF, LF, 1B, C)
8 position-player backups (one per starter slot)
9 pitchers              (3 rotation + 6 bullpen, youth scale)
3 jokers                (one of each archetype: power, speed, contact)
─────
28 per team × 32 nations = 896 league-wide
```

Empirically every team passed the shape audit post-rollover (28
total, 9 pitchers, 3 jokers per team, all three joker archetypes
present).

### Joker integration

`youth_sim._build_youth_engine_team` now:
- Walks youth_players rows; flags rows with `is_joker=1` separately
  and builds them as engine `Player` objects with `archetype` set
  from `joker_archetype`.
- Passes the 3 joker Players via `Team.jokers_available`.
- Sets `mgr_joker_aggression=0.5` (league mean) so the manager AI
  actually deploys them.

In the calibration sim, jokers produced 1262 PAs across the 63-game
tournament — confirming the manager AI is using them naturally.

### Schema additions

```sql
ALTER TABLE youth_players ADD COLUMN is_joker INTEGER DEFAULT 0;
ALTER TABLE youth_players ADD COLUMN joker_archetype TEXT DEFAULT '';
ALTER TABLE youth_players ADD COLUMN youth_potential_index REAL DEFAULT 1.0;
ALTER TABLE youth_players ADD COLUMN recruit_stars INTEGER DEFAULT 3;
```

Older saves get these via `init_youth_schema()`'s ALTER TABLE
migration block. New seeds populate them at creation. The default
YPI of 1.0 on legacy rows means "no governor" — older youth players
play at full ratings until they're regenerated.

### Files touched

| File | What changed |
|------|--------------|
| `o27v2/youth.py` | Roster shape constants, schema migrations, `_make_youth_player` rolls YPI + stars + joker archetype, `_spawn_roster` and `_refill_team` rebuilt for 28-player shape with role buckets, `top_prospects` switched to observed-stats sort, new `player_observed_stats()` aggregator, graduate-cascade child cleanup. |
| `o27v2/youth_sim.py` | `_make_engine_player` applies YPI to every unit-space rating, `_build_youth_engine_team` populates `jokers_available` and sets `mgr_joker_aggression=0.5`. |
| `o27v2/web/templates/youth.html` | Removed all numerical attribute columns. New star-macro renders 1-5★. New filter buttons: Hitters / Pitchers / By stars. Hitter view shows AVG; pitcher view shows K/BB/ER per appearances. |
| `o27v2/web/templates/youth_team.html` | Three sections: Position players + Jokers + Pitchers. All show stars + observed stats. No attribute reveal. Footer note explains the design intent. |
| `o27v2/web/app.py` | `/youth/team/<id>` now also pulls `player_observed_stats` per player; archetype-options switched to `bat / arm / stars`. |

### Known regression

Any youth player that already exists in a save dating from before
this commit has `youth_potential_index = 1.0` (the column default).
That means their existing tournament stats reflect "full potential"
rather than YPI-muted potential. If the user wants the diffusion to
apply retroactively, the cleanest path is a re-roll: reset the
tournament, re-roll YPI for surviving players, re-run. Players
created from this commit forward get YPI rolled at creation.

---

## Phase 3 — Real game sim for the youth tournament (post-AAR addition)

After the original AAR shipped, the heuristic per-game youth result
got replaced with the actual O27 PA-by-PA engine. Files:

| File | What changed |
|------|--------------|
| `o27v2/youth_sim.py` | New module. `simulate_youth_game(game_id)` builds two engine `Team`s from `youth_players`, calls `o27.engine.run_game`, persists score + winner + per-player stats. `_pick_youth_starter()` rotates SP across the tournament by start count desc, then `pitcher_skill` desc. |
| `o27v2/youth_sim.py` schemas | New `game_youth_batter_stats` + `game_youth_pitcher_stats` tables — slim shape (PA/AB/R/H/2B/3B/HR/RBI/BB/K/STY/OUT for batters; BF/OUT/H/R/ER/BB/K/HR/PITCHES for pitchers). No per-PA log; no super-inning phase splitting; no entry-type tracking. Enough for a recognisable box score. |
| `o27v2/youth.py` | `_simulate_unplayed_games` now calls `youth_sim.simulate_youth_game` instead of the heuristic. The heuristic is kept as a per-game fallback wrapped in try/except so a single engine bug doesn't brick the whole tournament. `reset_youth_tournament` now clears child stat rows before deleting `youth_games`. |
| `o27v2/web/app.py` | New route `/youth/game/<id>` with `get_box_score()` helper. |
| `o27v2/web/templates/youth_box_score.html` | Two batting tables + two pitching tables, side-headed by team, score banner up top with the winning side highlighted. |
| `o27v2/web/templates/youth_tournament.html` | Each played-game row in every knockout round now has a "Box →" link to the box score. |

### Roster shape adaptation

The pro engine assumes a 12-batter lineup (8 fielders + SP + 3 DH).
Youth rosters are 12 players (8 hitters + 4 pitchers) — no DHs. Two
options were on the table:

1. **Pad the youth lineup with three pitchers as DH-equivalents.**
   Bizarre — a pitcher batting four times in a youth tournament when
   he isn't on the mound makes no sense.
2. **Use a 9-batter lineup matching the original O27 README rules
   (8 fielders + SP, all 9 fielders bat).** The engine's `Team.lineup`
   is `len(self.lineup)`-driven everywhere — no hard-coded 12. This
   was the path taken.

Side effect: youth games naturally produce slightly fewer PAs per
27-out half than pro games (lineups cycle 9-deep instead of 12-deep),
which suppresses runs slightly. Final-game scoring of 17–2 in test
sims with avg 24.8 R/G is consistent with the pro league's documented
22–26 R/G/T target.

### Manager AI on a roster that has no archetype data

Youth teams have no `manager_archetype`, no `mgr_*` tendency dials —
those are pro-team columns. `_build_youth_engine_team` fills them
with the league-mean defaults (0.5 across the board, archetype="").
The manager AI's pitcher-pull logic still runs; it just runs with
neutral-everything tunings. Joker-related decisions short-circuit
because `jokers_available=[]` and `mgr_joker_aggression=0.0`.

### SP rotation

Each youth team has 4 pitchers and plays 3 group games + up to 4
knockout games = 7 max. The rotation:

```
For team T:
  pitchers = SELECT all is_pitcher=1 from youth_players WHERE team_id = T
  starts_so_far = COUNT starts per pitcher in this season's tournament
  starter = pitchers ORDER BY starts_so_far ASC, pitcher_skill DESC, id ASC
```

This guarantees no pitcher starts a second game until every arm has
started once (over the tournament's full bracket run). Knockout
relievers are picked live by the existing `pick_new_pitcher()` —
manager AI sees the 4-arm pool, scores by Stamina, and pulls the SP
when fatigue triggers fire just like in the pro sim.

### Performance

A full 63-game tournament runs in **~16.5 seconds** (~0.26 s/game) on
the reference dev machine. Acceptable inline during a `/api/season/advance`
call; well below any UI timeout.

### What's still not done

- **Geographic / pot-based group draw for the youth tournament.**
  Random draw only.
- **Performance-based youth development bonuses.** A player who
  carries a tournament gets the same dev draw as the bench. Good hook
  for a follow-up: feed tournament line into a `_grit_modulator`-style
  multiplier on the dev draw.
- **Auction visualisation as a live event.** Currently the report is
  a static page after the auction runs. An animated "lot 1, lot 2,
  lot 3" bid-by-bid replay would be a fun UI follow-up — the data is
  all in `auction_results`.
- **Dynamic keeper rules per tier.** A real IPL has different rules
  per franchise / season; we treat all teams identically. Could be a
  config knob.

---

## Files added / modified

```
NEW:
  o27v2/promotion.py
  o27v2/youth.py
  o27v2/auction.py
  o27v2/data/league_configs/56teams_tiered.json
  o27v2/web/templates/auction.html
  o27v2/web/templates/youth.html
  o27v2/web/templates/youth_team.html
  o27v2/web/templates/youth_tournament.html
  docs/aar-tiered-league-and-youth-and-auction.md   (this file)

MODIFIED:
  o27v2/league.py
  o27v2/schedule.py
  o27v2/web/app.py
  o27v2/web/templates/base.html
  o27v2/web/templates/new_league.html
  o27v2/web/templates/standings.html
```

No existing config was touched. No existing route was removed or
re-shaped. Every code path that pre-dated this branch flows through
unchanged when the active config is non-tiered, and the youth league
is fully separate-schema.
