"""
Engine tunables — runtime overrides for the o27 simulation constants.

The engine reads every knob as a module attribute on `o27.config` at call
time (e.g. `cfg.POWER_REDIST_HR`), so we can re-tune the whole simulation at
runtime simply by setattr-ing new values onto that module — no engine edits.
This module:

  * snapshots the pristine default of every editable scalar constant at import
    (before anything overrides them),
  * persists user overrides as a JSON blob in sim_meta (key 'engine_config'),
    surviving offseason resets and wiped only on a full reseed,
  * applies them onto o27.config, and
  * ships named presets (Deadball, Juiced, …) so a whole run environment can
    be flipped in one click.

Only simple scalars (int/float/bool) are exposed. The handful of structural
constants (PITCH_BASE, the WEAK/MEDIUM/HARD_CONTACT tables, PITCH_CATALOG) are
nested probability structures with internal invariants and are deliberately
NOT editable here.
"""
from __future__ import annotations

import json
import random

import o27.config as cfg
from o27v2 import db

_META_KEY = "engine_config"

# Names that are int/float but aren't tuning knobs: sanity-check targets,
# legacy stubs. Excluded from the editable set.
_DENYLIST_PREFIXES = ("TARGET_", "SANITY_")
_DENYLIST_NAMES = {"POWER_HR_WEIGHT_SCALE"}  # legacy stub, see config.py


def _is_editable(name: str, value) -> bool:
    if not name.isupper() or name.startswith("_"):
        return False
    if name in _DENYLIST_NAMES or name.startswith(_DENYLIST_PREFIXES):
        return False
    # bool is a subclass of int — allow it (rendered as a toggle).
    return isinstance(value, (int, float, bool))


# Pristine defaults, captured ONCE at import before any override is applied.
# Two domains: the o27 engine constants (auto-discovered) and a curated
# allowlist of o27v2-side knobs. We only expose o27v2 constants that the sim
# reads at call time (so a runtime override actually takes effect) — e.g.
# HOME_ADVANTAGE_SKILL. Roster-shape / archetype constants are import-bound or
# seed-time and are deliberately omitted to avoid sliders that do nothing.
import o27v2.config as v2cfg

_O27_DEFAULTS: dict[str, object] = {
    name: value
    for name, value in vars(cfg).items()
    if _is_editable(name, value)
}

_V2_ALLOWLIST = (
    "HOME_ADVANTAGE_SKILL",
    "GEN_SHIFT_SKILL", "GEN_SHIFT_CONTACT", "GEN_SHIFT_POWER",
    "GEN_SHIFT_EYE", "GEN_SHIFT_SPEED", "GEN_SHIFT_DEFENSE",
    "GEN_SHIFT_ARM", "GEN_SHIFT_PITCHING", "GEN_SHIFT_STAMINA",
    "JOKER_POWER_POWER", "JOKER_POWER_CONTACT", "JOKER_POWER_SPEED", "JOKER_POWER_EYE",
    "JOKER_SPEED_POWER", "JOKER_SPEED_CONTACT", "JOKER_SPEED_SPEED", "JOKER_SPEED_EYE",
    "JOKER_CONTACT_POWER", "JOKER_CONTACT_CONTACT", "JOKER_CONTACT_SPEED", "JOKER_CONTACT_EYE",
)
_V2_DEFAULTS: dict[str, object] = {
    name: getattr(v2cfg, name)
    for name in _V2_ALLOWLIST
    if hasattr(v2cfg, name) and name not in _O27_DEFAULTS
}

# Combined view used everywhere for validation / coercion / storage.
DEFAULTS: dict[str, object] = {**_O27_DEFAULTS, **_V2_DEFAULTS}


def _target_module(name: str):
    """Which config module owns this constant."""
    return v2cfg if name in _V2_DEFAULTS else cfg


def _coerce(name: str, value):
    """Coerce an incoming value to match the default's type."""
    default = DEFAULTS[name]
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "on", "yes")
        return bool(value)
    if isinstance(default, int):
        return int(round(float(value)))
    return float(value)


