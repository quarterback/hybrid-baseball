# After-Action Report — College Tier (Sub-Pro NCAA-Inspired League)

**Date completed:** 2026-06-01
**Branch:** `claude/name-sets-cities-expansion-OxkeB`
**Commits (oldest → newest), all on this branch:**

- `832f418` — Add college potential-growth model: interest rate × annual cap
- `c9a90f1` — Hook college tier into the engine: full data model + game sim
- `2f7eb29` — Add college league: schema, 64 programs, schedule, sim, postseason, rollover
- `a748c74` — Add college tier web UI: standings, program / player pages, postseason
- `dfcbe1a` — Expand college catalog to real conferences (195 programs across 18)
- `477008f` — Wire remaining college-tier pieces: per-game stats, leaders, box scores, draft board, sign-to-pro UI, college-stats on the pro player card
- `5cffe43` — Add team-stats panel + conference filter on college leaders

---

## What was asked for

Started from a conversation about NCAA softball during the WCWS. User
wanted to be able to simulate a sub-pro league in the game world — a
collegiate tier with its own schedule, postseason, and draft pipeline
that feeds the pro side. Big animating idea: an asymmetric reveal
mechanic where college players play with **suppressed grades**, scouts
get **noisy reports** of their true potential, and the player's actual
ceiling is only revealed once they sign to the pro side.

Direct quotes that drove the design:

> "the way we've modeled youth stats in my other games is that these
> players actually already have their full potential listed... what we
> have in the college or youth game is basically they get to access a
> percentage of it, but we never see their full current ratings. elite
> players can still be elite, hidden gems might be elite but haven't
> developed enough to show it. others are going to peak at 50 but in
> this world 50/80 might be elite. it creates nice asymmetry,
> especially with regard to exporting college players into a pro
> league."

> "pro side never sees full potential, they only get current grades,
> current stats and an end-of-season 'scouting service report' which
> gives you grades on the entire class, but the issue is...every team's
> report is blurred by anywhere between +/- 7 to 31, meaning you can
> swing big and miss."

> "there's the single shared report and then there's your own scouting
> department. each rolls differently and your job is to figure out who
> is telling the truth"

> "i don't want to deal with aluminum bats at all, but smaller fields
> yes."

> "real baseball conferences with real teams unless there's some reason
> not to do that"

> "if there are more softball schools than baseball schools, use them
> too"

Plus a hand-authored growth-math spec (see Section 4 below) that the
user dropped in mid-design and asked me to implement verbatim.

---

## Starting state

- The codebase had a working pro engine (`o27.engine.run_game`), a
  youth tier (`o27v2/youth.py` + `youth_sim.py`) using a YPI governor
  for talent suppression, and a Pro World Cup pattern
  (`o27v2/pro_worldcup.py`) for running player-driven games through
  the engine outside the main league.
- No sub-pro / collegiate tier existed. The youth side used a
  separate sim engine variant; nothing in the code stack played
  through the *full* pro engine at a lower talent level.
- No mechanism for hidden potential separate from displayed grades.
  Players in both youth and pro had one set of attributes the engine
  read directly.
- No scouting-report machinery (the pro side has scouting grades but
  not in the "blurred multiple sources" sense the user designed for
  college).
- Standings / leaders / box-score templates existed for the pro
  league but were specific to it.

---

## Locked design decisions (all from user-confirmed choices)

These got resolved interactively before any code shipped:

1. **Potential vs access split, per attribute.**
   Every college player carries a hidden `potential_X` (their true
   ceiling, 20-80) and a hidden `access_X` (Uniform(0.40, 0.95) lens
   drawn at generation). Displayed grade = `round(potential × access)`,
   clamped [20, 80]. The engine plays the *displayed* grade — the
   lens is what makes the box-score line.

2. **Access is static** across the player's college career. Hidden
   gems with low access *stay hidden* all 4 years. Only on pro
   signing does the lens drop.

