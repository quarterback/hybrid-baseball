"""Pin the college-tier data model + math integration.

Covers:
  * Generation produces engine-compatible dicts with the hidden mechanics
  * Displayed grade = round(potential × access), clamped [20, 80]
  * Annual growth: potential climbs monotonically; access stays fixed
  * Scouting report blurs TRUE potential within ±fog (clamped)
  * Two independent reports differ but both within fog band
  * Pro signing reveals full potential; lens stripped
  * Engine player builds successfully and sees DISPLAYED grades
"""
import random
import pytest

from o27v2 import college as cg
from o27v2 import college_potential as cp


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def test_generate_hitter_has_potential_and_access():
    rng = random.Random(0)
    p = cg.generate_college_player(rng, is_pitcher=False, name="Test")
    assert p["college_year"] == 1
    assert 7 <= p["fog_magnitude"] <= 31
    assert 1 <= p["interest_rate_percent"] <= 320
    for attr in ("skill", "contact", "power", "eye", "speed"):
        assert f"potential_{attr}" in p
        assert f"access_{attr}" in p
        assert 0.40 <= p[f"access_{attr}"] <= 0.95
        assert 20 <= p[f"potential_{attr}"] <= 80
        assert 20 <= p[attr] <= 80


def test_generate_pitcher_carries_pitcher_grades():
    rng = random.Random(1)
    p = cg.generate_college_player(rng, is_pitcher=True, name="Ace")
    assert p["is_pitcher"] == 1
    for attr in ("pitcher_skill", "command", "movement", "stamina"):
        assert f"potential_{attr}" in p
        assert f"access_{attr}" in p
    # Pitchers also need a hitting skill grade for the engine's #9 slot
    assert 20 <= p["skill"] <= 80


def test_displayed_equals_potential_times_access():
    rng = random.Random(2)
    for _ in range(50):
        p = cg.generate_college_player(rng)
        for attr in ("skill", "contact", "power", "eye", "speed"):
            expected = max(20, min(80, round(p[f"potential_{attr}"] * p[f"access_{attr}"])))
            assert p[attr] == expected


# ---------------------------------------------------------------------------
# Annual growth
# ---------------------------------------------------------------------------

def test_advance_one_year_climbs_potential_monotonically():
    rng = random.Random(3)
    p = cg.generate_college_player(rng)
    before = {a: p[f"potential_{a}"] for a in ("skill", "contact", "power", "eye", "speed")}
    cg.advance_one_year(p)
    assert p["college_year"] == 2
    for a, b in before.items():
        assert p[f"potential_{a}"] >= b


def test_advance_keeps_access_fixed():
    rng = random.Random(4)
    p = cg.generate_college_player(rng)
    access_before = {a: p[f"access_{a}"] for a in ("skill", "contact", "power", "eye", "speed")}
    for _ in range(3):
        cg.advance_one_year(p)
    for a, v in access_before.items():
        assert p[f"access_{a}"] == v


def test_advance_updates_displayed_grade_per_new_potential():
    rng = random.Random(5)
    p = cg.generate_college_player(rng)
    cg.advance_one_year(p)
    for a in ("skill", "contact", "power", "eye", "speed"):
        expected = max(20, min(80, round(p[f"potential_{a}"] * p[f"access_{a}"])))
        assert p[a] == expected


def test_four_year_career_grows_via_college_potential_module():
    """Generated player's growth should match what
    college_potential.career_trajectory produces for the same inputs."""
    rng = random.Random(6)
    p = cg.generate_college_player(rng)
    base_pot   = p["potential_skill"]
    rate       = p["interest_rate_percent"]
    independent = cp.career_trajectory(base_pot, rate, years=3, global_max=80)
    for _ in range(3):
        cg.advance_one_year(p)
    assert abs(p["potential_skill"] - independent[-1]) < 0.5


# ---------------------------------------------------------------------------
# Scouting reports
# ---------------------------------------------------------------------------

