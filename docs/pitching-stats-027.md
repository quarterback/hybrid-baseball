# O27 Pitcher Statistics

A guide to reading pitcher stats in O27.

## Why O27 Needs Different Stats

O27 is baseball played in one continuous half-inning per side, with each side batting until they record 27 outs. This structural change makes some traditional pitching stats less useful and creates room for new ones.

In MLB, pitchers work in three-out increments separated by bench breaks, lineup resets, and strategic pauses. A 6-inning start is built from six discrete units. In O27, a pitcher's appearance is one unbroken arc. He comes in at out 0 (or whenever the manager calls him), faces batters continuously, and leaves at whatever out the manager pulls him on. Fatigue accumulates monotonically. The 22nd batter he faces is doing so against a more tired version of him than the 5th.

Standard baseball stats like ERA, WHIP, and FIP still work, but they don't capture two things O27 makes visible: **when in the arc the runs scored**, and **how much a pitcher's stuff degrades across a long outing**.

This guide explains the stats O27 uses, what each one tells you, and how to read them.

## Game-Level Stats

These appear in box scores and on per-game pitching lines.

### GSc — Game Score

A single number summarizing how well a pitcher performed in a single game.

**Formula:** `clamp(0, 100, 50 + outs + 2 × max(0, K - 3) - 2H - 4ER - 2UER - BB - 4HR + 1 × FO)`

Where FO is foul-outs (three-foul-rule retirements). The score is bounded between 0 and 100 — disaster outings cap out at 0 rather than going negative, and theoretical perfection caps at 100.

**How to read it:**
- 90+ : exceptional outing
- 70-89 : strong start
- 50-69 : average
- 30-49 : below average
- under 30 : disaster

A 27-out shutout with double-digit strikeouts will land in the 90s. A 22-out start giving up 4 runs lands in the 60s. A 5-out blowup giving up 8 ER lands at or near 0 because of the clamp.

The +1 bonus per foul-out specifically rewards three-foul-rule cheap outs, which is an O27-specific way to retire batters that GSc would otherwise miss.

GSc is the fastest way to read a pitching line. It rolls counting stats into one summary number that's instantly comparable across games and pitchers. Lower scores tell you the start was bad without needing to interpret an inflated O27 ERA-equivalent.

### OUT — Out Number When Pulled

The team's out count at the moment the pitcher's last batter's plate appearance ended.

**How to read it:**
- 27 : pitcher finished the half (complete-game equivalent)
- 22-26 : workhorse start, manager went deep before going to the bullpen
- 14-21 : standard mid-arc pull
- under 14 : early hook, either struggle or strategic short outing

OUT is the simplest way to see when a pitcher exited. It tells you both the workload (how many outs he absorbed) and the manager's decision (when he was pulled). A starter pulled at OUT 16 with 4 ER tells a different story than a starter pulled at OUT 16 with 0 ER.

### OS% — Outs Share

The percentage of his team's 27 outs the pitcher recorded in this appearance.

**Formula:** `outs_recorded / 27 × 100`

A pitcher who records 18 outs has OS% 67%. A pitcher who records all 27 outs has OS% 100%. In super-innings, OS% is calculated against the round's 5-dismissal cap rather than 27.

**How to read it:**
- 80%+ : workhorse outing
- 50-79% : standard starter workload
- 25-49% : mid-relief or short start
- under 25% : late-relief or cameo appearance

OS% normalizes outs across pitchers regardless of role. It's the cleanest way to compare workload between starters and relievers — a closer with OS% 19% (5 outs) and a starter with OS% 78% (21 outs) are doing structurally different jobs, and the percentage tells you so without having to interpret raw out counts.

## Season Result Stats

These tell you how a pitcher performed across many appearances. Three different stats answer three different questions.

### wERA — Weighted ERA

What actually happened, accounting for when in the arc the runs scored.

**Formula:** Standard ERA, but earned runs are weighted by arc position before division:
- Runs scored during outs 1-9 weighted at 0.85
- Runs scored during outs 10-18 weighted at 1.00
- Runs scored during outs 19-27 weighted at 1.20

`wERA = (weighted_ER × 27 / outs_recorded) × C_w`

Where `C_w` is a calibration constant refit each season so league wERA tracks the league's actual run environment. Like the FIP constant in MLB, `C_w` isn't fixed — it gets recomputed against live data every time the season aggregator runs.

Super-inning outs roll into arc 3 (treated as a continuation of the late-arc phase).

**Why weighted?** A 3-run inning at the start of the half gives the offense plenty of runway to respond. The same 3 runs given up in outs 19-27 forces a late-half scramble. wERA reflects that runs given up late are more damaging than runs given up early.

