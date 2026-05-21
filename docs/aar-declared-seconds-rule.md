# After-Action Report — The Declared Seconds Rule (Reference)

**Date written:** 2026-05-21
**Branch:** `claude/fix-seconds-calculation-HszBM`
**PR:** #73

This is a reference AAR, not a change log: Declared Seconds was built incrementally across
several earlier branches and never had a single document explaining the rule end-to-end. A
box-score reader getting confused by a `-` in the seconds column (the trigger for this PR)
showed the gap. Everything below is the mechanic as it stands today, with file:line anchors.

---

## What Declared Seconds is

O27 plays **27-out halves**, not 9 innings. Each team bats once in regulation and is entitled
to 27 outs. **Declared Seconds** is an optional manager decision that lets the batting team
*end their regulation half early* and **bank** the unused outs for a second bite at the plate
later — a "rebuttal" half that runs after both teams' regulation halves are done.

The trade is: you give up the rest of your current at-bats (any runners on base are stranded)
in exchange for a fresh half later, against a fresh defensive alignment, with the lineup
picking up where it left off. It's a tempo/insurance lever — bank a few outs while leading to
guarantee yourself the last word, or while trailing to get a clean reset against a tired staff.

---

## The numbers (all in `o27/config.py:716-730`)

| Constant | Value | Meaning |
|----------|-------|---------|
| `SECONDS_MIN_DECLARE_OUT` | 22 | Earliest out you may declare. Declaring at out 22 banks 5 outs. |
| `SECONDS_MAX_DECLARE_OUT` | 26 | Latest out that still banks ≥ 1 (at out 27 there's nothing left). |
| `SECONDS_MAX_BANKED` | 6 | Hard cap on banked outs regardless of when you declare. |
| `SECONDS_MAX_ROUNDS_PER_TEAM` | 2 | Semantics: one regulation half + at most one seconds half per team. |
| `SECONDS_INSURMOUNTABLE` | 25 | Lead margin that triggers insurance-style declaration logic. |
| `SECONDS_BLOWOUT_MARGIN` | 20 | At/above this margin the game is treated as decided. |
| `DECLARE_BASE` / `DECLARE_LEAD_SCALE` / `DECLARE_PERSONA_SCALE` | 0.04 / 0.18 / 0.25 | Soft-formula weights. |

Banked outs = `min(27 − declared_at_out, SECONDS_MAX_BANKED)`
(`o27/engine/manager.py:1747`, restated in `o27/engine/game.py:527-528`). So declaring at out
22 → 5 banked, out 26 → 1 banked. There are **no** v2 overrides; `o27v2` inherits these.

---

## When the decision is made

The declaration AI is polled **before every plate appearance** once the team reaches the
window, via `ProbabilisticProvider._try_manager_action` → `manager.evaluate_declaration`
(`o27/engine/manager.py:1595`). It runs *instead of* the upcoming PA when it fires, so the half
ends cleanly: `state.outs` is set to 27 (`o27/engine/pa.py:396`), `is_half_over()` returns True,
runners on base are credited to LOB (`o27/engine/pa.py:392-395`), and the team's
`declared_at_out`, `declare_score_for`, and `declare_score_against` are stamped at the moment of
declaration (`o27/engine/manager.py:1737-1744`).

> The stamped score is **as-of declaration**, not final. This is why a box can read
> `ABQ o26 (17-0)` even though the opponent finishes with 16 — when ABQ declared, the opponent
> hadn't batted yet (ABQ batted first that game).

### The AI target-save model (`evaluate_declaration`, manager.py:1595-1749)

Hard gates first: never in super-innings or an existing seconds phase; only at out 21+; never
at/after out 27; only once per team. Then a continuous `target` ("how many outs is it worth
banking?") starts at **−3.0** — deliberately deep so that no single signal declares; it takes
several stacking pressures. Contributing factors:

- **Score differential** (−1.5 … +3.0): a comfortable lead (≥10) or a real deficit (≤−4) both
  push toward banking.
- **Pitcher fatigue tier** (−0.5 … +2.5): an `in_decay` arm is the strongest single nudge; a
  `fresh` one suppresses.
- **Lineup position** (−1.5 … +0.8): heart of the order due suppresses; bottom of the order due
  encourages.
- **Bullpen depth** (own and opponent), **opponent starter dealing**, and a key override: if
  the opponent already declared, `target = max(target, opp_banked * 0.8)` so you don't get
  out-banked and lose the last word.
- **Park / weather** offense skew (small).
- **Manager aggression persona**: multiplicative, `target *= (0.5 + agg)`, agg ∈ [0,1] (0.5 =
  neutral = 1.0×). Plus small Gaussian noise on marginal targets only, to avoid deterministic
  threshold behavior while not perturbing RNG on clearly-no/clearly-yes PAs.

Final: `target_int = clamp(round(target), 0, 6)`; **declare now** iff `target_int > 0` and
`outs_left ≤ target_int`.

---

## How the seconds round runs (`_run_seconds_rounds`, game.py:532-596)

The rebuttal halves run **in the same order as regulation** — driven by `first_batting_team` /
`second_batting_team`, *not* by home/away. Symmetric to top/bottom of the 9th:

1. **`seconds_first`** — the first-batting team plays its banked outs if it has any, **always**,
   even when leading (like the visitors always batting the top of the 9th). Never walks off.
2. **`seconds_second`** — the second-batting team plays only if it is **not already ahead**
   after the first team's rebuttal (`sb_score ≤ fb_score`, game.py:590-593). If it is ahead, the
   half is skipped (walk-off shortcut, like the home team not batting in a won bottom-9th). If it
   does play, it **walks off the instant it retakes the lead** — the first team has spent its
   seconds and can't answer.
3. A team that banked **0 outs has no seconds half at all.** Each team plays at most one.

Out-cap enforcement lives in `GameState.is_half_over` (`o27/engine/state.py:655-665`): in a
seconds phase the half ends at `outs ≥ batting_team.outs_banked` *or* `_seconds_walkoff()`. The
walk-off rules themselves are `_seconds_walkoff` (state.py:688-707, only fires in
`seconds_second` on a lead change) and the regulation counterpart `_regulation_walkoff`
(state.py:667-686, only in the regulation second half, lead held, first team out of banked
outs). When a seconds half starts, `_set_fielding_pitcher` (game.py:565) re-points the mound
because the fielding side has flipped. The batting lineup is *not* reset — it resumes where
regulation left off (game.py:563).

---

## Bat order (`should_bat_first`, manager.py:1558-1592)

Before the game, the **home** manager chooses to bat first or second. Base probability is a true
coin flip (`BAT_FIRST_BASE = 0.50`), and situational scalars (park, starter drag, opp bullpen,
persona, weather) are clamped to **±1 percentage point** by `BAT_FIRST_HOME_EDGE_CAP = 0.01`,
so the choice can't become a hidden home advantage. The choice assigns
`first_batting_team` / `second_batting_team` (game.py:86-94), which is what the seconds round
keys off — so "who bats first in seconds" follows the pre-game choice, independent of
home/away.

---

## Persistence & box-score rendering

**Games table** (`o27v2/db.py:194-209`) stores: `home_bats_first`; `{away,home}_declared_at`;
`{away,home}_seconds_used`; `{away,home}_declare_context` ("leading"/"trailing"/"tied");
`{away,home}_declare_score_for` / `_against`; and `seconds_outcome`
("seconds_fired" / "walkoff" / "regulation_walkoff" / "no_seconds"). These are written from the
final engine state in `o27v2/sim.py:1528-1597`.

**Footer line** (`o27v2/web/box_score.py:449-487`, `render_game_notes`): a balk-style one-liner.
Each declaration renders as `TEAM oN (X-Y)` where N is `declared_at`, X is the declaring team's
score and Y the opponent's, **both as of declaration**. Multiple declarations are
comma-separated under one `Seconds:` prefix, e.g.:

```
Seconds: NYY o24 (7-5), BOS o25 (6-8).
```

In the **line score**, regulation collapses to column "1" and the seconds round to column "2"
(`box_score.py:124-194`). A team that didn't bank outs has no seconds half, so its column-2
cell is `-`. **This `-` is correct, not a bug** — it's the most common point of reader
confusion (and the one that prompted this PR).

---

## Tests

- `tests/test_declared_seconds.py` — eligibility gates, the two-layer decision (blowout vs.
  tight, persona scaling), regulation and seconds walk-off rules, half caps (27 in regulation,
  banked-outs in seconds), bat-first ~50% with a binomial CI, and end-to-end integration
  (declares stamp, seconds run first→second, first team uses full allotment, second team skips
  when already ahead).
- `tests/test_stat_invariants.py` — seconds-phase out caps are respected per game, declaration
  values are consistent with half lengths, and stranded runners aren't double-counted in LOB.

---

## Things to remember

- **27 outs per half, not 9 innings.** "out 26" means the 26th of 27 regulation outs, not the
  26th inning. `declared_at_out` lives on a 0-27 scale.
- **The footer score is as-of declaration.** It can legitimately differ from the final score and
  from what the opponent eventually puts up — especially when the declaring team batted first.
- **`-` in the seconds column = the team banked no outs**, so it had no rebuttal half. Expected.
- **Seconds order follows the pre-game bat-order choice**, not home/away. `seconds_first` never
  walks off; `seconds_second` walks off on a lead change and is skipped entirely if already
  ahead.
- All seconds tuning lives in `o27/config.py:716-730`; there are no `o27v2` overrides.
