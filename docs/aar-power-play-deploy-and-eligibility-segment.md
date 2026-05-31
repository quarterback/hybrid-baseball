# AAR — Power Play, third segment: why the nickel never deployed (and surfacing it)

Follows `docs/aar-power-play-nickel-fielder.md` and
`docs/aar-power-play-ui-and-stats-segment.md`. This segment was driven by one
operator report — **"no matter what I do, the nickel never activates"** — and is
as much about the *debugging* as the fixes. All on branch
`claude/power-play-optional-rule-HmsMs`.

## Commits

| Commit | What |
|--------|------|
| `b66de86` | Fix: Engine Settings edits never reached the simulator (stale config) |
| `d3e42c0` | Make nickel eligibility ability-based, not position-gated |
| `07a07da` | Cap dedicated DHs at 3 (jokers); make PH bench bats real fielders |
| `1555ea8` | List jokers at their actual field position, not "DH" |
| `29632f7` | Add a 🪙 nickel-eligible tag on team rosters |
| `fe3fa98` | Record nickel appearances (NF) in a fielder's stat record |

---

## The report

The operator had the feature merged, toggled it on (per-league box **and** the
global Engine Settings checkbox), even lowered the eligibility thresholds — and
**no team ever deployed a nickel.**

## What went wrong in the diagnosis (the honest part)

For several rounds I kept *reproducing success* in fresh, seeded test DBs and
concluding "the code works — your league must not be opted in." That was the
wrong instinct: a green repro that dodges the user's actual conditions is not
evidence the user is wrong. Two real bugs were hiding behind those passing tests,
and the operator's own observations (not my tests) pointed at both.

**Lesson:** when a user insists a shipped feature doesn't work and your tests say
it does, the gap is in *what your test isn't reproducing* — environment,
process lifecycle, or data shape — not in the user.

## Root cause #1 — stale config (the global toggle)

`engine_config` applied the stored tuning **once per process** (`_applied`
latch); `ensure_applied()`, called at the top of every sim, was a permanent
no-op after the first call. So a setting toggled *after* the process started —
or saved by a different worker than the one simming (the norm on fly.dev) — was
written to the DB but **never re-read**. `cfg.POWER_PLAY_ENABLED` stayed `False`
and no team deployed. Restart-only, with nothing on screen to explain it.

*Reproduced* by applying config at "startup", then storing the override without
re-applying, then simming → 0 power-play rows. Fixed (`b66de86`): `ensure_applied`
now re-applies when the stored blob's signature changes (one cheap indexed
lookup; full re-apply only on actual change). After: same scenario fires (104
rows / 12 games). Regression test added. The per-league flag was always immune
(read straight from the team row each sim) — which is exactly why isolated tests
passed and this hid so long.

## Root cause #2 — eligibility starved by DH-heavy benches

Surfaced by the operator looking at a roster and noticing **seven DHs**. The
nickel could only be a *bench* player listed at **OF or SS**. But O27 benches
skewed to bat-only DH/joker/PH players, and the real gloves were all in the
starting lineup (on the field, excluded) — so the candidate pool was routinely
empty. The **position gate** failed, not the rating gate, which is why lowering
arm/field to 0.25 didn't help.

Fixes, all from the operator's direction:

- **Ability, not position (`d3e42c0`).** Eligibility is now: any player whose arm
  AND best glove (general/infield/outfield) clear the bar — regardless of listed
  position. Verified: default thresholds, 0 → 130 rows / 14 games.
- **≤3 DHs (`07a07da`).** Only the 3 jokers are dedicated DHs; the 2 pinch-hit
  specialists became loud-bat *fielders* at thin corners (real positions + gloves).
  Per-team DH 5 → 3; bench gains glove/injury/nickel depth.
- **Jokers listed at real positions (`1555ea8`).** A joker is a usage role
  (`roster_slot="joker"`/`game_position="J"`), not a position — so the "DH"
  placeholder is gone; jokers show 1B/RF/3B by archetype. Safe because every
  joker check keys on the role tags, never the position field. New leagues now
  carry **zero** "DH" position rows.

## Surfacing (the operator wanted to *see* it)

- **🪙 eligibility tag (`29632f7`)** on team rosters — who *can* be the nickel —
  using the engine's own effective thresholds + `scout.to_unit`, shown only when
  the rule is active for that team.
- **NF in the stat record (`fe3fa98`)** — the operator clarified the real intent:
  when reviewing a *fielder*, know he **played** nickel, not just that he's
  eligible. The nickel's per-game `game_position` is now stamped `NF` (extends to
  `SS-NF` if he also fielded elsewhere), so the box score shows `NF` and the
  player page carries a "🪙 Nickel — 10th defender in N games" badge.

## Verification

- Stale-config: reproduced → fixed → regression test; 17 engine_config tests green.
- Eligibility/firing: default thresholds now fire reliably (≈130 rows/14 games);
  rule-off still writes nothing.
- Roster shape: per-team DH = 3 (was 5); corners better stocked; jokers at real
  positions; joker mechanic intact (box scores still show joker entries).
- Surfacing: PP-off team shows no coin; PP-on shows 🪙 on eligible players; NF
  recorded and shown on box score + player page.
- Power-play suite 31, engine_config 17 — green. (One test updated to encode the
  new ability-based policy: a high-ability corner IF now qualifies.)

## For the operator

These all require a **deploy + refresh** to take effect — and the stale-config
fix is the linchpin: until it ships, the Engine Settings toggle still won't reach
the simulator. After deploy: toggle Power Play (Engine Settings or per-league),
sim fresh games, and you'll both *see* eligibility (🪙) and watch nickels deploy
and show up as `NF` in fielders' records. No threshold fiddling needed.
