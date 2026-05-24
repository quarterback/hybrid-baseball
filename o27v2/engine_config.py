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
DEFAULTS: dict[str, object] = {
    name: value
    for name, value in vars(cfg).items()
    if _is_editable(name, value)
}


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
}

PRESET_LABELS = {
    "deadball": "Deadball era (suppressed power, more small ball)",
    "juiced":   "Juiced / live-ball era (inflated power)",
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


def apply_overrides(force: bool = False) -> None:
    """Push the effective config onto o27.config. Resets every editable
    constant to its default first, so removing an override reverts cleanly.
    Idempotent and cheap (a few hundred setattrs); the DB is read once per
    process unless force=True."""
    global _applied
    if _applied and not force:
        return
    overrides = load_overrides()
    for name, default in DEFAULTS.items():
        setattr(cfg, name, overrides.get(name, default))
    _applied = True


def ensure_applied() -> None:
    """Apply once per process (called at the top of every game sim)."""
    if not _applied:
        apply_overrides()


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
