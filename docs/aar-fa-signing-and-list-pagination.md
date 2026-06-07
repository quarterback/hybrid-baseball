# After-Action Report — FA signing actually signs, clickable free agents, list pagination

**Date completed:** 2026-06-07
**Branch:** `claude/jolly-planck-AUmgY`

---

## TL;DR

Four complaints about the free-agent / college-prospect tooling, all real:

1. **Free agents weren't clickable, and even if they had been, the card 404'd.**
   `free_agents.html` rendered names as plain text, and `player_detail`'s
   `JOIN teams` was an *inner* join — so any player with `team_id IS NULL`
   (every FA) returned no row and 404'd. Fixed both: names now link, the join
   is a `LEFT JOIN`, and `player.html` tolerates a null team.

2. **"Run signing round" was a permanent no-op.** The eligibility gate was
   `active_count < ROSTER_TARGET (34)`, but seeded teams already carry ~42–44
   active players, so *no team was ever eligible* and nobody — prospects or
   veterans — ever signed. Reworked per the owner's direction.

3. **The college Draft Board, Prospect Index, and Import list dumped
   hundreds of unpaginated rows.** Added server-side pagination (50/page) via
   a shared `_pagination.html` macro, preserving the position/season filters.

---

## What changed

### Clickable free agents → working player cards
- `o27v2/web/templates/free_agents.html` — player name is now an
  `<a href="{{ url_for('player_detail', …) }}">`.
- `o27v2/web/app.py::player_detail` — `JOIN teams` → `LEFT JOIN teams`, and
  dropped the `t.id as team_id` alias so `player["team_id"]` keeps the
  player's own (NULL) value. Everything downstream already tolerates a NULL
  team (splits return nothing, transfer-target list shows all teams, league
  badge is skipped).
- `o27v2/web/templates/player.html` — breadcrumb and header guard on
  `player.team_id`: rostered players link to the team; FAs get a "Free Agents"
  breadcrumb and a **Free Agent** badge instead of a broken `team_detail`
  link (which `url_for` would have raised a BuildError on with `team_id=None`).

### Signing round that actually signs (`o27v2/fa_signing.py`)
Per the owner's explicit direction:
- **Cap raised to 50** (`SIGNING_ROSTER_CAP`), separate from the auction's
  re-roster target so raising it here doesn't perturb the auction.
- **Players under 21 don't count against the cap** (`_team_active_count` only
  counts `age >= 21`), so a team stacked with youth still reads as having room.
- **Prospects always sign, ignoring cap *and* budget.** A player is a prospect
  if signed from college (the `prospects` scope) or simply `age < 21`. For
  prospects every team is a candidate and the deduction uses
  `allow_overdraft=True`, so money never blocks a prospect signing.
- Veterans (21+, not a prospect) keep the original cap + budget gating.
- Distribution across teams still rides on `_team_valuation_noisefree`'s
  position-need term, which recomputes from the live roster after each sign,
  so prospects spread toward teams that need them rather than all landing on
  the single richest team.

### Pagination
- New `o27v2/web/templates/_pagination.html` — a `pager(endpoint, args, page,
  page_count)` macro (mirrors the inline pager already on `free_agents.html`).
- `college_draft_view`, `college_import_view`, `college_prospects` now slice
  to 50/page and pass `page`/`page_count`/`pager_args`. The Draft Board and
  Import slice **before** the per-row report processing, so we don't build
  variance flags for rows we won't render. Prospect Index passes a
  `rank_offset` so the `#` column keeps counting across pages.

---

## Validation

- `pytest o27/tests o27v2/tests` — **236 passed**.
- Seeded a 30-team DB + college tier, ran a rollover to produce 1,560
  graduates, and drove the live app:
  - FA name links resolve; an FA player card (`/player/<fa_id>`) returns 200
    and shows the **Free Agent** badge.
  - Draft Board paginates (32 pages), `?position=P` → 12 pages, and the pager
    links preserve `position=P`. Import paginates (32 pages). Prospect Index
    paginates (83 hitter pages / 47 pitcher pages) and page 2 starts at rank 51.
  - `POST /api/fa/sign-round` returns 302 → `/free-agents`.
- Direct engine test of the signing round: injected 60 youth FAs (age 18) and
  **zeroed every team's budget** (forced `spent = total_budget`); the round
  signed **all 60**, driving budgets negative — confirming prospects bypass
  both the roster cap and the budget.

## What I did NOT change
- The Sunday match-day waiver sweep (`waivers.py`) — it already does
  replacement-based claims and was working; the signing-round button is a
  separate, manual mechanism.
- Veteran signing economics, salary values on signed players, or the auction.
- Prospect-distribution fairness beyond what the valuation need-term gives:
  with budgets exhausted a stress batch of 60 youths concentrated on the
  highest-need teams (max 14 on one team). That's consistent with the
  "sign always, over any limits" directive; revisit if distribution matters.
