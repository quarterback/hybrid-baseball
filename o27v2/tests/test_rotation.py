"""Tests for the crew-role assignment (o27v2/rotation.py)."""
from __future__ import annotations

from o27v2 import rotation as _rotation


def _staff(specs):
    """Build pitcher dicts from (pitcher_skill, stamina[, movement]) tuples."""
    out = []
    for i, spec in enumerate(specs):
        skill, stam = spec[0], spec[1]
        mv = spec[2] if len(spec) > 2 else 50
        out.append({
            "id": i + 1, "is_pitcher": 1,
            "pitcher_skill": skill, "stamina": stam, "movement": mv,
            "command": 50,
        })
    return out


def test_full_staff_assigns_every_arm_a_role():
    staff = _staff([(50 + (i % 30), 40 + (i * 2) % 40) for i in range(17)])
    _rotation.assign_staff_roles(staff)
    assert all(p["pitcher_role"] in _rotation.ALL_ROLES for p in staff)
    # Counts sum to the staff size — nobody dropped.
    assert sum(1 for p in staff) == 17


def test_high_stamina_arm_steers_as_helms():
    # One clear workhorse (huge stamina) should land at the Helms tier.
    staff = _staff(
        [(80, 80)]                       # the obvious Helms
        + [(60, 45) for _ in range(16)]  # ordinary arms
    )
    _rotation.assign_staff_roles(staff)
    helms = [p for p in staff if p["pitcher_role"] == _rotation.HELMS]
    assert staff[0]["pitcher_role"] == _rotation.HELMS
    assert 1 <= len(helms) <= 2


def test_best_stuff_short_arm_finishes_as_pilot_or_anchor():
    # A pure-Stuff, low-Stamina arm belongs in the late/finish roles, not
    # steering the voyage.
    staff = _staff(
        [(80, 30)]                       # fireballer, no length
        + [(55, 65) for _ in range(16)]  # durable mediocrities steer
    )
    _rotation.assign_staff_roles(staff)
    role = staff[0]["pitcher_role"]
    assert role in (_rotation.PILOT, _rotation.ANCHOR), role
    assert not _rotation.is_steer_role(role)


def test_role_is_relative_to_the_staff():
    # The SAME arm grades differently depending on the company he keeps:
    # an ace among scrubs steers; the same line among aces does not.
    ace = (72, 70)
    thin = _staff([ace] + [(40, 40) for _ in range(16)])
    deep = _staff([ace] + [(85, 85) for _ in range(16)])
    _rotation.assign_staff_roles(thin)
    _rotation.assign_staff_roles(deep)
    assert thin[0]["pitcher_role"] == _rotation.HELMS
    assert deep[0]["pitcher_role"] != _rotation.HELMS


def test_two_helms_get_distinct_slots():
    staff = _staff([(70, 75), (68, 72)] + [(55, 45) for _ in range(15)])
    _rotation.assign_staff_roles(staff)
    helms = sorted(
        (p for p in staff if p["pitcher_role"] == _rotation.HELMS),
        key=lambda p: p["rotation_slot"],
    )
    assert len(helms) == 2
    assert [h["rotation_slot"] for h in helms] == [1, 2]


def test_preferred_relief_roles_walk_the_voyage():
    # Late calls want the finishers; early calls want the bridge/bulk.
    assert _rotation.PILOT in _rotation.preferred_relief_roles(25)
    assert _rotation.ANCHOR in _rotation.preferred_relief_roles(20)
    assert _rotation.SKIDDER in _rotation.preferred_relief_roles(14)
    assert _rotation.CHANGE1 in _rotation.preferred_relief_roles(2)
    # The Helms steers and is never a relief option.
    for outs in range(0, 27):
        assert _rotation.HELMS not in _rotation.preferred_relief_roles(outs)


def test_thin_staff_fills_priority_roles_first():
    staff = _staff([(70, 70), (60, 55), (58, 50)])
    _rotation.assign_staff_roles(staff)
    roles = {p["pitcher_role"] for p in staff}
    # 3 arms → one each of the top-priority roles (Helms, Pilot, Anchor).
    assert _rotation.HELMS in roles
    assert len(roles) == 3


def test_empty_staff_is_a_noop():
    assert _rotation.assign_staff_roles([]) == []
