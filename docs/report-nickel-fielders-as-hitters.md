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

## 4. How the refined rule maps onto O27 (correcting an earlier error)

> **Correction:** an earlier draft of this report claimed O27 "has no pitcher in
> the batting order" and that "the 3 jokers ARE the DH." That was **wrong** — it
> came from a stale comment in `league.py` (now fixed). The engine
> (`_ordered_lineup`) and every box score play it correctly: **the batting order
> is NINE — the 8 position starters PLUS the starting pitcher, and the pitcher
> bats** (e.g. "N. Shaughnessy p … 3 AB, 1 H"). Jokers are **tactical cut-ins**,
> not a fixed DH slot: the manager may insert a joker in front of any batter at
> most once per time through the order (up to 12 hitters in a cycle), after which
> the order returns to the top. The pitcher keeps his at-bats unless replaced.

Because the pitcher genuinely bats, the operator's rule maps **directly** — no
translation needed:

- A deployed nickel may **take over the pitcher's spot in the batting order**,
  becoming a DH for the pitcher. He's eligible to do this precisely because he's
  already in the field (deployed as the nickel).
- **If the nickel later leaves, the pitcher resumes hitting** — exactly "once
  replaced, the pitcher has to hit" (the MLB DH-forfeit rule).

### What that means to build

1. **Conversion move:** when the manager elects, swap the deployed nickel into
   the pitcher's batting-order slot (nickel hits, pitcher does not). The nickel
   stays the active fielder too — he's a true two-way 10th man until the window
   ends, then a DH.
2. **Forfeit on exit:** if that nickel is later removed/replaced, the pitcher's
   slot reverts to the pitcher hitting (no free re-DH).
3. **Restrict the path (operator's intent):** a deployed nickel's ONLY route to
   batting is this pitcher-slot conversion — he should NOT also be usable as an
   ordinary pinch-hitter for some other lineup spot. (Today any bench bat,
   including the nickel, can be pinch-hit anywhere; the refined rule narrows the
   nickel specifically to the pitcher slot.)

## 5. Status & recommendation

- **Current behavior is not a bug** — it's "technically legal" by the operator's
  own re-entry reasoning, and it's recorded honestly (NF + footnote + badge).
- The refined rule maps **directly** onto O27 (the pitcher really does bat, §4),
  so it's implementable as specified: a deployed nickel may take the pitcher's
  batting slot (becomes a DH); if he leaves, the pitcher hits again; and the
  nickel's only route to batting is that conversion. The build is a lineup-
  mechanics change (the pitcher-slot swap + forfeit-on-exit + restricting the
  nickel's pinch-hit path), not a roster or scoring change.
