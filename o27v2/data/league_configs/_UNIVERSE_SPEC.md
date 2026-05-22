# International League Preset Spec (peer-universe configs)

A hand-off reference for authoring **peer-universe** league configs for O27.
Each file describes a *world* of several **co-equal, fully independent**
major leagues that run at once (the global-basketball model, NOT a
promotion/relegation pyramid). Leagues play only themselves; players move
between leagues off the field (transfers / offseason), never via interleague
games. Each league pulls from its own talent distribution (a **style**) and
its own **locale** for player names — so the leagues play measurably different
baseball with no rule changes.

---

## 1. Where the file goes & how it loads

- Put each config at `o27v2/data/league_configs/<id>.json`.
- The loader (`get_league_configs`) auto-discovers every `*.json` in that
  directory at runtime, keyed by the config's `id`. No registration needed.
- `<id>` and the `"id"` field must match, be unique, and use only
  `[a-z0-9_]` (lowercase letters, digits, underscores).
- Do NOT reuse these reserved ids: `8teams`, `12teams`, `16teams`, `24teams`,
  `30teams`, `36teams`, `56teams_tiered`, `international`, `custom`.

Once present, the preset appears everywhere automatically (New League list,
multi-season + pre-sim-history dropdowns).

---

## 2. Top-level schema

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | ✅ | matches filename; `[a-z0-9_]`, unique |
| `label` | string | ✅ | human title shown in the UI |
| `team_count` | int | ✅ | MUST equal the sum of every `league_specs[].teams` |
| `level` | string | ✅ | use `"MLB"` (selects from the MLB team pool) |
| `schedule_mode` | string | ✅ | MUST be `"independent"` for a peer universe |
| `leagues` | string[] | ✅ | the league display names (same names used as keys below) |
| `league_specs` | object[] | ✅ | one entry per league (see §3) |
| `style_profiles` | object | ✅ | `{ "<league name>": <style> }` (see §4) |
| `name_regions` | object | ✅ | `{ "<league name>": <locale> }` (see §5) |
| `games_per_team` | int | ✅ | games each team plays (all within its own league) |
| `season_days` | int | ✅ | calendar budget; ~1.7–2.2 × (games_per_team) works |
| `intra_division_weight` | float | ✅ | use `0.5` |
| `inter_division_weight` | float | ✅ | use `0.5` |
| `season_year` | int | ⛔ optional | default 2026 |
| `season_start_month` | int | ⛔ optional | default 4 |
| `season_start_day` | int | ⛔ optional | default 1 |
| `all_star_break_month` | int | ⛔ optional | default 7 |
| `all_star_break_day` | int | ⛔ optional | default 13 |
| `all_star_break_days` | int | ⛔ optional | default 4 |
| `gender` | string | ⛔ optional | `"male"` (default), `"female"`, or `"mixed"` |

The league-name string is the join key across `leagues`, `league_specs[].name`,
`style_profiles`, and `name_regions`. They MUST match exactly.

---

## 3. `league_specs` entries

```json
{ "name": "Nippon League", "teams": 6, "divisions": 1 }
```

| Field | Type | Rules |
|---|---|---|
| `name` | string | unique within the universe; the join key |
| `teams` | int | **MUST be even** (≥ 2); even is required for a balanced schedule |
| `divisions` | int | ≥ 1; **`teams` must divide evenly by `divisions`** |

Leagues may be **different sizes** (an O27-MLB of 24 beside an O27-KBO of 10).
Within a league, games split between same-division and cross-division opponents
per the intra/inter weights; with `divisions: 1` everyone is one group.

---

## 4. `style_profiles` — a league's talent distribution

A style biases the *attributes the generator rolls* for that league's players
(both hitters and pitchers; each maker reads only its own keys). Because EVERY
team in a league shares the profile, intra-league competitive parity is
automatic — the style only shifts the league's NET run environment.

A value is **either** a preset key (string) **or** a custom bias dict.

### 4a. Preset keys (string)

| key | feel | net signature |
|---|---|---|
| `npb` | Nippon — contact / command | high AVG, low K both ways, few HR |
| `dominican` | Dominican — power / TTO | most HR + K, fewest walks |
| `european` | European — discipline / OBP | highest BB/OBP, low HR, low K |
| `caribbean` | Caribbean — speed / BABIP | most SB & triples, contact > power |
| `athletic` | Academy — toolsy / high-ceiling | power + speed + arm, lower polish (K-prone, wild) |
| `balanced` | neutral control | no bias |

(Aliases `contact`→npb, `power`→dominican, `speed_defense`→caribbean also work.)

### 4b. Custom bias dict (author your own archetype)

```json
{ "power": 18, "contact": -6, "eye": -8, "command": -5, "movement": -10 }
```

