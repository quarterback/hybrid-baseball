"""
College-tier player data model + engine integration.

Each college player carries three layered grade sets per attribute:

    potential_X    — the hidden true ceiling (20-80, climbs via the
                     interest-rate model in college_potential.py)
    access_X       — the lens that determines what fraction of potential
                     shows right now (Uniform 0.40-0.95, drawn once at
                     college generation, STATIC through career)
    displayed_X    — what the engine sees and the box score reflects:
                     round(potential_X × access_X), clamped [20, 80]

Plus two per-player scalars:

    interest_rate_percent — drawn at generation via
                            college_potential.draw_interest_rate()
                            (75% tier 1 / 20% tier 2 / 5% tier 3 super-bloomer)
    fog_magnitude         — Uniform(7, 31), drives scouting-report noise

End-of-season:
  * potential climbs (college_potential.grow_one_year applied per attribute)
  * access stays fixed — hidden gems with low access STAY hidden their
    whole college career, then play at their true grade in pro
  * fog stays fixed — but two independent draws are taken (shared
    scouting service + your own department), refreshed every year

On pro signing:
  * displayed → revealed: pro engine sees the full potential (no lens)
  * college career stats stamp onto the player card
  * scouting reports are no longer needed; you OWN the player and see
    actual grades

This module is the data model + math integration. League plumbing
(college schedule, conference / regional / WCWS postseason, the
per-team scouting department) layers on top.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, asdict

from o27v2 import college_potential as _cp
from o27v2 import scout as _scout


# ---------------------------------------------------------------------------
# Attributes modeled per college player
# ---------------------------------------------------------------------------
#
# These are the engine-facing grades that get the potential/access lens
# treatment. Engine has a wider set (defense_infield, defense_outfield,
# baserunning, etc.) but those don't need the hidden-potential mechanic
# at the college tier — they're rolled directly as displayed grades.

_HITTER_GRADES = ("skill", "contact", "power", "eye", "speed")
_PITCHER_GRADES = ("pitcher_skill", "command", "movement", "stamina")

# Canonical fielding positions the engine recognises (matches
# o27v2.pro_worldcup._HITTER_POSITIONS_ORDER).
_FIELDING_POSITIONS: tuple[str, ...] = ("CF", "SS", "2B", "3B", "RF", "LF", "1B", "C")

# Other engine-facing attrs that DON'T get the potential lens — rolled
# at displayed grade directly, no growth applied to them in college.
_DIRECT_HITTER_ATTRS = ("defense", "arm", "defense_infield",
                        "defense_outfield", "defense_catcher",
                        "baserunning", "run_aggressiveness")


def _all_modeled_grades(is_pitcher: bool) -> tuple[str, ...]:
    return _PITCHER_GRADES if is_pitcher else _HITTER_GRADES


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

# Base potential distribution for incoming freshmen — most kids in the
# 30-55 range, a few elite recruits up at 60-70. The 4-year growth
# system lets late bloomers climb from low starts.
_BASE_POT_MU    = 42.0
_BASE_POT_SIGMA = 9.0
_BASE_POT_MIN   = 22
_BASE_POT_MAX   = 72


def _roll_base_potential(rng: random.Random) -> int:
    """One attribute's freshman-year potential grade."""
    g = rng.gauss(_BASE_POT_MU, _BASE_POT_SIGMA)
    g = max(_BASE_POT_MIN, min(_BASE_POT_MAX, g))
    return int(round(g))


def _roll_access(rng: random.Random) -> float:
    """Per-attribute access lens: how much of the potential shows."""
    return round(rng.uniform(0.40, 0.95), 3)


def _roll_fog(rng: random.Random) -> int:
    """Per-player scouting fog magnitude — uniform on [7, 31]."""
    return rng.randint(7, 31)


def _displayed_grade(potential: int, access: float) -> int:
    """The visible grade an engine / scouting card surfaces."""
    g = int(round(potential * access))
    return max(20, min(80, g))


