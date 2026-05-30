# After-Action Report — The Power Play Rule (the Nickel Fielder)

**Date written:** 2026-05-30
**Branch:** `claude/power-play-optional-rule-HmsMs`
**PR:** _(pending)_

The Power Play is the first **optional, per-league** rule in the engine — a checkbox a league
can turn on, the way AL/NL split on the DH for thirty years. It ships **off by default**, so an
existing universe is byte-for-byte unchanged until someone flips the toggle. That makes it a
clean A/B lever: run identical talent with the rule on vs. off and read the difference straight
out of the stat lines.

This is a reference AAR: it documents the mechanic as built, with `file:line` anchors.

---

## What the Power Play is

O27 defense is normally **8 fielders + the pitcher**. When a league enables the Power Play, the
**fielding** manager may deploy a **10th defender** for a short, use-or-lose window: the **nickel
fielder** — `NF`, scorekeeping position **10** — a single *middle outfielder*. (We call him a
middle outfielder rather than splitting left-center / right-center; it reads cleaner in a
scorebook, where a flyout to him is `F10`.)

While the nickel is on the field, the extra outfielder cuts off the gaps: some extra-base hits
drop to singles, and some shallow outfield singles get run down for outs. It's a *prevent-defense*
lever the way Declared Seconds is a tempo lever — a thing a manager spends at the right moment.

The window is **use it or lose it**:

- at most **one window per defensive half**;
- up to **4 outs**, but it **always ends when the half ends** (open it late and it just runs
  short);
- a **fresh** window is available in a **Declared Seconds** frame;
- **never** available in **extra (super) innings**;
- **no carryover** — a window never bleeds from one half into the next.

---

## The numbers (all in `o27/config.py:678-707`)

| Constant | Value | Meaning |
|----------|-------|---------|
| `POWER_PLAY_ENABLED` | `False` | League opt-in. A plain bool, so `engine_config` renders it as a dashboard toggle. Off = zero behavior change. |
| `POWER_PLAY_WINDOW_OUTS` | `4` | Max outs the nickel stays on the field. |
| `POWER_PLAY_XBH_HELD_PROB` | `0.35` | While active: a double/triple is cut to a single. |
| `POWER_PLAY_SINGLE_OUT_PROB` | `0.12` | While active: a shallow outfield single is run down for a fly-out. |
| `POWER_PLAY_NICKEL_PO_SHARE` | `0.33` | Share of routine OF putouts re-credited to the nickel (logged under `NF`). |
| `POWER_PLAY_NICKEL_ARM_MIN` | `0.62` | Strong-arm bar for nickel eligibility. |
| `POWER_PLAY_NICKEL_FIELD_MIN` | `0.58` | Good-glove bar (OF or SS) for nickel eligibility. |
| `POWER_PLAY_SKIP_GAME_PROB` | `0.05` | Chance a team never deploys it this game. |
| `POWER_PLAY_MISTIME_PROB` | `0.09` | Chance a team mistimes its deployment this game. |
| `POWER_PLAY_BLOWOUT_MARGIN` | `8` | At/above this run gap the window is held (no good reason to spend it). |
| `POWER_PLAY_DEPLOY_BASE_EARLY / _MID / _LATE / _FORCED` | `0.03 / 0.15 / 0.50 / 0.90` | Per-AB deploy-probability ramp across the out arc. |
| `POWER_PLAY_CLOSE_GAME_MULT` | `1.4` | Tight game (≤2 runs) raises deploy urgency. |

The whole mechanic lives in one module, `o27/engine/power_play.py`. There are **no** `o27v2`
overrides — the active league fork inherits these constants. Attribute thresholds are on the
engine's 0–1 grade scale (so `0.62` arm ≈ a 62 on the 0–100 scout scale).

---

## How a league turns it on

`POWER_PLAY_ENABLED` is a scalar `bool` on `o27/config.py`, and `o27v2/engine_config.py`
auto-exposes every int/float/bool on that module as a runtime tunable — bools render as
checkboxes (`engine_config.bool_keys()`). The override store is applied at the top of every sim
via `engine_config.ensure_applied()` (`o27v2/sim.py:1464-1465`), so:

- the toggle shows up in the existing **runtime engine-tunables dashboard** with no new UI work;
- it saves into the **per-environment library**, which is how a given league/universe carries its
  own rule set — that *is* the per-league checkbox;
- because it's applied before the game state is built, real games respect it **without any
  `sim.py` change**. The engine reads it through `power_play.power_play_on(state)`
  (`power_play.py:46`), which honors a per-game override (`GameState.power_play_enabled`,
  `state.py:631`) when set and otherwise falls back to `cfg.POWER_PLAY_ENABLED`. Tests force the
  rule on per-game without touching global config.

