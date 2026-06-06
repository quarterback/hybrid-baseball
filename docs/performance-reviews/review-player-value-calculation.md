# Review Packet — Player Valuation Display Work

**Branch:** `claude/player-value-calculation-YjTrP`
**Date:** 2026-06-06
**Commits:**
- `9acaa46` — Split Est. value from Salary: live market value + surplus/deficit badge
- `5efd612` — Org strength: live roster+bench grade for display and development

This packet is a self-assessment of two related changes made in one session,
both triggered by the same underlying class of problem: **team/player pages
displaying numbers that looked precise but were either duplicated or
disconnected from the thing they claimed to measure.**

---

## What was your objective?

There was no formal spec — both items started as a user pointing at a phone
screenshot and asking "why does this number not make sense?" The objectives
emerged from those questions:

1. **Salary vs Est. value** — the player page showed two identical money figures
   (`Salary ₳223,457`, `Est. value ₳223,457`). Objective: make the two fields
   carry distinct, useful meaning instead of printing the same number twice.
2. **Org Strength** — the team page showed `27 · Sub-Repl` for a 22–11 (.666)
   first-place club. Objective: replace a stale/meaningless grade with a live
   proxy that reflects the actual roster, and (per the user's choice) make
   player development read the same value.

In both cases the real objective was *diagnosis first, then a fix the user had
signed off on* — not just shipping the first plausible change.

## What work did you complete?

**Item 1 — Salary / Est. value (`valuation.py`, `web/app.py`, `player.html`):**
- Added `market_value()` — a live valuation that always derives from
  `trade_value` (skill/age/role/archetype) and ignores the persisted salary.
- Repointed the player page's "Est. value" at `market_value()`, so it can
  diverge from the contract.
- Added a colored `+/-% vs salary` badge (green = bargain, red = overpay),
  hidden when the two match. `estimate_player_value()` behaviour was left
  unchanged for seeding/payroll.

**Item 2 — Org Strength (`league.py`, `development.py`, `web/app.py`,
`team.html`):**
- Added `compute_org_strength()` — a live 20–95 grade: active-roster mean
  composite blended `0.80/0.20` with bench depth.
- Team page now renders the live grade (recomputed each load); persisted column
  no longer displayed.
- Unified player development (`develop_players_for_team`) to read the live grade.
- Left the persisted `org_strength` column as a front-office/auction AI knob.

Each item got an AAR (`docs/aar-player-value-vs-salary.md`,
`docs/aar-org-strength-live-roster-proxy.md`) and was committed and pushed.

## What decisions mattered most?

1. **Treating both as design decisions, not bug fixes.** Neither "fix" had one
   obvious answer. I used `AskUserQuestion` to let the user choose the
   direction (surplus/deficit display; roster+bench proxy; unify development).
   That kept me from shipping a plausible-but-unwanted change.
2. **Splitting the org_strength concept in two rather than overwriting it.** The
   pivotal discovery was that `org_strength` is load-bearing for the *auction*,
   which runs before rosters exist. That ruled out simply making the column
   roster-derived. The right call was a new live grade for display+development,
   leaving the persisted column as a separate front-office knob.
3. **Not touching auction/FA/trade balance.** The user asked to unify
   *development*. Extending that to the AI bidding consumers would have changed
   competitive balance they hadn't asked about, so I scoped it out and said so.

## What obstacles did you encounter?

- **No runnable environment.** The sandbox had no DB, no Flask, and no
  `pytest`, so I couldn't exercise the real pages or run the suites. I fell
  back to `py_compile`, bare-Jinja template parsing, and direct unit calls to
  the new pure functions.
- **Misleading documentation.** An existing AAR described org_strength as being
  seeded from the roster via `_team_org_strength_from_roster`. The actual seed
  path uses a random roll and that function is dead code. I had to verify
  against the code rather than trust the docs.
- **A filter that can't go where it looks like it should.** The `money` Jinja
  filter emits an HTML `<span>`/button, so it can't be used inside a `title=""`
  attribute. I caught this before shipping and formatted the tooltip amount as
  plain text in Python instead.

## What mistakes did you make?

- **I overstated org_strength's role in my first explanation.** I told the user
  development was "its one real mechanical job" before I'd grepped all consumers.
  It also feeds auction bidding, FA signing, and trades. I corrected this
  explicitly once I found it, both in conversation and in the AAR — but it was a
  confident claim made from incomplete investigation, and it could have led the
  user to a worse decision.
- **First pass on the surplus tooltip used `{{ ... | money }}` inside a
  `title`.** I wrote it, then had to verify and rewrite it. Caught pre-commit,
  but it was avoidable had I checked the filter's output shape first.

## What assumptions did you make?

- That `is_active=0` bench players actually exist on pro rosters (so "bench
  depth" is meaningful). I couldn't confirm against a live DB — the seed code
  supports a reserve split, and `compute_org_strength` degrades gracefully to a
  pure active-mean if there's no bench, so the assumption is low-risk either way.
- That a freshly seeded league showing Salary == Est. value (no badge) is
  *correct, not a regression* — they share a code path at seed and only diverge
  as players age. I designed for that rather than forcing an artificial gap.
- That the offseason report surfacing the persisted column's deltas is
  acceptable to leave as-is. I flagged it as a follow-up rather than silently
  changing a report the user didn't ask about.

## What would you do differently?

- **Grep all consumers of a symbol before characterizing what it does.** The
  org_strength overstatement would have been avoided by a 30-second
  `grep org_strength` before I answered, not after.
- **Verify framework-specific gotchas (like the `money` filter's HTML output)
  up front** when writing into attributes, rather than writing-then-checking.
- I would still gate both changes behind `AskUserQuestion` — that worked well
  and is worth repeating.

## What evidence supports your assessment?

- **Functional unit checks (the strongest evidence available without a DB):**
  `compute_org_strength` returns 80 for a strong active-only roster, 70 once a
  weak bench is added, 38 for a thin weak roster, 50 for empty, and clamps at
  95. `market_value` returns a live band figure while `estimate_player_value`
  still returns the persisted salary — confirming the split.
- **Compilation/parse checks:** `py_compile` clean on all four Python modules;
  both templates parse through a bare Jinja `Environment`.
- **Code-traced claims:** the "org_strength is a random roll, not roster-derived"
  finding is grounded in `league.py:3101` and the comment at `league.py:3305-3307`,
  and the dead-code status of `_team_org_strength_from_roster` in a
  whole-repo grep with no callers.

## What should a manager know that isn't obvious from the final output?

- **The diffs look small; the risk surface was the reasoning, not the code.**
  The hard part was realizing org_strength is two concepts wearing one name, and
  that one of them (auction discipline) is consumed before rosters exist. A
  naive "just make it roster-based" fix would have compiled, passed any shallow
  review, and quietly destabilized the league-setup auction.
- **Neither change is verified in a running app.** Everything was validated by
  compilation, template parsing, and direct function calls. The behavior on a
  live DB — especially the development-unify path and the per-load recompute
  cost on the team page — has not been exercised. A reviewer with a seeded DB
  should click a team page, run an offseason, and confirm.
- **I left deliberate scope boundaries.** The persisted org_strength column still
  random-walks and still drives auction/FA/trade AI; the offseason report still
  shows its deltas. Those are intentional non-changes, documented in the AARs as
  follow-ups, not oversights.
- **One of my in-conversation explanations was wrong and later corrected.** If
  the manager only reads the final code and AARs, they won't see that I
  initially mischaracterized org_strength's mechanical role. It's worth knowing
  the first answer to the user was incomplete.