def generate_college_player(rng: random.Random,
                            is_pitcher: bool = False,
                            name: str = "",
                            country: str = "",
                            position: str = "") -> dict:
    """Build a freshman college player. All hidden mechanics rolled here.

    The returned dict is engine-compatible (skill/contact/power/etc.
    fields are the DISPLAYED grades, not potential) so it can flow
    through the existing engine-player conversion. Hidden fields
    (potential_*, access_*, interest_rate_percent, fog_magnitude) are
    additive and never read by the engine.
    """
    p: dict = {
        "name": name,
        "country": country,
        "is_pitcher": 1 if is_pitcher else 0,
        "is_active": 1,
        "college_year": 1,
        "position": position or ("P" if is_pitcher else ""),
        "interest_rate_percent": _cp.draw_interest_rate(rng),
        "fog_magnitude": _roll_fog(rng),
    }

    # Modeled grades: potential, access, and current-displayed.
    for attr in _all_modeled_grades(is_pitcher):
        pot = _roll_base_potential(rng)
        acc = _roll_access(rng)
        p[f"potential_{attr}"] = pot
        p[f"access_{attr}"]    = acc
        p[attr] = _displayed_grade(pot, acc)

    # Non-modeled attrs — direct displayed grade, no potential/access lens.
    # Hitters need glove + arm grades for engine sim; pitchers don't.
    if not is_pitcher:
        for attr in _DIRECT_HITTER_ATTRS:
            p[attr] = _roll_base_potential(rng)
        # Engine reads skill/contact/power/eye on hitters; pitcher_skill on pitchers.
        # Defaulted to 50 so the engine doesn't choke on missing fields.
        p["pitcher_skill"] = 0
    else:
        # Pitchers also need a basic skill grade (engine reads it for
        # hitting at the #9 slot per O27 rules). Use displayed pitcher_skill
        # as a proxy with a small downshift for the bat.
        p["skill"]     = max(20, p["pitcher_skill"] - 18)
        p["contact"]   = max(20, p["pitcher_skill"] - 18)
        p["power"]     = max(20, p["pitcher_skill"] - 20)
        p["eye"]       = max(20, p["pitcher_skill"] - 18)
        p["speed"]     = _roll_base_potential(rng)
        p["defense"]   = _roll_base_potential(rng)
        p["arm"]       = p["pitcher_skill"]  # arm scales with pitching
        p["baserunning"] = max(20, _roll_base_potential(rng) - 5)
        p["run_aggressiveness"] = _roll_base_potential(rng)
        p["defense_infield"]  = 0
        p["defense_outfield"] = 0
        p["defense_catcher"]  = 0

    # Engine-side fields with sane defaults.
    p.setdefault("stay_aggressiveness", 0.30)
    p.setdefault("contact_quality_threshold", 0.50)
    p.setdefault("bats",   "R")
    p.setdefault("throws", "R")
    p.setdefault("archetype", "")
    p.setdefault("pitcher_role", "")
    p.setdefault("hard_contact_delta", 0.0)
    p.setdefault("hr_weight_bonus", 0.0)
    p.setdefault("is_joker", 0)
    p.setdefault("roster_slot", "")
    if not is_pitcher:
        p.setdefault("stamina", _roll_base_potential(rng))

    return p


# ---------------------------------------------------------------------------
# Annual growth tick
# ---------------------------------------------------------------------------

def advance_one_year(player: dict) -> dict:
    """Apply one year of college development — grow every modeled
    potential via the college_potential growth function, refresh
    displayed grades, increment college_year. Access and fog stay fixed.

    Mutates and returns the input dict for chainability."""
    interest = player["interest_rate_percent"]
    for attr in _all_modeled_grades(bool(player.get("is_pitcher"))):
        pot_key = f"potential_{attr}"
        acc_key = f"access_{attr}"
        new_pot = _cp.grow_one_year(player[pot_key], interest, global_max=80)
        player[pot_key] = round(new_pot, 2)
        player[attr] = _displayed_grade(player[pot_key], player[acc_key])
    player["college_year"] = int(player.get("college_year", 1)) + 1
    return player


# ---------------------------------------------------------------------------
# Scouting reports
# ---------------------------------------------------------------------------

