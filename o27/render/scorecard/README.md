# O27 Printable Scorecard

A fork of the Swingley/Vicyorus baseball scorecard, modified to handle the
O27-specific surface (cycles instead of innings, 12-deep lineup, stay
tickmarks, jokers, Walk-Back, Declared Seconds).

## Upstream

- `templates/o27_scorecard.mp` is a fork of Christopher Swingley's
  baseball scorecard Metapost (2005, GPL-2), packaged on PyPI as
  `baseball_scorecard` by Vicyorus.
- `templates/final_scorecard.tex` is the upstream wrapper, unmodified.
- `LICENSE.upstream` is Vicyorus's project license (GPL-3); the embedded
  Swingley template retains its own GPL-2 header inside the `.mp` file.

The upstream Python adapter (`baseball_scorecard` on PyPI) is reusable as
the input layer — we feed it sim PBP via a thin adapter (`driver.py`,
WIP), and it emits Metapost which the fork renders.

## Toolchain

```
make            # full render of a sample game (driver → mp → pdf)
make clean
```

Requires: `texlive-metapost`, `texlive-latex-extra`, `pdflatex`, `mpost`,
`mptopdf`, and the `baseball_scorecard` PyPI package.

## O27 grafts (in progress)

The structural axis of the card is the **out number** (1 → 27 in
regulation, 28+ in extras). Innings don't exist on an O27 scorecard —
Declared Seconds and extras are both just continuations of the same
out-counter ruler. Everything else hangs off that ruler.

- [ ] Out-counter ruler at the top of the grid (replacing the inning
      number header). Continuous 1 → 27, then 28+ for extras.
- [ ] Column header label: INNINGS → OUT (singular ruler, not segmented)
- [ ] Lineup grid: 9 rows → 12 rows (8 fielders + SP + 3 DH)
- [ ] PA cells laid out against the out ruler rather than into fixed
      inning columns — column width follows PA sequence
- [ ] Stay tickmarks (1–3) in diamond corner
- [ ] Joker cooldown ticks (resets when the lineup turns over — visible
      from the lineup row but not a structural column)
- [ ] Walk-Back annotation + side-margin runner tracker
- [ ] Pitcher arc bar across the bottom plotted against the out ruler
- [x] Declared Seconds: a thick vertical decoration line at the column
      where the manager declared, plus a freeform "declared at out N"
      entry in the Notes area. Nothing drawn when no declaration
      happened. (Declaring is regulation-only — extras must be played
      out, see top-level README rule.) Macro: `draw_declared_seconds_divider`.
- [ ] Extras: outs 28+ on the same ruler, no special block
