# After-Action Report — Relief & finisher pitching stats (no saves/holds)

**Date completed:** 2026-06-03
**Branch:** `claude/friendly-hypatia-ShSHm`

---

## TL;DR

O27 has no saves or holds — and shouldn't, because those are artifacts of the
9-inning structure and the save *rule*, neither of which exists in a continuous
27-out half. The 27-out structure + times-through-order decay + a heavier late
arc means the bullpen taxonomy (starter → middle → one-inning closer) isn't
forced; clubs can split the 27 outs many ways (a deep ladder, a workhorse, or a
back-stage "finisher-starter"). So instead of saves, this adds **role-agnostic,
structure-proof relief value**:

- **Inherited-Runners Stranded % (IR-Stop%)** — of the runners on base when a
  reliever entered, the share he stranded. The one relief skill no rule
  structure can obsolete. *(Engine change — captured at the pitching change.)*
- **Entered Leverage (gmLI)** — average Leverage Index at the moment a pitcher
  enters; who gets the high-stakes calls. *(From the existing WPA/LI model.)*
- **Late-Arc Run Prevention (Late ER/BF)** — earned runs per batter faced in
  outs 19-27, the heavy third; a length-agnostic "finisher" read. *(From the
  existing arc buckets — no engine change.)*
- **Team Pitching Usage Shape** — arms used, outs/appearance, top-arm / top-2
  out share, starter share — so emergent structures (two-ace split vs ladder)
  are visible empirically. *(From the deduped appearance rows.)*

## Where things live

| Piece | File |
| --- | --- |
| Inherited-runner capture (engine) | `o27/engine/state.py` (SpellRecord + GameState fields + `inherited_runner_ids`), `o27/engine/manager.py` (`pitching_change`), `o27/engine/pa.py` (`_reconcile_inherited`) |
| Stat plumbing | `o27/stats/pitcher.py`, `o27v2/sim.py` (extract + both INSERTs), `o27v2/db.py` (schema + migration), `_PSTATS_DEDUP_SQL` in `app.py` |
| gmLI | `o27v2/analytics/wpa.py` (`build_player_wpa` → `by_pitcher[pid]["gmli"]`) |
| IR-Stop% / Late ER/BF | `_aggregate_pitcher_rows` in `app.py` |
| Usage shape | `o27v2/analytics/records.py` (`team_pitching_shape`) |
| Display | player card (Relief & Finisher panel), Stat Browser pitching views (IR-Stop%, Late ER/BF), `/teams/stats` (Pitching Usage Shape panel), glossary (Pitching · Relief & Finisher) |
| Tests | `o27/tests/test_relief.py` |

## Key design decisions

- **Inherited-runner capture mirrors the Walk-Back pattern.** At a pitching
  change the runners on base become the new arm's `inherited_runner_ids` and
  `ir_inherited`. After every event, `_reconcile_inherited` settles any tracked
  runner who left the bases; the number that *scored* is capped by the runs
  booked that event. Like `_reconcile_walk_back`, this is a deliberate
  heuristic — exact per-runner score/out identity isn't centralized in the
  engine, so the rare event that both scores a non-inherited run **and** retires
  an inherited runner can mis-credit by one. Bounded and uncommon; documented.
- **gmLI from `pa.id` order.** `game_pa_log` has no explicit appearance id, so
  entry = the lowest-`id` (earliest) PA a pitcher faced in each game. Because
  the log is ball-in-play-only, "entry" is the first BIP he faced — a close
  approximation. `gmli` is only stamped where WPA is already computed (leaders,
  player card), so it's intentionally absent from the Stat Browser (which skips
  WPA for speed) rather than shown as an empty column.
- **Late-Arc finisher uses `bf_arc3` as denominator.** Only batters-faced is
  bucketed by arc, not outs, so late-arc run prevention is ER per arc-3 BF
  rather than a true ERA. Honest given the data; no schema change.
- **IR-Stop% surfaced per-player, not as a top-10 card.** With 1 inherited
  runner a pitcher trivially shows 100%, so it lives on the player card (with
  the IR / IR-Sc counts beside it) and the sortable Stat Browser, not as a
  headline leader card.

## Validation

Fresh 150-game sims:
- IR capture invariant-clean: `ir_scored <= ir_inherited` on every row (0
  violations). League IR-scored rate runs high (~70%) — expected in a .350+
  AVG offense with the Walk-Back rule, with a small upward bias from the
  reconcile heuristic noted above.
- gmLI computed for the qualified pitcher pool, centered ~1.0 (0.37–1.36 range).
- Usage shape already exposes structure (e.g. a club at ~14 outs/appearance,
  top-2 arms = 52% of outs — a two-ace-ish split).
- `/leaders`, `/stats` (pitching advanced + all), `/player/<id>`, `/teams/stats`,
  `/glossary` all render 200.
- `o27/tests` + `o27/tests/test_relief.py` (5 new) + records/bunt suites pass
  (118 total); all 11 stat invariants pass on the simmed DB.

**Needs a resim to populate** (sim-time capture), like any box-stat addition.

## Deferred (next pass — the user's follow-on ideas)

These build on the same per-appearance *entry-context* spine and are the
natural next layer:

- **Terminal Outs (TO)** — outs recorded by a pitcher who entered with a lead
  and never relinquished it (the counting-stat finisher; scales with the
  back-stage-starter strategy).
- **Quality Finish (QF)** — entered and sealed 9+ final outs holding a
  lead/tie (the inverse of a Quality Start).
- **Lead Retention Rate (LR%) × outs/appearance** — the 2-D archetype grid.

These need entry-lead and finished-the-game (recorded out #27) context on the
spell record — a bounded extension of the inherited-runner capture added here.