def _blur(grade: float, fog: int, rng: random.Random) -> int:
    """Apply ±fog noise to one grade, clamp [20, 80]."""
    noisy = grade + rng.uniform(-fog, fog)
    if noisy < 20: return 20
    if noisy > 80: return 80
    return int(round(noisy))


def make_scouting_report(player: dict, rng: random.Random,
                         *, source: str = "service") -> dict:
    """Generate one noisy report on `player` — `source` is "service"
    (the shared scouting service every team reads) or "team:<id>" (your
    own department's report; one per pro team, all drawn independently).

    The report blurs TRUE POTENTIAL (not displayed) — scouting is trying
    to project the player's pro ceiling, not their current college
    output. You see the player's college stats separately and have to
    triangulate between the two reports + the stats.
    """
    fog = int(player["fog_magnitude"])
    report: dict = {"source": source, "fog_magnitude": fog}
    for attr in _all_modeled_grades(bool(player.get("is_pitcher"))):
        true_pot = float(player[f"potential_{attr}"])
        report[attr] = _blur(true_pot, fog, rng)
    return report


# ---------------------------------------------------------------------------
# Pro signing — reveal full potential, stamp college stats
# ---------------------------------------------------------------------------

def sign_to_pro(college_player: dict,
                college_career_stats: dict | None = None) -> dict:
    """Convert a college player to a pro-ready player dict.

      * Engine grades upgrade from `displayed` to true potential
      * Access / fog / interest_rate / potential_* fields stripped
      * `college_career_stats` (optional dict of accumulated college
        totals) stamped onto the player card for surfacing on
        /player/<id> and the FA listing

    The pro side never had visibility into potential during college —
    this is the moment of reveal. Hidden gems play UP to their true
    grade; college legends with mediocre potential play to whatever
    they actually are.
    """
    pro = dict(college_player)
    pro["college_career_stats"] = dict(college_career_stats or {})

    for attr in _all_modeled_grades(bool(college_player.get("is_pitcher"))):
        # Drop the lens — engine sees full potential.
        pro[attr] = int(round(college_player.get(f"potential_{attr}", college_player[attr])))
        pro.pop(f"potential_{attr}", None)
        pro.pop(f"access_{attr}",    None)

    # Strip the college-only scalars.
    for k in ("fog_magnitude", "interest_rate_percent", "college_year"):
        pro.pop(k, None)

    return pro


# ---------------------------------------------------------------------------
# Engine adapter — build a Player object for o27.engine.run_game
# ---------------------------------------------------------------------------

def make_engine_player(player: dict, *, home_bonus: float = 0.0):
    """Build an engine `Player` from a college player dict. The engine
    sees DISPLAYED grades — the lens is applied. Mirrors
    `o27v2.pro_worldcup._make_engine_player` so college games slot into
    the same engine path the pro World Cup uses.

    Late-bound import keeps this module independent of the engine at
    import time (matters for tests that don't load the full engine).
    """
    from o27.engine.state import Player

    def U(x): return _scout.to_unit(x)

    return Player(
        player_id=str(player.get("id", id(player))),
        name=str(player.get("name") or "College Player"),
        is_pitcher=bool(player.get("is_pitcher")),
        skill=U(player.get("skill", 50)) + home_bonus,
        speed=U(player.get("speed", 50)),
        pitcher_skill=U(player.get("pitcher_skill", 0)),
        stamina=U(player.get("stamina", 50)),
        stay_aggressiveness=float(player.get("stay_aggressiveness", 0.30)),
        contact_quality_threshold=float(player.get("contact_quality_threshold", 0.50)),
        archetype=str(player.get("archetype", "")),
        pitcher_role=str(player.get("pitcher_role", "")),
        hard_contact_delta=float(player.get("hard_contact_delta", 0.0)),
        hr_weight_bonus=float(player.get("hr_weight_bonus", 0.0)),
        contact=U(player.get("contact", 50)),
        power=U(player.get("power", 50)),
        eye=U(player.get("eye", 50)),
        command=U(player.get("command", 50)),
        movement=U(player.get("movement", 50)),
        bats=str(player.get("bats", "R")),
        throws=str(player.get("throws", "R")),
        defense=U(player.get("defense", 50)),
        arm=U(player.get("arm", 50)),
        defense_infield=U(player.get("defense_infield", 50)),
        defense_outfield=U(player.get("defense_outfield", 50)),
        defense_catcher=U(player.get("defense_catcher", 50)),
        baserunning=U(player.get("baserunning", 50)),
        run_aggressiveness=U(player.get("run_aggressiveness", 50)),
    )