- Allowed keys (omit any you don't want to touch; omitted = 0 = neutral):
  - **Hitter:** `contact`, `power`, `eye`, `speed`, `baserunning`,
    `run_aggressiveness`, `defense`, `arm`
  - **Pitcher:** `pitcher_skill` (stuff/velocity), `command` (control),
    `movement` (groundball / HR-suppression), `stamina`
- Each value is an integer in **−25 … +25** grade points. Positive = the
  league produces more of that trait.
- Engine coupling worth knowing (both sides of the ball are biased inside the
  league, so design for the NET effect):
  - **HR up** ⇐ hitter `power` ↑ AND/OR pitcher `movement` ↓ (fly-ball arms).
    Boosting pitcher `pitcher_skill` SUPPRESSES HR — don't raise it if you want
    a slugfest.
  - **K up** ⇐ pitcher `pitcher_skill` ↑ and hitter `contact`/`eye` ↓.
  - **BB up** ⇐ hitter `eye` ↑ while keeping pitcher `command` modest
    (high pitcher `command` cancels walks).
  - **AVG/BABIP up** ⇐ hitter `contact` ↑, pitcher `pitcher_skill`/`movement` ↓.
  - **SB / triples up** ⇐ hitter `speed`, `baserunning`, `run_aggressiveness` ↑.
- Reasonable magnitudes: headline trait ±10…20, supporting traits ±4…10.

Custom styles also bend player **development trajectories** over seasons (a
league's emphasis develops faster), which is what makes inter-league transfers
interesting.

---

## 5. `name_regions` — a league's locale (player & manager names)

Decoupled from style: a league can be located anywhere we have name data. A
value is **one** of:

1. A single **region id** (names pinned to that region):
   `afghan_central_asia`, `africa`, `africa_cricket`, `anzac`, `british_isles`,
   `canada`, `caribbean_cricket`, `caribbean_dutch`, `central_west_asia`,
   `east_asia`, `europe_eastern`, `europe_western`, `latin_america`, `malaysia`,
   `nordic`, `pacific_islands`, `south_america`, `south_asia`, `southeast_asia`,
   `us`
2. A named **preset blend** (id): `americas_pro`, `o27_year_1`, `o27_year_5`,
   `o27_year_10`, `global`, `european`, `asian_pro`, `nordic`, `us_only`
3. A **custom weighted blend** object, e.g.:
   `{ "africa": 0.45, "central_west_asia": 0.4, "africa_cricket": 0.15 }`
   (keys are region ids; weights are normalized automatically)

Omit a league from `name_regions` (or use `""`) to fall back to the default mix.

> Namespace note: `european` and `nordic` exist as BOTH a style key and a
> name-region preset. They're independent — `style_profiles` takes style keys,
> `name_regions` takes locale ids. Don't cross them.

---

## 6. Hard validation rules (a config is rejected if any fail)

1. `team_count` == Σ `league_specs[].teams`.
2. Every league's `teams` is **even** and ≥ 2.
3. Every league's `teams % divisions == 0`.
4. `schedule_mode` == `"independent"`.
5. League names are unique and identical across `leagues`, `league_specs`,
   `style_profiles`, `name_regions`.
6. Each style is a known preset key OR a dict whose keys are all in the allowed
   custom-attr set, each value an int in −25…+25.
7. Each locale is a known region id, a known preset id, or a weight dict of
   region ids.
8. `team_count` ≤ 86 total (the team pool size). Keep ≤ 36 to stay entirely in
   the MLB-flavored team pool; 37–86 backfills from AAA/AA/A team identities.

---

## 7. Behavior (what the engine does with this)

- **Independent scheduling:** each league plays a self-contained round-robin
  (per intra/inter weights) for `games_per_team` games, all on the same
  calendar so leagues run concurrently. Zero interleague games.
- **Per-league talent:** each league's draft pool is generated under its style
  + locale, then drafted within that league. Free agents pool across leagues.
- **Movement:** players cross leagues via transfers (player page → transfer)
  and offseason signings, not games.
- **Persistence:** each team stores its style on `teams.style_profile` (preset
  key, or the custom dict as JSON), surfaced as a badge.

---

## 8. Full annotated example

```json
{
  "id": "world_classic",
  "label": "World Classic (4 peer leagues)",
  "team_count": 28,
  "level": "MLB",
  "schedule_mode": "independent",
  "leagues": ["O27 MLB", "O27 NPB", "O27 KBO", "Caribbean Series"],
  "league_specs": [
    { "name": "O27 MLB",          "teams": 10, "divisions": 2 },
    { "name": "O27 NPB",          "teams": 6,  "divisions": 1 },
    { "name": "O27 KBO",          "teams": 6,  "divisions": 1 },
    { "name": "Caribbean Series", "teams": 6,  "divisions": 1 }
  ],
  "style_profiles": {
    "O27 MLB": "balanced",
    "O27 NPB": "npb",
    "O27 KBO": { "contact": 10, "eye": 6, "power": -6, "command": 8, "pitcher_skill": -4, "movement": 4 },
    "Caribbean Series": "caribbean"
  },
  "name_regions": {
    "O27 MLB": "us",
    "O27 NPB": "east_asia",
    "O27 KBO": "east_asia",
    "Caribbean Series": "caribbean_cricket"
  },
  "games_per_team": 72,
  "season_days": 150,
  "intra_division_weight": 0.5,
  "inter_division_weight": 0.5,
  "season_year": 2026,
  "season_start_month": 4,
  "season_start_day": 1,
  "all_star_break_month": 7,
  "all_star_break_day": 13,
  "all_star_break_days": 4
}
```

## 9. Minimal copy-paste skeleton

```json
{
  "id": "REPLACE_ME",
  "label": "REPLACE ME",
  "team_count": 0,
  "level": "MLB",
  "schedule_mode": "independent",
  "leagues": ["League A", "League B"],
  "league_specs": [
    { "name": "League A", "teams": 6, "divisions": 1 },
    { "name": "League B", "teams": 6, "divisions": 1 }
  ],
  "style_profiles": { "League A": "balanced", "League B": "balanced" },
  "name_regions":   { "League A": "global",   "League B": "global" },
  "games_per_team": 60,
  "season_days": 150,
  "intra_division_weight": 0.5,
  "inter_division_weight": 0.5,
  "season_year": 2026
}
```
(Remember to set `team_count` = sum of `teams`.)
