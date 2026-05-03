# O27: A Cricket-Influenced Variant of Baseball, and Why "Just Play Five Innings" Wasn't It

I've had a particular daydream for years: what would a baseball / cricket hybrid look like? Not as a thought experiment — as a real game you could actually play. Or, since I'm not building a stadium, a real game you could simulate well enough to know if it'd hold together as a sport.

My first instinct was the obvious one: **just shorten the game**. A baseball game becomes "official" in the rules if it gets rained out after five innings. So in principle, you could play a whole season as if every game rained out at the optimal moment. Five-inning baseball, about an hour and a half a game, two games in the time it takes to play one regulation game. T20-flavored MLB. The cricket-shortening pattern grafted onto baseball's calendar.

I built something else. The thing I built is called O27. The premise of this post is that O27 is a more interesting answer to "what would baseball look like with cricket sensibilities" than the five-inning version is — and the reason why says something about what cricket actually contributes when you graft it onto baseball. It's not the time. It's the concentration.

A clarification before going further: **O27 is meant to be a variant of baseball, not a different sport**. The same way softball is a variant of baseball, the same way T20 is a variant of cricket. It uses baseball's batter/pitcher/fielding structure, baseball's positional roster, baseball's strike zone, baseball's whole grammar. What it borrows from cricket is one specific structural decision — the single-innings-per-side concentration — and what it borrows from pesäpallo (Finnish baseball) is a tactical mechanic for what to do with that concentration. Everything else is baseball. This is the writeup of the simulation project I built to test if the idea has legs, what surprised me, and where the engine has earned out.

## The naive idea: 5-inning baseball, 300-game season

Take five innings as the unit. Two five-inning games back-to-back is about three hours of stadium time — same as one MLB game today. So you could plausibly run **doubleheaders as default**, every team plays a single three-hour event per day, and across a 180-day calendar you can fit a 300-game regular season without much trouble.

This sounds great. The math is interesting:

- **Counting stats explode.** A 30-HR hitter at the same per-PA rate becomes a ~42-HR hitter over 815 PAs. Numbers feel bigger because they are bigger.
- **Rate stats converge to truth.** This is the real argument for it. With ~9,000 PAs per team per season, statistical noise basically vanishes. Standings sort by true team quality with the precision of Premier League soccer, not the variance of the NFL. End-of-season leaderboards reflect who was actually best, not who got hot in September.
- **Rotation construction inverts.** A starter throws ~75 pitches in 5 IP and could plausibly start every fourth day. So you carry an 8–10 man rotation. Bullpens shrink to long-relief pieces. The closer position dies. Every roster is starter-heavy in a way no real baseball league has ever been.
- **Roster rotation becomes mandatory.** No position player can play 270 of 300 games at major-league intensity for 240 days without breaking. So every position has a 2-3 man platoon. This is closer to how cricket squads handle a long T20 season than how MLB handles its bench.

That's a real product. It would draw differently than MLB — stat-junkie heaven, slow-news-day kryptonite, daily doubleheader rhythm that compresses fan time commitment instead of expanding it. I wouldn't dismiss it.

But here's what stopped me when I started building toward it: **5-inning baseball is just MLB in less time**. It's a duration variant. Same skill optimization, same per-AB decisions, same archetypes, just fewer per game. The strategic depth is actually *less* than MLB — fewer pitching-change decisions, no late-inning bullpen chess, the closer position evaporates. It compresses what's already there. It doesn't ask any new questions about what baseball *is*.

It's basically baseball with an asterisk. The asterisk is interesting. But it's not a different game.

## The cricket question I was actually asking

When I think about what makes cricket *cricket* — what I'd actually want to graft into a baseball variant — none of it is "shorter game." T20 is shorter than test cricket, but T20 didn't invent cricket; cricket invented T20. The thing that's distinctively cricket isn't its duration. It's the structure of how innings work.

A cricket innings is **continuous**. You don't bat a half-inning, sit, bowl a half-inning, sit, repeat nine times. You bat your entire allocation in one go. The bowling side stays out there the whole time. Bowlers tire across overs; batters stack up runs in continuous spells; a great innings is a *concentration event*, not a series of three-out flurries.

That's the part of cricket that was nagging at me. Baseball's nine-inning structure is a built-in rest mechanism for everyone — pitchers get a between-innings break every time their team scores three outs, batters reset their PA windows constantly, the whole game is rhythmic stop-and-start. Cricket has none of that. Cricket is a stamina test. The bowler still hitting his line and length in the 18th over is a different beast than the one in the 1st.