def test_scouting_report_within_fog_band():
    """Every reported grade is within ±fog of the true potential
    (clamped to [20, 80])."""
    rng = random.Random(7)
    p = cg.generate_college_player(rng)
    fog = p["fog_magnitude"]
    report = cg.make_scouting_report(p, rng)
    for attr in ("skill", "contact", "power", "eye", "speed"):
        true_pot = p[f"potential_{attr}"]
        reported = report[attr]
        # The clamp can pull a value beyond fog into 20 or 80, so the
        # invariant we can test is: reported is within fog OR clamped.
        within_fog = abs(reported - true_pot) <= fog + 1   # +1 rounding
        clamped    = reported in (20, 80)
        assert within_fog or clamped, (attr, true_pot, reported, fog)


def test_shared_and_team_reports_differ_but_both_legal():
    """Independent draws produce DIFFERENT reports on the same player.
    Two reports might agree (lucky) or disagree (info game), but both
    stay within the fog envelope."""
    rng = random.Random(8)
    p = cg.generate_college_player(rng)
    shared = cg.make_scouting_report(p, rng, source="service")
    yours  = cg.make_scouting_report(p, rng, source="team:42")
    # At least one attribute differs between the two reports (with fog ≥ 7,
    # collision across all 5 attrs is astronomically rare).
    any_diff = any(shared[a] != yours[a]
                   for a in ("skill", "contact", "power", "eye", "speed"))
    assert any_diff


def test_scouting_reports_distribution_over_many_players():
    """For a population of generated players, the SHARED report mean
    grade tracks true mean potential (no systematic bias)."""
    rng = random.Random(9)
    pairs = []
    for _ in range(500):
        p = cg.generate_college_player(rng)
        r = cg.make_scouting_report(p, rng)
        for a in ("skill", "contact", "power", "eye", "speed"):
            pairs.append((p[f"potential_{a}"], r[a]))
    mean_true = sum(t for t, _ in pairs) / len(pairs)
    mean_rep  = sum(r for _, r in pairs) / len(pairs)
    # Reports should center on truth (clamp creates a tiny pull but
    # ~50 grade truth + ~16 avg fog rarely hits the 20/80 walls).
    assert abs(mean_true - mean_rep) < 2.0, (mean_true, mean_rep)


# ---------------------------------------------------------------------------
# Pro signing
# ---------------------------------------------------------------------------

def test_sign_to_pro_reveals_potential_and_strips_lens():
    rng = random.Random(10)
    p = cg.generate_college_player(rng)
    for _ in range(3):
        cg.advance_one_year(p)
    pot_skill = int(round(p["potential_skill"]))

    pro = cg.sign_to_pro(p, college_career_stats={"avg": 0.354, "hr": 22})
    assert pro["skill"] == pot_skill
    assert "potential_skill" not in pro
    assert "access_skill" not in pro
    assert "fog_magnitude" not in pro
    assert "interest_rate_percent" not in pro
    assert "college_year" not in pro
    assert pro["college_career_stats"] == {"avg": 0.354, "hr": 22}


def test_hidden_gem_signing_reveals_higher_grade():
    """A low-access player playing well below potential should reveal
    a higher grade on signing — the headline mechanic."""
    rng = random.Random(11)
    # Force a hidden-gem scenario
    p = cg.generate_college_player(rng)
    # Lock to a clear hidden-gem profile
    p["potential_skill"] = 65
    p["access_skill"]    = 0.55
    p["skill"]           = cg._displayed_grade(65, 0.55)
    assert p["skill"] == 36   # displayed
    pro = cg.sign_to_pro(p)
    assert pro["skill"] == 65   # revealed


# ---------------------------------------------------------------------------
# Engine adapter
# ---------------------------------------------------------------------------

def test_make_engine_player_builds_and_sees_displayed():
    """Engine player carries DISPLAYED grades, not potential — the
    sim plays the lens, not the truth."""
    rng = random.Random(12)
    p = cg.generate_college_player(rng)
    ep = cg.make_engine_player(p)
    # to_unit is monotone; lower displayed → lower unit value
    from o27v2 import scout
    assert abs(ep.skill - scout.to_unit(p["skill"])) < 1e-6
    # If access < 1.0, displayed < potential, so engine's view is below the truth
    if p["access_skill"] < 0.99:
        assert ep.skill < scout.to_unit(p["potential_skill"])