# --------------------------------------------------------------------------
# Curated grouping for the UI. Anything not listed here is still editable —
# it falls into the auto-generated "All other constants" section — so the
# dashboard covers 100% of the editable engine knobs.
# --------------------------------------------------------------------------
_CURATED: list[tuple[str, list[tuple[str, str]]]] = [
    ("Power & extra-base hits", [
        ("POWER_REDIST_HR",        "Hard contact → HR conversion"),
        ("POWER_REDIST_HARD_S2D",  "Hard: single → double"),
        ("POWER_REDIST_HARD_D2T",  "Hard: double → triple"),
        ("POWER_REDIST_MED_S2D",   "Medium: single → double"),
        ("POWER_REDIST_MED_GO2FO", "Medium: grounder → fly"),
        ("POWER_REDIST_WEAK_S2FO", "Weak: single → fly out"),
    ]),
    ("Contact quality mix (weak + medium + hard should sum to ~1.0)", [
        ("CONTACT_WEAK_BASE",     "Weak contact base rate"),
        ("CONTACT_MEDIUM_BASE",   "Medium contact base rate"),
        ("CONTACT_HARD_BASE",     "Hard contact base rate"),
        ("CONTACT_MATCHUP_SHIFT", "Matchup shift magnitude"),
    ]),
    ("Pitcher dominance", [
        ("PITCHER_DOM_BALL",     "Ball rate adj. when dominant"),
        ("PITCHER_DOM_CALLED",   "Called-strike adj."),
        ("PITCHER_DOM_SWINGING", "Swinging-strike adj."),
        ("PITCHER_DOM_CONTACT",  "Contact-event adj."),
    ]),
    ("Batter dominance", [
        ("BATTER_DOM_SWINGING", "Swinging-strike adj. (skilled batter)"),
        ("BATTER_DOM_CONTACT",  "Contact-event adj."),
    ]),
    ("Baserunning & inside-the-park", [
        ("RUNNER_EXTRA_SPEED_SCALE",   "Extra-base speed scaling"),
        ("RUNNER_EXTRA_DOUBLE_FROM_1B", "1B → 3B on a double"),
        ("ITP_HR_BASE_ATTEMPT",        "Inside-park HR attempt rate"),
        ("ITP_HR_BASE_SUCCESS",        "Inside-park HR success rate"),
    ]),
    ("Stolen bases", [
        ("SB_SUCCESS_BASE", "Stolen-base success baseline"),
    ]),
    ("Optional rules", [
        ("POWER_PLAY_ENABLED", "Power Play (deploy a 10th defender — the nickel fielder)"),
        ("IBB_ENABLE",         "Intentional walks"),
    ]),
    ("Context (o27v2)", [
        ("HOME_ADVANTAGE_SKILL", "Home-field skill bonus"),
    ]),
    ("New-player generation (grade points; affects players generated AFTER you save)", [
        ("GEN_SHIFT_SKILL",    "Hitter overall skill shift"),
        ("GEN_SHIFT_CONTACT",  "Contact shift"),
        ("GEN_SHIFT_POWER",    "Power shift"),
        ("GEN_SHIFT_EYE",      "Eye / plate discipline shift"),
        ("GEN_SHIFT_SPEED",    "Speed shift"),
        ("GEN_SHIFT_DEFENSE",  "Defense shift"),
        ("GEN_SHIFT_ARM",      "Arm shift"),
        ("GEN_SHIFT_PITCHING", "Pitcher Stuff + arsenal quality shift"),
        ("GEN_SHIFT_STAMINA",  "Pitcher stamina shift"),
    ]),
    ("Joker archetypes (grade centers; one power / speed / contact joker per team)", [
        ("JOKER_POWER_POWER",     "Power joker — power"),
        ("JOKER_POWER_CONTACT",   "Power joker — contact"),
        ("JOKER_POWER_SPEED",     "Power joker — speed"),
        ("JOKER_POWER_EYE",       "Power joker — eye"),
        ("JOKER_SPEED_POWER",     "Speed joker — power"),
        ("JOKER_SPEED_CONTACT",   "Speed joker — contact"),
        ("JOKER_SPEED_SPEED",     "Speed joker — speed"),
        ("JOKER_SPEED_EYE",       "Speed joker — eye"),
        ("JOKER_CONTACT_POWER",   "Contact joker — power"),
        ("JOKER_CONTACT_CONTACT", "Contact joker — contact"),
        ("JOKER_CONTACT_SPEED",   "Contact joker — speed"),
        ("JOKER_CONTACT_EYE",     "Contact joker — eye"),
    ]),
]