3. **Potential climbs every year** via the interest-rate model
   (Section 4). Some kids grow fast, most are ordinary, a few are
   rare super-bloomers.

4. **Interest rate per player, drawn once** at generation. Static
   for the whole college career. Three tiers, weighted across the
   class:
   - 75% draw `Uniform(0, 100)` — ordinary developer
   - 20% draw `Uniform(101, 200)` — late bloomer
   - 5% draw `Uniform(201, 320)` — rare super-bloomer headliner

5. **Per-player fog magnitude**, drawn `Uniform(7, 31)` at
   generation, static for college career. Applies uniformly to every
   attribute (per-player, not per-attribute). A player is a "mystery
   box" across the board or a "well-known commodity" across the
   board — not split per skill.

6. **Two independent scouting reports** per draft-eligible senior:
   - Shared scouting service (single report every pro team reads)
   - Your own scouting department (independent draw)

   Both refresh annually. Each grade is
   `clamp(potential + Uniform(−fog, +fog), 20, 80)`, drawn
   independently. When the two agree → high confidence. When they
   diverge → strategic ambiguity. The user's phrasing: *"you can
   swing big and miss"*.

7. **Reveal on signing.** Pro engine sees full `potential_X` (lens
   stripped). College career stats stamp onto the pro player card.
   College page backlinks from the pro player page.

8. **25% of creole names skip the Zaryanification filter** — wait,
   that's the other AAR. Carrying on.

9. **No aluminum bats.** Smaller fields only.
   `park_hr = 1.08` modifier on college park preset; `park_hits = 1.0`.

10. **WCWS shape matches reality**: 16 regional sites × 4 teams each
    in double-elim → 16 winners → 8 super-regionals (best-of-3) → 8
    teams advance to a 8-team double-elim CWS. The user's later
    "how big is the wcws?" check confirmed 8 was right.

11. **Real conferences + real schools.** Made-up conference names
    explicitly out. Liberal school inclusion: include schools that
    only play softball (not baseball), include schools that dropped
    baseball (Wyoming), include D3 academic powers (UAA, Centennial
    w/ Johns Hopkins, NESCAC). Membership by real conference
    affiliation, not by sport.

---

## What shipped

### 1. Pure growth math — `o27v2/college_potential.py` (commit `832f418`)

User dropped a complete spec mid-conversation. The implementation
mirrors it exactly:

```
raw_gain      = (interest_rate_percent / 100) × P_current
cap_base      = round_half_up( C_MAX × (1 − (P − P_MIN) / (P_MAX − P_MIN)) )
                clamped to [C_MIN, C_MAX] = [4, 15]
cap_effective = int( cap_base × tier_multiplier(interest_rate_percent) )
gain          = min(raw_gain, cap_effective)
P_new         = P + gain
```

Tier multipliers: 0-100% → 1.0×, 101-200% → 1.3×, 201-320% → 1.6×.

The cap shape is the elegant piece: lower-P players have large yearly
caps (room to climb); higher-P players have small caps (already near
ceiling). Combined with the tier multiplier, this gives:

| Starting P | Interest | Y0 → Y1 → Y2 → Y3 → Y4 |
|---|---|---|
| 30 | 214% (tier 3) | 30 → 50 → 62 → 70 → 76 |
| 60 | 80% (tier 1)  | 60 → 65 → 69 → 73 → 77 |

Both worked-example trajectories pinned as `test_college_potential.py`
regression tests. `draw_interest_rate(rng)` exposes the 75/20/5 class
distribution.

### 2. Player data model + engine adapter — `o27v2/college.py` (commit `c9a90f1`)

Pure-Python, no DB. Functions:

- `generate_college_player(rng, is_pitcher, name, country, position)` —
  rolls all hidden mechanics + engine-compatible displayed grades.
- `generate_college_roster(rng, program_name)` — 23 players
  (8 canonical-pos starters + 3 jokers + 4 backups + 8 pitchers).
