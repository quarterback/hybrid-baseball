# After-Action Report — rename "Savant / Statcast" → "O27 Index" (O27i)

**Date completed:** 2026-06-02
**Branch:** `claude/peaceful-thompson-ZB5UY`
**Commit:** `d332915`
**Scope:** `o27v2/web/app.py`, templates (`o27i_*.html`, `base.html`,
`player.html`).

---

## Why

The advanced-stats hub shipped under borrowed MLB branding — "Savant" for the
percentile player page, "Statcast" for the batted-ball leaderboard. That reads
as a knockoff and doesn't fit the house voice, which already has period-styled
in-world publications (the **Almanac** and the **Gazette**). The hub was
renamed to **O27 Index** (short form **O27i**) — named for the game itself, in
keeping with that voice.

## What changed

A controlled, repo-wide token rename — no behaviour change, pure
identity/plumbing:

| Was | Now |
| --- | --- |
| `/savant` | `/o27i` |
| `/leaderboard/statcast` | `/o27i/leaders` |
| `/player/<id>/savant` | `/player/<id>/o27i` |
| endpoints `savant_home`, `statcast_leaderboard`, `player_savant` | `o27i_home`, `o27i_leaders`, `player_o27i` |
| templates `savant.html`, `savant_home.html`, `statcast_leaderboard.html` | `o27i_player.html`, `o27i_home.html`, `o27i_leaders.html` |
| internal helpers `_SAVANT_*`, `_savant_*` | `_O27I_*`, `_o27i_*` |
| nav label "Savant" / page titles "Statcast Leaderboard" | "O27 Index" / "O27 Index — Leaders" |
| player-page button "Savant" | "O27i" |

CSS classes (`.savant-*` → `.o27i-*`), search-box element ids, and the JS
redirect path were renamed too, so no `savant`/`statcast` token survives in any
served page.

## Validation

- Every new route returns 200 (`/o27i`, `/o27i/leaders`, `/player/<id>/o27i`,
  and the JSON variant); the three old routes return 404.
- Nav reads "O27 Index" and links to `/o27i`; the player-page button reads
  "O27i" and links through.
- Grep confirms zero `savant` / `Savant` / `statcast` / `Statcast` strings in
  any rendered page (home, leaders, player percentile page).

## What I deliberately did NOT change

- **Historical AARs keep their original names and contents** —
  `aar-baseball-savant-feasibility.md`, `aar-savant-percentile-page.md`,
  `aar-savant-phases-2-4.md`. Those document *how the page was built at the
  time* and are an accurate record; renaming them would falsify history. They
  reference "Savant" because that's what it was called then.
- No engine, schema, or stat-math change — this was branding + routing only.

## Follow-on

This rename was the first commit of a larger arc on the same branch (expanded
metric suite, Prospect Index, exact OAA/WP) — see
`docs/aar-expanded-metrics-and-prospect-index.md`.
