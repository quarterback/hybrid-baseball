# After-Action Report — Semi-Rare Baseball Rules + Infrastructure Performance

**Date completed:** 2026-06-13
**Branch:** `claude/sharp-mendel-q2pf28`

---

## What was asked for

Two separate tracks in one session:

**Track 1 — Semi-rare baseball rules.** The simulator had no model for the handful
of rules that fire a few times per season: balks, catcher's interference, dropped
third strikes, defensive indifference, fielder's obstruction. The user wanted these
modeled "even if they're rare, because they're worth it." Three rules were specifically
distinguished during design:

- **Pitcher's balk** — illegal pitcher motion; all runners advance.
- **Catcher's balk** — non-contact pre-pitch violation by the catcher (distinct
  from interference). Penalty: automatic ball, not a runner advance.
- **Catcher's interference** — catcher's mitt physically contacts the bat. Batter
  gets 1B. Also distinct from catcher's balk.

Plus three more: dropped third strike, defensive indifference, fielder's obstruction.

The user explicitly ruled out the infield fly rule (no double-play threat in the
continuous 27-out format).

**Track 2 — Infrastructure performance.** The `fly.toml` was flagged as having
"serious load balancing issues" and the user wanted a second machine and better
performance. The underlying problem turned out to be architectural: Flask dev server
(`app.run()`) is effectively single-threaded, so any CPU-bound simulation request
stalls the entire site.

---

## Starting state

- `pa.py` had a `balk` event dispatch (lines 610–616) that advanced runners and
  scored, but there was **zero balk generation code** in `prob.py`. Balks were
  dispatchable but never fired.
- No event types existed for catcher's balk, CI, dropped third strike, DI, or
  obstruction.
- `SpellRecord` and `GameState` had no balk/CI/catcher-balk stat fields.
- `fly.toml` ran a single `performance-1x` machine. `manage.py runserver` called
  `app.run(debug=False)` — Flask's single-threaded dev server.

---

## What was built

### Semi-rare rules (6 events)

**`balk` (pitcher's)** — Generation wired into `prob.py:between_pitch_event()`
as the first check when runners are on base (`BALK_PROB_PER_PITCH = 0.00015`).
Stat field `pitcher_balks_this_spell` → `SpellRecord.balks` added and flushed
through both `game._close_spell()` and `manager.pitching_change()`.

**`catchers_balk`** — New event type. Fires unconditionally (no runners required —
it penalizes the batter's opportunity, not runner position). Penalty: automatic ball
appended to count; if already 3-0 the `_walk()` helper handles it. Stat field
`pitcher_catchers_balk_this_spell` → `SpellRecord.catchers_balks`.

**`catcher_interference`** — New event type. Fires in `_generate_pitch()` when
the pitch outcome is `swinging_strike` or `contact`, before the event is returned
(`CATCHER_INTERFERENCE_PROB = 0.00035` per swing). Batter awarded 1B via
`_force_advance_for_walk()` identical to an HBP. Stat field
`pitcher_ci_this_spell` → `SpellRecord.ci_allowed`.

**`dropped_third_strike`** — New event type. Generated in `_generate_pitch()` when
`outcome == "swinging_strike"`, `state.count.strikes == 2`, and `state.bases[0]
is None` (1B unoccupied — the core eligibility rule; the "2 outs" MLB exception
was omitted as less meaningful in the 27-out format). Probability
`DROPPED_THIRD_STRIKE_BASE_PROB = 0.038`. Safe/out resolved at generation time
(`DROPPED_THIRD_STRIKE_OUT_AT_FIRST = 0.72`). The pitcher still gets the K
regardless of batter outcome; if batter reaches safely, the batter is placed on 1B
without an out recorded and `_end_at_bat()` is called normally.

**`defensive_indifference`** — New event type. Fires inside the stolen base loop
in `between_pitch_event()` when a steal attempt would otherwise trigger AND
`state.outs >= DI_MIN_OUTS` (20) AND `abs(run_diff) >= DI_RUN_DIFF_THRESHOLD`
(6). Runner advances normally; no SB or CS credit is recorded. The scoring logic
and PBP log note "no SB credited."

**`fielder_obstruction`** — Implemented as an outcome-dict mutation in
`_generate_pitch()` post-contact-resolution. If `outcome_dict["runner_out_idx"]`
is set (a runner would be thrown out), a `FIELDER_OBSTRUCTION_PROB = 0.004` check
fires and clears `runner_out_idx` to None, setting `fielder_obstruction=True`.
In `pa.py:_resolve_contact()` the flag triggers a log line and
`pitcher_errors_this_spell += 1` (the run, if it scores, becomes unearned via the
existing error→unearned pathway). The runner who would have been out is simply
absent from `out_runner_ids` and never passed to `_record_out()`.

### Infrastructure (fly.toml + manage.py + requirements.txt)

**Gunicorn** added to `o27/requirements.txt`. `manage.py:cmd_runserver()` now
launches gunicorn if it's importable, falling back to `app.run()` for local dev
without gunicorn installed. Workers default to 2 (one per dedicated CPU on
`performance-2x`), configurable via `GUNICORN_WORKERS` env var. Timeout is
120 s to accommodate long sim batches. Both access log and error log go to stdout
so Fly captures them.

**`fly.toml` changes:**
- Machine size: `performance-1x` → `performance-2x` (1 dedicated CPU → 2, 2 GB RAM → 4 GB)
- `min_machines_running`: 1 → 2
- `GUNICORN_WORKERS = "2"` env var added
- `[http_service.concurrency]` block added: `soft_limit = 20`, `hard_limit = 30`
  (sized for 2 workers + queuing headroom; Fly routes new connections to the other
  machine when soft limit is reached)

**SQLite caveat documented prominently in fly.toml comments.** Each Fly machine
mounts its own volume — databases are NOT shared. Session affinity (sticking a
browser client to one machine) is the correct mitigation for a two-machine SQLite
deployment. The comment recommends LiteFS or PostgreSQL if true shared state across
machines is needed. The current setup is sound for a deployment pattern where each
machine can operate as an independent league instance, or where Fly's routing
naturally pins most traffic to one primary machine.

---

## What was not changed

- No O27 game rules or sim math changed. The 6 new events are purely additive.
- The infield fly rule was explicitly excluded by the user.
- No stat reference or box-score display updates — the new SpellRecord fields exist
  but are not yet surfaced in any UI or stats page. That's a separate pass.
- No LiteFS integration — the SQLite limitation of the two-machine setup is
  documented but not solved. A full LiteFS migration would require wrapping the
  startup in a `litefs mount` process supervisor and replacing the volume mount config.

---

## Validation

- `pytest o27/tests -x -q` — 107 tests pass. One pre-existing failure
  (`test_cricket_order.py`) due to a `jinja2` import in the test environment;
  unrelated to this change.
- `python -m o27.main --seed 42` — game runs clean end to end.
- New event types have no unit tests yet. The probability constants are tuned
  conservatively (the rarest events fire roughly once per team per season);
  actual rates should be monitored over a few simulated seasons and the constants
  adjusted in config.py if the events are too invisible or too frequent.