# ---------------------------------------------------------------------------
# Roster + engine Team construction
# ---------------------------------------------------------------------------

# Real NCAA D1 baseball active rosters are 35-man (27 travel-eligible).
# Shape mirrors the pro convention's structural conventions enough for
# the engine builder to slot players into starters / backups / jokers /
# pitching staff without surprises.
ROSTER_SIZE = 35


def generate_college_roster(rng: random.Random,
                            program_name: str = "Program",
                            *, country: str = "US",
                            name_picker=None) -> list[dict]:
    """Build a 35-man college roster — 8 canonical-position starters,
    3 jokers (the DH slot), 11 fielder backups for PH/PR/defensive
    depth, 13 pitchers (rotation + bullpen). Each player gets an
    auto-assigned `position`.

    `name_picker` is a `() -> (name, country)` callable from
    `o27v2.league.make_name_picker`. Defaults to a US-only picker so
    the names match NCAA reality. Mixed gender (matches the pro
    pool's default) so college rosters aren't all men.
    """
    if name_picker is None:
        from o27v2.league import make_name_picker
        name_picker = make_name_picker(rng, gender="mixed",
                                       region_weights={"us": 1.0})

    def _draw_name() -> tuple[str, str]:
        nm, ctry = name_picker()
        return nm, (ctry or country)

    roster: list[dict] = []

    # 8 canonical starters (one at each fielding position).
    for pos in _FIELDING_POSITIONS:
        nm, ctry = _draw_name()
        roster.append(generate_college_player(
            rng, is_pitcher=False, name=nm,
            country=ctry, position=pos,
        ))

    # 3 jokers (the DH role — drafted explicitly as bat-only, fixed
    # in the lineup). Stat profile leans hit-skill heavy.
    for _i in range(3):
        nm, ctry = _draw_name()
        jk = generate_college_player(rng, is_pitcher=False, name=nm,
                                     country=ctry, position="")
        jk["is_joker"]    = 1
        jk["roster_slot"] = "joker"
        for attr in ("skill", "contact", "power"):
            jk[f"potential_{attr}"] = min(80, jk[f"potential_{attr}"] + 6)
            jk[attr] = _displayed_grade(jk[f"potential_{attr}"], jk[f"access_{attr}"])
        roster.append(jk)

    # 11 fielder backups for PH / PR / defensive substitution coverage.
    # High-rotation positions get double-deep (CF/SS/2B/C), the rest
    # get one body each — mirrors the pro convention so engine subs
    # land naturally.
    backup_positions = (
        # High-rotation depth (4)
        "CF", "SS", "2B", "C",
        # Corner backups (3)
        "3B", "1B", "LF",
        # Extra-depth (4)
        "RF", "CF", "SS", "2B",
    )
    for pos in backup_positions:
        nm, ctry = _draw_name()
        roster.append(generate_college_player(
            rng, is_pitcher=False, name=nm,
            country=ctry, position=pos,
        ))

    # 13 pitchers — rotation (4) + bullpen (9). Engine picks the SP
    # per game and the rest are available out of the pen.
    for _i in range(13):
        nm, ctry = _draw_name()
        roster.append(generate_college_player(
            rng, is_pitcher=True, name=nm,
            country=ctry, position="P",
        ))

    # Stamp synthetic ids so the engine has player_id keys.
    for idx, pl in enumerate(roster):
        pl["id"] = pl.get("id") or idx + 1
    assert len(roster) == ROSTER_SIZE, (len(roster), ROSTER_SIZE)
    return roster


