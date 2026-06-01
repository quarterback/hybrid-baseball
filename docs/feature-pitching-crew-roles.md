# Feature Report — The Pitching Crew (nautical staff roles)

**Status:** complete, on branch `claude/peaceful-cori-9n9DG`.
**Scope:** a canonical, skill-derived pitching-staff structure for O27 — the
*crew* — replacing the role-less "everything is live-derived" model. Covers the
role taxonomy, where roles are stored and re-derived, how the engine consumes
them, and where they surface in the UI.

---

## 1. Why this exists

Before this, O27 had **no persisted pitching role at all**. The manager AI picked
today's starter as the most-rested, highest-Stamina arm and assigned relievers at
appearance time by where in the 27-out arc the call landed. That kept aging arms
drifting naturally out of the rotation, but it meant a club had **no actual
rotation and no defined bullpen** — every reliever was interchangeable, and there
was no source of truth for "who is this team's ace, who closes, who eats bulk
innings."

The ask: give teams a real staff structure, recognizing that **arms move between
roles** as skills, health, and form change.

## 2. Why *nautical* roles and not Starter / Closer / Setup

O27 is not baseball-with-a-bullpen. There are **no innings to reset, no 3-out
save, no "every fifth day" rotation** — a game is one continuous **27-out
voyage**. MLB's closer/setup ladder is built on inning resets that don't exist
here, and the "Workhorse / Starter / Opener" framing was a non-starter (it dressed
a Stamina tuning knob up as if it were a real role).

So the staff is a **ship's crew** that *conducts the voyage* through its handful of
moments:

| Code | Role | What he does |
|------|------|--------------|
| `HM` | **Helms** | Steers the voyage out — the club's primary arm. Like a softball ace he takes the ball most days and carries the early arc. |
| `1C` | **First Change** | First change of the watch; first hand to relieve the Helms and hold the heading. |
| `2C` | **Second Change** | Second change of the watch; carries the middle arc. |
| `BO` | **Bosun** | Works the deck through the long middle — the durable **bulk** hand who soaks innings or follows a short Helms outing. |
| `SK` | **Skidder** | Slides in to **skid the ship through a rough patch** — the situational / deception arm for a high-leverage matchup. |
| `AN` | **Anchor** | Drops anchor to steady the ship and **hold the heading late**. |
| `PI` | **Pilot** | The harbor pilot who guides the ship **into port** — the final-outs finisher. |

## 3. Two principles baked into the design

1. **Nobody owns a title.** A role is not an identity a player carries around — it
   is *where his skills place him on **this** team's staff*. The same arm is a Helms
   on a thin staff and only a Skidder on a stacked one. Roles are therefore always
   assigned **relative to the team** and re-derived whenever the staff changes. This
   is enforced in code (`assign_staff_roles` ranks within the staff) and verified by
   `test_rotation.py::test_role_is_relative_to_the_staff`.

2. **Orchestration over who-is-throwing.** The stored crew is only a *default* — the
   manager always flexes it live by **fatigue, Stuff and the matchup**, and keeps a
   man in a role only while it works (injury, tiredness, or a rough patch move him).
   *Who* is on the mound matters less than the fact that a crew is always conducting
   each moment of the voyage.

This is the same model **ZenGM Baseball** uses on its Pitching depth chart (the `S1–S5
/ CL / RP` *slots* that auto-sort and can be hand-pinned, distinct from the `SP/RP`
**Pos** label and the split `OvrSP / OvrRP` fit ratings) — here the slots are the
nautical crew, the card label is the coarse Starter/Reliever read, and the
per-role fit is the `_ROLE_FIT` weighting.

## 4. How it's stored

A pitcher's crew assignment lives on his player row, re-using the long-dormant
`pitcher_role` column plus a new `rotation_slot`:

- `players.pitcher_role` — the crew code (`HM/1C/2C/BO/SK/AN/PI`), or `''` for an
  unroled arm (reserve depth / legacy rows).
- `players.rotation_slot` — usage rank **within** a role (1 = primary). The two
  Helms get slots 1 and 2 so the steering arm naturally alternates.

Both are added idempotently in `init_db()` (the `rotation_slot` column via the same
ALTER-on-boot path as every other migration), so **existing saves auto-upgrade**.
Legacy rows read as "no crew role" and fall back to the old live-derivation
behavior everywhere — old saves keep working untouched.

## 5. How roles are assigned — `o27v2/rotation.py`

`assign_staff_roles(pitchers)` profiles each arm (Stuff = `pitcher_skill`, Stamina,
and a Movement+Command "deception" read) and drafts the staff into the crew
complement in priority order — the marquee roles take their best-fitting arms
first, the watch-change corps picks up the remainder:

- **Helms / Bosun** weight Stamina (they carry length).
- **Pilot / Anchor** weight pure Stuff (short, late, high-leverage).
- **Skidder** weights deception (the rough-patch specialist).
- **First / Second Change** take the balanced remainder.

For a full ~17-arm staff this yields roughly `2 HM · 3 1C · 3 2C · 3 BO · 2 SK · 2
AN · 2 PI`, scaling cleanly to any staff size (`_role_counts`).

**"Guys change roles at times"** is just re-running the assignment. It fires:

- at **league seed**, per team, after the archetype roster-tilt finalizes the
  active/reserve split (`league.py`);
- at **season rollover**, after a season of development/decay reshuffles the staff
  (`development.develop_players_for_team`);
- after **trades**, for both clubs — an arm slots into his new team relative to the
  company he now keeps (`trades._do_trade`);
- on **injury call-ups**, the cover logic leans on the steering tier (`injuries.py`);
- and **by hand** on the rotation page (auto-assign button, or per-arm pin).

## 5a. The manager's situational staff review

The structural re-derivations above all key on *skill*. On top of them sits the
**skipper's own review of how his arms are actually throwing** — usage- and
form-driven, not skill-driven, and deliberately **not run every game**.

`maybe_review_staffs(game_date)` rides the same idempotent `sim_meta` cadence as the
weekly waiver sweep, but on a slower **~14-day** interval (a manager re-reads his
staff every couple of weeks, not every night). It's wired into all three sim drivers
(`simulate_next_n` / `simulate_date` / `simulate_through`) right next to the sweep,
and wrapped so a review failure never blocks the games.

Per team, `review_staff_for_team`:

1. Reads each active arm's **recent run prevention** (ER / outs over a 21-day window)
   and **self-normalizes** it against the staff's own recent rates — robust to O27's
   run environment, and it compares a man to his teammates, not an absolute bar.
2. Folds that hot/cold signal into each arm's **perceived Stuff only** (never Stamina
   — a physical attribute), then re-derives the crew on the adjusted ratings.
3. **Scales the whole swing by the skipper's reactivity** — a blend of his
   `quick_hook` and `bullpen_aggression`. A dead-ball traditionalist (reactivity
   < 0.12) doesn't re-tool mid-stream at all; a modern fireman-ball manager churns
   roles readily. *Managers keep a man in his role while it works* — only a clear
   enough swing, scaled by how reactive the skipper is, moves anyone.
4. Persists only the arms that actually moved and returns role-change events
   (`event_type: "staff_review"`) for the news feed.

So a Pilot who gets shelled for two weeks slides down the leverage ladder while a
Skidder who's been untouchable climbs it — but a patient skipper rides his guys, and
nobody is re-judged off a three-out cameo (a minimum-sample gate). Verified by
`test_rotation.py` (hot promoted / cold demoted, patient-manager no-op, cadence
idempotency) and an 8-team season smoke.

## 6. How the engine consumes them (canonical default + live override)

- **Steering pick** (`sim.py`, `_db_team_to_engine`): today's starter is chosen
  **fatigue-first** — the staff is tiered by rest exactly as before, and within the
  freshest tier the **Helms is preferred**, then higher Stamina. A gassed Helms
  drops a tier and the staff spot-steers with whoever is rested. With two Helms the
  freshest takes his turn. No special "every other day" rule — *the Helms is
  fatigue-governed like any arm.* No crew roles → reduces exactly to the old pick.

- **Relief calls** (`o27/engine/manager.py`, `pick_new_pitcher`): after the rest
  filter, candidates are scoped to the crew roles that fit the **moment of the
  voyage** (`preferred_relief_roles`): into-port → Pilot/Anchor, late hold →
  Anchor/Pilot/Skidder, rough patch → Skidder/2nd Change/Bosun, middle watch → the
  Changes/Bosun. If no role-matched arm is rested/available, it **falls through to
  the full pool** so Stuff/Stamina/matchup still decide — the role never traps a
  call. (The mapping is mirrored inline so the `o27` engine keeps no upward
  dependency on `o27v2`.)

Verified end-to-end: in a 6-game smoke the **Helms made all 12 starts**, with the
bullpen filling relief by role (Bosun bulk most-used, then Pilot/Skidder/Anchor/Changes).

## 7. Where it surfaces

- **Player cards / roster table:** a coarse **Starter / Reliever** read only — the
  crew title is deliberately *not* stamped on the card.
- **New team rotation page** (`/team/<id>/rotation`, "Rotation & crew »" link off the
  team page): the fleshed-out staff board, the crew in voyage order, each arm with
  Stuff/Stamina. **Auto-assign from skills** re-derives the whole crew; a per-arm
  dropdown pins a man to a role by hand. Copy reminds the reader these are roles,
  not titles, and that in-game usage flexes by fatigue/Stuff/matchup.

## 8. Tests & migrations

- `o27v2/tests/test_rotation.py` — assignment, team-relativity, two-Helms slotting,
  the voyage relief preference, thin-staff fill, empty-staff no-op; plus the manager
  review: hot-promoted/cold-demoted, patient-manager no-op, persona blend, cadence
  idempotency.
- `o27v2/tests/test_phase8_db_migration.py` — updated: every **active** arm seeds
  with a valid crew code and every staff carries a Helms (was: asserting the role is
  never persisted).
- `o27v2/tests/test_trades.py` — `trade_value` snapshot preserved (steering arms are
  valued as front-line pitchers, the rest of the crew as swing/relief; weights
  unchanged so `valuation._BANDS` stays calibrated).
- Full `o27` engine + `o27v2` manager/saves/archetype/engine-config suites pass.
- **Migration:** new `rotation_slot` column added idempotently in `init_db()`;
  existing saves upgrade on boot with no manual step.
