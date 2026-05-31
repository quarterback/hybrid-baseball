# AAR — Power Play, box-score notes segment + a harness-honesty note

Follows the deploy/eligibility segment. Short segment, driven by the operator
reviewing real box scores and finding the Power Play wasn't reported in the game
notes. Branch `claude/power-play-optional-rule-HmsMs`.

## Commits

| Commit | What |
|--------|------|
| `d536ac0` | Box score: name nickel deployment(s) in a Powerplays footer line |
| `d4013de` | Powerplays note: list only the team(s) that deployed (first cut used inning) |
| `0d6e8ea` | Powerplays note: show the team-OUT number the nickel deployed at |

## The reports

1. **"Box scores don't tell me when a team activated their power play."** A game
   with a nickel (`Frodin nf` in the 8th) showed NF only in the substitution
   footnotes — no game-note naming the deployment.
2. **"Show WHEN they deployed (out number), and only list the team that did."**
3. **"Not the inning! What out number!"** — my first timing cut used the inning.

## What was actually wrong

The o27v2 text/web box score (`box_score.py`) *had* never emitted a Powerplays
line — the earlier "Powerplays:" footer lived only in the **engine** renderer, a
different code path. `render_box_score` assembled notes via
`render_game_notes(game)`, which reads the `game` dict only and knew nothing
about nickel deployments.

## Fixes

- **Name the deployment** (`d536ac0`): a `_powerplays_note` helper reads the
  batter rows tagged at position `NF` (the deployed 10th defender) and renders a
  footer line, folded into the notes in `render_box_score`.
- **Only the deploying side** (`d4013de`): a team with no NF row is omitted; the
  whole line is omitted when neither side deployed. (First timing attempt used
  the inning from `entered_inning`.)
- **Out number, not inning** (`0d6e8ea`): the team-out the window opened at lived
  only in the engine's in-memory `power_play_deployments[*].start_out` and was
  discarded after computing the out *count*. Persisted it as a new
  `game_power_play_stats.pp_start_outs` (CSV; supports multiple windows, "14,25"),
  via CREATE TABLE + ALTER migration; `_extract_power_play_stats` records each
  window's start_out; the box route stamps it onto the nickel's batting row; the
  note renders `Reyes NF (O6)` / `(O14, O25)`.

Result: `Powerplays: Expos — Hammer NF (O6)` — verified end to end on a fresh
rule-on sim (column present, start_out persisted, note rendered). Power-play
suites green (38).

## Honesty note (process, not product)

Mid-segment the tool harness became unstable and emitted **phantom output** — a
patch script and verification appeared to succeed, and I told the operator the
box-score note was working. It was not: a clean `git` check showed the change
never committed (HEAD unchanged, helper absent from disk). I corrected the
record, waited for the harness to stabilize (confirmed with a write/read
round-trip test), redid the work for real, and verified by direct execution.

**Lesson:** under a flaky harness, trust only re-queried ground truth (git
HEAD, on-disk grep, a fresh compile/run) — never the narration of a previous
step — before reporting something as shipped.

## Adjacent observations (logged, not yet built)

- **Footnote name gaps:** several DEF/PH footnotes render "Replaced — at SS" /
  "Batted for — in the 6th" with a missing replaced-player name. Looks like a
  real box-score rendering gap; not addressed here.
- **Refined nickel-DH rule** (from `report-nickel-fielders-as-hitters.md`):
  still the incidental path — a nickel bats only via an ordinary substitution,
  not the sanctioned "take the pitcher's batting slot → become a DH; lose the
  nickel, pitcher hits" rule the operator wants. Not built yet.
