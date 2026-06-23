# AAR — Box score: surface pitches, drop the redundant OS%

## The problem

The box-score pitching line led with **BF** and **OS%** and buried the pitch
count (**P**) at the far right, past H/R/ER/BB/K/HR:

```
                         BF  OUT  OS%    H    R   ER   BB    K   HR    P   IR
Waidner .............     9    9  33%    2    0    0    0    4    0   33    -
Rico ................     7    5  19%    2    3    3    1    2    0   38  2-0
```

Two issues, both sharpened by the move to cascading pitching (starts are short,
a half is spread across ~6 arms):

1. **OS% is redundant in a single game.** It's computed as `round(outs/27*100)`
   — a pure restatement of the OUT column (9→33%, 5→19%). It carried no
   information the box didn't already show.
2. **Pitch count is the headline workload number now**, but it was the last
   stat before IR, disconnected from BF/OUT.

## The change

Both box renderers now lead with `BF  OUT  P  P/BF` and drop OS%:

```
                         BF  OUT    P P/BF    H    R   ER   BB    K   HR   IR
Waidner .............     9    9   33  3.7    2    0    0    0    4    0    -
Rico ................     7    5   38  5.4    2    3    3    1    2    0  2-0
Bauman ..............     7    5   22  3.1    2    2    2    2    2    0  1-1
```

- **P** moves into the workload group, next to BF and OUT.
- **P/BF** (pitches per batter) ties the two together — the relationship that
  was missing. High = labouring (Rico, 5.4), low = efficient (Bauman, 3.1).
  Unlike OS%, it's meaningful per-game.
- `o27v2/web/box_score.py` (the live HTML box) and `o27v2/web/box_text.py`
  (the text export) both updated; `box_text` keeps its GSc column.

## Validation

- `o27v2/tests/test_box_score.py` + the box-render tests in
  `tests/test_template_renders.py` pass (they pin IR-last and decision
  rendering, both preserved).
- Eyeballed the rendered table with realistic cascade lines (above).

## What I did NOT change

- **Season views.** OS% / OS+ / AOR remain on the leaderboards and player page,
  where outs-share *is* informative across a season (it's not just OUT/27
  there). This change is scoped to the per-game box, where OS% was redundant.
- **Stat storage.** No DB or aggregator changes — purely a rendering reorder.