**How to read it:**
- League average wERA tracks the run environment baseline (recalibrated each season)
- A pitcher 25% below league average is elite
- A pitcher 25% above league average is replacement-level

A pitcher with raw weighted ER per 27 outs of 9.50 in a 12.00 league has wERA roughly 9.00 if his runs were arc-distributed evenly — slightly better than raw ER suggests because the formula is rewarding him for not blowing late-arc leads.

wERA is "what happened to him, properly contextualized." It includes luck, defense, sequencing — everything that affects run scoring.

### xFIP — Expected Fielding Independent Pitching

What should have happened, given only outcomes the pitcher controls.

**Formula:** `(13 × HR + 3 × BB - 2 × (K + FO)) × 27 / outs + C_x`

Where FO is foul-outs (three fouls in an at-bat). The constant `C_x` is refit each season so league xFIP equals league wERA.

**Why this formula?** xFIP isolates the three "true outcomes" that don't depend on defense or luck: home runs, walks, and strikeouts. In O27, foul-outs are added to strikeouts because they're another way the pitcher retires the batter through pitch sequencing alone, without help from the field.

**How to read it:**
- Lower than wERA → pitcher has been unlucky (poor defense, bad sequencing, BABIP variance)
- Higher than wERA → pitcher has been lucky (great defense, favorable sequencing)
- Equal to wERA → results match underlying skill

xFIP is the talent-isolation stat. Two pitchers with identical xFIP have the same underlying skill regardless of what happened around them. Use xFIP to project forward; use wERA to argue about who deserves awards based on actual results.

### Decay — Late-Arc Degradation

How much a pitcher's stuff falls off across the 27-out arc.

Decay uses the K% definition below (which includes foul-outs). The formula compares K% in the early third of the arc (outs 1-9) to K% in the late third (outs 19-27), restricted to appearances where the pitcher faced batters in both arc phases.

**Formula:** `Decay = (K%_outs_1-9 - K%_outs_19-27) × 100`

**Why this matters in O27 specifically.** MLB pitchers rarely face hitters across a continuous long outing — they get pulled around 20 batters faced. In O27, a starter routinely faces 25-35 batters in one continuous half. The rate at which his stuff degrades over that span is a real, measurable, season-stable skill that distinguishes workhorses from grinders.

**How to read it:**
- 0 or negative : pitcher actually settles in late (rare, valuable)
- 0-15 : durable workhorse, stuff barely degrades
- 15-30 : moderate fade, manageable with bullpen support
- 30+ : significant late-arc degradation, needs bullpen support before outs 19-27

Two pitchers with the same season ERA-equivalent and same overall K% can have very different Decay numbers. One is a true workhorse who can absorb 25 outs without losing his edge. The other survives the late arc on guile after losing his stuff. Both valuable, differently — and the difference shows up in Decay.

## Workload Stats

### OS+ — Outs Share Plus

A season-long indexed version of OS%, normalized to league average.

**Formula:** `(pitcher's avg OS%) / (league avg OS%) × 100`

League average OS+ is always 100 by construction. A pitcher with OS+ 270 averages outs share 2.7x league average — a true workhorse who routinely goes deep into games. A pitcher with OS+ 50 averages half the league baseline — a short-relief specialist or struggling starter.

**How to read it:**
- 200+ : workhorse ace
- 100-199 : starter who carries his share
- 50-99 : reliever
- under 50 : limited-role specialist

Like ERA+ in MLB, OS+ neutralizes era and league context. A 250 OS+ in any O27 league is a workhorse, regardless of when or where the season was played.

### AOR — Average Out Reached

The mean OUT value across all of a pitcher's appearances in a season.

**How to read it:**
- 22-26 : workhorse starter
- 18-21 : standard starter, normally pulled mid-late arc
- 12-17 : long reliever or spot starter
- 5-11 : middle reliever
- under 5 : closer or specialist

AOR tells you usage at a glance. It's a simpler signal than OS+ — just "where in the arc does this pitcher typically end his appearance" — but it captures the same workload story in a way that's instantly readable.

### WS% — Workhorse Start Percentage

Percentage of a pitcher's starts that qualify as a "workhorse start": 18+ outs recorded with 6 or fewer earned runs.

**Why this threshold?** 18 outs is two-thirds of a half-inning — the equivalent of a pitcher carrying his team through the first two phases of the arc. 6 ER in a 12.00-run-environment league is the rough equivalent of a 3-ER start in MLB. The combination is the O27 quality-start bar.

**How to read it:**
- 70%+ : ace
- 50-69% : reliable mid-rotation starter
- 30-49% : back-end starter
- under 30% : not a regular starter

WS% is the O27 equivalent of MLB's Quality Start rate.

