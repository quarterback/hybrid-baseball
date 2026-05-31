# Report — Nickel fielders as hitters

**Why this exists:** the original Power Play spec said the nickel (the 10th
defender) would **never hit**. In play, nickels *do* sometimes appear in the
batting line — e.g. `e-Roby nf … 3 AB, 1 H` with the footnote
"Replaced — at NF in the 3rd." This report explains exactly how that happens,
why it's currently legal, and the operator's refined rule for how it *should*
work.

---

## 1. The original spec

The nickel is a **defensive** addition: a fielding manager deploys a 10th
defender for a short, use-or-lose window. He cuts off gaps and tightens the
unit. The spec assumed he is a pure glove — **he does not bat**.

## 2. What actually happens (current behavior)

Two facts combine:

1. **The Power Play deployment is defense-only and virtual.** `power_play.py`
   never adds the nickel to the batting order — it only reads the lineup (to
   exclude on-field players from nickel selection), sets window state, and
   applies the catch-conversion + presence effects. Neither the manager AI nor
   the game loop turns a deployment into a batting-lineup move. *Deploying the
   nickel cannot, by itself, give him a plate appearance.*

2. **The nickel is a real bench player, and the manager can use him for normal
   substitutions.** The same bench glove that gets picked as the nickel is also
   a candidate for the manager's ordinary pinch-hit / pinch-run / defensive-sub
   decisions. When the manager substitutes him **into the batting order** (a
   separate decision), he takes a lineup slot and bats — and his game line is
   tagged `NF` (the nickel position) by the deploy stamp.

So "the nickel hit" is really: *the player who was the nickel was also,
separately, put into the batting order by a normal substitution.* The NF tag on
the batting row is the honest record of "came in as the nickel and stayed in."

### Why it's legal (no re-entry violation)

Baseball normally forbids re-entry: once you leave the game, you can't come
back. The nickel never *leaves*. When the window closes he is **deactivated, not
removed** — he's still in the game, just no longer the active 10th man. So his
continued presence and any subsequent at-bat don't break re-entry rules. As the
operator put it: *"the power play ends, the nickel isn't removed, just not
active — it's technically legal either way."*

### Where it shows up

- **Box score:** his row shows position `NF` with his batting line, plus a
  footnote like "Replaced — at NF in the 3rd."
- **Player page:** a "🪙 Nickel — 10th defender in N games" badge on the
  Fielding tab.
- **Powerplays footer:** names the nickel and his putouts.

## 3. The refined rule (operator's intended design)

The current path is *incidental* — a nickel bats only because he happened to be
chosen for an unrelated substitution. The operator wants it **principled**:

> A fielding nickel may become a hitter **only by taking over the pitcher's spot
> in the batting order** — effectively becoming a DH. He can do this only because
> he was already in the field (deployed as the nickel). **Once that swap is made,
> if the nickel later leaves, the pitcher has to hit** (you lose the DH, the way
> MLB does when a team forfeits its DH).

In short: nickel-as-hitter should be one sanctioned move — *nickel assumes the
pitcher's bat → becomes a DH* — not "any bench guy who pinch-hit and happened to
also be the nickel."

## 4. Open questions before implementing the refined rule

The refined rule is clean in a traditional lineup, but O27's lineup model needs
translation, so these need the operator's call:

1. **O27 has no pitcher in the batting order.** The lineup is **8 fielders + 3
   jokers** (the jokers ARE the DH role); the pitcher does **not** bat. So
   "replace the pitcher's batting slot" has no direct slot to take. Options:
   - (a) The nickel takes one of the **joker/DH** slots (he becomes a 4th DH-type
     bat, displacing a joker) — closest analog to "becomes a DH."
   - (b) The nickel takes the slot of the **fielder he replaced** when he came in
     (current-ish behavior, just formalized).
   - (c) Re-introduce an actual pitcher batting slot only when a nickel converts —
     the most literal reading of the operator's rule, but the biggest change.

2. **"Once replaced, the pitcher has to hit."** In O27 the pitcher never hits, so
   this consequence only bites under option (c). Under (a)/(b) there's no pitcher
   bat to fall back to — so we'd need a different "cost" (e.g. the nickel
   conversion burns a joker slot for the rest of the game).

3. **Restrict the path:** today *any* bench player can pinch-hit (including the
   nickel). The refined rule implies the nickel's ONLY route to batting is the
   sanctioned conversion — should we actively *prevent* a deployed nickel from
   being used as an ordinary pinch-hitter, leaving only the pitcher-slot/DH
   conversion?

## 5. Status & recommendation

- **Current behavior is not a bug** — it's "technically legal" by the operator's
  own re-entry reasoning, and it's recorded honestly (NF + footnote + badge).
- The refined rule is a **design change to lineup mechanics**, gated on the
  answers in §4 — especially how "the pitcher's slot" maps onto O27's
  no-pitcher-batting, joker-as-DH lineup. Recommend settling §4(1) and §4(2)
  before any implementation.
