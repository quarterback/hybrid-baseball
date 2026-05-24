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


def test_save_and_load_named_environment(fresh_db):
    ec.save_overrides({"POWER_REDIST_HR": 0.2, "SB_SUCCESS_BASE": 0.8})
    assert ec.save_environment("Slap Hit League") is True
    # Change the working tuning to something else.
    ec.save_overrides({"POWER_REDIST_HR": 0.95})
    assert cfg.POWER_REDIST_HR == 0.95
    # Loading the saved env makes it live again.
    assert ec.load_environment("Slap Hit League") is True
    assert cfg.POWER_REDIST_HR == 0.2
    assert cfg.SB_SUCCESS_BASE == 0.8
    assert ec.list_environments()["Slap Hit League"]["POWER_REDIST_HR"] == 0.2


def test_empty_environment_name_rejected(fresh_db):
    assert ec.save_environment("   ") is False
    assert ec.list_environments() == {}


def test_delete_and_load_missing_environment(fresh_db):
    ec.save_overrides({"POWER_REDIST_HR": 0.2})
    ec.save_environment("Temp")
    ec.delete_environment("Temp")
    assert "Temp" not in ec.list_environments()
    assert ec.load_environment("Temp") is False


def test_o27v2_knob_applies_to_v2_config(fresh_db):
    import o27v2.config as v2cfg
    assert "HOME_ADVANTAGE_SKILL" in ec.DEFAULTS
    ec.save_overrides({"HOME_ADVANTAGE_SKILL": 0.3})
    assert v2cfg.HOME_ADVANTAGE_SKILL == pytest.approx(0.3)
    ec.reset_overrides()
    assert v2cfg.HOME_ADVANTAGE_SKILL == ec.DEFAULTS["HOME_ADVANTAGE_SKILL"]


def test_generation_shift_knobs_apply_and_drive_league_gen(fresh_db):
    import o27v2.config as v2cfg
    from o27v2 import league
    for k in ("GEN_SHIFT_POWER", "GEN_SHIFT_CONTACT", "GEN_SHIFT_PITCHING"):
        assert k in ec.DEFAULTS
    ec.save_overrides({"GEN_SHIFT_POWER": 15, "GEN_SHIFT_CONTACT": -8})
    assert v2cfg.GEN_SHIFT_POWER == pytest.approx(15)
    # league reads the shift at call time, rounded to grade points.
    assert league._gen_shift("power") == 15
    assert league._gen_shift("contact") == -8
    assert league._gen_shift("speed") == 0
    assert league._gen_shift(None) == 0
    ec.reset_overrides()
    assert league._gen_shift("power") == 0


def test_jokers_drawn_as_three_archetypes(fresh_db):
    from o27v2.league import seed_league
    ec.reset_overrides()
    seed_league(rng_seed=11, config_id="8teams")
    teams = db.fetchall(
        "SELECT DISTINCT team_id FROM players "
        "WHERE roster_slot='joker' AND team_id IS NOT NULL"
    )
    assert teams
    for t in teams:
        jk = db.fetchall(
            "SELECT archetype FROM players "
            "WHERE roster_slot='joker' AND team_id=?", (t["team_id"],)
        )
        assert sorted(j["archetype"] for j in jk) == ["contact", "power", "speed"]
    # The power joker out-powers the speed joker; the speed joker is faster.
    pwr = db.fetchone("SELECT AVG(power) v FROM players WHERE roster_slot='joker' "
                      "AND archetype='power' AND team_id IS NOT NULL")["v"]
    spd_pwr = db.fetchone("SELECT AVG(power) v FROM players WHERE roster_slot='joker' "
                          "AND archetype='speed' AND team_id IS NOT NULL")["v"]
    spd_spd = db.fetchone("SELECT AVG(speed) v FROM players WHERE roster_slot='joker' "
                          "AND archetype='speed' AND team_id IS NOT NULL")["v"]
    pwr_spd = db.fetchone("SELECT AVG(speed) v FROM players WHERE roster_slot='joker' "
                          "AND archetype='power' AND team_id IS NOT NULL")["v"]
    assert pwr > spd_pwr
    assert spd_spd > pwr_spd


def test_youth_jokers_drawn_as_archetypes(fresh_db):
    import random
    from o27v2 import youth
    ec.reset_overrides()
    rng = random.Random(3)
    by_arch = {
        a: youth._make_youth_player(rng, "CF", 0, "Kid", "US", 17,
                                    is_joker=True, joker_archetype=a)
        for a in ("power", "speed", "contact")
    }
    assert by_arch["power"]["power"] > by_arch["speed"]["power"]
    assert by_arch["speed"]["speed"] > by_arch["power"]["speed"]
    assert by_arch["contact"]["contact"] >= by_arch["power"]["contact"]
    for a, p in by_arch.items():
        assert p["roster_slot"] == "joker"
        assert p["archetype"] == a


def test_characterize_labels_by_run_environment():
    assert ec.characterize({"hr_per_game": 0.6, "r_per_game": 9}) \
        == "Deadball · pitcher-dominant"
    assert ec.characterize({"hr_per_game": 2.8, "r_per_game": 19}) \
        == "Normal-power · normal-scoring"
    assert ec.characterize({"hr_per_game": 6.0, "r_per_game": 33}) \
        == "Extreme-power · explosive"
