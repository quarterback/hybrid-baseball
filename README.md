# O27

A baseball variant compressed into one continuous 27-out half-inning per side. Cricket's T20 ethos applied to baseball — the same total outs, restructured into a single arc that changes the strategic shape of the sport. Unlike my last attempt at a fake computer sport, [Viperball](https://github.com/quarterback/viperball), this one has been in my head for a long time. I've surely tweeted about it before, presupposing you could "Cricket Baseball" but shortening the game somehow. I debated between a 5-inning game and ending it there, but tests with that early on made it clear that would just be "short, more random, even more boring baseball." When I considered 27 outs / 1 innings, it felt like "ohhh... now this is a marathon and a sprint all at once."

It's not written in the rules anywhere, but assume every pitcher in this sport either throws sidearm or is a submariner.

This repository contains the rules, the simulation engine, the stats methodology, and the web UI for browsing simulated O27 seasons. A live build runs at [hybrid-baseball.fly.dev](https://hybrid-baseball.fly.dev).

## What O27 Is

O27 is baseball with one structural change: each side bats until they record 27 outs. There are no innings. The starting pitcher takes the mound at out 0 and pitches until he's pulled. Hitters cycle through the lineup as many times as the half lasts. The home team can stop early if they're winning at out 27 of the bottom half.

That single change — eliminating innings — produces a sport that resembles baseball in many respects but plays very differently in others. Pitcher fatigue accumulates monotonically across one continuous arc instead of resetting between innings. Top-of-order hitters get 5–7 plate appearances per game instead of 4–5. Manager decisions matter more because there are no inning resets to mask a tiring pitcher. Roster construction is forced to value workhorse stamina over short-burst dominance.

If you want the long version of the design argument, the post that goes through "why not just play five innings" and lands here is in [`docs/blog-o27-vs-five-inning-baseball.md`](docs/blog-o27-vs-five-inning-baseball.md).

## The Rules

**Lineup and roster**
- 12 batters per side: 8 fielders + the starting pitcher + 3 DHs. The SP must bat — no DH replacing him.
- 3 jokers per roster: tactical insertions the manager can drop into any spot in the rotation, once per cycle. Jokers don't take a roster slot or a field position. They add a PA to the rotation when used. A manager who never deploys his jokers leaves offense on the table. Each team rosters one joker of each archetype (power, speed, contact).
- Pinch hitting still exists separately and works as in MLB — the pinch hitter replaces a regular permanently in the lineup and the field.
- Active roster is 34 players (12 fielders + 3 DH + 19 pitchers), with 13 reserves behind them. Reserves promote ephemerally to cover injuries — no DB-level "callup," just whoever fits the hole that day.

**Second-chance ABs (the "stay" mechanic, borrowed from pesäpallo)**
- When a batter makes contact in the field of play, he chooses whether to run or stay at the plate. If he stays, runners advance, the batter stays at the plate to await the next pitch, and the contact event counts as a strike — the count carries normally. A stay that drives a runner home is what produces most of O27's RBI.
- Maximum three contact events per AB. A foul ball counts as a strike. Three fouls is a foul-out (no infinite-foul protection like MLB).
- Walks at four balls regardless of how many second-chance events the batter has accumulated.
- The mechanic is fundamentally a *runner-advancement* tool, not a hit-creation tool. Big stays move runners around the bases without ending the AB; that's the lever skilled hitters pull.

**Pitching**
- All pitchers in O27 throw sidearm or submarine. This is a structural feature of the sport's history, not a rule that's enforced game-to-game.
- 17-pitch catalog including conventional pitches (four-seam, sinker, cutter, slider, curveball, changeup, splitter) and O27-specific pitches that the sport's structure makes viable (knuckleball, spitter, eephus, screwball, gyroball, palmball, Vulcan changeup, Sisko slider, walking slider, 10-to-2 curve).
- Each pitcher has a per-pitcher repertoire (typically 2–5 pitches) with individual quality ratings, count-aware usage patterns, and platoon effects.
- No persisted starter/reliever role. The manager AI picks today's SP as the highest-Stamina arm not used in the last four sim days, and assigns relievers at appearance time based on where in the 27-out arc the call is happening: late-arc → max Stuff, mid-arc → mid, early-arc → max Stamina.

**Defense, parks, weather**
- Each park has its own fence geometry (asymmetric where appropriate), shape archetype, and architectural quirks; spray charts and HR rates reflect the park, not a league-wide constant. Pre-modern park shapes are part of the catalog.
- Defensive shifts fire from a manager `shift_aggression` rating against batter spray, with adaptability ratings letting hitters bunt or go opposite-field against extreme shifts.
- A per-game weather draw mutates batted-ball physics on the margin.

**Game length**
- Tied games go to super-innings: each side fields 5 batters and bats until 5 dismissals. Repeat until a winner.
- Regular season length is per league config (8/12/16/24/30/36 teams, plus a 56-team tiered config). The default 30-team config plays 162 games per team.

## How the Stats Are Different

Because plate appearances and at-bats diverge structurally in O27 (a single AB can contain multiple stays), traditional batting average breaks down. The stat suite is recalibrated to handle this. The full reference catalog with formulas and code pointers lives in [`docs/stats-reference.md`](docs/stats-reference.md); a high-level summary:

**Batting**
- **PAVG (Plate Average):** hits per plate appearance. Bounded 0–1. Reads the way batting average used to.
- **BAVG (Batting Average):** hits per at-bat. Same denominator as MLB BA but reflects the multi-hit-AB reality of O27, so it can exceed 1.000 for stay-heavy hitters.
- **Δ (BAVG − PAVG):** the second-chance productivity signal. High Δ = effective use of stays.
- **RAD:** graded total bases from per-base runner advancement (a pesäpallo-derived counting stat — moving a runner to 2nd is worth less than moving him to 3rd is worth less than scoring him).
- OBP, SLG, OPS, ISO, BABIP, and wOBA all use PA as the denominator and calibrate to a higher run environment than MLB. wOBA's linear weights separate 1B from STAY explicitly.

**Pitching**
- Innings pitched is gone. Replaced by **OUT** (the team's out count when the pitcher's last batter's PA ended) for game-level lines, and total outs recorded for season workload.
- **OS%:** outs share — percentage of the team's 27 outs the pitcher recorded.
- **wERA:** weighted ERA. Earned runs are weighted by where in the 27-out arc they occurred (outs 1–9 at 0.85, outs 10–18 at 1.00, outs 19–27 at 1.20). Runs given up late are more damaging because there's less arc remaining for the offense to respond.
- **xRA:** expected runs allowed. A linear-weights formula calibrated to league wERA. Replaces FIP/xFIP, which produce nonsense at the tails in O27 small samples.
- **Decay:** the rate at which a pitcher's K% drops between outs 1–9 and outs 19–27. Drift-corrected against the league baseline so 0 = league norm. Genuinely O27-specific — MLB doesn't measure this because pitchers don't pitch long enough in single appearances for arc-degradation to be a stable skill.
- **Game Score (GSc):** per-appearance summary, bounded 0–100, with an O27-specific bonus for foul-outs.
- **pWAR:** anchored to wERA. Runs-per-win is recalibrated per season against the live run environment.

**Analytics suite** (in `o27v2/analytics/`): linear weights, expected wOBA from contact quality, base runs, run expectancy by base-out state, Pythagorean projection. All re-derived from league data each render cycle rather than borrowed from MLB constants.

## What This Does to the Sport

The structural rules compound into a sport that produces different baseball outcomes:

**Higher run environment.** Lineups are deeper (12 useful hitters plus 3 jokers), pitchers don't get inning resets, and stays let offenses advance runners without spending outs. League R/G/team runs well above MLB's ~4.5.

**Workhorse pitchers are valuable in ways modern MLB has lost.** A B+ starter who can give you 24 outs is worth more than a lights-out reliever who can only give you 6, because there's no inning-by-inning bullpen ladder. Stamina is the most valuable pitching attribute. Career arcs are longer because sidearm/submarine deliveries are less stressful — the 38-year-old workhorse is much more common than in MLB.

**Contact specialists matter.** Every contact event in O27 costs a strike, so the patient hitter who can keep ABs alive across multiple PAs produces real offense in a way he doesn't in MLB. High-contact, high-eye hitters carve out a distinct archetype.

**Pitcher archetypes are deeper than MLB's.** Because the league is structurally sidearm/submarine, the value calculus shifts toward deception, movement, and arsenal depth over peak velocity. The 5-pitch starter who can keep showing the lineup something new is more valuable than in MLB. The pure-velocity reliever is less dominant. The knuckleball comes back hard.

**Intentional walks are rare.** With three jokers available per game who can be inserted at the most damaging moment, walking an elite hitter doesn't dodge the threat — it just sets up a different one. Pitchers don't pitch around sluggers.

**Manager decisions are weight-bearing.** Joker insertions are a real per-rotation decision. Pitching changes don't get masked by inning resets. Lineup construction has to think about which hitters get the most PAs across a 27-out arc. Named managers carry attributes (shift aggression, hook patience, pinch tendencies, RISP-pressure response) that materially change in-game outcomes.

## What Got Built on Top of the Rules

The sim has grown well past "rules engine + box score." A non-exhaustive list of the systems that wrap around the engine:

- **Player archetypes.** Position players classify into archetypes from their attribute grades (e.g. contact hitter, three-true-outcomes slugger, defense-first up-the-middle). Pitcher arsenal and shape work similarly.
- **Currency, valuation, auctions.** Every player has a salary in *guilders*, derived from current attributes. Live auction-style player markets sit on top.
- **Trade engine.** Motivation-driven, with front-office personalities (rebuilders, win-now buyers, value hunters). Deadline activity, in-season trades, and waivers all fire from team state, not random rolls.
- **Injuries and aging.** Tiered IL (DTD / short / long), age-curve modifiers, position-specific risk, year-over-year drift baked into the offseason.
- **Schedule, playoffs, awards.** Configurable team counts (8–36 + tiered), division-aware round-robin, playoff bracket, end-of-season awards and scouting.
- **Youth pipeline.** A separate development sim feeds prospects into the league.
- **Realism layer.** Ballparks (with pre-modern shapes), weather, batted-ball physics (EV / LA / spray), handedness splits, defensive shifts, RISP-pressure modifier, leadership flare on the firing side of a leverage swing.
- **Stat invariants test suite.** `make test-invariants` runs nine assertions that catch every mathematically-impossible-stat bug the project has shipped before (phase-out caps, OR reconciliation, pitcher↔batter cross-check, OS% bounds, league FIP within 0.05 of league ERA, etc.). Required to pass before any release.
- **Newspaper-style box score and Markdown export** for LLM-friendly game writeups.

Every meaningful change is logged in [`docs/`](docs/) as an AAR with the reasoning, the tradeoffs, and the verification numbers — that's where the design history actually lives.

## Repository Structure

- `o27/` — the original O27 simulator, kept as the reference Flask implementation.
  - `engine/` — game loop, plate-appearance resolution, manager AI, baserunning, batted-ball physics, fielding, park effects, weather, pitch-by-pitch probabilities, stay mechanic, per-game state.
  - `stats/`, `render/` — batter/pitcher/team stat accumulators and Jinja2 play-by-play renderer.
  - `web/`, `stats_site/` — the operational Flask GUI and the stats-browsing blueprint mounted at `/stats`.
  - `almanac/` — standalone Fangraphs-style static stats site generator. Reads from the o27v2 SQLite DB or a season-bundle JSON; emits a self-contained HTML/CSS/JS archive with sortable heatmap leaderboards and a downloadable CSV/JSON/ZIP bundle for every dataset. `python -m o27.almanac build --source o27v2/o27v2.db --out site/`.
  - `tests/test_rules.py` — 100+ rule-verification tests.
  - `config.py` — every tunable constant in one file.
  - `tune.py` — batch tuner for calibration runs.
- `o27v2/` — the 30-team league fork. This is the active code path for the live deployment.
  - `web/` — Flask app, templates, box-score and text-export renderers (Baseball-Reference-style IA).
  - `sim.py`, `db.py`, `manage.py` — game runner, SQLite persistence, CLI.
  - `league.py`, `schedule.py` — league seeding (8–36 team configs), schedule generation.
  - `injuries.py`, `trades.py`, `waivers.py`, `transactions.py`, `front_office.py` — roster movement.
  - `auction.py`, `currency.py`, `valuation.py` — economy.
  - `archetypes.py`, `scout.py`, `managers.py`, `development.py`, `youth.py`, `youth_sim.py` — player modeling and pipeline.
  - `playoffs.py`, `awards.py`, `season_archive.py`, `promotion.py` — season lifecycle.
  - `analytics/` — linear weights, xwOBA, base runs, run expectancy, Pythagorean.
  - `data/league_configs/` — `8teams.json` through `36teams.json` plus `56teams_tiered.json`.
  - `tests/` — archetype, linear-weights, RISP-pressure, trade, migration tests.
- `tests/` — top-level invariant suite (`make test-invariants`) that runs against a populated DB.
- `docs/` — methodology references and ~40 AARs documenting every system shipped.
- `Dockerfile`, `fly.toml`, `DEPLOY.md` — Fly.io deployment for `hybrid-baseball.fly.dev`.
- `lib/`, `scripts/`, `pnpm-workspace.yaml` — TypeScript-side workspace scaffolding (API spec, codegen, React client). Independent of the Python sim.

## Running It

Requires Python 3.11+ (Docker image uses 3.12). Dependencies are declared in `pyproject.toml`.

```
pip install -e .
python o27v2/manage.py resetdb               # default --config 30teams
python o27v2/manage.py sim 2430              # full 30-team, 162-game season
python o27v2/manage.py runserver             # web app on $PORT (default 5001)
```

Then browse to `http://localhost:5001` to explore the stats.

Useful one-offs:

```
python o27v2/manage.py configs               # list available league configs
python o27v2/manage.py initdb --config 16teams
python o27v2/manage.py tune 200 --config 12teams   # calibration run + targets report
python o27v2/manage.py backfill_arc          # replay played games via stored seeds
python o27v2/manage.py backfill_salaries     # recompute every player's guilder salary
python o27v2/manage.py smoke                 # 10-seed engine smoke test
make test-invariants                         # stat-invariant suite against o27v2.db

# Almanac (static stats site, Fangraphs-style):
python -m o27.almanac build --source o27v2/o27v2.db --out site/   # build
python -m o27.almanac build --source season-bundle.json --out site/  # rebuild from a JSON bundle
python -m o27.almanac serve --out site/                           # local preview
python -m o27.almanac ingest --source <path>                      # validate / inspect a source
```

Deployment to Fly.io (`hybrid-baseball` app, `ams` region, `o27v2_data` volume mounted at `/data`) is documented in [`DEPLOY.md`](DEPLOY.md).

## Status

Active development. Engine, stats methodology, web UI, economy, trades, playoffs, and the youth pipeline are functional and producing complete simulated seasons. New systems land roughly weekly; recent additions include defensive shifts (with adaptability and bunt-against-shift), per-base runner-advancement analytics, the leadership-flare model, named-manager attribute effects, and a motivation-driven trade engine with front-office personalities. The pitch repertoire system, ballpark geometry, and weather model all continue to evolve.

The sim is not a complete game product. There's no manager-mode play, no human-driven GM workflow. It's a data exploration tool for browsing what the rules produce, with views designed to surface stats and archetypes for human reading or LLM ingestion.

## Why O27 Exists

The original idea came from wondering what would happen if baseball borrowed cricket's T20 structure — not by shortening the game (5-inning baseball is just bad baseball — see the blog post under `docs/`), but by changing the structure of an inning to create the same kind of strategic compression T20 brought to cricket. The stay mechanic borrowed from pesäpallo handles the second question that opens up once you do: with one continuous half-inning, what tactical lever does the batter still have over the outcome of an at-bat?

The O27 simulation exists to test whether that combination produces a sport worth caring about. Months of iteration suggest it does. The rules produce baseball that resembles itself in many respects but plays differently in ways that feel native to the sport rather than imposed on it. Lineup cycling matters more. Pitcher fatigue matters more. Manager decisions matter more. The kind of player who's marginalized in modern MLB — the contact specialist, the workhorse, the junkballer, the knuckleballer — finds real value here.

Whether it ever becomes a real game played by real humans is a separate question. As a thought experiment with working math behind it, it's been instructive enough to be worth shipping.

## License

Parachute Commons License