def config_fields() -> list[tuple[str, list[tuple[str, str]]]]:
    """Curated groups (filtered to constants that actually exist) plus an
    auto-built 'All other constants' group covering everything else."""
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    seen: set[str] = set()
    for label, items in _CURATED:
        present = [(n, lbl) for n, lbl in items if n in DEFAULTS]
        if present:
            groups.append((label, present))
            seen.update(n for n, _ in present)
    rest = sorted(n for n in DEFAULTS if n not in seen)
    if rest:
        groups.append(("All other constants", [(n, n) for n in rest]))
    return groups


def bool_keys() -> set[str]:
    return {n for n, v in DEFAULTS.items() if isinstance(v, bool)}


# --------------------------------------------------------------------------
# Presets — named override bundles. "default" is the empty bundle (no
# overrides). The coupled CONTACT_*_BASE triple is always set together so it
# keeps summing to ~1.0.
# --------------------------------------------------------------------------
PRESETS: dict[str, dict[str, object]] = {
    "deadball": {
        "POWER_REDIST_HR":        0.12,
        "POWER_REDIST_HARD_S2D":  0.26,
        "POWER_REDIST_HARD_D2T":  0.18,
        "CONTACT_WEAK_BASE":      0.30,
        "CONTACT_MEDIUM_BASE":    0.52,
        "CONTACT_HARD_BASE":      0.18,
        "PITCHER_DOM_CONTACT":   -0.08,
        "RUNNER_EXTRA_DOUBLE_FROM_1B": 0.22,
    },
    "juiced": {
        "POWER_REDIST_HR":        0.72,
        "POWER_REDIST_HARD_S2D":  0.34,
        "CONTACT_WEAK_BASE":      0.12,
        "CONTACT_MEDIUM_BASE":    0.46,
        "CONTACT_HARD_BASE":      0.42,
    },

    # --- Era recreations -------------------------------------------------
    "era_1968": {
        "CONTACT_WEAK_BASE":      0.26,
        "CONTACT_MEDIUM_BASE":    0.52,
        "CONTACT_HARD_BASE":      0.22,
        "POWER_REDIST_HR":        0.20,
        "PITCHER_DOM_BALL":      -0.10,
        "PITCHER_DOM_SWINGING":   0.055,
        "PITCHER_DOM_CONTACT":   -0.10,
        "PITCHER_COMMAND_CALLED": 0.05,
        "GIDP_BASE_PROB":         0.16,
        "SB_SUCCESS_BASE":        0.62,
        "SB_ATTEMPT_PROB_PER_PITCH": 0.06,
        "GEN_SHIFT_PITCHING":     12,
        "GEN_SHIFT_POWER":       -8,
        "GEN_SHIFT_CONTACT":     -4,
        "GEN_SHIFT_SPEED":        4,
    },
    "era_1987": {
        "POWER_REDIST_HR":        0.66,
        "PARK_HR_MAX":            1.35,
        "PARK_HR_MIN":            0.95,
        "CONTACT_WEAK_BASE":      0.16,
        "CONTACT_MEDIUM_BASE":    0.48,
        "CONTACT_HARD_BASE":      0.36,
        "GEN_SHIFT_POWER":        12,
    },
    "era_2010s_tto": {
        "POWER_REDIST_HR":        0.60,
        "POWER_REDIST_MED_GO2FO": 0.30,
        "POWER_REDIST_WEAK_S2FO": 0.30,
        "CONTACT_WEAK_BASE":      0.14,
        "CONTACT_MEDIUM_BASE":    0.46,
        "CONTACT_HARD_BASE":      0.40,
        "PITCHER_DOM_SWINGING":   0.05,
        "BATTER_DOM_SWINGING":   -0.02,
        "BATTER_EYE_BALL":        0.06,
        "GIDP_BASE_PROB":         0.10,
        "GEN_SHIFT_POWER":        10,
        "GEN_SHIFT_EYE":          6,
        "GEN_SHIFT_CONTACT":     -6,
        "GEN_SHIFT_PITCHING":     6,
    },

    # --- Stylistic identities --------------------------------------------
    "junkball": {
        "POWER_REDIST_HR":        0.28,
        "CONTACT_WEAK_BASE":      0.30,
        "CONTACT_MEDIUM_BASE":    0.52,
        "CONTACT_HARD_BASE":      0.18,
        "MOVEMENT_GB_WEIGHT_SCALE": 0.10,
        "CONTACT_MOVEMENT_TILT":  0.18,
        "PITCHER_DOM_SWINGING":  -0.01,
        "BATTER_DOM_SWINGING":   -0.09,
        "PITCHER_COMMAND_CALLED": 0.05,
        "GIDP_BASE_PROB":         0.17,
        "GEN_SHIFT_PITCHING":    -8,
        "GEN_SHIFT_POWER":       -6,
    },
    "launch_circus": {
        "POWER_REDIST_HR":        0.80,
        "POWER_REDIST_MED_GO2FO": 0.40,
        "POWER_REDIST_WEAK_S2FO": 0.40,
        "CONTACT_WEAK_BASE":      0.16,
        "CONTACT_MEDIUM_BASE":    0.42,
        "CONTACT_HARD_BASE":      0.42,
        "PITCHER_DOM_SWINGING":   0.06,
        "BATTER_DOM_SWINGING":    0.00,
        "GIDP_BASE_PROB":         0.07,
        "PARK_HR_MAX":            1.40,
        "GEN_SHIFT_POWER":        15,
        "GEN_SHIFT_CONTACT":     -8,
    },
    "contact_carnival": {
        "CONTACT_WEAK_BASE":      0.12,
        "CONTACT_MEDIUM_BASE":    0.50,
        "CONTACT_HARD_BASE":      0.38,
        "BATTER_DOM_SWINGING":   -0.10,
        "BATTER_CONTACT_SWINGING": -0.12,
        "PITCHER_DOM_SWINGING":  -0.01,
        "POWER_REDIST_HR":        0.38,
        "POWER_REDIST_HARD_S2D":  0.38,
        "DEFENSE_RANGE_SHIFT_SCALE": 0.16,
        "GEN_SHIFT_CONTACT":      12,
        "GEN_SHIFT_POWER":       -6,
    },
    # College-softball SCORING ENVIRONMENT (not a softball conversion — no slap/
    # steal identity). Reproduces the *shape* of D1 softball offense in O27:
    # high batting average / high BABIP (hits fall in), low power, and the circle
    # (pitching) — not double plays — keeping the big inning in check, so it
    # settles into the low-scoring band.
    #
    # Anchored to 2026 NCAA Div I team data (national averages, which sit below
    # the top-50 leaderboards): ~.295 BA, ~5 R/game and ERA ~3.7 over a 21-out
    # softball game → ~6.4 R/team/game once scaled to O27's 27 outs and
    # discounted for the Walk-Back HR tail. Two signatures the data makes
    # explicit: (1) a huge ace-vs-field spread — ERA leaders at ~1.35 vs a ~3.7
    # mean → high CONTACT_MATCHUP_SHIFT + strong pitcher dominance; (2) double
    # plays are RARE (national leader only 0.84/game, ~half of baseball) → GIDP
    # is pushed BELOW O27 default, and run suppression comes from the circle
    # instead. The absolute R/G is governed by O27's structure — verify with a
    # benchmark and read the characterize() band rather than expecting parity.
    "college_scoring": {
        "CONTACT_WEAK_BASE":      0.26,   # contact-rich: hits fall in (high BABIP/BA)
        "CONTACT_MEDIUM_BASE":    0.56,
        "CONTACT_HARD_BASE":      0.18,   # low hard contact → few XBH/HR
        "CONTACT_MATCHUP_SHIFT":  0.32,   # big ace-vs-field spread (ERA 1.35 → ~3.7)
        "POWER_REDIST_HR":        0.12,   # power suppressed hard
        "POWER_REDIST_HARD_S2D":  0.24,   # some gap doubles survive
        "POWER_REDIST_HARD_D2T":  0.10,
        "PITCHER_DOM_SWINGING":   0.055,  # elite arms miss bats
        "PITCHER_DOM_CONTACT":   -0.10,   # circle suppresses contact (the run check)
        "BATTER_DOM_SWINGING":   -0.05,   # but the field still puts the ball in play
        "BATTER_EYE_BALL":        0.04,   # moderate walks
        "PARK_HR_MAX":            1.00,   # no hitter-park HR boost
        "GIDP_BASE_PROB":         0.13,   # softball turns few DPs (≈half of baseball)
        "GEN_SHIFT_POWER":       -10,
        "GEN_SHIFT_CONTACT":       8,
        "GEN_SHIFT_PITCHING":      6,
    },
    "speed_demon": {
        "SB_ATTEMPT_PROB_PER_PITCH": 0.09,
        "SB_SUCCESS_BASE":        0.70,
        "SB_ATTEMPT_SPEED_THRESHOLD": 0.45,
        "ITP_HR_BASE_ATTEMPT":    0.30,
        "ITP_HR_BASE_SUCCESS":    0.62,
        "RUNNER_EXTRA_SPEED_SCALE": 0.55,
        "RUNNER_EXTRA_DOUBLE_FROM_1B": 0.45,
        "SPEED_ADVANCE_MOD":      0.20,
        "POWER_REDIST_HARD_D2T":  0.20,
        "POWER_REDIST_HR":        0.40,
        "GEN_SHIFT_SPEED":        15,
        "GEN_SHIFT_POWER":       -6,
    },
    "workhorse": {
        "WORKHORSE_CHANGE_BASE":  40,
        "WORKHORSE_STAMINA_THRESHOLD": 0.50,
        "RELIEVER_CHANGE_BASE":   20,
        "RELIEVER_ENTRY_OUTS_MIN": 24,
        "FATIGUE_DEBT_PER_PITCH": 0.003,
        "FATIGUE_DEBT_MAX_PENALTY": 0.25,
        "FATIGUE_DEBT_BUDGET_SCALE": 130,
        "PITCHER_COMMAND_CALLED": 0.04,
        "GEN_SHIFT_STAMINA":      15,
    },

    # --- Engine stress-tests (intentional extremes) ----------------------
    "knifes_edge": {
        "POWER_REDIST_HR":        0.85,
        "POWER_REDIST_HARD_S2D":  0.40,
        "POWER_REDIST_HARD_D2T":  0.18,
        "CONTACT_WEAK_BASE":      0.08,
        "CONTACT_MEDIUM_BASE":    0.42,
        "CONTACT_HARD_BASE":      0.50,
        "BATTER_DOM_SWINGING":   -0.10,
        "BATTER_DOM_CONTACT":     0.06,
        "BATTER_EYE_BALL":        0.08,
        "PITCHER_DOM_BALL":       0.0,
        "PITCHER_DOM_SWINGING":  -0.02,
        "PITCHER_DOM_CONTACT":    0.0,
        "DEFENSE_ERROR_BASE":     0.075,
        "PARK_HR_MAX":            1.40,
        "GEN_SHIFT_POWER":        15,
        "GEN_SHIFT_CONTACT":      10,
        "GEN_SHIFT_EYE":          8,
        "GEN_SHIFT_PITCHING":    -12,
    },
    "pitchers_hellscape": {
        "POWER_REDIST_HR":        0.06,
        "POWER_REDIST_HARD_S2D":  0.18,
        "CONTACT_WEAK_BASE":      0.34,
        "CONTACT_MEDIUM_BASE":    0.52,
        "CONTACT_HARD_BASE":      0.14,
        "PITCHER_DOM_BALL":      -0.11,
        "PITCHER_DOM_CALLED":     0.04,
        "PITCHER_DOM_SWINGING":   0.07,
        "PITCHER_DOM_CONTACT":   -0.11,
        "PITCHER_COMMAND_BALL":  -0.10,
        "PITCHER_COMMAND_CALLED": 0.06,
        "BATTER_DOM_SWINGING":    0.0,
        "BATTER_EYE_BALL":        0.01,
        "DEFENSE_ERROR_BASE":     0.020,
        "GIDP_BASE_PROB":         0.18,
        "PARK_HR_MAX":            1.00,
        "GEN_SHIFT_PITCHING":     15,
        "GEN_SHIFT_POWER":       -12,
        "GEN_SHIFT_CONTACT":     -8,
    },
}

