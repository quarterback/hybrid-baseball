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

- [ ] Column header: INNINGS → CYCLES
- [ ] Lineup grid: 9 rows → 12 rows (8 fielders + SP + 3 DH)
- [ ] Stay tickmarks (1–3) in diamond corner
- [ ] Joker cooldown strip per cycle
- [ ] Walk-Back annotation + side-margin runner tracker
- [ ] Pitcher arc bar across the bottom (by out-range, not by inning)
- [ ] Declared Seconds banked/spent ledger
