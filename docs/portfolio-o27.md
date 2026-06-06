# Super Innings — portfolio listing

Source copy for the cargo.site project page. Two cuts: a short index tile and
the full listing. Placeholders are in ‹brackets›.

---

## Short cut (index tile — 3–4 sentences)

**Super Innings — a fictional sport, fully simulated.** Baseball with one
structural change — each side bats until 27 outs, no innings — carried all the way
to its consequences: a deterministic engine, a statistics suite rebuilt from first
principles, an economy, a youth pipeline, and a season-browsing interface for
reading what the rules produce. Then a spectator layer on top — fantasy, a
sportsbook, and AI radio broadcasts, all read-only consumers of the sim's truth.
Hand-rolled and dependency-light — no NumPy, no pandas; designed and built solo,
with every meaningful change logged as a written decision record.

‹Live → superinnin.gs› · ‹Source›

---

# Super Innings

*An O27 baseball simulator.*

**A fictional sport, fully simulated.** Baseball with one structural change — each
side bats until it records 27 outs, no innings — carried all the way to its
consequences: a deterministic engine, a statistics methodology rebuilt from first
principles because the old one breaks under the new rules, an economy, a youth
pipeline, a season-browsing interface for reading what the rules actually produce —
and, on top of all that, the apparatus that makes a real sport a sport: a fantasy
and betting metagame and an AI broadcast booth.