PRESET_LABELS = {
    "deadball": "Deadball era (suppressed power, more small ball)",
    "juiced":   "Juiced / live-ball era (inflated power)",
    "era_1968": "1968 Year of the Pitcher (pitcher-dominant, low power)",
    "era_1987": "1987 Lively Ball (HR-heavy spike, otherwise ordinary)",
    "era_2010s_tto": "2010s Launch Angle / Three True Outcomes (HR + K + BB)",
    "junkball": "Junkball League (soft stuff, weak contact, low K)",
    "launch_circus": "Launch-Angle Circus (extreme three-true-outcomes)",
    "contact_carnival": "Contact Carnival (highest BABIP, lowest K)",
    "college_scoring": "College Softball scoring environment (high contact, low power, low-scoring)",
    "speed_demon": "Speed Demon League (steals, triples, inside-the-park HRs)",
    "workhorse": "Workhorse Era (starters go deep, bullpen quiet)",
    "knifes_edge": "Knife's Edge (max-offense stress test)",
    "pitchers_hellscape": "Pitcher's Hellscape (min-offense stress test)",
}


# --------------------------------------------------------------------------
# Load / save / apply.
# --------------------------------------------------------------------------
def load_overrides() -> dict[str, object]:
    """Stored overrides, validated against the editable set."""
    row = db.fetchone("SELECT value FROM sim_meta WHERE key = ?", (_META_KEY,))
    if not row or not row.get("value"):
        return {}
    try:
        stored = json.loads(row["value"])
    except Exception:
        return {}
    out: dict[str, object] = {}
    for k, v in stored.items():
        if k in DEFAULTS:
            try:
                out[k] = _coerce(k, v)
            except (TypeError, ValueError):
                continue
    return out