- `displayed_grade(potential, access)` — the lens function.
- `advance_one_year(player)` — grows every modeled potential
  through `college_potential.grow_one_year`, refreshes displayed
  grades, increments `college_year`. Access and fog stay fixed.
- `make_scouting_report(player, rng, source=...)` — independent
  ±fog draws against TRUE potential, clamped [20, 80].
- `sign_to_pro(player, college_career_stats)` — reveal moment.
  Lens stripped, displayed → potential, college stats stamped.
- `make_engine_player(player)` — converts the displayed grades to
  the engine's `Player` dataclass (same pattern as
  `pro_worldcup._make_engine_player`).
- `build_engine_team(name, roster, role, rng)` — full `Team`
  construction including 8 starters by canonical position, jokers
  in the DH slot, lineup ordering via the existing
  `o27v2.sim._ordered_lineup` helper, smaller-field park preset.
- `sim_college_game(...)` — end-to-end game runner using the pro
  engine's `run_game`. Returns the final `GameState`; optional
  `return_renderer=True` gives the renderer back so per-player box
  rows can be extracted.

### 3. League infrastructure — `o27v2/college_league.py` (commits `2f7eb29` + later)

Persistence + season machinery layered on top of `o27v2/college.py`.

**Schema** (created idempotently via `init_college_schema()`):

| Table | Purpose |
|---|---|
| `college_programs` | name, short_name, conference, region, home_city, lat/lon, season |
| `college_players` | engine grades + hidden potential/access/interest/fog + graduation/sign tracking |
| `college_games` | regular + postseason, phase-tagged (`regular` / `regional` / `super_regional` / `cws`), `bracket_meta` JSON |
| `college_batter_stats` | per-game per-player batting line |
| `college_pitcher_stats` | per-game per-player pitching line |
| `college_scouting_reports` | season, player, source ('service' or 'team:N'), per-attribute grades |
| `college_meta` | per-season phase tracker |

**Catalogue** (195 programs across 18 real conferences after the
`dfcbe1a` realignment commit, embracing 2024+ NCAA realignment):

- **Power**: SEC (16, w/ Texas + Oklahoma), ACC (18, w/ Stanford +
  Cal + SMU), Big 12 (16, w/ BYU + UCF + Houston + Cincinnati + 4
  ex-Pac-12), Big Ten (18, w/ UCLA + USC + Oregon + Washington),
  new Pac-12 (8, WSU/OSU rebuild)
- **G5**: Mountain West (w/ Wyoming despite dropped baseball), Big
  West, American (12), Sun Belt (12), Conference USA (9)
- **Mid-D1**: Atlantic 10 (12), Missouri Valley (10), Big East (8),
  Ivy League (8), Patriot League (8)
- **D3 academic**: UAA (Brandeis, CMU, Case Western, Chicago, Emory,
  NYU, Rochester, Wash U), Centennial (Johns Hopkins + Swarthmore +
  Haverford + F&M + Gettysburg + Dickinson + Muhlenberg + Ursinus),
  NESCAC (Williams, Amherst, Wesleyan, Tufts, Middlebury, Bowdoin,
  Bates, Colby)

Inclusion is by real conference *membership*, not by whether the
school actually fields D1 baseball today. Softball-only schools and
dropped-baseball schools count.

**Schedule generator** — full round-robin within each conference (8-,
12-, 16-, 18-team confs each via the standard circle rotation),
Fri/Sat/Sun weekend cadence. Mid-week Tue/Wed single games fill out
to ~35-65 games per team depending on conference size. Round-robin
games are 3-game weekend series at the same home park.

**Game sim** (`sim_game(game_id)`):
1. Loads both rosters
2. Runs through `_cg.sim_college_game(...)` with `return_renderer=True`
3. Writes the final score to `college_games`
4. Extracts per-side batter rows from the renderer
5. Extracts per-side pitcher rows from the game state's `spell_log`
6. Persists both into `college_batter_stats` / `college_pitcher_stats`