‹Role: Designer & sole engineer› · ‹2026› · Live →
[superinnin.gs](https://superinnin.gs) ·
Source → ‹github.com/quarterback/hybrid-baseball›

---

## Overview

Super Innings takes one idea — apply cricket's T20 structural bet to baseball, not
by shortening the game but by restructuring the inning — and builds the entire
apparatus needed to find out whether it's any good. Remove innings. Let each side
bat until 27 outs. A marathon and a sprint at once.

Everything else falls out of that move: pitcher fatigue accumulates across one
continuous arc instead of resetting; top-of-order hitters get six or seven plate
appearances; manager decisions stop hiding behind inning resets; roster
construction has to value the workhorse over the short-burst reliever.

The project is the proof. It's a working simulation, an analytics suite derived
from each league's own data rather than borrowed from MLB constants, a set of
reading surfaces designed for a human — or an LLM — to interpret, and a spectator
economy built on top of all of it. Less a game than an argument, with the math
built to settle it.

It is also, deliberately, a demonstration of how I take a loose idea, pressure-
test it, and build the full stack of systems required to know whether it holds.

---

## The design

Three borrowed and invented mechanics that turn out to belong together:

- **The continuous 27-out half-inning** (the T20 ethos) — no resets, so fatigue,
  lineup depth, and bullpen management all become load-bearing.
- **The "stay"** (from pesäpallo) — on contact in play, the batter may stay at
  the plate and advance runners at the cost of a strike. The lever a skilled
  hitter keeps over an at-bat; the source of most of the sport's RBI. I didn't
  invent it — I *recognized* it. Collapse the game into one half and a question
  opens up (what lever does a hitter still have over an at-bat?); pesäpallo had
  already answered it. Knowing which borrowed part fits the hole the new rule
  cuts is the rarest input here.
- **The joker** — three tactical pinch-batters a manager can drop into any spot,
  once per joker per time through the order. No overall cap, so the same joker
  comes back as the lineup cycles. Offense left on the table if unused.

And two mechanics that show how far the design goes once you pull the thread:

- **The Walk-Back** — a home-run hitter is returned to third base as a live,
  persistent bonus runner, scored or put out like any other.
- **Declared Seconds** — a manager can end his regulation half early and *bank*
  the unused outs for a second trip through the lineup later. A genuinely novel
  risk decision with no equivalent in the real game. Regulation only — not
  available in extras, which must be played out as normal 3-out frames.

The whole sport runs on a structural conceit — every pitcher throws sidearm or
submarine — which reshapes the value of velocity, deception, and arsenal depth,
and brings the knuckleballer and the 38-year-old workhorse back from the margins.

---

## What it does differently

The rules compound into measurably different baseball, and the engine produces
all of it without special-casing:

- A higher run environment and deeper lineups (12 hitters + 3 jokers).
- **Stamina as the premier pitching attribute** — a B+ arm worth 24 outs beats a
  dominant reliever worth 6.
- **Contact specialists with real value**, because every contact event spends a
  strike.
- **Manager decisions that bear weight** — jokers, hooks, Declared Seconds, and
  lineup order all move outcomes; named managers carry attributes that change
  games.
- Tied games go to **3-out extra innings** — a deliberate revision after the
  original five-out super-inning let offenses run up double-digit frames.

There is no runs-per-game target. Super Innings is **variance-first**: whatever the
mechanics produce *is* the run environment, and the rate stats are observed
outputs, not knobs. I tore out an early R/G "target band" the moment I realized
aiming at it was a category error — and let fatigue, not a dial, decide the league.
It settled near 33 runs a game with a standard deviation north of nine. Wild, by
design.

---

## What I built

A simulation that long ago outgrew "rules engine + box score."

**The engine** — pitch-by-pitch probabilistic resolution; batted-ball physics
(exit velocity / launch angle / spray); a 17-pitch catalog with per-pitcher
repertoires, count-aware usage, and platoon effects; the stay mechanic;
fatigue that ramps across the arc; baserunning with its own event layer
(TOOTBLAN, pickoffs, hit-and-run, sacrifice bunts); a full defensive model
(range, errors, shifts with adaptability, DRS/dWAR, position-player pitching);
a skill-derived, team-relative **pitching crew** (a nautical staff — Helms,
Changes, Bosun, Skidder, Anchor, Pilot — re-derived on any roster change); and a
manager AI that selects starters and deploys relievers by where in the 27-out arc
the call lands.

**Parks, weather, geometry** — every ballpark carries an asymmetric fence
profile, a shape archetype (pre-modern revivals, converted cricket ovals, tiny
urban bandboxes), and architectural quirks; a per-game weather draw mutates
batted-ball physics on the margin. Field geometry is a first-class, tunable
input — the same swing is a souvenir in one park and a long out in another.

**Analytics, re-derived** — because plate appearances and at-bats diverge
structurally, batting average breaks, so the whole catalog is rebuilt:
plate-average and a second-chance productivity signal; an arc-weighted ERA;
expected runs allowed and weighted on-base from linear weights; run expectancy
by base-out state, BaseRuns with cluster-luck decomposition, win-probability
added, park-adjusted plus-stats, and a Pythagorean projection — **all refit
from each league's own data every render cycle**, never borrowed from MLB. A
fictional sport has no constants to import; it has to generate its own ground
truth and then measure it.

**The world around the sport** — a guilder (ƒ) economy with attribute-derived
salaries, Indian-numbering display and currency toggles, and IPL-style live
auctions; a motivation-driven trade engine with front-office personalities;
tiered injuries and aging curves; a youth-development pipeline with a 48-team
World Cup and a 64-program college tier feeding it; configurable leagues from 8 to
56 teams with promotion/relegation; playoffs, awards, a Hall of Fame (gated league
hall plus criteria-based team halls), career multi-season leaderboards, and a
season archive. Concurrent save files let multiple leagues coexist.

**Tunable by design** — a runtime engine-tunables dashboard exposes every
constant, ships era and stylistic presets, a guard-railed randomizer, and a
saved-environment library, with a one-click benchmark that auto-labels the run
environment a tuning produces. A peer-universe builder composes co-equal leagues
whose styles emerge from infrastructure — field size, climate, and the talent
pipeline each region draws on — rather than from national cliché; a region's
home-run rate can range 2.5× from geometry and talent alone, with no rule
changes. A self-contained guide lets anyone paste a plain-English style ("a
deadball pitcher's duel", "a launch-angle circus") into an LLM and get a working
tunables blob back.

**The reading surfaces** — a Baseball-Reference-style browsing app; a
Fangraphs-style "almanac" stats explorer (its own static-site generator and a
live blueprint, with sortable heatmap leaderboards and downloadable data
bundles); a newspaper-style box score with Markdown export; and the **Gazette**, a
read-only, LLM-ready news desk that turns a day's slate into a voiced writeup.

---

## The spectator economy

The most recent turn is the one I'd point to first now, because it's where the
project stopped being only an instrument. Real sports aren't kept alive by the
players — they're kept alive by the apparatus around the field: fantasy leagues,
betting markets, broadcast booths. That apparatus is exactly how a non-playable
simulation becomes something a person can be a *fan* of. So I built it.

- **CapSpace** — a fantasy-and-betting metagame (~7,300 lines). Six modes: a
  Walk-Back home-run game, a pitching game on the save-equivalent "Finisher" stat,
  daily-fantasy slates, streak picks, 5×5 roto categories, and draft-once best
  ball — plus a sportsbook with moneylines and run totals. One persistent wallet
  per save, buy-ins out and winnings in, a Rookie→Hall-of-Famer career ladder. The
  lineup math is exact, not greedy: best ball solves the optimal lineup by dynamic
  programming and validates draft coverage with bipartite matching — because a
  fictional player has no name recognition, so recent form and real stats are the
  only thing a daily-fantasy player can actually read.
- **o27audio** — an AI broadcast booth. Press "Listen" on a game and a two-host
  call is generated from the persisted pitch-by-pitch log: a model scripts the
  play-by-play and color, a TTS model voices the two announcers, stdlib audio
  stitching renders the clip. A per-game show, a league roundup, and a worker that
  auto-narrates new game-days.

The architecture is the point, and it's the cleanest expression of the whole
project's spine: **neither layer ever simulates anything.** They are strictly
read-only consumers of the sim's persisted, seed-deterministic truth. The betting
market prices games it has no power to alter; the broadcaster narrates a game that
already happened, exactly as it happened. The simulation is the source of truth,
and everything else is parasitic on it — which is why an entire spectator economy
could grow in days without touching the engine's core schema.

---

## The engineering

Deliberately dependency-light. The entire simulation, economy, and analytics
stack is hand-rolled.

- **Language / runtime** — Python 3.11+
- **Web** — Flask + Jinja2, server-rendered, with vanilla JavaScript on the
  client; WCAG-AA light/dark theming with a toggle, grouped navigation, and a
  mobile-responsive layout
- **Persistence** — SQLite, with an idempotent migration layer and concurrent
  save files
- **Math** — pure standard library; no NumPy, no pandas, no ML framework — every
  formula, probability table, and calibration constant is written out and owned
- **AI layer** — the Gazette and the audio booth call an LLM for *script*, never
  for *truth*; the numbers they speak about all come from the deterministic engine
- **Performance** — a whole-page HTML result cache keyed on a cheap data
  fingerprint (the leaders page went ~2548 ms → 2.3 ms, ~1100×); the bottleneck
  was never the database but per-load recompute under contention
- **Deploy** — Docker on Fly.io with a mounted data volume

The constraint is the point: nothing important hides behind a library default.

---

## How I work

The thing I'd point a collaborator to isn't a feature — it's the discipline
around the features.

- **Every meaningful change ships with an After-Action Report.** Over a hundred
  AARs record the ask, the design tradeoffs, the verification numbers, and the
  honest open gaps. The design history lives in writing, not in my head.
- **Calibration over assertion.** New environments aren't trusted; they're
  simulated and measured. A nine-assertion invariant suite has to pass before any
  release — it catches every mathematically-impossible-stat bug the project has
  ever shipped.
- **Willing to revise the rules when the data argues.** Super-innings became
  3-out extra innings; the Walk-Back was rebuilt from a one-pitch phantom into a
  persistent runner — both driven by a box score that didn't read right.
- **Refusing the easy version.** When the worldwide-leagues feature risked
  collapsing into national-character clichés, I rebuilt it so a region's style
  emerges from infrastructure and economics, and verified the result.
- **Paying the unglamorous tax.** Two divergent algorithms had been disagreeing
  about which pitcher took the loss on 30 of 120 final scores; I collapsed them
  into one canonical path (zero mismatches). That correctness work is what earns
  the right to make the fun claims.
- **Designing for the reader.** The front end and the spectator layer exist to
  surface what the rules produce for a human or an LLM to interpret.

---

## By the numbers

The ones that describe the thing, not the typing:

- **Full 162-game seasons, seed-reproducible end to end** — every stat row traces
  back to a simulated plate appearance.
- **16 league configurations** (8–56 teams), with promotion/relegation tiers.
- A **17-pitch catalog** and a **9-tier talent ladder** every attribute rolls
  against independently.
- **~33 runs per game, standard deviation north of 9** — an *emergent* run
  environment, not a tuned target.
- **Six fantasy/betting game modes** and AI audio broadcasts, none of which ever
  re-simulate.
- **100+ After-Action Reports** and a **9-assertion invariant suite** that has to
  pass before any release.
- A whole-page result cache that took the leaders page **~1100× faster**
  (2548 ms → 2.3 ms) — the bottleneck was recompute, not the database.

---

## A note on scope

Super Innings is a thought experiment with a working build, not a commercial game.
There's still no manager-mode play — you don't take the field or run a club. But
the recent work complicates the old "it's only an instrument" framing in a way I
like: by growing the fantasy, betting, and broadcast apparatus that real sports
accrete, it became a sport you can be a *fan* of without being a player. The
metagame is the way in.

That's the honest framing, and the one I'd defend: the value here is the systems
thinking, the analytical rigor, and the willingness to carry an idea all the way to
the point where the data can answer the question — and then keep going, until the
thing has an audience.

---

‹Live build› · ‹Source› · ‹Design notes / AARs›