def effective() -> dict[str, object]:
    """The live value of every editable constant (default + overrides)."""
    eff = dict(DEFAULTS)
    eff.update(load_overrides())
    return eff


def is_overridden(name: str) -> bool:
    return name in load_overrides()


_applied = False
# Signature (the raw stored-blob string) of the overrides this process last
# applied. ensure_applied() re-applies when it changes, so a setting toggled
# after startup — or saved by another worker — takes effect without a restart.
_applied_sig: str | None = None


def _stored_signature() -> str:
    """Cheap fingerprint of the stored override blob (one indexed lookup)."""
    row = db.fetchone("SELECT value FROM sim_meta WHERE key = ?", (_META_KEY,))
    return (row.get("value") if row else "") or ""


def apply_values(overrides: dict) -> None:
    """Push an override dict onto the live config modules (no DB read).
    Resets every editable constant to its default first, so any constant not
    in `overrides` reverts cleanly. Used by apply_overrides and by the
    benchmark subprocess (which gets its overrides passed in, not from DB)."""
    for name, default in DEFAULTS.items():
        val = overrides.get(name, default)
        try:
            val = _coerce(name, val)
        except (TypeError, ValueError):
            val = default
        setattr(_target_module(name), name, val)


def apply_overrides(force: bool = False) -> None:
    """Push the effective stored config onto the config modules. Idempotent
    and cheap (a few hundred setattrs); the DB is read once per process unless
    force=True."""
    global _applied, _applied_sig
    if _applied and not force:
        return
    apply_values(load_overrides())
    _applied = True
    _applied_sig = _stored_signature()