### pWAR — Pitcher Wins Above Replacement

A pitcher's value in wins, anchored to wERA.

**Formula:**
- `VORP = (replacement_wERA - my_wERA) × outs / 27`
- `pWAR = VORP / runs_per_win`

Where `replacement_wERA = league_wERA × 1.2` (a replacement-level pitcher allows 20% more weighted runs than league average).

**How to read it:**
- 5.0+ : MVP-level pitching season
- 3.0-4.9 : All-Star
- 1.0-2.9 : Above-average regular
- 0.0-0.9 : Below average
- negative : worse than replacement-level

pWAR rolls run prevention (wERA) and workload (outs) into a single value number. A pitcher with elite wERA but only 80 outs is less valuable than a pitcher with good wERA and 600 outs — pWAR captures that.

## Per-Plate-Appearance Rate Stats

These don't depend on the run environment and travel cleanly across leagues, eras, or sports variants.

### K%

Strikeouts per plate appearance, expressed as a percentage. **Foul-outs count as strikeouts** in this calculation, since they're a pitcher-induced retirement through pitch sequencing alone. This applies everywhere K% appears — in the rate stat, in the K-rate inputs to xFIP and Decay, and in K-BB%.

**Formula:** `K% = (K + FO) / BF`

**How to read it:**
- 30%+ : elite swing-and-miss
- 22-29% : above average
- 16-21% : average
- under 16% : below average, contact pitcher

### BB%

Walks per plate appearance, expressed as a percentage.

**Formula:** `BB% = BB / BF`

**How to read it:**
- under 5% : elite command
- 5-7% : above average
- 7-10% : average
- 10%+ : command issues

### HR%

Home runs per plate appearance, expressed as a percentage.

**Formula:** `HR% = HR / BF`

**How to read it:**
- under 2% : elite HR suppression
- 2-3% : above average
- 3-4% : average
- 4%+ : home-run prone

K% minus BB% (often called K-BB%) is a quick proxy for command-and-stuff combined. Top pitchers post K-BB% above 20%; replacement-level pitchers are under 10%.

### Secondary Per-Pitch Indicators

The player page also surfaces two secondary per-pitch indicators in the "Per-Batter Rates" panel: **O/P** (outs per pitch, an efficiency measure) and **P/BF** (pitches per batter faced, a labor measure). Neither is a headline stat, but they give a per-pitch lens the rate stats don't.

## How to Read a Pitcher's Page

The fastest read on a pitcher across one season:

**Look at OS+ first.** Tells you what role he plays. 200+ is a workhorse, sub-100 is a reliever.

**Look at wERA + xFIP together.** Same → results match skill. wERA much higher than xFIP → unlucky. wERA much lower → lucky, may regress.

**Look at Decay.** Low Decay + high OS+ → true workhorse. High Decay + high OS+ → grinder who survives on guile, needs bullpen help.

**Look at K% / BB% / HR%.** The underlying skill profile. Two pitchers with the same wERA can have very different rate profiles.

**Look at WS% and pWAR.** WS% tells you how often he actually delivered a quality start. pWAR rolls everything into total value in wins.

A pitcher's full story isn't in any one stat. wERA tells you what happened, xFIP tells you what should have, Decay tells you how he holds up, OS+ tells you how much work he absorbs, pWAR tells you what all of that adds up to in wins, and the rate stats tell you what kind of pitcher he is underneath. Read them together.

## What's Different from MLB

If you're coming from MLB, the main translation work:

- **ERA** is replaced by **wERA**. Same concept, weighted by arc position, calibrated per season. League average is much higher than MLB because the run environment is structurally higher in O27.
- **FIP** is replaced by **xFIP**. Same concept, foul-outs added to Ks, constants recalibrated to track league wERA.
- **WHIP** is dropped. Use **K%** and **BB%** as separate columns instead.
- **K/9, BB/9, HR/9** are dropped. **K%, BB%, HR%** (per PA) are environment-neutral.
- **Quality Start (6+ IP, ≤3 ER)** becomes **Workhorse Start (18+ outs, ≤6 ER)**.
- **Innings Pitched** is replaced by **OUT** (game) and total outs recorded (season). The natural unit in O27 is outs, not innings.
- **WAR** becomes **pWAR**, anchored to wERA instead of FIP or RA9.
- **Decay** is new. MLB doesn't measure this because pitchers don't pitch long enough in single appearances for arc-degradation to be a stable skill.

The conceptual stat triples — what happened (ERA → wERA), what should have (FIP → xFIP), how durable (no MLB equivalent → Decay) — give you the same analytical lenses MLB fans are used to, calibrated to a sport with different structural rules.