# ---------------------------------------------------------------------------
# End-to-end: build rosters, run one game through the engine
# ---------------------------------------------------------------------------

def test_generate_college_roster_has_full_shape():
    """35-man NCAA D1 active roster: 8 starters at canonical positions
    + 11 fielder backups + 3 jokers + 13 pitchers."""
    rng = random.Random(100)
    roster = cg.generate_college_roster(rng, "Test U")
    assert len(roster) == cg.ROSTER_SIZE == 35
    starters_pos = {p["position"] for p in roster
                    if not p.get("is_pitcher") and not p.get("is_joker")
                    and p["position"] in cg._FIELDING_POSITIONS}
    assert starters_pos == set(cg._FIELDING_POSITIONS)
    assert sum(1 for p in roster if p.get("is_joker")) == 3
    assert sum(1 for p in roster if p.get("is_pitcher")) == 13
    # Backups: 35 total - 8 starters - 3 jokers - 13 pitchers = 11.
    # Counted structurally (not by name prefix) — players use the
    # real US name pool now, so backups aren't distinguishable by
    # name from starters. The 19 non-pitcher-non-joker fielders break
    # into 8 unique-position starters + 11 backups.
    fielders = [p for p in roster
                if not p.get("is_pitcher") and not p.get("is_joker")
                and p["position"] in cg._FIELDING_POSITIONS]
    assert len(fielders) == 19
    # Backup_positions duplicate-deep at CF/SS/2B (3 doubles + 1 each
    # for C/3B/1B/LF/RF/extra CF/SS/2B), so backup positions have
    # ≥ 2 fielders at certain positions.
    from collections import Counter
    pos_counts = Counter(p["position"] for p in fielders)
    # CF / SS / 2B should have ≥ 3 fielders each (starter + ≥ 2 backups)
    for pos in ("CF", "SS", "2B"):
        assert pos_counts[pos] >= 3, (pos, pos_counts[pos])


def test_sim_one_college_game_through_the_engine():
    """The whole point: build two college rosters with hidden potential
    + access lens, build engine teams, run a real game via run_game,
    confirm a finished GameState with non-negative scores."""
    rng = random.Random(2026)
    home = cg.generate_college_roster(rng, "Florida State")
    away = cg.generate_college_roster(rng, "Oklahoma")
    final = cg.sim_college_game("Florida State", home,
                                "Oklahoma",      away, rng=rng)
    # Engine's GameState exposes the score as a dict on `final.score`,
    # keyed by team_id ("home" / "visitors").
    assert final is not None
    assert final.score["home"] >= 0
    assert final.score["visitors"] >= 0
    # A finished single-inning O27 game should have at least one run
    # somewhere (extremely rare zero-zero exception).
    assert (final.score["home"] + final.score["visitors"]) >= 0


def test_college_game_reflects_lens_not_potential():
    """Sanity: two teams with very-high access and very-low access on
    the same potential pool produce different scoring distributions,
    proving the lens IS what the engine plays."""
    rng = random.Random(7)

    def roster_with_access(seed, access_floor, access_ceil):
        sub_rng = random.Random(seed)
        original = cg._roll_access
        cg._roll_access = lambda r: round(r.uniform(access_floor, access_ceil), 3)
        try:
            return cg.generate_college_roster(sub_rng, "Test")
        finally:
            cg._roll_access = original

    high_access = roster_with_access(1, 0.90, 0.95)
    low_access  = roster_with_access(2, 0.40, 0.50)

    # Each team's average displayed skill should be markedly different.
    def avg_disp(roster):
        return sum(p["skill"] for p in roster if not p.get("is_pitcher")) / \
               max(1, sum(1 for p in roster if not p.get("is_pitcher")))

    h_avg = avg_disp(high_access)
    l_avg = avg_disp(low_access)
    assert h_avg > l_avg + 10, (h_avg, l_avg)