**Postseason** (`run_postseason(season)`):
- Regional: top 64 programs by win-pct, seeded into 16 four-team
  brackets, each run as a double-elimination ladder (last unbeaten
  wins).
- Super-Regional: 16 regional winners → 8 best-of-3 pairings.
- CWS (the WCWS analog): 8 super-regional winners → 8-team
  double-elimination. Last team standing is the champion.

Each postseason game is written to `college_games` with the
appropriate `phase` tag and `bracket_meta` JSON so the UI can
render the bracket.

**Scouting reports** (`generate_scouting_reports(season)`):
- One shared-service report per draft-eligible senior
- Plus one per pro team — *each an independent draw*
- All persisted to `college_scouting_reports`

**Annual rollover** (`annual_rollover(season, next_season)`):
1. Seniors → `graduated=1`, `is_active=0` (signing-eligible)
2. Surviving juniors/sophs/freshmen aged up: potential grows via
   `_cg.advance_one_year(p)`, college_year++, displayed grades
   refreshed; access stays fixed
3. Programs carried forward as new rows with `season = next_season`
4. ~6 fresh freshmen generated per program
5. New schedule generated

**Pro signing** (`sign_graduate_to_pro(college_player_id)`):
1. Pulls a graduated college row
2. Builds a pro-side player dict via `_cg.sign_to_pro(...)` —
   displayed → potential, lens stripped, college career stats
   stamped via the `college_career_stats` field
3. Aggregates the player's full college batting + pitching career
   via `_career_stats(player_id)` and includes it
4. Inserts into the pro `players` table as a free agent
   (`team_id = NULL`)
5. Backlinks: `college_players.signed_pro_player_id` set to the
   new pro id so the pro page can find this player's college origin

**Leaders + filters** (`batter_leaders` / `pitcher_leaders`):
- Sort by HR / AVG / OPS / RBI / H / R (batters)
- Sort by ERA / WHIP / K / IP / K9 (pitchers)
- `conference="SEC"` filter (joined through programs)
- `program_id=N` filter (for team-specific leaders)
- Min-PA / min-outs gates to avoid sample-size noise

**Team totals** (`program_team_totals(program_id, season)`):
Aggregates one program's full season batting + pitching line —
matches the box-score reporting shape NCAA team-stats pages use.

### 4. Web UI (commits `a748c74` + `477008f` + `5cffe43`)

**Routes** under `/college`:

| Route | What you can do |
|---|---|
| `GET /college` | Landing: action bar (seed / sim / postseason / rollover), standings grouped by conference |
| `GET /college/program/<id>` | Roster (displayed grades), team-batting + team-pitching season totals, top 8 batters + top 8 pitchers on this team, full schedule with W-L results |
| `GET /college/player/<id>` | Displayed grades + admin view of hidden potential/access/interest tier/fog, plus all scouting reports if senior |
| `GET /college/leaders` | Top 50 batters + top 50 pitchers, sort tabs per column, **conference filter dropdown** scoping both boards |
| `GET /college/game/<id>` | Full home/away box score — every batter line + every pitcher line |
| `GET /college/postseason` | Bracket view: CWS, Super-Regionals, all 16 Regional brackets |
| `GET /college/draft` | Two-row-per-player draft board: shared service report row + your-dept row side-by-side, green/red tint on disagreements, "Sign" button per player |
| `POST /api/college/seed` | Create 64 programs + rosters for a season |
| `POST /api/college/sim-season` | Sim all unplayed regular-season games |
| `POST /api/college/run-postseason` | Run regionals → super-regionals → CWS + generate scouting reports |
| `POST /api/college/rollover` | Graduate seniors, age remaining, generate new freshman class, new schedule |
| `POST /api/college/sign/<id>` | Sign a graduated senior → pro free-agent pool, redirect to pro player page |

