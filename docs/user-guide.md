# O27 — User Guide

*For someone arriving cold.* O27 is a lot, and most of it is optional. This guide
gets you from "what is this" to "I know what I'm looking at" without making you
read the whole repo. Read it top to bottom the first time; after that it's a map
you can jump around in.

If you only have two minutes, read [§1](#1-what-this-actually-is) and
[§2](#2-the-sport-in-90-seconds). If you want to *run* it, jump to
[§4](#4-running-it-locally). If you're staring at the live site wondering where to
click, go to [§5](#5-a-tour-of-the-web-app).

---

## 1. What this actually is

O27 is a **fictional sport** — baseball with one structural change, each side bats
until it records 27 outs, no innings — and a **simulation of it** carried all the
way to its consequences: a game engine, a statistics suite rebuilt from scratch
because the old one breaks under the new rules, an economy, a youth and college
pipeline, and a set of reading surfaces for browsing what the rules produce.

What it is **not**, so you set expectations correctly:

- **It is not a game you play.** There's no manager mode, no GM-you. You don't
  pitch, bat, or make trades by hand. You sim seasons and *read* what happens.
- **It is a data-exploration tool.** The whole front end exists to surface stats,
  standings, box scores, and archetypes for a human — or an LLM — to interpret.
- **It is an argument with the math built to settle it.** The point is whether the
  rules produce a sport worth caring about. The sim is how that gets answered.

The shortest path to "getting it" is to open the [live build](https://hybrid-baseball.fly.dev),
look at a box score, and notice that batting averages can exceed 1.000 and a single
game can have 30 runs. That's not a bug — it's the sport. The rest of this guide
explains why.

## 2. The sport in 90 seconds

You don't need the full rulebook to start reading. Six ideas carry most of it; the
complete rules are in the [README](../README.md#the-rules).

1. **27 outs, no innings.** Each side bats until it makes 27 outs — one long arc
   instead of nine resets. A starting pitcher keeps going until he's pulled, and
   his fatigue builds the whole way. The home team can stop early if it's already
   ahead.
2. **The stay.** On contact in play, the batter can *run* (normal baseball) or
   *stay* at the plate — runners advance, the batter keeps hitting, and the contact
   costs a strike. Stays are how runners get driven in. This is why a batting line
   can read multiple hits in one at-bat, and why **batting average can exceed
   1.000**.
3. **Jokers.** Three tactical pinch-batters a manager can drop into any lineup
   spot, once per joker per time through the order. They add offense if used well.
4. **The Walk-Back.** A home run sends the hitter to *third base* as a live bonus
   runner — he stays there until driven in or put out.
5. **Declared Seconds.** A manager can end his regulation half early and *bank* the
   unused outs for a second trip through the lineup later. A real risk/reward lever.
6. **Variance is the design.** O27 runs *hot* — ~30+ runs a game with wild
   game-to-game swings — on purpose. There is no "correct" run total to tune
   toward. If a number looks high, that's usually the sport, not an error.

Two pieces of flavor worth knowing because they show up everywhere: every pitcher
throws **sidearm or submarine** (a structural conceit that reshapes pitching
value), and pitching staffs are organized as a **nautical crew** (Helms, the
Changes, Bosun, Skidder, Anchor, Pilot) rather than a rotation/bullpen — see
[the crew feature report](feature-pitching-crew-roles.md).

## 3. The two things you'll read most: a box score and a stat line

**Reading a box score.** Open any game page. It looks like a newspaper box score,
with one twist: pitching is measured in **outs**, not innings (there are no
innings). You'll see `OUT`, `OS%` (the share of the team's 27 outs a pitcher
recorded), and arc-aware figures instead of ERA-as-headline. Runs given up late in
the 27-out arc are weighted more heavily because there's less game left to answer
them.

**Reading a stat line.** The unfamiliar columns are deliberate. The ones you'll hit
first:

| Stat | What it is |
|---|---|
| **PAVG** | hits per *plate appearance*, bounded 0–1 — reads like old batting average |
| **BAVG** | hits per *at-bat* — can exceed 1.000 because of multi-hit (stay) ABs |
| **Δ** | BAVG − PAVG — how much extra a hitter squeezes out of stays |
| **RAD** | graded runner-advancement value (moving a runner to 3rd > to 2nd) |
| **OUT / OS%** | a pitcher's outs recorded / share of the team's 27 |
| **wERA / xRA** | arc-weighted and expected runs allowed (ERA is context here, not the headline) |

You do not need to memorize these. **Every page links to the in-app Glossary (under
the Stats menu, [§5](#5-a-tour-of-the-web-app)), and the full formulas with code
pointers are in [`stats-reference.md`](stats-reference.md).** When a column confuses
you, that's where to look.

## 4. Running it locally

Requires Python 3.11+. Everything is dependency-light standard-library Python; there's
nothing to compile.

```bash
pip install -e .
python o27v2/manage.py resetdb        # build DB, seed the default 30-team league + schedule
python o27v2/manage.py sim 2430       # simulate a full 162-game season (2430 games)
python o27v2/manage.py runserver      # web app on http://localhost:5001
```

Open `http://localhost:5001` and start clicking. That's the whole loop: **seed →
sim → browse.** You don't have to sim a full season first — sim a few hundred games
and the pages already fill in.

Other commands you'll actually use:

```bash
python o27v2/manage.py configs                 # list league sizes (8 → 56 teams, tiered)
python o27v2/manage.py resetdb --config 16teams  # seed a different size
python o27v2/manage.py sim 200                  # sim just the next N games
python o27v2/manage.py smoke                    # 10-seed engine sanity check, no DB
make test-invariants                            # the stat-sanity gate (should pass)
```

If a stat ever looks *impossible* (not just high — impossible, like negative or
out-of-bounds), `make test-invariants` is the tool that catches it. It encodes
every mathematically-impossible-stat bug the project has shipped before.

## 5. A tour of the web app

The top navigation groups everything. Here's what each group is for and where to
start in it. (Same layout on the [live site](https://hybrid-baseball.fly.dev) and
your local server.)

### Games — *Scores · Gazette · Standings · Schedule · Playoffs*
Your home base. **Scores** is today's slate and recent finals. **Standings** is one
wide sortable table per league. Click any game to get its box score, play-by-play,
and a Markdown export (the `export.md` link) built for pasting into an LLM. The
**📰 [Gazette](feature-gazette.md)** turns a day's games into a voiced, news-style
writeup — pick a date and a voice and it generates copy from the real results.

### Players — *Players · Teams · Compare · Free Agents*
**Players** is a searchable, paginated index filterable by team/position. Each
player page is a career sheet with archetype, attributes, salary, and splits.
**Compare** puts players side by side; **Teams** drills into a roster (including its
pitching **crew** on the rotation page).

### Stats — *Leaders · Stat Browser · Analytics · Distributions · Glossary*
**Leaders** is top-N per stat. **Stat Browser** (the Fangraphs-style *almanac*) is
the deep one: sortable heatmap leaderboards with downloadable CSV/JSON bundles.
**Analytics** and **Distributions** show league-wide shapes. **Glossary** is your
decoder ring — start here whenever a column is unfamiliar.

### League — *Overview · Transactions · Auction · Youth · Pro World Cup · College · Economy · Financials · History · Seasons · Hall of Fame · Almanac*
Everything that wraps the games. **Transactions** logs every roster move.
**Auction**, **Economy**, **Financials**, and **Free Agents** are the guilder
economy. **[College](aar-college-tier.md)** (64 NCAA-inspired programs) and
**Youth** are the talent pipeline feeding the pros. **Pro World Cup** is the
international event. **Hall of Fame** and **History/Seasons** are the long-memory
views across multiple simulated seasons.

### Manage — *Engine Settings · Saves · New League · Universe*
The control room. **Saves** holds multiple named leagues that coexist
independently. **Engine Settings** (the *Engine Tunables* dashboard) exposes every
engine constant with presets, a randomizer, and a saved-environment library — this
is how you reshape the sport itself (next section). **New League / Universe** build
fresh worlds.

There's also a **Sim** button in the top bar (Sim Today / Week / Month / Season) so
you can advance the calendar without dropping to the CLI.

## 6. Make it your own: tuning the sport

The single most powerful feature, and the easiest to miss. **Manage → Engine
Settings** lets you change the constants the engine reads live — contact rates,
fatigue, park geometry, weather, everything — and the change takes effect on the
next sim. Save a configuration as a named environment and it's labelled by the run
environment it produces.

You don't have to know the knobs. [`tuning-guide-for-llms.md`](tuning-guide-for-llms.md)
is a **self-contained document you paste into any capable LLM** along with a
plain-English description — *"a deadball pitcher's duel," "a launch-angle home-run
circus," "a speed-and-steals track meet"* — and it hands back a tuning blob you drop
straight into the Engine Settings dashboard. That's the intended workflow for
building your own leagues. There are also built-in presets (including
softball-derived scoring) if you'd rather start from one of those.

## 7. Where to go deeper

You've now got the whole surface. The deep ends, in rough order of usefulness:

- **The full rules** — [README → The Rules](../README.md#the-rules).
- **Why O27 exists / why not just five innings** — [the blog post](blog-o27-vs-five-inning-baseball.md).
- **Every stat, with formulas** — [`stats-reference.md`](stats-reference.md).
- **How the project got here** — [`project-trajectory.md`](project-trajectory.md):
  the month-long arc and a dated changelog.
- **Every design decision** — the ~85 After-Action and feature reports in
  [`docs/`](.). Each records the ask, the tradeoffs, and the verification numbers.
  The trajectory doc is the reading order through them.
- **Deploying it** — [`DEPLOY.md`](../DEPLOY.md) (Fly.io).

## 8. FAQ for the confused newcomer

**A batting average over 1.000 — is that broken?** No. Stays let one at-bat produce
multiple hits. `BAVG` (per at-bat) can exceed 1.000; `PAVG` (per plate appearance)
is the bounded-0–1 stat that reads like old batting average.

**Why is a game 30 runs?** O27 is variance-first and runs hot by design — deeper
lineups, no inning resets, and stays moving runners for free. There's no target run
total. See [§2](#2-the-sport-in-90-seconds), point 6.

**Where are innings in the box score?** Gone. Pitching is measured in **outs** and
**OS%**. One game is 27 outs per side.

**What's a "joker" / "stay" / "Walk-Back" / "Declared Seconds"?**
[§2](#2-the-sport-in-90-seconds) is the short version; the
[README rules](../README.md#the-rules) are the long one.

**What's with the boat words on the pitching staff?** That's the
[nautical crew](feature-pitching-crew-roles.md) — O27's name for staff roles, since
a game is one continuous voyage rather than a rotation of innings.

**Can I break it by tuning?** The Engine Settings randomizer is guard-railed, and
`make test-invariants` will tell you if a configuration produced impossible stats.
You can make the sport weird; you can't easily make it incoherent.

**I want the absolute minimum to see something interesting.**
`pip install -e . && python o27v2/manage.py resetdb && python o27v2/manage.py sim 300 && python o27v2/manage.py runserver`,
then open `http://localhost:5001`, click any final score, and read the box.
