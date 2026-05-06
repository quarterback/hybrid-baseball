# O27

A baseball variant compressed into one continuous 27-out half-inning per side. Cricket's T20 ethos applied to baseball — the same total outs, restructured into a single arc that changes the strategic shape of the sport. Unlike my last attempt at a fake computer sport [Viperball](https://github.com/quarterback/viperball), this one has been in my head for a long time. I've surely tweeted about it before, presupposing you could "Cricket Baseball" but shortening the game somehow. I debated between a 5 inning game and ending it, but tests with that when I started here made it clear that'd just be "short, more random and even more boring baseball. When I considered 27 outs/1 innings, it felt like "ohhh...now this is a marathon and a sprint all at once" 

It's not written in the rules or anything, but assume everyone pitcher in this sport, either throws sidearm or is a submariner. 

This repository contains the rules, the simulation engine, the stats methodology, and the web UI for browsing simulated O27 seasons.

## What O27 Is

O27 is baseball with one structural change: each side bats until they record 27 outs. There are no innings. The starting pitcher takes the mound at out 0 and pitches until he's pulled. Hitters cycle through the lineup as many times as the half lasts. The home team can stop early if they're winning in the bottom half.

That single change — eliminating innings — produces a sport that resembles baseball in many respects but plays very differently in others. Pitcher fatigue accumulates monotonically across one continuous arc instead of resetting between innings. Top-of-order hitters get 5-7 plate appearances per game instead of 4-5. Manager decisions matter more because there are no inning resets to mask a tiring pitcher. Roster construction is forced to value workhorse stamina over short-burst dominance.

## The Rules

**Lineup and roster**
- 9 fielders bat in a fixed order. The starting pitcher must bat — no DH replacing him.
- 3 jokers per roster: tactical insertions the manager can drop into any spot in the rotation, once per cycle. Jokers don't take a roster slot or a field position. They add a PA to the rotation when used. A manager who never deploys his jokers leaves offense on the table.
- Pinch hitting still exists separately and works as in MLB — the pinch hitter replaces a regular permanently in the lineup and the field.

**Second-chance ABs**
- When a batter makes contact in the field of play, he chooses whether to run or stay at the plate. If he stays, runners advance, the batter is credited with a hit, and he stays at the plate to await the next pitch. The contact event counts as a strike — the count carries normally.
- Maximum three contact events per AB. A foul ball counts as a strike. Three fouls is a foul-out (no infinite-foul protection like MLB).
- Walks at four balls regardless of how many second-chance hits the batter has accumulated.

**Pitching**
- All pitchers in O27 throw sidearm or submarine. This is a structural feature of the sport's history, not a rule that's enforced game-to-game.
- Pitch repertoire: 17-pitch catalog including conventional pitches (four-seam, sinker, cutter, slider, curveball, changeup, splitter) and O27-specific pitches that the sport's structure makes viable (knuckleball, spitter, eephus, screwball, gyroball, palmball, Vulcan changeup, Sisko slider, walking slider, 10-to-2 curve).
- Each pitcher has a per-pitcher repertoire (typically 2-5 pitches) with individual quality ratings, count-aware usage patterns, and platoon effects.

**Game length**
- Tied games go to super-innings: each side fields 5 batters and bats until 5 dismissals. Repeat until a winner.
- Regular season: 162 games per team.

## How the Stats Are Different

Because plate appearances and at-bats diverge structurally in O27 (a single AB can contain multiple PAs through stays), traditional batting average breaks down. The stat suite is recalibrated to handle this:

**Batting**
- **PAVG (Plate Average):** hits per plate appearance. Bounded 0-1. Reads the way batting average used to. League average lands around .280-.320.
- **BAVG (Batting Average):** hits per at-bat. Same denominator as MLB BA but reflects the multi-hit-AB reality of O27. League average lands around .330-.380.
- **Δ (BAVG - PAVG):** the second-chance productivity signal. High Δ = effective use of second-chance ABs.

OBP, SLG, OPS, ISO, BABIP, and wOBA all use PA as the denominator and calibrate to a higher run environment than MLB.

