# O27 — portfolio listing

Source copy for the cargo.site project page. Two cuts: a short index tile and
the full listing. Placeholders are in ‹brackets›.

---

## Short cut (index tile — 3–4 sentences)

**O27 — a fictional sport, fully simulated.** Baseball with one structural
change — each side bats until 27 outs, no innings — carried all the way to its
consequences: a deterministic engine, a statistics suite rebuilt from first
principles, an economy, a youth pipeline, and a season-browsing interface for
reading what the rules produce. ~59,000 lines of hand-rolled, dependency-light
Python; designed and built solo, with every meaningful change logged as a
written decision record.

‹Live → hybrid-baseball.fly.dev› · ‹Source›

---

# O27

**A fictional sport, fully simulated.** Baseball with one structural change —
each side bats until it records 27 outs, no innings — carried all the way to its
consequences: a deterministic engine, a statistics methodology rebuilt from
first principles because the old one breaks under the new rules, an economy, a
youth pipeline, and a season-browsing interface for reading what the rules
actually produce.

‹Role: Designer & sole engineer› · ‹2026› · Live →
[hybrid-baseball.fly.dev](https://hybrid-baseball.fly.dev) ·
Source → ‹github.com/quarterback/hybrid-baseball›

---

## Overview

O27 takes one idea — apply cricket's T20 structural bet to baseball, not by
shortening the game but by restructuring the inning — and builds the entire
apparatus needed to find out whether it's any good. Remove innings. Let each
side bat until 27 outs. A marathon and a sprint at once.

Everything else falls out of that move: pitcher fatigue accumulates across one
continuous arc instead of resetting; top-of-order hitters get six or seven plate
appearances; manager decisions stop hiding behind inning resets; roster
construction has to value the workhorse over the short-burst reliever.

The project is the proof. It's a working simulation, an analytics suite derived
from league data rather than borrowed from MLB constants, and a set of reading
surfaces designed for a human — or an LLM — to interpret. Less a game than an
argument with the math built to settle it.

It is also, deliberately, a demonstration of how I take a loose idea, pressure-
test it, and build the full stack of systems required to know whether it holds.

---

## The design

Three borrowed and invented mechanics that turn out to belong together:

- **The continuous 27-out half-inning** (the T20 ethos) — no resets, so fatigue,
  lineup depth, and bullpen management all become load-bearing.
- **The "stay"** (from pesäpallo) — on contact in play, the batter may stay at
  the plate and advance runners at the cost of a strike. The lever a skilled
  hitter keeps over an at-bat; the source of most of O27's RBI.
- **The joker** — three tactical pinch-batters a manager can drop into any spot,
  any time, as often as he likes. Offense left on the table if unused.

And two mechanics that show how far the design goes once you pull the thread:

- **The Walk-Back** — a home-run hitter is returned to third base as a live,
  persistent bonus runner, scored or put out like any other.
- **Declared Seconds** — a manager can end his regulation half early and *bank*
  the unused outs for a second trip through the lineup later. A genuinely novel
  risk decision with no equivalent in the real game.

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

---

## What I built

A simulation that long ago outgrew "rules engine + box score."

**The engine** — pitch-by-pitch probabilistic resolution; batted-ball physics
(exit velocity / launch angle / spray); a 17-pitch catalog with per-pitcher
repertoires, count-aware usage, and platoon effects; the stay mechanic;
fatigue that ramps across the arc; baserunning with its own event layer
(TOOTBLAN, pickoffs, hit-and-run, sacrifice bunts); a full defensive model
(range, errors, shifts with adaptability, DRS/dWAR, position-player pitching);
and a manager AI that selects starters and deploys relievers by where in the
27-out arc the call lands.

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
from each league's own data every render cycle**, never borrowed from MLB.

**The world around the sport** — a guilder (ƒ) economy with attribute-derived
salaries, Indian-numbering display and currency toggles, and IPL-style live
auctions; a motivation-driven trade engine with front-office personalities;
tiered injuries and aging curves; a youth-development pipeline with a 48-team
World Cup; configurable leagues from 8 to 56 teams with promotion/relegation;
playoffs, awards, a Hall of Fame (gated league hall plus criteria-based team
halls), career multi-season leaderboards, and a season archive. Concurrent save
files let multiple leagues coexist.

**Tunable by design** — a runtime engine-tunables dashboard exposes every
constant, ships era and stylistic presets, a guard-railed randomizer, and a
saved-environment library, with a one-click benchmark that auto-labels the run
environment a tuning produces. A peer-universe builder composes co-equal leagues
whose styles emerge from infrastructure — field size, climate, and the talent
pipeline each region draws on — rather than from national cliché; a region's
home-run rate can range 2.5× from geometry and talent alone, with no rule
changes.

**The reading surfaces** — a Baseball-Reference-style browsing app; a
Fangraphs-style "almanac" stats explorer (its own static-site generator and a
live blueprint, with sortable heatmap leaderboards and downloadable data
bundles); and a newspaper-style box score with Markdown export for LLM-readable
game writeups.

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
- **Tooling** — a peripheral TypeScript workspace for design mockups and
  codegen, independent of the Python core
- **Deploy** — Docker on Fly.io with a mounted data volume

The constraint is the point: nothing important hides behind a library default.

---

## How I work

The thing I'd point a collaborator to isn't a feature — it's the discipline
around the features.

- **Every meaningful change ships with an After-Action Report.** Nearly sixty
  AARs record the ask, the design tradeoffs, the verification numbers, and the
  honest open gaps. The design history lives in writing, not in my head.
- **Calibration over assertion.** New environments aren't trusted; they're
  simulated and measured. A nine-assertion invariant suite has to pass before
  any release — it catches every mathematically-impossible-stat bug the project
  has ever shipped.
- **Willing to revise the rules when the data argues.** Super-innings became
  3-out extra innings; the Walk-Back was rebuilt from a one-pitch phantom into a
  persistent runner — both driven by a box score that didn't read right.
- **Refusing the easy version.** When the worldwide-leagues feature risked
  collapsing into national-character clichés, I rebuilt it so a region's style
  emerges from infrastructure and economics, and verified the result.
- **Designing for the reader.** The whole front end exists to surface what the
  rules produce for a human or an LLM to interpret, not to gamify it.

---

## By the numbers

~59,000 lines of Python · ~60 engineering AARs · 271 commits across two weeks of
focused iteration · 53 server-rendered views · 16 league configurations (8–56
teams) · a 17-pitch catalog · full 162-game seasons, seed-reproducible end to
end.

---

## A note on scope

O27 is a thought experiment with a working build, not a commercial game. There's
no manager-mode play — it's an instrument for studying what the rules produce.
That's the honest framing, and the one I'd defend: the value here is the systems
thinking, the analytical rigor, and the willingness to carry an idea all the way
to the point where the data can answer the question.

---

‹Live build› · ‹Source› · ‹Design notes / AARs›
