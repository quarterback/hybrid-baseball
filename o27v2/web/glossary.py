"""Stat glossary — single source of truth for the /glossary page and the
click-through anchors on the Leaders page.

Each entry's `key` doubles as the anchor id (`g-<key>`) AND matches the
stat key used by the leaderboard `card()` macro, so a label on Leaders can
deep-link straight to its definition with `url_for('glossary') + '#g-' + key`.

Definitions are kept terse — they mirror the inline tooltips already in the
templates so the two never disagree. Longer-form prose lives in
docs/stats-reference.md and docs/pitching-stats-027.md.
"""

from __future__ import annotations

# Each section: {"title", "blurb", "entries": [{"key", "abbr", "name", "desc"}]}
GLOSSARY_SECTIONS: list[dict] = [
    {
        "title": "Batting · Headline & Rate",
        "blurb": "PA-denominated rate stats and league-relative indices.",
        "entries": [
            {"key": "pavg", "abbr": "PAVG", "name": "Plate Average",
             "desc": "Hits divided by plate appearances (H / PA). O27's headline batting average — denominated by PA rather than AB so the 2C mechanic doesn't distort it."},
            {"key": "bavg", "abbr": "BAVG", "name": "Batting Average",
             "desc": "Traditional hits per at-bat (H / AB). Shown alongside PAVG; the gap between them (Δ2C) is the value added by Second-Chance at-bats."},
            {"key": "avg", "abbr": "AVG", "name": "Batting Average",
             "desc": "Hits per at-bat. On the leaderboard's MLB-Equivalent (XO) cards this is the crossover-scaled value; natively O27 leads with PAVG."},
            {"key": "obp", "abbr": "OBP", "name": "On-Base Percentage",
             "desc": "Rate of reaching base safely: (H + BB + HBP) / (AB + BB + HBP + SF)."},
            {"key": "slg", "abbr": "SLG", "name": "Slugging Percentage",
             "desc": "Total bases per at-bat — weights extra-base hits by their base value."},
            {"key": "ops", "abbr": "OPS", "name": "On-Base Plus Slugging",
             "desc": "OBP + SLG. A quick one-number read of overall offensive production."},
            {"key": "ops_plus", "abbr": "OPS+", "name": "OPS Plus",
             "desc": "OPS relative to league average, scaled so 100 = league average and higher is better."},
            {"key": "iso", "abbr": "ISO", "name": "Isolated Power",
             "desc": "SLG − BAVG. Measures raw extra-base power, stripping out singles."},
            {"key": "babip", "abbr": "BABIP", "name": "Batting Avg on Balls in Play",
             "desc": "Hit rate on balls put in play (excludes HR and strikeouts). High values can signal good contact or good luck."},
            {"key": "woba", "abbr": "wOBA", "name": "Weighted On-Base Average",
             "desc": "O27-tuned linear weights, PA-denominated. Each offensive event is weighted by its run value, then scaled to the OBP range."},
            {"key": "woba_plus", "abbr": "wOBA+", "name": "wOBA Plus",
             "desc": "wOBA relative to league average (100 = average, higher = better)."},
            {"key": "wrc_plus", "abbr": "wRC+", "name": "Weighted Runs Created Plus",
             "desc": "Park-adjusted, league-relative offense. 100 = league average at this park; >100 = better than league after stripping park bias."},
        ],
    },
    {
        "title": "Batting · Power & Counting",
        "blurb": "Raw totals.",
        "entries": [
            {"key": "hr", "abbr": "HR", "name": "Home Runs", "desc": "Home runs hit."},
            {"key": "rbi", "abbr": "RBI", "name": "Runs Batted In", "desc": "Runs driven in by the batter."},
            {"key": "h", "abbr": "H", "name": "Hits", "desc": "Total hits."},
            {"key": "r", "abbr": "R", "name": "Runs", "desc": "Runs scored."},
            {"key": "sb", "abbr": "SB", "name": "Stolen Bases", "desc": "Bases stolen successfully."},
            {"key": "fo", "abbr": "FO", "name": "Foul-Outs",
             "desc": "Times the batter was retired under the O27 3-foul rule (three fouls in a PA = out). A subset of total outs, tracked separately from strikeouts."},
            {"key": "fo_pct", "abbr": "FO%", "name": "Foul-Out Rate",
             "desc": "Foul-outs per plate appearance (FO / PA) — how often a hitter fouls himself out. O27-specific; lower is better for the batter."},
        ],
    },
    {
        "title": "Batting · O27-Native (Second Chance)",
        "blurb": "The load-bearing O27 mechanic: on a contact event the batter may 'stay' (spend a strike from their AB budget) instead of running.",
        "entries": [
            {"key": "stays", "abbr": "2C", "name": "Second-Chance ABs",
             "desc": "Contact events where the batter chose to stay (spending a strike) instead of running. The signature O27 decision."},
            {"key": "stay_hits", "abbr": "2C-H", "name": "Second-Chance Hits",
             "desc": "Hits credited on 2C events (a subset of total H)."},
            {"key": "stay_conv_pct", "abbr": "2C-Conv%", "name": "Second-Chance Conversion %",
             "desc": "Fraction of 2C events that credited a hit. Talent-weighted by the batter's eye vs the pitcher's command on subsequent swings."},
            {"key": "stay_rbi", "abbr": "2C-RBI", "name": "Second-Chance RBI",
             "desc": "RBI driven by 2C events — surfaces 2C-mechanic specialists."},
            {"key": "stay_rbi_pct", "abbr": "2C-RBI%", "name": "Second-Chance RBI Share",
             "desc": "Share of the batter's total RBI that came from 2C events."},
            {"key": "stay_diff", "abbr": "Δ2C", "name": "2C Average Lift",
             "desc": "BAVG − PAVG; quantifies the hit value added by the Second-Chance mechanic."},
        ],
    },
    {
        "title": "Batting · Runner Advancement",
        "blurb": "How well a batter moves the runners already on base. The starting base refers to where the runner began the PA, NOT the hit type.",
        "entries": [
            {"key": "adv_1b_pct", "abbr": "1B%", "name": "Advance from First %",
             "desc": "Rate at which the runner who started on FIRST advanced to a higher base or scored during this batter's PA."},
            {"key": "adv_2b_pct", "abbr": "2B%", "name": "Advance from Second %",
             "desc": "Rate at which the runner who started on SECOND advanced to third or scored during this batter's PA."},
            {"key": "adv_3b_pct", "abbr": "3B%", "name": "Advance from Third %",
             "desc": "Rate at which the runner who started on THIRD scored during this batter's PA — the pure RBI-conversion metric."},
            {"key": "adv_total_pct", "abbr": "All%", "name": "Overall Advancement %",
             "desc": "Overall advancement rate across all PAs with any runner on base. Composite of 1B%/2B%/3B% weighted by opportunity."},
            {"key": "rad_total", "abbr": "RAD", "name": "Runners Advanced",
             "desc": "Total bases gained by runners during this batter's PAs. The runner-movement analogue of MLB Total Bases."},
        ],
    },
    {
        "title": "Fielding",
        "blurb": "PO/E credited per fielder via position-weighted attribution on each ball in play.",
        "entries": [
            {"key": "po", "abbr": "PO", "name": "Putouts", "desc": "Outs recorded as the primary fielder on a play."},
            {"key": "a", "abbr": "A", "name": "Assists", "desc": "Throwing outs and double/triple-play chain pivots."},
            {"key": "chances", "abbr": "TC", "name": "Total Chances", "desc": "PO + A + E — total fielding opportunities."},
            {"key": "rf", "abbr": "RF", "name": "Range Factor",
             "desc": "(PO + A) × 27 / outs the fielder's team played. Higher = more involvement per inning."},
            {"key": "fld_pct", "abbr": "FldPct", "name": "Fielding Percentage",
             "desc": "(PO + A) / (PO + A + E). Share of chances handled cleanly."},
            {"key": "e", "abbr": "E", "name": "Errors", "desc": "Misplays charged to the fielder. Lower is better."},
            {"key": "drs", "abbr": "DRS", "name": "Defensive Runs Saved",
             "desc": "Runs a fielder saved (or cost) relative to an average defender at the position."},
        ],
    },
    {
        "title": "Pitching · Result-Tier",
        "blurb": "O27's run-prevention trio. The 27-out structure and lineup-cycling make raw ERA misleading, so these correct for arc position and contact quality.",
        "entries": [
            {"key": "werra", "abbr": "wERA", "name": "Weighted ERA",
             "desc": "Earned runs weighted by arc position (outs 1-9 ×0.85, 10-18 ×1.00, 19-27 ×1.20), league-anchored to raw ER/27. Lower is better."},
            {"key": "wera_plus", "abbr": "wERA+", "name": "Weighted ERA Plus",
             "desc": "Park-adjusted, league-relative wERA on the ERA+ scale (100 = average, higher = better)."},
            {"key": "xra", "abbr": "xRA", "name": "Expected Runs Allowed",
             "desc": "Non-negative linear-weights estimate of runs allowed (HR≈1.4, single≈0.45, BB/HBP≈0.32), anchored so league xRA = league wERA. Lower is better."},
            {"key": "decay", "abbr": "Decay", "name": "Late-Arc Decay",
             "desc": "Drift-corrected late-arc fade in K% points. 0 = matches league norm; positive = fades worse; negative = holds up better. Lower is better."},
            {"key": "late_k_pct_pct", "abbr": "LateK%", "name": "Late K Rate",
             "desc": "Arc-3 K% (outs 19-27, including foul-outs). The short-relief sibling to Decay for arms that never see arc-1 sample."},
            {"key": "gsc_plus", "abbr": "GSc+", "name": "Game Score Plus",
             "desc": "League-relative Game Score (100 = league average, higher = better)."},
            {"key": "gsc_index", "abbr": "GSc Index", "name": "Game Score Index",
             "desc": "Z-score-normalized Game Score on a 100/15 scale. Accounts for the spread of pitcher talent, so it's comparable across league sizes."},
        ],
    },
    {
        "title": "Pitching · Workload & Stuff",
        "blurb": "Per-appearance volume and dominance.",
        "entries": [
            {"key": "gsc_avg", "abbr": "GSc avg", "name": "Game Score (avg)",
             "desc": "Average per-appearance Game Score (50 = neutral, 100 = perfection)."},
            {"key": "os_plus", "abbr": "OS+", "name": "Outs Share Plus",
             "desc": "League-relative outs per appearance (100 = average). A workload index."},
            {"key": "k_pct", "abbr": "K%", "name": "Strikeout Rate",
             "desc": "(K + foul-outs) / batters faced. O27 K% counts foul-outs as strikeouts."},
            {"key": "bb_pct", "abbr": "BB%", "name": "Walk Rate",
             "desc": "Walks per batter faced. Lower is better."},
            {"key": "k_minus_bb_pct", "abbr": "K-BB%", "name": "K minus BB Rate",
             "desc": "(K − BB) / batters faced. A quick-read dominance signal."},
            {"key": "fo_pct_pit", "abbr": "FO%", "name": "Foul-Out Rate",
             "desc": "Foul-outs induced per batter faced (FO / BF) — how often a pitcher fouls hitters out. The foul-out slice of the K% blend; higher is better."},
        ],
    },
    {
        "title": "Pitching · Counting & Value",
        "blurb": "Raw totals and wins above replacement.",
        "entries": [
            {"key": "w", "abbr": "W", "name": "Wins", "desc": "Wins credited. An SP earns it with ≥12 outs and the lead; otherwise the most-effective reliever."},
            {"key": "k", "abbr": "K", "name": "Strikeouts", "desc": "Strikeouts recorded."},
            {"key": "fo_induced", "abbr": "FO", "name": "Foul-Outs Induced",
             "desc": "Batters the pitcher retired under the O27 3-foul rule. Tracked separately from strikeouts, though K% folds the two together."},
            {"key": "outs", "abbr": "Outs", "name": "Outs Recorded", "desc": "Pitcher workload in outs (one full O27 game = 27)."},
            {"key": "war", "abbr": "WAR", "name": "Wins Above Replacement",
             "desc": "Total value over a replacement-level player, in wins. Uses an O27-fitted runs-per-win factor (~21 vs MLB's ~10)."},
        ],
    },
    {
        "title": "Pitching · Walk-Back & Arsenal",
        "blurb": "O27-specific rules and pitch-mix usage.",
        "entries": [
            {"key": "wb_stop_pct", "abbr": "Walk-Back Stop%", "name": "Walk-Back Stop Rate",
             "desc": "Strand rate for the rule-placed Walk-Back runner. After every HR, the next PA can drive home the HR-hitter from 3B for an extra unearned run. Higher = better."},
            {"key": "fastball_pct", "abbr": "FB%", "name": "Fastball Usage",
             "desc": "Share of typed pitches that were fastballs (4-seam / sinker / cutter)."},
            {"key": "breaking_pct", "abbr": "BR%", "name": "Breaking-Ball Usage",
             "desc": "Share of typed pitches that were breaking balls."},
            {"key": "offspeed_pct", "abbr": "OFF%", "name": "Off-Speed Usage",
             "desc": "Share of typed pitches that were off-speed (change / split / palm / knuckle / eephus)."},
        ],
    },
    {
        "title": "Pitching · MLB-Readable Rates",
        "blurb": ("Familiar MLB-shaped rate stats. On the leaderboard these honor the "
                  "XO Crossover toggle; the MLB-Equivalent section always shows the "
                  "z-anchored version."),
        "entries": [
            {"key": "era", "abbr": "ERA", "name": "Earned Run Average",
             "desc": "Earned runs × 27 / outs (O27's 27-out game). Lower is better."},
            {"key": "whip", "abbr": "WHIP", "name": "Walks + Hits per IP",
             "desc": "(BB + H) per inning pitched. Lower is better."},
            {"key": "k9", "abbr": "K/9", "name": "Strikeouts per 9",
             "desc": "Strikeouts per nine innings. Higher is better."},
            {"key": "bb9", "abbr": "BB/9", "name": "Walks per 9",
             "desc": "Walks per nine innings. Lower is better."},
            {"key": "hr9", "abbr": "HR/9", "name": "Home Runs per 9",
             "desc": "Home runs allowed per nine innings. Lower is better."},
            {"key": "oavg", "abbr": "oAVG", "name": "Opponent Batting Average",
             "desc": "Batting average of the hitters this pitcher faced. Lower is better."},
            {"key": "oobp", "abbr": "oOBP", "name": "Opponent On-Base %",
             "desc": "On-base percentage allowed. Lower is better."},
            {"key": "oslg", "abbr": "oSLG", "name": "Opponent Slugging",
             "desc": "Slugging percentage allowed. Lower is better."},
            {"key": "oops", "abbr": "oOPS", "name": "Opponent OPS",
             "desc": "OBP + SLG allowed. Lower is better."},
            {"key": "babip_allowed", "abbr": "BABIP", "name": "Opponent BABIP",
             "desc": "Batting average on balls in play allowed (excludes HR and strikeouts)."},
        ],
    },
    {
        "title": "Win Probability & Leverage",
        "blurb": "Built empirically from this league's own outcomes — every PA's pre/post state joined to the final result.",
        "entries": [
            {"key": "wpa", "abbr": "WPA", "name": "Win Probability Added",
             "desc": "Sum of the win-probability swing across a player's PAs. Positive = the player moved the needle for his team."},
            {"key": "li_avg", "abbr": "LI", "name": "Leverage Index (avg)",
             "desc": "Average leverage of the situations a player appeared in. 1.0 = league norm; >1 = disproportionately high-stakes spots."},
        ],
    },
    {
        "title": "MLB-Equivalent (XO Crossover)",
        "blurb": ("O27 rate stats z-anchored to MLB league mean and spread, so the "
                  "numbers read like MLB stats while preserving rank order exactly. "
                  "Formula: xo = MLB_mean + ((value − O27_mean) / O27_sd) × MLB_sd. "
                  "XO is a reading layer — it never changes an underlying calculation. "
                  "Counting stats (HR, RBI, K) have no XO equivalent and pass through unchanged."),
        "entries": [
            {"key": "xo", "abbr": "XO", "name": "Crossover",
             "desc": "The crossover scale itself. Any rate stat shown on the XO scale carries the same player ranking as its native version — only the number translates to an MLB-readable value."},
        ],
    },
]

# Flat lookup: key -> entry. Used to validate that a Leaders card has a
# matching glossary entry (so we only render a link when the anchor exists).
GLOSSARY_BY_KEY: dict[str, dict] = {
    e["key"]: e for sec in GLOSSARY_SECTIONS for e in sec["entries"]
}
