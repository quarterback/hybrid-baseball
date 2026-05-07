# O27 Stats Reference

A complete catalog of every statistic, rate, and analytic computed in O27 / Hybrid Baseball — what each one means, what it is derived from, and where it lives in the codebase.

Use this as the single source of truth when explaining a stat. If a stat is missing from this doc, please add it.

> Notation: `H` = hits, `PA` = plate appearances, `AB` = at-bats, `BB` = walks, `HBP` = hit-by-pitch, `K` = strikeouts, `HR` = home runs, `BF` = batters faced, `ER` = earned runs, `FO` = foul-outs (O27's 3-foul rule), `STY` = stays.

---

## 1. Batting — Counting Stats

| Stat | Abbr | Meaning | Derived from | Source |
|---|---|---|---|---|
| Plate Appearances | PA | Times the batter steps to the plate | Counted per PA event | `o27/stats/batter.py:12` |
| At-Bats | AB | PAs that aren't walks or HBP | `PA − BB − HBP` | `o27/stats/batter.py:13` |
| Hits | H | Times batter reached safely on a batted ball | Counted on contact outcome | `o27/stats/batter.py:15` |
| Runs | R | Times batter crossed home | Incremented when runner scores | `o27/stats/batter.py:14` |
| RBI | RBI | Runs driven in by this batter | Credited on contact that scores runners | `o27/stats/batter.py:19` |
| Doubles | 2B | Hit landing on 2B | Per double event | `o27/stats/batter.py:16` |
| Triples | 3B | Hit landing on 3B | Per triple event | `o27/stats/batter.py:17` |
| Home Runs | HR | Hit clearing all four bases | Per HR event | `o27/stats/batter.py:18` |
| Walks | BB | Reached on 4 balls | Counted at 4-ball count | `o27/stats/batter.py:20` |
| Strikeouts | K | Dismissed on 3 strikes | Counted at 3-strike count | `o27/stats/batter.py:21` |
| Hit By Pitch | HBP | Reached after being struck by a pitch | Per HBP event | `o27/stats/batter.py:22` |
| Outs Recorded | OR | Times this batter was retired | Per out event | `o27/stats/batter.py:24` |
| Stolen Bases | SB | Successful steal attempts | Per successful steal | `o27/stats/batter.py:61` |
| Caught Stealing | CS | Out attempting to steal | Per CS event | `o27/stats/batter.py:62` |
| Foul-Outs | FO | Outs via O27's 3-foul rule | Counted on 3rd foul in an AB | `o27/stats/batter.py:63` |
| Reached on Error | ROE | Reached base on a defensive error (not a hit) | Per fielder error | `o27/stats/batter.py:64` |
| Putouts | PO | Outs recorded as primary fielder | Per fielding out | `o27/stats/batter.py:68` |
| Errors | E | Defensive miscues | Per error event | `o27/stats/batter.py:69` |
| Grounded Into DP | GIDP | AB resulting in a double-play grounder | Per DP outcome | `o27/stats/batter.py:58` |
| Grounded Into TP | GITP | AB resulting in a triple-play grounder | Per TP outcome | `o27/stats/batter.py:59` |

---

## 2. Batting — O27 "Stay" Mechanic Stats

The "stay" is O27's second-chance hit mechanic — a batter can elect to stay in the AB to advance runners. These stats track that.

| Stat | Abbr | Meaning | Derived from | Source |
|---|---|---|---|---|
| Stays | STY | Total stay events chosen | Per stay event | `o27/stats/batter.py:23` |
| Stay Hits | STAY_H | Hits credited via a stay | Subset of `H` from stays | `o27/stats/batter.py:26` |
| Stay RBI | STAY_RBI | RBI generated specifically by stay events | Counted when a stay scores runners | `o27/stats/batter.py:25` |
| Multi-Hit ABs | MHAB | At-bats credited with 2+ hits (via stays) | Count of ABs with multiple hits | `o27/stats/batter.py:27` |
| 2C Opportunities (1B/2B/3B) | C2_OP_* | Stay events with a runner on each base | Per stay-with-runner event | `o27/stats/batter.py:34-38` |
| 2C Advances (1B/2B/3B) | C2_ADV_* | Runners advanced by a stay (3B = scored) | Per advance from a stay | `o27/stats/batter.py:35-39` |

---

## 3. Batting — Rates & Averages

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Plate Average | PAVG | O27's primary batting average — hits per PA | `H / PA` | `o27v2/web/app.py:713` |
| Batting Average | AVG | Legacy alias for PAVG in O27 | `H / PA` | `o27v2/web/app.py:714` |
| H/AB (Stayer) | BAVG | Hits per AB; can exceed 1.0 because of multi-hit ABs from stays | `H / AB` | `o27v2/web/app.py:731` |
| Stay Differential | STAY_DIFF | Second-chance productivity signal | `BAVG − PAVG` | `o27v2/web/app.py:734` |
| On-Base % | OBP | Reach-base rate | `(H + BB + HBP) / PA` | `o27v2/web/app.py:716` |
| Slugging % | SLG | Total bases per PA (PA-denominated, not AB) | `TB / PA` | `o27v2/web/app.py:722` |
| OPS | OPS | Combined reach + power | `OBP + SLG` | `o27v2/web/app.py:723` |
| Isolated Power | ISO | Extra-base power above batting average | `SLG − AVG` | `o27v2/web/app.py:736` |
| BABIP | BABIP | Hits-per-ball-in-play (luck/contact signal) | `(H − HR) / (PA − K − BB − HBP − HR)` | `o27v2/web/app.py:743` |
| Strikeout % | K% | Strikeouts per PA | `K / PA` | `o27v2/web/app.py:744` |
| Walk % | BB% | Walks per PA | `BB / PA` | `o27v2/web/app.py:745` |
| Home Run % | HR% | HRs per PA | `HR / PA` | `o27v2/web/app.py:746` |
| BB/K Ratio | BB/K | Plate discipline | `BB / K` (or `BB` if `K=0`) | `o27v2/web/app.py:747` |
| Stolen Base % | SB% | Steal success rate | `SB / (SB + CS)` | `o27v2/web/app.py:750` |

### O27-Native Rates

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Stay % | STAY% | Share of PAs in which the batter stayed | `STY / PA` | `o27v2/web/app.py:770` |
| Stay RBI per Stay | STAY_RBI/STY | Run-driving efficiency of stays | `Stay_RBI / STY` | `o27v2/web/app.py:774` |
| Stay RBI % | STAY_RBI% | Fraction of total RBI driven by stays | `Stay_RBI / RBI` | `o27v2/web/app.py:780` |
| Stay Conversion % | STAY_CONV% | Share of stays that produced a hit | `Stay_H / STY` | `o27v2/web/app.py:787` |
| Foul-Out % | FO% | Share of PAs ending in a 3-foul out | `FO / PA` | `o27v2/web/app.py:791` |
| Multi-Hit AB % | MHAB% | Share of ABs with 2+ hits | `MHAB / AB` | `o27v2/web/app.py:795` |

---

## 4. Batting — Sabermetric Value

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Weighted On-Base Avg | wOBA | Linear-weights offense, O27-tuned | `(0.72·BB + 0.74·HBP + 0.95·1B + 1.30·2B + 1.70·3B + 2.05·HR) / PA` | `o27v2/web/app.py:761-765` |
| OPS+ | OPS+ | League-relative OPS (100 = avg) | `(OPS / league_OPS) × 100` | `o27v2/web/app.py:800` |
| wOBA+ | wOBA+ | League-relative wOBA (100 = avg) | `(wOBA / league_wOBA) × 100` | `o27v2/web/app.py:801` |
| Batting VORP | bVORP | Runs above replacement, batting | `(wOBA − replacement_wOBA) × PA / 1.20` | `o27v2/web/app.py:808` |
| Offensive WAR | WAR_OFF | Batting wins | `bVORP / runs_per_win` | `o27v2/web/app.py:823` |
| Batting WAR | bWAR | Total batting+def wins for position players | `(bVORP + dDRS) / runs_per_win` | `o27v2/web/app.py:825` |

### Expected wOBA (luck-stripping)

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Expected wOBA | xwOBA | wOBA computed from contact quality (BABIP-luck removed) | League-avg wOBA-per-BIP at each contact quality × BIP events + actual BB/HBP | `o27v2/analytics/expected_woba.py:88-186` |
| wOBA − xwOBA | — | Luck differential | `wOBA − xwOBA` | `o27v2/analytics/expected_woba.py:177` |

---

## 5. Fielding & Defense

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Fielding Chances | CH | Total chances | `PO + E` | `o27v2/web/app.py:835` |
| Fielding % | FLD% | Successful fielding rate | `PO / (PO + E)` | `o27v2/web/app.py:836` |
| Position Defense | POS_DEF | Position-adjusted defense rating | `0.6 × sub-group + 0.4 × general` | `o27v2/web/app.py:370-399` |
| Defensive Runs Saved | dDRS | Defensive value in runs | `(POS_DEF − 0.5) × 2 × (G / 162) × position_drs_range` | `o27v2/web/app.py:820` |
| Defensive WAR | dWAR | Defensive wins | `dDRS / runs_per_win` | `o27v2/web/app.py:821` |

---

## 6. Pitching — Counting Stats

| Stat | Abbr | Meaning | Source |
|---|---|---|---|
| Batters Faced | BF | Total hitters faced | `o27/stats/pitcher.py:16` |
| Outs Recorded | OUT | Total outs recorded | `o27/stats/pitcher.py:17` |
| Hits Allowed | H | Hits surrendered | `o27/stats/pitcher.py:18` |
| Runs Allowed | R | Total runs scored against | `o27/stats/pitcher.py:19` |
| Earned Runs | ER | Runs not scored on errors | `o27/stats/pitcher.py:19` |
| Unearned Runs | UER | Runs scored on errors | `o27/stats/pitcher.py:20` |
| Walks | BB | Walks issued | `o27/stats/pitcher.py:21` |
| Strikeouts | K | Strikeouts recorded | `o27/stats/pitcher.py:22` |
| Home Runs Allowed | HR | HRs surrendered | `o27/stats/pitcher.py:24` |
| HBP Allowed | HBP | Batters hit by this pitcher | `o27v2/db.py:248` |
| Pitches Thrown | P | Total pitches | `o27/stats/pitcher.py:25` |
| Stolen Bases Allowed | SB_A | Bases stolen against | `o27/stats/pitcher.py:29` |
| Caught Stealing | CS | Runners caught stealing | `o27/stats/pitcher.py:30` |
| Foul-Outs Induced | FO | 3-foul outs induced | `o27/stats/pitcher.py:31` |
| Spells Pitched | SPELL | Separate appearances | `o27/stats/pitcher.py:26` |
| Max Spell | MAX_SPELL | Longest continuous outing (BF) | `o27/stats/pitcher.py:27` |
| Wins | W | SP gets W if 12+ outs; otherwise most-effective reliever | `o27v2/web/app.py:499-579` |
| Losses | L | Most ER on the losing team | `o27v2/web/app.py:571-578` |

---

## 7. Pitching — Arc Buckets (O27 Native)

O27 splits a 27-out outing into three "arcs" of 9 outs each so we can track when damage happens. Arc 3 also includes super-innings (SI).

| Bucket | Outs | Used in |
|---|---|---|
| Arc 1 | 1–9 | wERA weight 0.85 |
| Arc 2 | 10–18 | wERA weight 1.00 |
| Arc 3 | 19–27 (+SI) | wERA weight 1.20 |

Per-arc counters: `ER_ARC{1,2,3}`, `K_ARC{1,2,3}`, `FO_ARC{1,2,3}`, `BF_ARC{1,2,3}` — see `o27v2/db.py:256-267`.

---

## 8. Pitching — Result-Tier Stats (O27 Native)

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Weighted ERA | wERA | ERA weighted by when runs scored across the three arcs | `(0.85·ER_ARC1 + 1.00·ER_ARC2 + 1.20·ER_ARC3) × 27 / outs × C_w` | `o27v2/web/app.py:1147` |
| Expected FIP | xFIP | Expected runs from pitcher-controlled events only | `(13·HR + 3·BB − 2·(K + FO)) × 27 / outs + C_x` | `o27v2/web/app.py:1153-1161` |
| Decay | DECAY | Late-game K-rate degradation, league-corrected | `(K%_ARC1 − K%_ARC3) × 100 − league_drift` | `o27v2/web/app.py:1175-1180` |
| Decay (Raw) | DECAY_RAW | Decay before league-drift correction | `(K%_ARC1 − K%_ARC3) × 100` | `o27v2/web/app.py:1179` |
| Late K % | LATE_K% | K% during arc 3 only (relievers/closers) | `(K_ARC3 + FO_ARC3) / BF_ARC3` | `o27v2/web/app.py:1197` |

---

## 9. Pitching — Per-Game Performance

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Game Score | GSc | Single-number outing summary, clamped to [0, 100] | `clamp(0, 100, 50 + outs + 2·max(0, K−3) − 2H − 4ER − 2UER − BB − 4HR + 1·FO)` | `o27v2/web/app.py:839-864` |
| Outs Share % | OS% | Pct of the team's 27 outs the pitcher recorded | `outs / 27 × 100` | `o27v2/web/app.py:1207` |

---

## 10. Pitching — Workload & Season

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Game Score Avg | GSc_AVG | Mean Game Score across appearances | `Σ GSc / G` | `o27v2/web/app.py:1215-1219` |
| Game Score Plus | GSc+ | League-relative Game Score | `(GSc_AVG / league_GSc_AVG) × 100` | `o27v2/web/app.py:1254` |
| Avg Outs Reached | AOR | Mean outs per appearance | `outs / G` | `o27v2/web/app.py:1207` |
| Outs Share Plus | OS+ | League-relative AOR | `(AOR / league_AOR) × 100` | `o27v2/web/app.py:1209-1211` |
| Workhorse Start % | WS% | Share of starts with ≥18 outs and ≤6 ER | `count(qualifying starts) / starts` | `o27v2/web/app.py:1223` |
| Per-Game Decay | DECAY_PG | Mean per-appearance Decay (unweighted) | `mean(per-appearance Decay)` | `o27v2/web/app.py:466` |
| Arc-3 Reach Rate | ARC3_REACH% | Pct of appearances reaching arc 3 | `count(BF_ARC3 > 0) / G` | `o27v2/web/app.py:464` |

---

## 11. Pitching — Rate Stats

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Strikeout % (pit) | K% | Includes foul-outs | `(K + FO) / BF` | `o27v2/web/app.py:1226` |
| Walk % (pit) | BB% | Walks per BF | `BB / BF` | `o27v2/web/app.py:1227` |
| Home Run % (pit) | HR% | HRs per BF | `HR / BF` | `o27v2/web/app.py:1228` |
| K minus BB % | K-BB% | Plain Ks minus walks (no foul-outs) | `(K − BB) / BF` | `o27v2/web/app.py:1231` |
| Outs / Pitch | O/P | Efficiency | `outs / pitches` | `o27v2/web/app.py:1247` |
| Pitches / BF | P/BF | Labor per hitter | `pitches / BF` | `o27v2/web/app.py:1248` |
| Foul-Out % (pit) | FO%_PIT | Foul-outs induced per BF | `FO / BF` | `o27v2/web/app.py:1249` |

---

## 12. Pitching — Opponent-Facing

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Opponent Avg | oAVG | Batting average allowed | `H / (BF − BB − HBP)` | `o27v2/web/app.py:1235` |
| Opponent BABIP | oBABIP | BABIP allowed | `(H − HR) / (BF − BB − HBP − K − HR)` | `o27v2/web/app.py:1237` |
| Opponent OBP | oOBP | OBP allowed | `(H + BB + HBP) / BF` | `o27v2/web/app.py:1241` |
| Opponent SLG | oSLG | SLG allowed (approximation) | `(H + 3·HR) / (BF − BB − HBP)` | `o27v2/web/app.py:1242` |
| Opponent OPS | oOPS | OBP+SLG allowed | `oOBP + oSLG` | `o27v2/web/app.py:1243` |

---

## 13. Pitching — Value Stats

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Pitcher VORP | pVORP | Runs above replacement | `(replacement_wERA − wERA) × (outs / 27)` | `o27v2/web/app.py:1264` |
| Pitcher WAR | pWAR | Pitcher wins | `pVORP / runs_per_win` | `o27v2/web/app.py:1268` |

---

## 14. Team-Level Stats

| Stat | Abbr | Meaning | Formula | Source |
|---|---|---|---|---|
| Runs | R | Team runs scored | Σ runs per game | `o27/stats/team.py:15` |
| Outs | OUT | Team out count in a half-inning (caps at 27) | counted | `o27/stats/team.py:16` |
| Hits | H | Team hits | Σ per game | `o27/stats/team.py:17` |
| Stays | STY | Team stay events | Σ per game | `o27/stats/team.py:18` |
| Run Rate | R/OUT | O27's primary efficiency stat (~0.43 league avg) | `R / OUT` | `o27/stats/team.py:22-26` |
| Target Runs | TARGET_R | Home team's target after the away half ends | set on inning end | `o27/stats/team.py:19` |
| Required Run Rate | REQ_RR | R/OUT needed from current state to win | `(target − scored) / outs_remaining` | `o27/stats/team.py:29-40` |
| Required Run Rate (Full) | REQ_RR_FULL | Required efficiency over the full inning | `target / 27` | `o27/stats/team.py:42-47` |
| Net Run Rate | NRR | Cricket-style multi-game tiebreaker | `(R_for / OUT_faced) − (R_against / OUT_bowled)` | `o27/stats/team.py:49-57` |
| Wins | W | Season wins | counted | `o27v2/db.py:33` |
| Losses | L | Season losses | counted | `o27v2/db.py:34` |
| Win % | W% | Winning percentage | `W / (W + L)` | `o27v2/web/app.py:303-307` |
| Games Behind | GB | Deficit to division leader | `(leader_W − team_W + team_L − leader_L) / 2` | `o27v2/web/app.py:310-314` |

---

## 15. Sabermetric Analytics

### Run Expectancy (RE24-O27)

| Metric | Meaning | Computation | Source |
|---|---|---|---|
| RE24 Matrix | Expected runs from `(bases, outs)` state | Mean future runs per cell | `o27v2/analytics/run_expectancy.py:100-178` |
| RE by Outs Remaining | 1D run-expectancy curve | Mean future runs per outs-recorded | `o27v2/analytics/run_expectancy.py:181-221` |
| RE by Bases | Collapsed-by-outs run expectancy by base state | Mean future runs per bases mask | `o27v2/analytics/run_expectancy.py:224-248` |
| Base State | 3-bit mask: bit0=1B, bit1=2B, bit2=3B | derived | `o27v2/analytics/run_expectancy.py:34-44` |
| Outs Bucket | 9 grouped buckets (0-2, 3-5, …, 24-26) for the 27-out arc | derived | `o27v2/analytics/run_expectancy.py:56-68` |

### Pythagorean

| Metric | Meaning | Formula | Source |
|---|---|---|---|
| Pythag Win % (fitted) | Empirically-fit exponent for the O27 run environment | `R^k / (R^k + RA^k)`, `k*` minimizes MSE | `o27v2/analytics/pythag.py:62-140` |
| Pythag Wins (fitted) | Expected wins | `Pythag% × G` | `o27v2/analytics/pythag.py:122-124` |
| Luck (fitted) | Over/underperformance | `Actual_W − Pythag_W_fitted` | `o27v2/analytics/pythag.py:126` |
| Pythag Win % (MLB 1.83) | MLB-default exponent reference | `R^1.83 / (R^1.83 + RA^1.83)` | `o27v2/analytics/pythag.py:110-111` |
| Luck (MLB) | Over/under vs MLB-default | `Actual_W − Pythag_W_default` | `o27v2/analytics/pythag.py:125` |

---

## 16. Awards

| Award | Criteria | Source |
|---|---|---|
| MVP | Highest OPS among PA-qualifiers | `o27v2/awards.py:100-170` |
| Cy Young | Lowest ERA among outs-qualifiers | `o27v2/awards.py:172-180` |
| Rookie of the Year | Best rookie ≤23: `max(OPS / 0.800, 4.0 / ERA)` | `o27v2/awards.py:182-208` |
| World Series MVP | Highest OPS (bat) or ERA-inverted score (arm) in the series | `o27v2/analytics/awards.py:213-286` |

---

## 17. Game-Level Fields

| Field | Meaning | Source |
|---|---|---|
| Game Date | Sim date of the game | `o27v2/db.py:114` |
| Home / Away Score | Final runs by team | `o27v2/db.py:117-118` |
| Winner ID | Winning team (NULL while super-innings unresolved) | `o27v2/db.py:119` |
| Super-Inning Count | Rounds played past regulation if tied | `o27v2/db.py:120` |

---

## 18. Player Attributes (20–80 Scout Scale)

These are inputs to outcome probability rather than stats produced by play. They show up across reports and influence everything above.

| Attribute | Meaning | Source |
|---|---|---|
| Skill / Contact | Batter's contact ability | `o27v2/db.py:59, 74` |
| Power | Slugging / HR potential | `o27v2/db.py:75` |
| Eye | Plate discipline / walk ability | `o27v2/db.py:76` |
| Speed | Raw foot speed | `o27v2/db.py:60` |
| Baserunning | Reads & route quality on the bases | `o27v2/db.py:93` |
| Run Aggressiveness | Willingness to take extra bases | `o27v2/db.py:94` |
| Pitcher Skill / Stuff | K-stuff | `o27v2/db.py:61, 75` |
| Command | Walk control | `o27v2/db.py:77` |
| Movement | Pitch movement quality | `o27v2/db.py:78` |
| Stamina | Long-outing durability | `o27v2/db.py:71` |
| Defense (general) | General fielding ability | `o27v2/db.py:86` |
| Arm | Throwing strength/accuracy | `o27v2/db.py:87` |
| Defense IF / OF / C | Position-group defense | `o27v2/db.py:88-90` |
| Work Ethic | Season-long performance lift | `o27v2/db.py:106` |
| Work Habits | Context-dependent skill multiplier | `o27v2/db.py:107` |

---

## 19. Outcome Modifiers (not stats, but commonly confused for them)

These shift probabilities and feed into the stats above; they don't appear directly on stat lines.

| Modifier | Meaning | Source |
|---|---|---|
| Dominance Modifiers | Strike-rate / contact-rate shifts per attribute | `o27/config.py:90-101` |
| Stay Aggressiveness | How often a batter chooses to stay | `o27v2/db.py:62` |
| Contact Quality Threshold | Eye-vs-command modifier for 2C conversion | `o27v2/db.py:63` |
| Hard Contact Delta | Extra-base hit weighting | `o27v2/db.py:66` |
| HR Weight Bonus | HR-frequency lift | `o27v2/db.py:67` |
| Park Factors | HR + hit multipliers by venue | `o27v2/db.py:35-36` |
| Weather Modifiers | Temperature / wind / humidity / precipitation / cloud effects | `o27v2/db.py:126-130` |
| Manager Tendencies | Hook timing, bullpen aggression, joker usage, pinch-hitting, etc. | `o27v2/db.py:39-47` |

---

## Quick Glossary

- **Arc** — One of three 9-out segments of a 27-out outing (Arc 1: outs 1–9, Arc 2: 10–18, Arc 3: 19–27+SI). Used to weight when damage happens.
- **SI** — Super-Innings: extra innings, played as additional outs after regulation if the game is tied. Counted into Arc 3.
- **Stay (2C)** — O27's second-chance hit: a batter can elect to stay in the AB to advance runners. Drives the `STY*`, `MHAB`, `2C_*`, and Stay-related rate stats.
- **Foul-Out (FO)** — O27 retires a batter on the third foul ball in the AB. FO is treated like a strikeout for K%/Decay purposes but tracked separately.
- **PAVG vs BAVG** — PAVG is per-PA (cleanest "true" rate); BAVG is per-AB and can exceed 1.0 because of multi-hit ABs from stays. Their gap (`STAY_DIFF`) is an O27-native productivity signal.
- **Replacement level** — Used in VORP/WAR; computed from league offense/defense with O27-tuned constants.
- **`runs_per_win`** — Conversion factor from runs to wins, derived from O27's run environment (see `o27v2/analytics/pythag.py`).