def ensure_applied() -> None:
    """Apply at the top of every game sim, re-reading the stored tuning if it
    changed since this process last applied it.

    The check is a single indexed lookup; the full re-apply only runs when the
    blob actually changed. This is what makes an Engine Settings edit (e.g.
    toggling Power Play) take effect on the next sim WITHOUT a server restart —
    even when the sim runs in a different worker process than the one that saved
    it. Previously this applied once per process and silently ignored every
    later edit, so a freshly-checked setting never reached the simulator."""
    global _applied, _applied_sig
    sig = _stored_signature()
    if not _applied or sig != _applied_sig:
        apply_values(load_overrides())
        _applied = True
        _applied_sig = sig


def save_overrides(partial: dict) -> dict[str, object]:
    """Merge a (partial) update into the stored overrides and apply. Values
    equal to the default are dropped so the blob stays minimal."""
    merged = load_overrides()
    for k, v in partial.items():
        if k not in DEFAULTS:
            continue
        try:
            cv = _coerce(k, v)
        except (TypeError, ValueError):
            continue
        if cv == DEFAULTS[k]:
            merged.pop(k, None)
        else:
            merged[k] = cv
    _store(merged)
    apply_overrides(force=True)
    return merged


def _store(overrides: dict) -> None:
    db.execute(
        "INSERT OR REPLACE INTO sim_meta (key, value) VALUES (?, ?)",
        (_META_KEY, json.dumps(overrides)),
    )


def reset_overrides() -> None:
    """Drop all overrides and restore defaults on o27.config."""
    db.execute("DELETE FROM sim_meta WHERE key = ?", (_META_KEY,))
    apply_overrides(force=True)