So the actual question I wanted to answer was: **what does baseball look like if you remove the inter-inning rest?** Not less baseball. Different baseball. With cricket's tempo grafted in.

That question turns out to have a *lot* of cascading consequences.

## How O27 actually works

The base rules:

- **One innings per side, of 27 outs.** Visitors bat first until they're retired 27 times. Home team bats second to the same target. Pitchers don't get a between-innings break — they're on the mound until they're pulled, and once pulled they're done.
- **9-batter base lineup + 3 tactical jokers.** The base lineup has eight defensive positions plus the starting pitcher — same 9 as a no-DH MLB lineup. On top of that, each team has **three jokers** available per game: tactical pinch-hitters who can be inserted into any rotation through the order, but each one only ONCE per cycle. Jokers don't take a roster spot, don't take a field position, and don't replace anyone permanently — they bat one PA, then return to the bench. The manager decides whether and when to use them. (More on this below — it's the load-bearing strategic mechanic.)
- **The "stay" rule** (this is the load-bearing one, and it's the most pesäpallo-influenced thing in the engine — see below). When the batter puts a ball into the field of play and runners are on, the batter chooses *run* or *stay*. Run resolves the AB normally. Stay credits the batter with a hit, lets the runners advance per the fielding play, and uses one strike from the batter's three-strike budget. The count carries forward. The batter waits for another pitch.
- **The 3-foul-out rule.** Three fouls in a single AB ends the AB as an out. Not three foul *strikes*; three fouls total, anywhere in the count. Caps the infinite-foul wars that are theoretically possible in MLB.
- **Super-innings tiebreaker.** If the game is tied after regulation, each team picks five batters from their lineup and gets a sudden-death five-out half. Roughly inspired by cricket's super-over but with O27's own scoring shape.
- **Per-27-outs stat scale.** ERA isn't per-9-IP, it's per-27-outs. K/27, BB/27, etc. The whole statistical lexicon translates because one 27-out side is the natural unit.

That's the rule set. Six bullet points. The interesting part isn't any one rule — it's how each cascades from the original premise of "remove the inter-inning rest" plus the secondary premise of "let the batter dance the runners with the count."

## The stay rule in detail (because this is the part I kept getting wrong)

The stay rule is genuinely novel — neither baseball nor cricket has it cleanly — and it took me several iterations to get the engine implementation right. The corrected, definitive rule:

**A batter has a budget of three "strikes worth" of contact opportunities per at-bat.** Called strikes count. Swinging strikes count. Fouls count. Hitting the ball into play and choosing to stay counts. Three strikes ends the AB.

**A ball outside the zone costs nothing.** It's just a ball, it goes in the count. Four balls is a walk. Walks aren't part of the strike budget.

**When the batter hits the ball into play, he chooses run or stay.**
- *Run*: the at-bat ends with the play resolution. A single is a single, a fly out is a fly out. Standard baseball. That contact event spent one strike from the budget — but since the AB ended anyway, the strike count is moot.
- *Stay*: the batter is credited with a hit, runners advance as the fielding play would have advanced them, and one strike is spent. The count carries forward — if you stayed at 1-1, the count is now 1-2.

**Because the count carries**, a batter at 1-2 who stays is at 2 strikes — one more contact event ends the at-bat, hit or out. A batter at 0-0 who stays three times in a row generates three hits and ends the AB at 0-3. **Maximum three hits per AB**, only achievable from a 0-0 start with no called or swinging strikes mixed in.

**A stay-end-on-3-strikes is NOT a batter-out.** The hit was credited, the runners advanced, the RBIs counted. The batter just goes back to the dugout — the AB is over because they spent their runway. No team out is recorded.

**A caught fly is the only thing that turns a stay into an actual out**, because the ball was caught before it reached the field of play. The stay decision is moot at that point.

The strategic shape this produces:

- **The decision to stay is a real cost.** You're trading "this contact event ends the at-bat" for "this contact event extends the at-bat by one more pitch, but I'm now closer to a strikeout." A batter at 1-1 who stays is at 1-2 — one more hittable pitch and the at-bat is over.
- **The replacement for the sacrifice is real but bounded.** You can advance the runner without spending a team out, but you're spending a strike. There's no infinite-extension version of this rule. A batter who chains stays is gambling against his own runway, and the runway is short.
- **The pitcher's calculus changes too.** With a runner on, an aggressive batter might stay on a weak grounder rather than run — so "induced soft contact with runner on first" doesn't necessarily produce a fielder's choice the way it would in MLB. The defensive read on whether the batter is going to run or stay becomes a real on-the-fly tactical question.
- **At 0-2 and 1-2, staying is *good* on weak/medium contact.** You convert what would have been an out into a hit + AB-end + no team out. The 2-strike-stay isn't a punishment — it's the high-leverage moment when stays pay off the most.

This is the part that was nagging me about earlier versions of the simulator: a "fresh 0-0 count" version of stay made multi-hit ABs trivially common, which blew up stat distributions in unrecoverable ways. The actual rule — strike-spent, count-carries — is much more constrained, and the strategic shape that falls out is what makes the rule earn its keep.

## How the rest of O27 cascades from those two premises

**Pitcher fatigue becomes the dominant question.** In MLB a starter throws 6 innings = 18 outs spread across 6 sessions, with full rest between each. In O27 a starter throws 27 outs *consecutively*. There is no break. Stuff doesn't determine endurance — *Stamina* does. A high-Stuff/low-Stamina arm is a one-inning closer in any league; in O27 they get pasted in the 24th out. A high-Stamina/medium-Stuff arm is a workhorse who can grind a complete game. The *Maddux archetype* — soft contact, surgical command, eats outs — is disproportionately valuable. So is the pure stamina horse who'd be a #5 starter in MLB but is an ace here.

**Lineups don't get longer — they get supplemented tactically.** This is the biggest single design decision and it's where O27 diverges most clearly from MLB. The base lineup is the same 9 batters MLB uses (8 fielders + SP). What changes is what sits on the bench: three **jokers** per game, available for the manager to insert into any rotation through the order, each one once per cycle.

A joker is **not** a fourth-DH or a 10th-batter slot. Three properties make them distinct:

1. **They don't take a roster position.** Jokers come from the bench — DH-positioned bats, utility infielders, anyone the manager wants to designate as today's joker pool. They don't displace a fielder.
2. **They don't take a field position.** A joker bats; that's it. They never take the field. The fielding 8 is unchanged.
3. **They're optional per insertion AND once per cycle.** A manager can insert all three jokers in one cycle, none, or somewhere in between. The same joker can't be inserted twice in the same cycle through the order, but they can be inserted again next cycle.

Functionally: each joker is a **pinch-hitter with no cost** — three of them, deployable per the manager's read of the situation, available again next cycle. Pinch hits in the traditional MLB sense still exist as a separate mechanic (you can permanently swap a struggling regular for a fresh bat, who then takes the lineup spot AND a field position), but jokers are the cheaper, repeatable version.

The strategic depth this adds is enormous. A manager who never inserts his jokers is leaving offense on the table; a manager who burns all three in the first cycle has nothing left for the late half. The decision tree at every PA is real:

- Insert the **power joker** (Bonds-tier slugger) when there are men on — multi-run HR is the highest-EV play
- Insert the **contact joker** (Molitor-tier high-OBP) or the **speed joker** (Henderson-tier) with no one on — start a rally, get the leadoff guy on, set up the heart of the order
- Save jokers entirely in a blowout where the leverage doesn't justify the cycle-cost
- Burn all three in one cycle when the leverage is genuinely peak (tied game, 22nd out, runners in scoring position)

This is closer to how a cricket captain manages his bowling rotation than how an MLB manager manages a bullpen. Cricket captains have a finite number of overs from each bowler; they choose which bowler to use against which batter, holding their best for the matchup that matters most. O27 managers do the same with their jokers, but on the offensive side. **Manager AI quality becomes a real differentiator** — a "joker leverage index" measuring the share of insertions that landed in genuinely high-leverage spots is itself a stat. Over a 162-game season the differential between a good O27 manager and a bad one is measurable.

The joker pool itself is flexible per game. The same 3 names don't have to play every day:

- A Rickey Henderson / Lou Brock / Tim Raines speed-specialist
- A traditional Bonds / Ortiz power bat
- A Paul Molitor-style high-OBP technician

…can all be in the joker pool together. Or the manager can use one of the slots to **rest a regular fielder** — your starting catcher gets a "joker day" where he bats but doesn't squat, while a backup catches the field. None of this is reachable from MLB's fixed lineup-position model. And because jokers aren't pinned to any slot, they can land anywhere in the rotation the manager calls for.

**Run environments inflate.** With no inter-inning rest, more PAs per game (~84 vs MLB's ~75 — coming from stays, which can put a single AB at up to 3 PAs, plus joker insertions adding tactical PAs at high-leverage moments), and the stay mechanic letting baserunners advance more freely, you get *more offense*. The simulator's baseline runs at ~25 R/G total, about 2.5x MLB. This is structural. Pushing it back down toward MLB's 9 R/G would be modeling something else, not O27.

**Workhorse pitchers become more valuable than aces.** This is the biggest design surprise. In MLB, an elite-Stuff arm is the most valuable type because they neutralize "third time through the order." In O27 the third time through the order is just *the order*; everyone faces it. So Stamina dominates. The pitchers who'd be ace closers in MLB are short-burst arms in O27 who eat 6-9 outs. The pitchers who'd be workhorse #2 starters in MLB are the ace tier in O27.

## The stat language

Once stays exist, the basic stat machinery has to evolve. An at-bat can produce up to 3 plate appearances; PAs and ABs decouple in a way they don't in MLB.

**PAVG (Plate Average) = H / PA.** This is the headline batting average in O27. Bounded 0.000–1.000. PA is the natural denominator because every contact event is one PA, whether the AB ends or extends — and it makes the math read cleanly across multi-hit ABs. The exact league mean is whatever the talent distribution and rule set produce; the structural argument is that with stays converting would-be-outs into hits, joker insertions adding tactical PAs in high-leverage spots, and 27-out continuous halves keeping hitters cycling, hits-per-game in O27 should naturally be above MLB's per-game total even at a modestly higher PAVG. That's what the simulator produces.

**BAVG (Batting Average) = H / AB.** This is the secondary "stayer profile" metric — inherits MLB's batting-average semantics (per-AB rate). In O27 it can exceed 1.000, because multi-hit ABs are real (max 3 hits in 1 AB via stays). Read together with PAVG, it diagnoses style:

| PAVG | BAVG | Profile |
|---|---|---|
| High | ~1.000 | Slap-and-go contact hitter, runs on contact |
| High | >1.000 | Productive stayer — uses stays well, extends rallies |
| Mid | >1.000 | Stays a lot but ABs often end in strikeouts; volatile |
| Low | ~1.000 | Poor hitter who doesn't try anything fancy |
| Low | >1.000 | Tries to stay but gets caught out — bad strategic decisions |

That's actually a nice diagnostic. PAVG tells you "how often you get hits at all"; BAVG tells you "what you do with your at-bats." Together they describe whether a hitter is a take merchant, a slap hitter, a productive stayer, or a stay-too-much guy. The stat-line shape can't be expressed by any single MLB stat.

The rest of the suite carries over with PA-denomination throughout:

- **OBP** = (H + BB + HBP) / PA — already PA-denominated in MLB, identical in O27
- **SLG** = total bases / PA (NOT per AB — stays make per-AB SLG ill-defined). League ~.550-.600, top sluggers approach 1.000.
- **OPS** = OBP + SLG. Top hitters approach 1.500-1.700.
- **ISO** = SLG - PAVG. Pure power isolated from contact rate.
- **BABIP** redefined: (H - HR) / (PA - K - BB - HBP - HR). League BABIP runs ~.450-.500 because stays count as both numerator and denominator events.
- **wOBA** with O27-tuned weights (1B 0.95, 2B 1.30, 3B 1.70, HR 2.05, BB 0.72, HBP 0.74), PA-denominated. Walks and singles nudged up vs MLB because the stay mechanic raises baserunner-advance value on those events.
- **K/PA, BB/PA, HR/PA** for pitchers — per-batter rates. **K/27, BB/27, HR/27** for workload rates. **ERA**, **WHIP**, **FIP** all per-27-outs.
- **WAR** with O27-fitted runs-per-win factor. Live baselines refit per render cycle. The constant lands at ~21 in O27 (vs ~10 in MLB) because runs are cheaper relative to wins in this run environment.

A new stat the engine surfaces — **Δstay = BAVG - PAVG** — quantifies how much of a hitter's value comes from stays specifically. A guy with PAVG .400 / BAVG 1.150 has Δstay = .750 — those are stay-driven hits. PAVG .400 / BAVG 1.000, Δstay = .600 — more conventional contact profile. You don't see that in MLB box scores.

## Five-inning baseball vs O27, side by side

| | 5-inning ball | MLB | O27 |
|---|---|---|---|
| Outs per game | 30 | 54 | 54 |
| Base lineup size | 9 | 9 | 9 |
| Joker pool | none | none | 3 (per-cycle reusable) |
| PAs per game (est) | ~45 | ~75 | ~84 (stays + jokers) |
| Runs per game (avg) | ~5 | ~9 | ~25 |
| Game length | ~1h45 | ~3h | ~2h |
| Within-AB tactics | MLB-standard | baseline | stay + foul-out |
| Per-rotation tactics | none | pinch-hit / sub | joker insertions |
| Max hits per AB | 1 | 1 | 3 |
| PA / AB ratio | 1.0 | ~1.0 | up to 3.0 |
| Rotation depth | 8–10 SPs | 5 SPs | 4–5 SPs (workhorse) |
| Closer position | dead | yes | rare |
| Within-game variance | very high | medium | medium-low |
| Strategic depth | less than MLB | baseline | more than MLB |

5-inning ball compresses MLB; O27 *re-architects* it. Look at the strategic-depth row — that's the load-bearing distinction. 5-inning baseball *removes* decisions (no bullpen, no late-inning chess). O27 *adds* them (stay or run, foul-out tactics, workhorse rotation, joker insertions per rotation, dance-the-runners with the count).

## What I learned by building the simulator

I didn't set out to write a sport. I set out to test if the idea would work. The simulator is the experiment — it lets me sample seasons, look at stat distributions, see whether elite players actually look elite, see whether mediocre teams actually lose more, see whether the rule set produces a coherent game.

Four things stood out:

**The Pedro line emerged.** In a recent test season, the simulated league produced a pitcher (Kazuomi Ahn, fictional, Stuff 92 / Command 79 — Elite+ tier) with a 4.38 ERA, 10.4 K/27, and a .167 opponent batting average across 86 batters faced. That last number — .167 oAvg — is *exactly* Pedro Martinez's 2000 BAA. It happened by accident, and that's when I knew the engine was producing real stat shapes, not just numbers. An elite pitcher in O27 should look unmistakably like an elite pitcher: Pedro-tier suppression of contact, Maddux-tier walk rates, Cy Young-tier ERA+. They do.

**The stay rule's first implementation was wrong, and the symptoms told the story.** I had stays resetting the count to 0-0 and not crediting a strike. That made multi-hit ABs trivially common (a batter could dance forever) and blew up the statistical distributions. Switching to the correct rule — strike spent, count carries — capped the dancing at 3 PAs per AB, brought the league offensive profile back into a defensible range, and made the stay decision actually feel like a *decision* in the simulator. Bad rules show up in the stats faster than I expected.

**Workhorse pitchers needed an explicit moat.** The first version picked the highest-Stamina arm as the SP every game and rode him until fatigue. That was the right *idea* — Stamina dominates — but the implementation had a subtle bug where the same arm would pitch every single day. A real workhorse can throw a complete game, but he can't throw one every day. Fixing this required a 5-day rolling pitch debt that decays daily, plus a tier-based rest filter on the SP picker, plus rotation cycling. The fix made the simulated standings stop looking like every team had one Cy Young arm and four spot starters — which is the right outcome.

**Defense matters in O27 the way it matters in cricket.** With more BIPs per game and more late-half tired-pitcher contact, the value of an actually-good fielder shows up more starkly. The simulator now has per-position defense ratings (infield / outfield / catcher sub-groups, on top of general glove and arm), errors that fire as ROE events charging UER, catcher arm reducing SB success, and DRS / dWAR computed against a positional-value table. The +SS at the top of those leaderboards is genuinely worth a couple wins per season above replacement, the same way Andrelton Simmons used to be in real life.

**The first joker model was wrong, and the corrected version surfaced manager-AI as a real lever.** I built jokers as fixed bottom-of-order DH slots first — the manager had no decision to make, the lineup was set, the jokers just batted when their slot came up. That collapsed the entire tactical layer. The corrected model — three jokers in a separate pool, available for per-PA insertion subject to once-per-cycle, with the manager AI deciding whether and when based on leverage — is much harder to build right but produces a genuinely interesting strategic surface. The cleanest sign that the rule is working: a "joker leverage index" emerged as a real differentiator. Two managers with similar rosters can end up several wins apart over a season based purely on joker timing — like a cricket captain holding his death-overs bowler for the right matchup, or burning his best closer in a non-save situation. Manager AI quality goes from a flavor consideration to a measurable stat.

## Why O27 ended up more interesting than the 5-inning version

Three reasons, in increasing order of importance:

**1. It forces position redesign.** 5-inning baseball doesn't change what a starter or closer is — it just shifts the proportions. O27 destroys the closer position outright (the bowler stays in until pulled), turns the SP role into a workhorse-stamina premium, makes the catcher's arm a *team-defense input* via SB suppression, and makes Power and Eye independently valuable instead of correlated through "good hitter."

**2. It produces archetypes that don't exist in MLB.** The dance-the-runners contact specialist is an O27-native type. The free-swinger who fouls himself into the dugout is an O27-native type. The high-PAVG / high-BAVG productive stayer who takes ABs and turns them into 3-hit innings is an O27-native type. The pure-tactical "joker" — a Bonds or a Henderson kept on the bench specifically to be deployed in high-leverage rotations — is an O27-native type that has no MLB analog (the closest is "elite pinch-hitter," but pinch-hitters in MLB are usually one-shot deals, not three-times-a-game tactical assets). None of these are reachable from the 5-inning rule set.

**3. It asks a real question about what makes baseball *baseball*.** The question 5-inning baseball asks is "what if you played less baseball?" That's a sample-size question. The question O27 asks is "what does cricket's *concentration* — and pesäpallo's *contact-budget* layered on top — do to baseball's *structure*?" That's a sport-design question. The first one has a known-shape answer (less variance per game, more variance per series, all the rate stats stay the same). The second one had no clear answer when I started — the stay mechanic, the foul-out cap, the workhorse premium, the inflated run environment, the per-position-defense importance, the PAVG/BAVG distinction, the joker tactical layer with manager-AI as a measurable differentiator, all emerged from working through consequences. *That's* the part that's worth modeling.

## Where this goes

I'm not building a real league. I'm building a simulator that produces a believable league, and using it to test whether the idea has legs. So far the answer is yes — the engine produces stat lines that read like a coherent sport, the strategic archetypes diverge from MLB in legible ways, and the league tells stories I can follow.

The honest counter-argument is that O27 is *less* viewable than 5-inning baseball. A daily 3-hour doubleheader gives a fan two complete games and one trip to the stadium. An O27 game is a single ~2-hour concentration event with no inter-inning rest, no bathroom break, no "the last out of the seventh is when we get nachos" rhythm. That's a real cost. It's also why I lean toward thinking O27 stays at 162 games or even fewer — every game is a bigger experience, so you don't need as many.

Both ideas survive contact with reality. They live at opposite ends of the same design axis. 5-inning baseball maximizes statistical truth across a long season; O27 maximizes per-game density and forces the sport itself to be different. They're answering opposite questions about what makes baseball worth watching. I find the second question more interesting, and the simulator I've been building has convinced me that the answer to it is genuinely a sport rather than a gimmick.

Cricket gave baseball one specific thing here: the *innings as concentrated event*. Pesäpallo gave it a second: the *contact-as-budget* tactical layer that makes the long innings strategically rich rather than just longer. The joker mechanic is the third thing — three pinch-hitters-with-no-cost as a *recurring tactical resource*, with the manager calling on them like a cricket captain calling on his strike bowler — and it's the piece that ties the rest together by making manager AI quality a real variable. If I'd taken the schedule instead of the structure, I'd have a slightly faster MLB. Taking the concentration plus the contact budget plus the joker rotation gave me something that needed its own probability tables, its own value-stat constants, its own rotation philosophy, its own player archetypes, and its own definition of what a good manager looks like — and that's what made it worth building.

---

*The simulator is open source at \[link\]. A 162-game season takes about 3 minutes to sim end-to-end. Standings, leaderboards, full sabermetric suite (PAVG / BAVG / Δstay, OPS+, ERA+, WAR with O27-tuned baselines, position-aware DRS, Pythagorean expected wins) all live-fitted to whatever the league has actually produced. If you find it interesting, tell me what looks broken — that's where the next version comes from.*
