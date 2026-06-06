# Feature Report — Cricket Batting Order (the joker-free flip)

**Status:** complete, on branch `claude/o27-cricket-batting-order-k6hhp`.
**Scope of this report:** the optional Cricket Batting Order rule — what it does, the
controls that toggle it, and how it is plumbed. Complements the build-log AAR
(`docs/aar-cricket-batting-order.md`).

---

## 1. What it is

**Cricket Batting Order** is an optional, per-league rule. While it is on, a side's
batting order **flips at the end of each trip through the lineup** — the 1-9 order
becomes 9-1 for the next cycle, so the tail (the pitcher hitting 9th, the weaker bats at
the bottom) rotates up to the top and the openers drop to the bottom.

The flip is **gated on the manager not having deployed a joker during that trip.**
Deploying a joker locks the order for that cycle — no flip. So the rule turns the joker
into a genuine tactical fork:

> A joker insertion buys a high-leverage pinch-hitter **now** but forfeits the order
> churn this cycle. A manager who holds his jokers lets the order keep flipping; one who
> burns a joker every cycle keeps the order he started with.

The name nods to cricket, where the batting order is not a fixed carousel. O27's overall
structure is already the cricket adaptation (one continuous 27-out half); this rule
brings a little of that order-churn flavour into the lineup itself.

### Why "the flip" and not a full cricket dismissal model
This is deliberately the *smallest* cricket-order change: it **reorders** the existing
cycle, it does not change who bats or how outs are recorded. We considered (and did not
build) a true cricket "out = retired, next man in, all-out ends the innings" model — that
conflicts head-on with O27's defining 27-out half (a ~9-12 batter side would be all-out
long before 27 outs). The flip keeps every other rule — outs, stays, jokers, walk-back,
Declared Seconds — exactly as-is. See the AAR for the design conversation.

### In-game effect
- At every cycle boundary (lineup wraps to the top), if no joker was deployed during the
  trip just completed, `Team.lineup` is reversed in place. The next cycle bats 9-1.
- Two consecutive joker-free cycles flip and then flip back — the order oscillates.
- The flip persists across phases (regulation / Declared Seconds / super-innings) exactly
  as `lineup_position` does today; the rule just churns the order the engine already
  carries forward.
- **Play-by-play:** each flip prints one line, e.g.
  `Cricket order flips (joker-free trip) — Castillo now leads off.`

---

## 2. How to turn it on — three controls that compose

| Control | Where | Scope | Storage |
|---|---|---|---|
| **Global default** | Engine Settings → "Optional rules" → Cricket Batting Order | every league in the save | `sim_meta` (`engine_config`) → `cfg.CRICKET_BATTING_ORDER_ENABLED` |
| **Per-league (single)** | New-league builder → "Optional rules" checkbox | the league being created | `teams.cricket_order_enabled` |
| **Per-league (universe)** | Peer-league universe builder → per-league "Cricket Order" Off/On select | each league independently | `teams.cricket_order_enabled` |
| **Per-league (existing)** | `/league/edit` → per-league "CO Off / CO On" select | flip an existing league without rebuilding | `teams.cricket_order_enabled` |

**Composition rule (in `o27/engine/cricket_order.py:cricket_order_on`):** a game reads its
per-team override first, then falls back to the global default. `sim.py` sets the
override to `True` on **both** teams only when the league opted in (both sides bat, and
`advance_lineup` reads `team.cricket_order_enabled`); when the league did **not** opt in
it leaves the override unset (`None`) so the global toggle still applies. Net effect:

> Cricket Batting Order is on for a game if **the league opted in OR the global default is on.**

This is the exact same shape as Power Play's `power_play_on`, so the two optional rules
behave identically with respect to global vs. per-league control and can be enabled
independently.

### Why a `<select>`, not a checkbox, in the universe builder / league editor
Both read repeating per-league fields as parallel `getlist()` arrays aligned by index. An
unchecked checkbox doesn't submit, which would misalign each league's flag from its name.
An Off/On `<select>` always submits exactly one value per row, preserving the alignment —
identical to the Power Play treatment.

---

## 3. End-to-end plumbing

| Layer | File | What |
|---|---|---|
| Rule gate + flip | `o27/engine/cricket_order.py` | `cricket_order_on(team)` (override-or-config); `maybe_invert_on_cycle(team)` (reverse the lineup on a joker-free wrap, return a PBP line) |
| Cycle hook | `o27/engine/state.py` | `Team.cricket_order_enabled` field; `Team.advance_lineup()` calls `maybe_invert_on_cycle` at the wrap, BEFORE clearing `jokers_used_this_cycle`, and returns the optional flip line |
| PA wiring | `o27/engine/pa.py` | both `advance_lineup()` call sites append the flip line to the raw log (no-renderer path) and stash it on `state.cricket_flip_msg` (renderer path) |
| Renderer | `o27/render/render.py` | `render_event` emits `state.cricket_flip_msg` once and clears it |
| Global default | `o27/config.py` | `CRICKET_BATTING_ORDER_ENABLED: bool = False` |
| Dashboard toggle | `o27v2/engine_config.py` | auto-exposed under "Optional rules" |
| Storage | `o27v2/db.py` | `teams.cricket_order_enabled` column (CREATE TABLE + idempotent ALTER migration) |
| Per-game read | `o27v2/sim.py` | sets `home_team`/`visitors_team.cricket_order_enabled = True` from the league flag (home row authoritative) |
| New-league UI | `o27v2/web/templates/new_league.html` + `app.py` | checkbox → `UPDATE teams SET cricket_order_enabled = 1` |
| Universe UI | `o27v2/web/templates/universe_new.html` + `app.py` | per-league `lg_cricket_order` select → `UPDATE … WHERE league = ?` |
| Edit UI | `o27v2/web/templates/league_edit.html` + `app.py` | per-league `league_co` select → toggle on existing league |

**Off = zero behaviour change.** With the rule off (the default), `cricket_order_on`
short-circuits before touching the lineup, so `advance_lineup` is byte-for-byte
unchanged, the renderer emits nothing, and no migration or sim path does anything.

---

## 4. Verification

- `pytest o27/tests/test_cricket_order.py` — 9 tests: off-by-default inertness; per-team
  off overriding global on; joker-free flip (and the PBP line naming the new leadoff);
  flip-back over two clean cycles; joker locking the order; joker locking only its own
  cycle; global-default driving when no override; short-lineup no-crash.
- `pytest o27/tests` — full engine suite, **114 passed** (the `advance_lineup` return-type
  change and `pa.py`/`render.py` edits regress nothing).
- Full random games via `ProbabilisticProvider` with the rule on: flips fire (8 per
  game with two joker-less demo sides) and the **pitcher who hit 9th leads off** the next
  cycle, confirming the 1-9 → 9-1 reversal; with the rule off, zero flip lines.
- DB: fresh `init_db` creates `teams.cricket_order_enabled` (default 0); re-`init_db` is
  idempotent; the ALTER migration adds the column to a legacy `teams` table missing it.
- All changed Python files byte-compile; all three edited Jinja templates parse.

## 5. Not changed / possible follow-ups
- **No new stats.** Unlike Power Play, the flip produces no stat family — it only reorders
  PAs the existing stat machinery already records. A "flips per game" telemetry line could
  be added later if it proves interesting.
- **No manager AI awareness.** The joker-deployment AI does not currently weigh "deploying
  this joker forfeits my flip." The rule is mechanically correct, but the AI does not yet
  treat the lock as a cost. A natural follow-up is to fold the flip's value into
  `manager.should_insert_joker` when the rule is on.