def apply_preset(name: str) -> dict[str, object]:
    """Replace overrides with a named preset bundle (empty = back to default).
    Unknown preset names clear overrides."""
    bundle = PRESETS.get(name, {})
    overrides: dict[str, object] = {}
    for k, v in bundle.items():
        if k in DEFAULTS:
            try:
                overrides[k] = _coerce(k, v)
            except (TypeError, ValueError):
                continue
    _store(overrides)
    apply_overrides(force=True)
    return overrides


# --------------------------------------------------------------------------
# Eclectic randomizer — roll a guard-railed random tuning. Each knob draws
# from a SENSIBLE per-knob range (roughly default ±50%, clamped well inside the
# math limits so nothing hits a singularity), and only a handful of knobs are
# perturbed per roll — randomizing every knob tends to cancel out to
# near-default, so targeted randomization produces more legible weirdness.
# --------------------------------------------------------------------------
_RANDOMIZE_RANGES: dict[str, tuple[float, float]] = {
    # Power / extra-base hits
    "POWER_REDIST_HR":           (0.10, 0.85),
    "POWER_REDIST_HARD_S2D":     (0.18, 0.42),
    "POWER_REDIST_HARD_D2T":     (0.04, 0.22),
    "POWER_REDIST_MED_S2D":      (0.10, 0.32),
    "POWER_REDIST_MED_GO2FO":    (0.06, 0.40),
    "POWER_REDIST_WEAK_S2FO":    (0.08, 0.40),
    # Pitcher / batter dominance
    "PITCHER_DOM_BALL":          (-0.12, -0.02),
    "PITCHER_DOM_SWINGING":      (-0.02, 0.07),
    "PITCHER_DOM_CONTACT":       (-0.12, -0.01),
    "PITCHER_COMMAND_CALLED":    (0.01, 0.06),
    "BATTER_DOM_SWINGING":       (-0.11, 0.00),
    "BATTER_DOM_CONTACT":        (0.00, 0.07),
    "BATTER_EYE_BALL":           (0.01, 0.08),
    # Baserunning / steals / defense / DPs
    "SB_SUCCESS_BASE":           (0.40, 0.78),
    "SB_ATTEMPT_PROB_PER_PITCH": (0.02, 0.10),
    "DEFENSE_ERROR_BASE":        (0.020, 0.075),
    "GIDP_BASE_PROB":            (0.07, 0.18),
    "ITP_HR_BASE_ATTEMPT":       (0.08, 0.35),
    "RUNNER_EXTRA_SPEED_SCALE":  (0.20, 0.55),
    "MOVEMENT_GB_WEIGHT_SCALE":  (0.02, 0.12),
    "PARK_HR_MAX":               (1.00, 1.40),
    # Talent-pool shape (grade points; only bites on a reseed/regen)
    "GEN_SHIFT_POWER":           (-12, 15),
    "GEN_SHIFT_CONTACT":         (-10, 12),
    "GEN_SHIFT_SPEED":           (-8, 15),
    "GEN_SHIFT_PITCHING":        (-12, 15),
    "GEN_SHIFT_EYE":             (-8, 10),
    "GEN_SHIFT_STAMINA":         (-8, 15),
}

_RANDOMIZE_N_MIN = 8
_RANDOMIZE_N_MAX = 12


def randomize_overrides(seed: int | None = None,
                        n_knobs: int | None = None) -> dict[str, object]:
    """Replace the working tuning with a random, guard-railed bundle. Perturbs
    8–12 knobs drawn from `_RANDOMIZE_RANGES`, and ~70% of the time also sets
    the coupled CONTACT_*_BASE triple coherently (so it still sums to ~1.0).
    Reproducible when `seed` is given."""
    rng = random.Random(seed)
    keys = [k for k in _RANDOMIZE_RANGES if k in DEFAULTS]
    lo_n, hi_n = _RANDOMIZE_N_MIN, min(_RANDOMIZE_N_MAX, len(keys))
    n = n_knobs if n_knobs is not None else rng.randint(lo_n, hi_n)
    n = max(1, min(n, len(keys)))
    chosen = rng.sample(keys, n)

    overrides: dict[str, object] = {}
    for k in chosen:
        lo, hi = _RANDOMIZE_RANGES[k]
        try:
            overrides[k] = _coerce(k, rng.uniform(lo, hi))
        except (TypeError, ValueError):
            continue

    # Coherent contact-quality mix (sums to ~1.0) on most rolls.
    if rng.random() < 0.70 and {"CONTACT_HARD_BASE", "CONTACT_WEAK_BASE",
                                "CONTACT_MEDIUM_BASE"} <= set(DEFAULTS):
        hard = rng.uniform(0.16, 0.46)
        weak = rng.uniform(0.10, 0.34)
        med  = max(0.20, 1.0 - hard - weak)
        total = hard + weak + med
        overrides["CONTACT_HARD_BASE"]   = round(hard / total, 3)
        overrides["CONTACT_WEAK_BASE"]   = round(weak / total, 3)
        overrides["CONTACT_MEDIUM_BASE"] = round(med / total, 3)

    _store(overrides)
    apply_overrides(force=True)
    return overrides


