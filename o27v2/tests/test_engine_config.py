"""
Engine tunable override tests: discovery, persistence, runtime application
onto o27.config, presets, and reset.
"""
import pytest

import o27.config as cfg
import o27v2.db as db
from o27v2 import engine_config as ec


@pytest.fixture()
def fresh_db(tmp_path):
    orig = db._DB_PATH
    db._DB_PATH = str(tmp_path / "eng.db")
    try:
        db.init_db()
        ec._applied = False
        yield db
    finally:
        ec.reset_overrides()   # restore o27.config defaults for other tests
        ec._applied = False
        db._DB_PATH = orig


def test_discovers_scalar_constants():
    assert len(ec.DEFAULTS) > 100
    for name in ("POWER_REDIST_HR", "CONTACT_HARD_BASE", "PITCHER_DOM_BALL",
                 "SB_SUCCESS_BASE"):
        assert name in ec.DEFAULTS
    # Sanity-check targets and legacy stubs are excluded.
    assert not any(n.startswith("TARGET_") for n in ec.DEFAULTS)
    assert "POWER_HR_WEIGHT_SCALE" not in ec.DEFAULTS


def test_save_override_mutates_config_and_persists(fresh_db):
    default = ec.DEFAULTS["POWER_REDIST_HR"]
    ec.save_overrides({"POWER_REDIST_HR": "0.05"})
    assert cfg.POWER_REDIST_HR == 0.05
    assert ec.load_overrides() == {"POWER_REDIST_HR": 0.05}
    # Persists across a fresh process-load (simulated by forcing re-apply).
    ec._applied = False
    ec.ensure_applied()
    assert cfg.POWER_REDIST_HR == 0.05
    # Restore for the assertion below.
    ec.reset_overrides()
    assert cfg.POWER_REDIST_HR == default


def test_value_equal_to_default_is_not_stored(fresh_db):
    default = ec.DEFAULTS["POWER_REDIST_HR"]
    ec.save_overrides({"POWER_REDIST_HR": default})
    assert "POWER_REDIST_HR" not in ec.load_overrides()


def test_unknown_and_garbage_keys_ignored(fresh_db):
    ec.save_overrides({"NOT_A_REAL_CONSTANT": 1, "POWER_REDIST_HR": "oops"})
    assert ec.load_overrides() == {}


def test_deadball_preset_suppresses_power_and_keeps_contact_sum(fresh_db):
    ec.apply_preset("deadball")
    assert cfg.POWER_REDIST_HR == pytest.approx(0.12)
    assert cfg.POWER_REDIST_HR < ec.DEFAULTS["POWER_REDIST_HR"]
    s = cfg.CONTACT_WEAK_BASE + cfg.CONTACT_MEDIUM_BASE + cfg.CONTACT_HARD_BASE
    assert s == pytest.approx(1.0, abs=0.01)
    assert cfg.CONTACT_HARD_BASE < ec.DEFAULTS["CONTACT_HARD_BASE"]


def test_juiced_preset_inflates_power(fresh_db):
    ec.apply_preset("juiced")
    assert cfg.POWER_REDIST_HR > ec.DEFAULTS["POWER_REDIST_HR"]
    assert cfg.CONTACT_HARD_BASE > ec.DEFAULTS["CONTACT_HARD_BASE"]


def test_reset_restores_all_defaults(fresh_db):
    ec.apply_preset("deadball")
    ec.reset_overrides()
    assert ec.load_overrides() == {}
    assert cfg.POWER_REDIST_HR == ec.DEFAULTS["POWER_REDIST_HR"]
    assert cfg.CONTACT_HARD_BASE == ec.DEFAULTS["CONTACT_HARD_BASE"]


def test_config_fields_cover_every_editable_constant(fresh_db):
    listed = set()
    for _, items in ec.config_fields():
        listed.update(name for name, _ in items)
    assert listed == set(ec.DEFAULTS)