**Pitching**
- Innings pitched is gone. Replaced by **OUT** (the team's out count when the pitcher's last batter's PA ended) for game-level lines, and total outs recorded for season workload.
- **OS%:** outs share — percentage of the team's 27 outs the pitcher recorded.
- **wERA:** weighted ERA. Earned runs are weighted by where in the 27-out arc they occurred (outs 1-9 weighted at 0.85, outs 10-18 at 1.00, outs 19-27 at 1.20). Runs given up late are more damaging because there's less arc remaining for the offense to respond.
- **xRA:** expected runs allowed. A linear-weights formula calibrated to league wERA. Replaces FIP/xFIP, which produce nonsense at the tails in O27 small samples.
- **Decay:** the rate at which a pitcher's K% drops between outs 1-9 and outs 19-27. Drift-corrected against the league baseline so 0 = league norm. Genuinely O27-specific — MLB doesn't measure this because pitchers don't pitch long enough in single appearances for arc-degradation to be a stable skill.
- **Game Score (GSc):** per-appearance summary, bounded 0-100, with an O27-specific bonus for foul-outs.
- **pWAR:** anchored to wERA. Runs-per-win is recalibrated per season against the live run environment (typically 16-18 in O27 vs MLB's ~10).

## What This Does to the Sport

The structural rules compound into a sport that produces different baseball outcomes:

**Higher run environment.** Lineups are deeper (9 fielders + 3 jokers means 12 useful hitters), pitchers don't get inning resets, and second-chance ABs let offenses advance runners without spending outs. League R/G runs around 22-26 vs MLB's ~9.

**Workhorse pitchers are valuable in ways modern MLB has lost.** A B+ starter who can give you 24 outs is worth more than a lights-out reliever who can only give you 6, because there's no inning-by-inning bullpen ladder to deploy. Stamina is the most valuable pitching attribute. Career arcs are longer because sidearm/submarine deliveries are less stressful — the 38-year-old workhorse is much more common than in MLB.

**Contact specialists matter.** Every contact event in O27 costs a strike, so the patient hitter who can keep ABs alive across multiple PAs produces real offense in a way he doesn't in MLB. High-contact, high-eye hitters carve out a distinct archetype — they don't necessarily lead the league in HR, but their second-chance AB conversion rates and Δ values mark them as a structurally different kind of star.

**Pitcher archetypes are deeper than MLB's.** Because the league is structurally sidearm/submarine, the pitching value calculus shifts toward deception, movement, and arsenal depth over peak velocity. The 5-pitch starter who can keep showing the lineup something new is more valuable than in MLB. The pure-velocity reliever is less dominant. The knuckleball comes back hard — knuckleballers face less stamina-driven degradation than velocity pitchers and pitch into their late 40s.

**Intentional walks are rare.** With three jokers available per game who can be inserted at the most damaging moment, walking an elite hitter doesn't dodge the threat — it just sets up a different one. Pitchers don't pitch around sluggers. League-wide BB% for top-power hitters tracks league average, structurally different from the Bonds-style walk-the-bat dynamic of MLB.

**Manager decisions are weight-bearing.** Joker insertions are a real per-rotation decision. Pitching changes don't get masked by inning resets. Lineup construction has to think about which hitters get the most PAs across a 27-out arc.

## Repository Structure

- `o27/` — engine, simulation logic, configuration, tests
  - `engine/` — per-pitch probability model, plate appearance resolution, fielding, baserunning, pitcher and batter state
  - `tests/` — property tests, invariant tests, redistribute tests
  - `config.py` — all tuning constants in one place
- `o27v2/` — Flask web application, database layer, simulation runner
  - `web/` — Flask app, templates, static assets
  - `sim.py` — runs games, persists results
  - `db.py` — SQLite schema and access layer
  - `manage.py` — CLI for resetting DB, seeding rosters, running seasons, replaying games
- `docs/` — methodology guides, AARs, design notes

## Running It

Requires Python 3.11+.
pip install -r requirements.txt
python o27v2/manage.py resetdb
python o27v2/manage.py seed
python o27v2/manage.py simulate --games 2430
python o27v2/manage.py runserver

Then browse to `http://localhost:5000` to explore the stats.

## Status

Active development. Engine, stats methodology, and web UI are functional and producing complete simulated seasons. The pitch repertoire system, talent flow through contact events, and pitcher attribute architecture are recent additions and continue to evolve.

The sim is not a complete game product. There's no manager-mode play, no in-season transactions UI, no GM workflow. It's a data exploration tool for browsing what the rules produce, with views designed to surface stats and archetypes for human reading or LLM ingestion.

## Why O27 Exists

The original idea came from wondering what would happen if baseball borrowed cricket's T20 structure — not by shortening the game (5-inning baseball is just bad baseball), but by changing the structure of an inning to create the same kind of strategic compression T20 brought to cricket.

The O27 simulation exists to test whether that hunch produces a sport worth caring about. Three months of iteration suggests it does. The rules produce baseball that resembles itself in many respects but plays differently in ways that feel native to the sport rather than imposed on it. Lineup cycling matters more. Pitcher fatigue matters more. Manager decisions matter more. The kind of player who's marginalized in modern MLB — the contact specialist, the workhorse, the junkballer, the knuckleballer — finds real value here.

Whether it ever becomes a real game played by real humans is a separate question. As a thought experiment with working math behind it, it's been instructive enough to be worth shipping.

## License

Parachute Commons License 