**Pro player page integration** (`templates/player.html`): when a pro
player has a row in `college_players` with
`signed_pro_player_id = THIS_PLAYER_ID`, a "Signed from college" panel
renders under the player header. Shows the program (linked back to
`/college/program/<id>`), graduation year, full college batting line
(PA / AVG / H / HR / RBI / BB / K), and pitching line (IP / ERA / K /
BB / H / HR) — the *reveal* moment is now in the UI: hidden grades
come up on the pro card, college stats stay attached.

**Nav**: new entry under League → College after Pro World Cup. All
college routes added to `g_league` active-state list so the dropdown
stays highlighted across subpages.

---

## How the asymmetry-reveal mechanic surfaces in play

The whole game is in the spread between what *you* see and what's
*true*. Walking through a typical hidden gem:

**Year 1** — freshman generated with `potential_skill=62`,
`access_skill=0.42`, `interest_rate=185%` (tier 2).
- Displayed `skill = round(62 × 0.42) = 26`. Bench-tier.
- His stats reflect a back-end role player.
- Scouting reports won't generate until he's a senior.

**Years 2-4** — annual rollover runs growth on potential
(`62 → ~70 → ~75 → ~79`), access stays 0.42, displayed climbs
gradually `26 → 29 → 31 → 33`. He's still a fringe contributor on
the college stat line.

**Senior year scouting reports** generate. Fog magnitude (say 24):
- Shared service: random draw → `79 ± 24` → reports something like
  `64` skill.
- Your dept: independent draw → reports something like `82` skill.
- The two disagree wildly. You can't tell which is right.

**Decision** — draft him cheaply because he doesn't look like much
on stats, hoping your scout was the right read, OR pass because the
gap between the two reports is too risky.

**If signed** — pro player created with `skill = 79`. Plays at his
true grade in the pro engine. Big surprise on the player card. The
"college batting" line stamped on the pro page still shows the
fringe-tier college stats: that's the proof of the asymmetry.

The mirror case — a college legend with `potential=45` and
`access=0.95`, who displays as 43 with great stats, looks elite,
gets blurred to a 38-58 range in reports — feels great on draft day
but plays at 45 in pro and underperforms. Same mechanism.

---

## Things that went sideways

- **First conference catalogue was made-up names with real schools**
  (Southeastern / Big Plains / Pacific Coast etc.). User correctly
  pushed back: *"i wanted real baseball conferences with real teams
  unless there's some reason not to do that"*. Rewrote to actual
  NCAA conference structure post-2024 realignment.
- **Bad hex literals in `_C_0_1_1_E_G_E` / `_S_C_H_E_D_` / etc.** —
  Python doesn't accept G/H/etc. in hex. Used `0x5CHED01` and friends
  during a `sed` pass; had to fix the H literal. Caught immediately
  by import.
- **`db.execute()` returns lastrowid directly**, not a cursor. My
  first pass used `cur = db.execute(...); cur.lastrowid` from
  muscle-memory with other libs. Fixed in a handful of spots
  during initial commit; tests caught it.
- **`run_game()` returns `(state, list[str])`** — the list is
  rendered output lines, NOT a renderer. Pro World Cup builds a
  `Renderer()` and passes it in as the third argument; the renderer
  accumulates stats. First version of `sim_college_game(return_renderer=True)`
  tried to use the return tuple as the renderer and crashed. Fixed
  by mirroring the WC pattern.
- **`college_year` double-incremented** during seed: I assigned
  `target_year` to the dict, then called `advance_one_year` which
  itself increments. Year 4 freshmen ended up at year 7. Fixed by
  just advancing N-1 times and letting the function land the value.
- **`players.position` NOT NULL** in the pro schema — graduated
  jokers / DH-only college players got rejected on sign. Defaulted
  to `"P"` for pitchers, `"RF"` for hitters when crossing the
  signing boundary.
- **Test asserting "top batters"** failed because the rendered text
  is `"FSU top batters"` (program short_name substituted in). Test
  assertion was the bug, not the template. Updated.

---