---

## When the decision is made (`maybe_open_window`, `power_play.py:245`)

The fielding manager is polled **once per AB**, before the first pitch, from
`ProbabilisticProvider.__call__` (`o27/engine/prob.py:2153-2157`) — the same cadence as the shift
decision, gated by a per-AB flag (`state.power_play_checked_this_ab`) reset on new-batter
detection. Hard gates, in order:

1. rule off → no-op (returns **before** any RNG draw — see "Behavior-neutral" below);
2. super-inning → never (extras get no window);
3. a window is already active → don't stack;
4. the `(phase_number, fielding_team_id)` key is already in `state.power_play_used`
   (`state.py:639`) → already spent this half (use-or-lose).

Then per-game behavior is rolled **once per team** (`_ensure_game_rolls`, `power_play.py:201`),
lazily so it varies game-to-game rather than being a sticky manager trait:

- **`power_play_skip`** (`POWER_PLAY_SKIP_GAME_PROB`, ~5%): the team never deploys this game.
- **`power_play_mistime`** (`POWER_PLAY_MISTIME_PROB`, ~9%): the team deploys at a bad time —
  half the time *too early* (first chance), half *too late* (crammed into the final outs).

The well-timed deploy probability (`_deploy_prob`, `power_play.py:210`) ramps across the arc —
`EARLY` before out 12, `MID` to the late-arc threshold (`LATE_GAME_OUTS_THRESHOLD = 20`), `LATE`
after, and `FORCED` once only the window's worth of outs remains (so a healthy manager almost
always spends it before it's wasted). A close game multiplies urgency; a **blowout zeroes it out**
— the window is genuinely never spent when the game is out of hand, even if that means forfeiting
it.

When the roll fires, the manager picks a nickel. The half's key is marked **used either way** —
if no eligible nickel exists, the chance is forfeited rather than re-rolled every AB.

---

## Nickel eligibility (`find_nickel`, `power_play.py:163`)

The nickel is **not a new player type** — eligibility is *derived* from existing attributes, so
the manager leverages real roster depth. A candidate must be a rostered player **not currently on
the field** (drawn from `Team.bench`, falling back to the off-field roster), who clears every bar:

- eligible at **OF or SS** — primary `position` or any entry in `role_field_pos`
  (`_positions_for`, `power_play.py:107`);
- **arm ≥ `POWER_PLAY_NICKEL_ARM_MIN`**;
- **glove ≥ `POWER_PLAY_NICKEL_FIELD_MIN`** at the qualifying spot (`defense_outfield` for OF,
  `defense_infield` for SS, with general `defense` as a floor — `_nickel_field_grade`,
  `power_play.py:121`);
- not already substituted out.

**Pitchers are a wild card.** A true two-way arm (strong arm, good glove, OF/SS in his
`role_field_pos`) can be the nickel — but only as a *fallback*: a qualifying position player is
always preferred, and a pitcher is excluded outright if he has **already appeared in the game**
(`_has_appeared`, `power_play.py:134`). You never pull a guy off the mound and stick him at NF —
that makes no sense. In practice this is rare and skews toward lightly-used relievers, which is
exactly the intent. Real o27v2 players carry `arm` / `defense_outfield` / `position` /
`role_field_pos` and a populated bench (`o27v2/sim.py:400-455`, `:688-694`), so the pool is live
in the league path.

---

## The fielding effect (`apply_nickel_defense`, `power_play.py:293`)

Called inside `resolve_contact` right after the shift layer (`o27/engine/prob.py:1730-1733`), and
only when a window is active **and** the fielding team is the side that deployed:

- **double / triple → single** at `POWER_PLAY_XBH_HELD_PROB` (the nickel cuts off the gap);
  tallied on `Team.pp_xbh_held`.
- **single → fly_out** at `POWER_PLAY_SINGLE_OUT_PROB` (the nickel runs it down); the batter is
  out on a caught fly, tallied on `Team.pp_hits_converted`, and the **nickel is credited the
  putout**.

Putout attribution flows through `nickel_putout_for` (`power_play.py:329`) at the fielder-credit
step (`prob.py:1772-1779`): the conversion he made is always his, and while the window is active
he also picks up ~`POWER_PLAY_NICKEL_PO_SHARE` of routine fly/line outs. The credit is the real
nickel's `player_id`, tagged as position `NF`, and the outcome dict carries
`nickel_play` / `fielder_pos` for downstream rendering (`prob.py:1794-1795`). Converting a single
to a fly-out adds an out through the normal `_record_out` path, so batter↔pitcher out
reconciliation and the stat invariants stay intact.