# --------------------------------------------------------------------------
# Named environments — the user's own library of tunings. Each is just a
# snapshot of an override set, stored by name. The "working" overrides
# (load_overrides) are what's live; saving snapshots them under a name,
# loading copies a snapshot back into the working set.
# --------------------------------------------------------------------------
_ENVS_KEY = "engine_environments"


def list_environments() -> dict[str, dict]:
    """name -> override dict, for every saved environment."""
    row = db.fetchone("SELECT value FROM sim_meta WHERE key = ?", (_ENVS_KEY,))
    if not row or not row.get("value"):
        return {}
    try:
        data = json.loads(row["value"])
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for name, ov in data.items():
        if not isinstance(ov, dict):
            continue
        clean: dict[str, object] = {}
        for k, v in ov.items():
            if k in DEFAULTS:
                try:
                    clean[k] = _coerce(k, v)
                except (TypeError, ValueError):
                    continue
        out[str(name)] = clean
    return out


def _store_environments(envs: dict[str, dict]) -> None:
    db.execute(
        "INSERT OR REPLACE INTO sim_meta (key, value) VALUES (?, ?)",
        (_ENVS_KEY, json.dumps(envs)),
    )


def save_environment(name: str, overrides: dict | None = None) -> bool:
    """Snapshot an override set under `name` (defaults to the current working
    set). Overwrites an existing environment of the same name. Returns False
    for an empty name."""
    name = (name or "").strip()[:60]
    if not name:
        return False
    snapshot = load_overrides() if overrides is None else {
        k: _coerce(k, v) for k, v in overrides.items() if k in DEFAULTS
    }
    envs = list_environments()
    envs[name] = snapshot
    _store_environments(envs)
    return True


def load_environment(name: str) -> bool:
    """Make a saved environment the working (live) tuning and apply it.
    Returns False if the name isn't found."""
    envs = list_environments()
    if name not in envs:
        return False
    _store(envs[name])
    apply_overrides(force=True)
    return True


def delete_environment(name: str) -> None:
    envs = list_environments()
    if name in envs:
        del envs[name]
        _store_environments(envs)


# --------------------------------------------------------------------------
# Characterising a tuning by what it produces. Given the per-team-per-game
# stats from a benchmark sim, derive a short descriptive label so an
# environment is identified by its run environment, not just its name.
# Bands are rough, O27-calibrated (the sport runs hot: ~19 R/G, ~2.8 HR/G
# per team at default).
# --------------------------------------------------------------------------
def _band(value: float, cuts: list[tuple[float, str]]) -> str:
    for ceil, label in cuts:
        if value < ceil:
            return label
    return cuts[-1][1]


def characterize(stats: dict) -> str:
    """Return a short label like 'Deadball · pitcher-dominant' from a
    benchmark stat dict carrying per-team 'hr_per_game' and 'r_per_game'."""
    hr = float(stats.get("hr_per_game") or 0.0)
    r = float(stats.get("r_per_game") or 0.0)
    power = _band(hr, [
        (1.0, "Deadball"), (2.0, "Low-power"), (3.5, "Normal-power"),
        (5.0, "High-power"), (float("inf"), "Extreme-power"),
    ])
    scoring = _band(r, [
        (12.0, "pitcher-dominant"), (17.0, "low-scoring"),
        (23.0, "normal-scoring"), (30.0, "high-scoring"),
        (float("inf"), "explosive"),
    ])
    return f"{power} · {scoring}"
