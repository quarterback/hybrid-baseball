# Ron Bronson
## SuperInnings
Baseball restructured into one continuous 27-out half per side — a full simulated world with its own stats, its own economy, and its own history.

Designer & sole engineer · 2026 · hybrid-baseball.fly.dev ↗

Cricket figured something out with T20: keep the sport, compress the structure, and a new strategic shape falls out of it. I'd been carrying around the baseball version of that thought for years — what happens if you keep all 27 outs but play them as a single arc? Each side bats once, straight through, until they record 27 outs. The starter takes the mound at out 0 and pitches as long as he can hold the line. The top of the order comes up six or seven times. A marathon and a sprint at once.

I'd built a fake computer sport before ([Viperball](https://github.com/quarterback/viperball)), so I knew the move: write the rules down, build the engine, and let the math tell you whether the sport is worth caring about. Months of iteration say it is. Pitcher fatigue accumulates across one continuous voyage, so manager decisions carry real weight. Lineup construction becomes a question of who gets the most plate appearances across the arc. And the players modern baseball has marginalized — the contact specialist, the workhorse, the junkballer, the knuckleballer — find real value here. Every pitcher in this sport throws sidearm or submarine. That's just the sport's history.

Under the hood it's a Python engine. The UI reveals a vast world.

### The sport

A few rules do most of the work. Lineups run 12 deep, and every roster carries three **jokers** — tactical hitters a manager can drop into any spot in the order, once per trip through the lineup. Batters who make contact can choose to **stay** at the plate, trading a strike to advance their runners — a mechanic borrowed from pesäpallo, and the lever skilled hitters pull all game. A home run triggers the **Walk-Back**: the hitter returns to third as a live bonus runner who stays in play until he's driven in or put out. And a manager can end his half early and bank the unused outs — **Declared Seconds**, a risk/reward call that buys a second trip through the lineup later in the game.

Pitching staffs are organized as a **nautical crew** rather than a rotation and bullpen, because the game is one continuous voyage: the Helms steers it out, the watch changes relieve him, the Bosun carries bulk, the Anchor holds late, the Pilot lands the final outs. Roles are derived from skill and relative to the staff — the same arm is a Helms on a thin team and a Skidder on a deep one. The pitch catalog runs 17 deep, and the sport's structure brings back pitches baseball left behind: the knuckleball, the eephus, the spitter, the screwball.

### Stats that fit the sport

A single at-bat can contain multiple hits here, so the entire stat suite is recalibrated from first principles. **PAVG** reads the way batting average used to; **BAVG** can climb past 1.000 for stay-heavy hitters, and the gap between them measures second-chance productivity. Innings pitched gives way to outs recorded and **outs share**. **wERA** weights runs by where in the 27-out arc they were surrendered — a run in the final stretch hurts more, because less arc remains to answer it. **Decay** measures how a pitcher's strikeout rate erodes across the voyage, a skill this sport makes measurable. The analytics layer — linear weights, expected wOBA, run expectancy, Pythagorean projection — is re-derived from league data every cycle, so the math always belongs to this universe.

### The world on top

The engine grew a league, and the league grew a civilization. Thirty teams play 162-game seasons (configs run from 8 teams to a 56-team tiered pyramid with promotion and relegation). Players have salaries in *guilders*, traded on live auction markets. A motivation-driven trade engine pits rebuilders against win-now buyers at the deadline. Players age, get injured, develop, and retire; a youth pipeline and a 64-program college tier feed the pros; a 48-team youth World Cup and a Pro World Cup crown international champions. There's a gated Hall of Fame, career leaderboards, end-of-season awards, named managers whose personalities change in-game outcomes, and a Fangraphs-style almanac for browsing it all.

Every ballpark has its own fence geometry, including pre-modern shapes. Weather mutates batted-ball physics game by game. A runtime tuning dashboard exposes every engine constant with era presets and a saved-environment library — and there's a self-contained guide you can hand to an LLM to generate a whole play style from a plain-English description: "a deadball pitcher's duel," "a launch-angle circus."

### How it works

Every game is seed-deterministic: same seed, same roster state, same result. A stat-invariant suite runs nine assertions before any release, catching every mathematically-impossible-stat bug the project has ever shipped. Roughly 85 after-action reports in the repo document each system as it landed — the reasoning, the tradeoffs, the verification numbers. The design history lives in the open.

### The architecture

The core is Python with SQLite underneath and a Flask app on top. It simulates a full 30-team, 162-game season in minutes and runs anywhere Python runs. The web UI is a window into an engine that exists independent of it — the same data exports as a static almanac, a newspaper-style box score, and Markdown built for LLM ingestion.

### What you can do right now

- **Browse a season** — standings, schedules, playoff brackets, awards.
- **Read a game** — pitch-by-pitch play-by-play, newspaper box scores, per-game pitcher arcs.
- **Study the players** — archetypes, career pages, scouting grades, salary valuations.
- **Follow the economy** — auctions, trades, waivers, front-office personalities.
- **Explore the almanac** — sortable heatmap leaderboards across every season, with full CSV/JSON exports.
- **Tune the universe** — adjust engine constants, apply era presets, and build peer leagues with their own styles.
- **Read the methodology** — the full stats reference, the design AARs, and the month-long project trajectory.

Explore it at hybrid-baseball.fly.dev ↗

Reach me at contact@ronbronson.com · more of my work lives at ronbronson.com.