def build_engine_team(program_name: str, roster: list[dict], *,
                     team_role: str, rng: random.Random):
    """Build an engine `Team` from a college roster — mirrors
    `pro_worldcup._build_wc_engine_team` so college games run through
    the same engine path."""
    from o27.engine.state import Team

    HOME_BONUS = 0.005 if team_role == "home" else 0.0

    engine_players: list = []
    starters_by_pos: dict = {}
    backup_hitters: list = []
    pitchers: list = []
    jokers: list = []
    starter_engine = None

    pitcher_pool = [p for p in roster if p.get("is_pitcher")]
    if not pitcher_pool:
        raise ValueError(f"{program_name} has no pitchers")
    # Pick today's SP by highest stamina (mirrors WC fallback logic).
    starter_pick = max(pitcher_pool, key=lambda p: p.get("stamina", 50))

    for p in roster:
        is_joker = (p.get("roster_slot") == "joker" or bool(p.get("is_joker")))
        is_pitcher = bool(p.get("is_pitcher"))
        ep = make_engine_player(p, home_bonus=HOME_BONUS)
        engine_players.append(ep)
        if is_joker:
            jokers.append(ep); continue
        if is_pitcher:
            pitchers.append(ep)
            if p is starter_pick:
                starter_engine = ep
            continue
        pos = str(p.get("position") or "")
        if pos in _FIELDING_POSITIONS and pos not in starters_by_pos:
            starters_by_pos[pos] = ep
        else:
            backup_hitters.append(ep)

    if starter_engine is None and pitchers:
        starter_engine = pitchers[0]

    starting_fielders = [starters_by_pos[pos] for pos in _FIELDING_POSITIONS
                         if pos in starters_by_pos]
    while len(starting_fielders) < 8 and backup_hitters:
        starting_fielders.append(backup_hitters.pop(0))

    from o27v2.sim import _ordered_lineup, _assign_game_positions
    _assign_game_positions(starting_fielders, [starter_engine], jokers)
    lineup = _ordered_lineup(starting_fielders, [starter_engine])

    if starter_engine in engine_players:
        engine_players = [starter_engine] + [p for p in engine_players
                                              if p is not starter_engine]

    team = Team(
        team_id=team_role,
        name=program_name,
        roster=engine_players,
        lineup=lineup,
        # Smaller fields per the design — modest HR bump, neutral hits.
        park_hr=1.08,
        park_hits=1.00,
        defense_rating=0.5,
        catcher_arm=0.5,
        manager_archetype="",
        mgr_quick_hook=0.5,
        mgr_bullpen_aggression=0.5,
        mgr_leverage_aware=0.5,
        mgr_joker_aggression=0.5,
        mgr_pinch_hit_aggression=0.5,
        mgr_platoon_aggression=0.5,
        mgr_run_game=0.5,
        mgr_bench_usage=0.5,
        jokers_available=jokers,
    )
    team.bench = list(backup_hitters)
    return team


def sim_college_game(home_program: str, home_roster: list[dict],
                     away_program: str, away_roster: list[dict],
                     *, rng: random.Random,
                     return_renderer: bool = False):
    """Run one college game through the pro engine.

    Default: returns the final GameState (scores on `final.score['home']` /
    `final.score['visitors']`).

    With `return_renderer=True`: returns (final_state, renderer) so callers
    can extract per-player box-score rows via the renderer's
    `batter_stats_for_phase()` / `_batter_stats` and the state's
    `spell_log` (pitcher records).
    """
    from o27.engine.state import GameState
    from o27.engine.game  import run_game
    from o27.engine.prob  import ProbabilisticProvider
    from o27.render.render import Renderer

    home_team = build_engine_team(home_program, home_roster,
                                  team_role="home", rng=rng)
    away_team = build_engine_team(away_program, away_roster,
                                  team_role="visitors", rng=rng)
    state = GameState(visitors=away_team, home=home_team)
    renderer = Renderer() if return_renderer else None
    final, _lines = run_game(state, ProbabilisticProvider(rng), renderer)
    if return_renderer:
        return final, renderer
    return final