---

## Window lifecycle (open → tick → close)

- **Open:** `maybe_open_window` stamps `power_play_open_out = state.outs` (`state.py:635`),
  records the deploying side, the nickel's id, and appends a deployment record
  (`power_play_deployments`, `state.py:642`) with `start_out = outs + 1`.
- **Tick:** every recorded out calls `power_play.note_out` from `_record_out`
  (`o27/engine/pa.py:160-161`), which extends the active deployment's `end_out` to the current
  out count and **retires the nickel** once the window has spanned `POWER_PLAY_WINDOW_OUTS`
  (`is_window_active`, `power_play.py:71`).
- **Close on half-end:** `run_half` calls `power_play.clear_window` at every half start
  (`o27/engine/game.py:312-314`), the same place `batter_override` is cleared. That single line is
  what guarantees **no carryover** into a Declared Seconds frame or a super-inning — each half
  makes a fresh decision.

---

## Box-score rendering (`format_powerplays_line`, `power_play.py:357`)

When the rule is on, the box score gains a `Powerplays:` line directly under the `Seconds:` line
(`o27/render/render.py:33-35`, `:541`; template `o27/render/templates/box_score.j2:81-83`). It
mirrors the Declared Seconds footer:

- **single window:** `Powerplays: New York (O14-17)` — the out range the nickel covered;
- **two windows** (a team that deployed in regulation *and* its seconds frame):
  `Powerplays: Boston (1: O11, 2: O25)` — each deployment's start out, labeled `1:`/`2:`;
- **neither team used it:** `Powerplays: None`.

When the rule is off the line is omitted entirely.

---

## Behavior-neutral when off (seed-replay safety)

The hard requirement for an optional rule in this engine is that **off = identical**, because
games are replayed from stored seeds (`backfill_arc`). Every hook returns **before consuming any
RNG** when the rule is off or no window is active: `maybe_open_window` short-circuits on
`power_play_on`; `apply_nickel_defense`, `nickel_putout_for`, and `note_out` short-circuit on
`is_window_active` (open_out is `None`). So the rule-off path draws **zero** extra randoms and
adds only two always-`None`/`False` keys to the outcome dict.

Verified empirically: across a seed sweep, every reproducible seed was byte-identical to the
pre-change baseline in **both final score and total RNG draw count**. (A handful of high-event
extra-inning games diverge, but they diverge **baseline-against-baseline too** — pre-existing
cross-process nondeterminism from `hash()`-salted tiebreaks on near-identical synthetic rosters,
not anything the Power Play introduced.)

---

## Tests

`o27/tests/test_power_play.py` — 21 cases:

- **off by default**: `power_play_on` False, deploy is a no-op, footer line `None`; per-game
  override beats config both ways;
- **eligibility**: strong arm+glove wins; SS qualifies; an ineligible position (1B) is rejected;
  position player preferred over a two-way pitcher; pitcher wild-card chosen only when no position
  player qualifies; a pitcher who has appeared is excluded; an on-field player is excluded;
- **lifecycle**: window opens and expires after exactly `WINDOW_OUTS`; use-or-lose (one per half);
  never in a super-inning; `clear_window` prevents carryover; blowout suppresses deployment;
- **effect**: XBH held to a single; single run down for an out with the nickel credited; inert
  when the window is closed;
- **box-score line**: `None`, single-window, two-team, and regulation+seconds (`1:/2:`) forms.

Regression: the engine, render, declared-seconds, realism-identity, and `engine_config` suites all
pass with the new toggle exposed.

---

## Things to remember

- **It's opt-in and off by default.** Nothing happens in an existing league until the
  `POWER_PLAY_ENABLED` checkbox is flipped in the engine-tunables dashboard / saved environment.
- **The nickel is `NF` / position 10**, a single middle outfielder — `F10` in a scorebook.
- **Eligibility is derived, not rostered.** A bench OF/SS with a strong arm and a good glove is
  the nickel; a two-way pitcher is a rare wild card and *never* one who has already pitched.
- **One window per defensive half, use it or lose it.** Up to 4 outs, ends with the half, fresh in
  Declared Seconds, never in extras, never carried over.
- **Off = byte-identical / zero extra RNG.** Required for seed replay; don't add an RNG draw to the
  rule-off path.
- **All tuning lives in `o27/config.py:678-707`**; there are no `o27v2` overrides.