## Decisions you made along the way

Each resolved via direct user input mid-build:

- **Access distribution**: Uniform 0.40-0.95 per attribute
- **Scouting fog magnitude**: Uniform 7-31 per player (not per
  attribute)
- **Per-player fog** (not per-attribute) — kid is a "mystery box
  across the board" or "well-known across the board"
- **Two reports per player**: shared service + your own department,
  independent draws
- **Fog stays static** through college; annual reports give you
  triangulation via refresh
- **Interest rate distribution**: tier buckets 75/20/5 (not
  continuous distribution)
- **Access fixed across years**; only potential grows
- **No aluminum bats**, smaller fields only (single park modifier)
- **Real conferences + real schools**; liberal with inclusion
- **D3 academic powers** (UAA, Centennial with Johns Hopkins,
  NESCAC) included alongside D1
- **Dropped-baseball schools** like Wyoming kept in their real
  conferences
- **WCWS = 8 teams** confirmed mid-build; matches existing build
- **Team stats + conference filter** identified as crucial near the
  end; added.

---

## Where things live (relay reference)

| Feature | File |
|---|---|
| Growth math (interest tiers + cap formula) | `o27v2/college_potential.py` |
| Player data model (potential/access/interest/fog) | `o27v2/college.py` |
| Engine adapter (displayed → engine Player) | `o27v2/college.py:make_engine_player` |
| Roster builder (23-man with jokers + pitchers) | `o27v2/college.py:generate_college_roster` |
| Team builder (engine Team from college roster) | `o27v2/college.py:build_engine_team` |
| Game sim (engine + renderer) | `o27v2/college.py:sim_college_game` |
| Scouting report generator | `o27v2/college.py:make_scouting_report` |
| Pro signing data prep | `o27v2/college.py:sign_to_pro` |
| Schema + seeding + 195-program catalogue | `o27v2/college_league.py` |
| Schedule generator | `o27v2/college_league.py:generate_schedule` |
| Game sim → DB (with stat extraction) | `o27v2/college_league.py:sim_game` |
| Per-game stat row extractors | `o27v2/college_league.py:_persist_batter_rows / _persist_pitcher_rows` |
| Standings | `o27v2/college_league.py:standings` |
| Leaders (with conference + program filters) | `o27v2/college_league.py:batter_leaders / pitcher_leaders` |
| Team totals | `o27v2/college_league.py:program_team_totals` |
| Box-score for one game | `o27v2/college_league.py:game_box` |
| Draft class (graduated seniors + reports) | `o27v2/college_league.py:draft_class` |
| Pro signing (with college stats stamped) | `o27v2/college_league.py:sign_graduate_to_pro` |
| Postseason runner | `o27v2/college_league.py:run_postseason` |
| Annual rollover | `o27v2/college_league.py:annual_rollover` |
| Web routes (all `/college/*`) | `o27v2/web/app.py` (search `college_view`) |
| Templates | `o27v2/web/templates/college_*.html` |
| Tests | `tests/test_college*.py` (28/28 green) |

---

## Open items (not yet started)

- **Real recruiting class previews** — currently freshmen
  auto-generate at rollover. Could expose a "recruiting class"
  scouting view with the incoming class for the next season.
- **Conference championships** — a separate weekend tournament
  before regionals (real NCAA softball has these). Currently
  regional seeding is straight win-pct.
- **Player development on the pro side** — once a college player
  signs, no further growth (he plays at his static `potential`).
  Could layer an aging curve later.
- **Sortable column headers** on the leaders table — currently
  sort tabs, could be inline.
- **Recruiting filters on the draft board** — sort by position,
  conference, year, etc. Current view is name-ordered.
- **Save preset** — the new-league preset list doesn't include the
  college tier. Could add a "Run college season alongside pro" toggle
  so users get both worlds in one save.
- **Per-conference standings page** — currently grouped on the main
  index; a dedicated conference page would also surface conference
  schedule + champion.
