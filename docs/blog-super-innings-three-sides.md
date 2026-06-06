# A sport that doesn't exist, carried all the way to its consequences

*On building Super Innings — and on what it's actually a demonstration of.*

I built a baseball variant and then I built everything required to find out whether
it was any good. That sentence is the whole project, and it's also the reason I
have trouble talking about it: the thing is a game design, a piece of engineering,
and an argument about a sport, all at once, and most rooms only have ears for one
of those at a time. So let me try to say it as one thing.

## The one structural change

Super Innings (it started life as "O27") is baseball with a single rule removed:
there are no innings. Each side bats until it records 27 outs — one continuous
half per team — and then the other side does the same. That's it. Everything else
that's strange about the sport falls *out* of that one change rather than being
bolted onto it.

This is the cricket move. T20 didn't make cricket better by shortening it
arbitrarily; it changed the structural shape of an innings and let a different
strategic game emerge. I'd wondered for years whether baseball had the same trick
in it. The obvious version — just play five innings — I tried and threw away
early, because it's exactly what it sounds like: short, more random, more boring
baseball. Twenty-seven outs in one unbroken half-inning is different in kind. It's
a marathon and a sprint at once. Pitcher fatigue now accumulates monotonically
across the whole arc instead of resetting every inning. Top-of-order hitters get
six or seven plate appearances instead of four. The manager can't hide a tiring
arm behind an inning break. Roster construction suddenly values the workhorse over
the short-burst reliever. None of that is a rule I wrote. It's all just consequence.

That's the design thesis, and it's the part that's hardest to take credit for
because it looks like luck: the good mechanics are the ones you *discover* are
already implied by your premise, not the ones you invent to patch it. The one
mechanic I did go borrow is the **stay**, from the Finnish sport pesäpallo. Once
you collapse the game into one half-inning, a real question opens up — *what lever
does a hitter still have over an at-bat?* — and pesäpallo had already answered it:
on contact in play, let the batter choose to stay at the plate and advance the
runners, at the cost of a strike. It's a runner-advancement tool, not a hit
machine, and it's where most of the sport's offense actually comes from. The only
reason I knew to reach for it is that I know this whole family of bat-and-ball
games well enough to know which borrowed part fits the hole the new rule cuts. The
tooling is downstream of that taste. I want to be honest that the domain knowledge
is the rarest input here, not the line count.

## The part where it became three projects in a trenchcoat

You can't browse a sport that doesn't exist. There's no Baseball-Reference for it,
no Statcast, no hundred years of priors to fit your numbers against. So to find
out whether the rules produce anything worth caring about, I had to build the
entire apparatus that a real sport spends a century accreting — and that's where
the engineering and the design stop being separable.

The most important consequence of "the sport doesn't exist" is that **you can't
borrow any constants.** Traditional batting average breaks immediately, because a
single at-bat can now contain multiple stays — at-bats and plate appearances
diverge structurally, so a stay-heavy hitter can carry a batting average north of
1.000. Every familiar stat had to be rebuilt from first principles, and the whole
analytics suite — linear weights, expected weighted-on-base, run expectancy by
base-out state, BaseRuns with a cluster-luck decomposition, an arc-weighted ERA
that punishes runs given up late because there's less arc left to answer them — is
refit from *each league's own simulated data every render cycle*, never imported
from MLB. That's not an engineering aesthetic. It's forced. A fictional universe
has to generate its own ground truth and then measure it. The dependency-light
build (pure-standard-library math, no NumPy, no pandas, every probability table
written out and owned) is the same decision as the design conceit, seen from the
other side.

And because the run environment is an *output*, I made the single most important
call of the project a refusal: there is no target for runs-per-game. Early on I'd
treated a band of "22–26 R/G" as something to tune toward, and at some point I
realized that was a category error. Super Innings is variance-first. Whatever the
mechanics produce *is* the run environment; the rate stats are observations, not
knobs. I tore the target out, let fatigue become the dominant pitching axis, and
the league settled around 33 runs a game with a standard deviation north of nine —
wild, by design. Choosing to be surprised by your own system instead of
puppeteering it is, I think, the actual mark of design maturity, and it's the one
I'm proudest of. The corollary is that I revise the rules when the box score
argues back: the five-out "super-inning" tiebreaker got scrapped after it let
offenses run up double-digit frames, and the home-run "Walk-Back" went from a
one-pitch phantom to a persistent runner who stands on third until he's driven in
— both because a printed box score didn't read right.

