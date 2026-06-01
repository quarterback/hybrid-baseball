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


def test_manager_reactivity_blends_persona():
    patient = {"mgr_quick_hook": 0.0, "mgr_bullpen_aggression": 0.0}
    churner = {"mgr_quick_hook": 1.0, "mgr_bullpen_aggression": 1.0}
    assert _rotation._manager_reactivity(patient) == 0.0
    assert _rotation._manager_reactivity(churner) == 1.0
    mixed = {"mgr_quick_hook": 0.8, "mgr_bullpen_aggression": 0.2}
    assert abs(_rotation._manager_reactivity(mixed) - 0.5) < 1e-9


# ---- DB-backed review tests (tiny hand-built league) ----------------------
import os
import tempfile


def _mini_staff_db():
    """Init a temp DB with one team and a uniform 8-arm staff. Returns
    (restore_fn, team_id, [player_ids])."""
    from o27v2 import db
    orig = db._DB_PATH
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db._DB_PATH = path
    db.init_db()
    db.execute(
        "INSERT INTO teams (id, name, abbrev, city, division, league, "
        "mgr_quick_hook, mgr_bullpen_aggression) "
        "VALUES (1, 'Testers', 'TST', 'Testville', 'East', 'TestLg', 0.8, 0.8)"
    )
    pids = []
    for i in range(8):
        # Two clear Helms (high stamina); the rest uniform so recent form
        # is what separates them.
        stamina = 80 if i < 2 else 50
        pid = db.execute(
            "INSERT INTO players (team_id, name, position, is_pitcher, is_active, "
            "pitcher_skill, stamina, movement, command) "
            "VALUES (1, ?, 'P', 1, 1, 50, ?, 50, 50)",
            (f"Arm{i}", stamina),
        )
        pids.append(pid)
    _rotation.assign_roles_for_team(1)

    def restore():
        db._DB_PATH = orig
        os.unlink(path)
    return restore, 1, pids


def _log_appearance(team_id, player_id, game_date, outs, er):
    from o27v2 import db
    gid = db.execute(
        "INSERT INTO games (season, game_date, home_team_id, away_team_id, played) "
        "VALUES (1, ?, 1, 1, 1)",
        (game_date,),
    )
    db.execute(
        "INSERT INTO game_pitcher_stats (game_id, team_id, player_id, outs_recorded, er) "
        "VALUES (?, ?, ?, ?, ?)",
        (gid, team_id, player_id, outs, er),
    )


def test_review_promotes_hot_demotes_cold():
    from o27v2 import db
    restore, team_id, pids = _mini_staff_db()
    try:
        # Among the six non-Helms arms, give one a sparkling line and one a
        # shelling, with the rest middling, over the review window.
        hot, cold = pids[2], pids[3]
        for d in ("2026-04-10", "2026-04-13", "2026-04-16"):
            _log_appearance(team_id, hot,  d, outs=9, er=0)   # 0.00 ER/out
            _log_appearance(team_id, cold, d, outs=9, er=8)   # awful
            for mid in pids[4:]:
                _log_appearance(team_id, mid, d, outs=9, er=3)
        events = _rotation.review_staff_for_team(team_id, "2026-04-20")
        assert events, "a clear hot/cold split should move someone"
        roles = {p["id"]: p["pitcher_role"]
                 for p in db.fetchall(
                     "SELECT id, pitcher_role FROM players WHERE team_id = 1")}
        high_leverage = {_rotation.PILOT, _rotation.ANCHOR, _rotation.SKIDDER}
        assert roles[hot] in high_leverage, roles[hot]
        assert roles[cold] not in high_leverage | set(_rotation.STEER_ROLES), roles[cold]
    finally:
        restore()


def test_patient_manager_does_not_re_tool():
    from o27v2 import db
    restore, team_id, pids = _mini_staff_db()
    try:
        db.execute("UPDATE teams SET mgr_quick_hook = 0.0, "
                   "mgr_bullpen_aggression = 0.0 WHERE id = 1")
        for d in ("2026-04-10", "2026-04-13", "2026-04-16"):
            _log_appearance(team_id, pids[2], d, outs=9, er=0)
            _log_appearance(team_id, pids[3], d, outs=9, er=8)
        assert _rotation.review_staff_for_team(team_id, "2026-04-20") == []
    finally:
        restore()


def test_maybe_review_is_idempotent_within_interval():
    from o27v2 import db
    restore, team_id, pids = _mini_staff_db()
    try:
        for d in ("2026-04-10", "2026-04-13", "2026-04-16"):
            _log_appearance(team_id, pids[2], d, outs=9, er=0)
            _log_appearance(team_id, pids[3], d, outs=9, er=8)
        _rotation.maybe_review_staffs("2026-04-20")
        # A second call a few days later (inside the interval) is a no-op.
        assert _rotation.maybe_review_staffs("2026-04-22") == []
    finally:
        restore()