This is also why the project ships with a discipline most hobby projects don't.
Every meaningful change gets a written After-Action Report — there are over a
hundred of them now — recording the ask, the tradeoffs, the verification numbers,
and the honest open gaps. A nine-assertion invariant suite has to pass before any
release; it catches every mathematically-impossible-stat bug the project has ever
shipped (a pitcher's outs-share exceeding 27, league FIP drifting from league ERA,
batter and pitcher ledgers failing to reconcile). The design history doesn't live
in my head. It lives in writing, and the math isn't trusted, it's measured.

## The turn that surprised me

Here's the part that's new, and the part that changed how I think the project
should be described. For a long time my honest framing was: *this isn't a game, it's
an instrument.* There's no manager mode, no GM you play as. It's a telescope for
looking at what the rules produce. That was true, and I'd have defended it.

Then, over a few days at the start of June, the project quietly grew the thing that
makes a real sport a *sport*: an audience economy.

Real sports aren't kept alive by the people on the field. They're kept alive by
the apparatus around the field — fantasy leagues, betting markets, broadcast booths,
beat writers. None of those people play. They consume the game's output and build
a second game on top of it. And it turns out that's exactly how you make a
non-playable simulation into something a person can be a *fan* of.

So now there's **CapSpace** — about 7,300 lines of fantasy and betting metagame
sitting on top of the sim. Six modes: a Walk-Back home-run game, a pitching game
built on the new save-equivalent "Finisher" stat, daily-fantasy slates, streak
picks, 5×5 roto category leagues, draft-once best ball, and an actual sportsbook
with moneylines and run totals. There's one persistent wallet per save; buy-ins go
out, winnings come in, and you climb a career ladder from Rookie to Hall of Famer.
The lineup math is real — best ball solves the *exact* optimal lineup by dynamic
programming and validates draft coverage with bipartite matching, because a
fictional player has no name recognition, so the only thing a daily-fantasy player
can actually read is the player's recent form and real stats.

And there's **o27audio** — a standalone service that generates two-host AI radio
broadcasts of a game. Claude writes the play-by-play and color script from the
persisted pitch-by-pitch log; a text-to-speech model voices the two announcers;
standard-library audio stitching renders the clip. You press "Listen" on a game and
get a broadcast of it on your phone.

What I want to point at isn't the feature count. It's the *architecture*, because
it's the cleanest expression of the whole project's spine. **Neither CapSpace nor
the audio service ever simulates anything.** They can't. They are strictly
read-only consumers of the sim's persisted, seed-deterministic truth — the box
scores, the pitch-by-pitch logs, the stat ledgers that the engine already wrote
down. The betting market prices games it has no power to alter; the broadcaster
narrates a game that already happened, exactly as it happened. The simulation is
the source of truth, and everything else is parasitic on it in the good way. That
separation is why I could grow an entire spectator layer in days without touching
the engine's core schema, and it's the property I'd point any collaborator to first.

(The same week, less glamorously, I learned the read path was murdering itself:
heavy pages weren't slow because of the database — the SQL ran in milliseconds —
they were recomputing a couple of seconds of aggregation on every load and
ballooning to half a minute under contention. A whole-page HTML cache keyed on a
cheap data fingerprint, invalidated only when a sim or trade actually changes the
data, took the leaderboard page from 2,548 milliseconds to 2.3 — about eleven
hundred times faster. I also finally collapsed two different algorithms that had
been disagreeing about which pitcher took the loss on 30 of 120 final scores into a
single canonical one. Zero mismatches now. The unglamorous correctness work is the
tax you pay for being allowed to make the fun claims.)

## So how do I talk about it?

As one act, seen from three sides. The domain knowledge generated the *design*; the
design's refusal of borrowed constants forced the *engineering* shape; and the
engineering discipline — the AARs, the invariant suite, the source-of-truth
separation — is what let one person hold a system this large without it collapsing
into mud. It's not three skills stacked up. It's deep knowledge of a genre expressed
as a buildable system, with enough rigor to actually answer the question the system
was built to ask.

And the honest scope note stays, because it's load-bearing: Super Innings is a
thought experiment with a working build, not a commercial product. The value isn't
that it's finished — it's that an idea got carried all the way to the point where
the data could answer whether it was any good. Months of seasons say it is. The
contact specialist, the workhorse starter, the junkballer, the 38-year-old
submariner — all the players modern baseball has marginalized — find real value
here, and they find it because of the rules, not because I told them to. That's the
result I was after. Everything else is just the apparatus I had to build to be able
to see it.
