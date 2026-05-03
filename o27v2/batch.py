"""
O27v2 Batch Runner — Phase 8 tuning tool.

Usage:
    python o27v2/batch.py --games 500
    python -m o27v2.batch --games 500

Runs N games with deterministic seeds 0..N-1, extracts per-game metrics,
and prints a formatted table comparing actuals to PRD v2 targets.

PRD v2 targets:
  Avg runs/game        22–26
  Avg stays/game        1.0–2.5
  Pitching changes/game 2–4 workhorse | 6–10 committee
  Joker insertions/game 5–9
  Super-inning rate     <8%

All tunable parameters live in o27/config.py and o27v2/config.py.
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys

from o27v2 import scout as _scout
import os

_workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)

from o27.engine.state import GameState, Team, Player
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer, _NON_PA_EVENTS
from o27v2.league import generate_players, _load_teams_db
from o27v2 import config as v2cfg


# ---------------------------------------------------------------------------
# FastRenderer (suppresses text output, preserves stat tracking)
# ---------------------------------------------------------------------------

class FastRenderer(Renderer):
    """Stat-tracking renderer that suppresses all text output."""

    def render_event(self, event: dict, ctx: dict, state_after) -> list[str]:
        batter = ctx["batter"]
        etype  = event["type"]
        if etype not in _NON_PA_EVENTS and batter.player_id != self._current_pa_batter_id:
            self._on_new_pa(batter)
            self._current_pa_batter_id = batter.player_id
        disp = self._build_disp(event, ctx, state_after)
        self._update_stats(event, ctx, state_after, disp)
        return []

    def render_half_header(self, state) -> str:                    return ""
    def render_halftime(self, state) -> list[str]:                 return []
    def render_half_summary(self, state, which) -> list[str]:      return []
    def render_box_score(self, state) -> list[str]:                return []
    def render_partnership_log(self, state) -> list[str]:          return []
    def render_spell_log(self, state) -> list[str]:                return []
    def render_super_inning_log(self, state) -> list[str]:         return []
    def render_super_inning_tie(self) -> list[str]:                return []
    def render_super_inning_round_header(self, state, rn, v5, h5) -> list[str]: return []
    def render_super_inning_round_summary(self, state, rn, vr, hr) -> list[str]: return []
    def render_game_over(self, state) -> list[str]:                return []

    def total_stays(self) -> int:
        return sum(s.sty for s in self._batter_stats.values())


# ---------------------------------------------------------------------------
# Team builder (no DB required)
# ---------------------------------------------------------------------------

_TEAM_DEFS: list[dict] | None = None


def _get_team_defs() -> list[dict]:
    global _TEAM_DEFS
    if _TEAM_DEFS is None:
        all_teams = _load_teams_db()
        mlb = [t for t in all_teams if t["level"] == "MLB"]
        _TEAM_DEFS = mlb if len(mlb) >= 20 else all_teams
    return _TEAM_DEFS


def _make_team(team_idx: int, role: str) -> Team:
    defs     = _get_team_defs()
    team_def = defs[team_idx % len(defs)]
    bonus    = v2cfg.HOME_ADVANTAGE_SKILL if role == "home" else 0.0
    players  = generate_players(team_idx, random.Random(team_idx * 31 + 7), home_bonus=bonus)
    roster: list[Player] = []
    for p in players:
        pl = Player(
            player_id=f"{role}_{team_idx}_{p['name']}",
            name=p["name"],
            is_pitcher=bool(p["is_pitcher"]),
            skill=_scout.to_unit(p["skill"]),
            speed=_scout.to_unit(p["speed"]),
            pitcher_skill=_scout.to_unit(p["pitcher_skill"]),
            stay_aggressiveness=float(p["stay_aggressiveness"]),
            contact_quality_threshold=float(p["contact_quality_threshold"]),
            archetype=str(p.get("archetype") or ""),
            pitcher_role=str(p.get("pitcher_role") or ""),
            hard_contact_delta=float(p.get("hard_contact_delta") or 0.0),
            hr_weight_bonus=float(p.get("hr_weight_bonus") or 0.0),
        )
        roster.append(pl)
    return Team(
        team_id=role,
        name=team_def["name"],
        roster=roster,
        lineup=list(roster),
    )


def _find_pitcher(team: Team) -> str | None:
    for p in team.roster:
        if p.is_pitcher:
            return p.player_id
    return team.roster[0].player_id if team.roster else None


# ---------------------------------------------------------------------------
# Per-game metrics extraction
# ---------------------------------------------------------------------------

def _collect_metrics(state: GameState, renderer: FastRenderer) -> dict:
    v_runs   = state.score.get("visitors", 0)
    h_runs   = state.score.get("home", 0)
    total    = v_runs + h_runs
    run_rate = total / 54.0

    top_pa = sum(s.batters_faced for s in state.spell_log if s.half == "top")
    bot_pa = sum(s.batters_faced for s in state.spell_log if s.half == "bottom")
    total_pa = top_pa + bot_pa

    spell_lengths = [
        s.batters_faced for s in state.spell_log if s.half in ("top", "bottom")
    ]

    # Count only the manager's normalized "joker_inserted" events (past-tense).
    # The provider emits "joker_insertion" (intent); apply_event appends that to
    # state.events too, so counting "joker_insertion" would double the total.
    joker_insertions = sum(
        1 for e in state.events if e.get("type") == "joker_inserted"
    )

    joker_by_arch: dict[str, int] = {}
    for e in state.events:
        if e.get("type") == "joker_inserted":
            jid  = e.get("joker_id", "")
            arch = ""
            for team in (state.visitors, state.home):
                p = team.get_player(jid)
                if p:
                    arch = getattr(p, "archetype", "")
                    break
            joker_by_arch[arch] = joker_by_arch.get(arch, 0) + 1

    v_ids = {p.player_id for p in state.visitors.roster}
    h_ids = {p.player_id for p in state.home.roster}
    v_stays = sum(s.sty for pid, s in renderer._batter_stats.items() if pid in v_ids)
    h_stays = sum(s.sty for pid, s in renderer._batter_stats.items() if pid in h_ids)

    wh_changes   = 0
    comm_changes = 0
    for e in state.events:
        if e.get("type") != "pitching_change":
            continue
        old_id = e.get("old_pitcher_id", "")
        role   = ""
        for team in (state.visitors, state.home):
            p = team.get_player(old_id)
            if p:
                role = getattr(p, "pitcher_role", "")
                break
        if role == "workhorse":
            wh_changes += 1
        elif role == "committee":
            comm_changes += 1

    return {
        "total_runs":      total,
        "v_runs":          v_runs,
        "h_runs":          h_runs,
        "run_rate":        run_rate,
        "total_pa":        total_pa,
        "top_pa":          top_pa,
        "bot_pa":          bot_pa,
        "stays":           v_stays + h_stays,
        "v_stays":         v_stays,
        "h_stays":         h_stays,
        "joker_insertions": joker_insertions,
        "joker_power":     joker_by_arch.get("power",   0),
        "joker_speed":     joker_by_arch.get("speed",   0),
        "joker_contact":   joker_by_arch.get("contact", 0),
        "wh_changes":      wh_changes,
        "comm_changes":    comm_changes,
        "had_super":       state.super_inning_number > 0,
        "spell_lengths":   spell_lengths,
    }


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _agg(values: list[float]) -> dict:
    if not values:
        return {"mean": 0.0, "median": 0.0, "std": 0.0, "min": 0, "max": 0}
    return {
        "mean":   statistics.mean(values),
        "median": statistics.median(values),
        "std":    statistics.pstdev(values),
        "min":    min(values),
        "max":    max(values),
    }


def aggregate(game_metrics: list[dict]) -> dict:
    n = len(game_metrics)
    if n == 0:
        return {}

    super_count = sum(1 for m in game_metrics if m["had_super"])
    all_spells  = [sl for m in game_metrics for sl in m["spell_lengths"]]

    return {
        "n_games":         n,
        "runs":            _agg([m["total_runs"]       for m in game_metrics]),
        "v_runs":          _agg([m["v_runs"]           for m in game_metrics]),
        "h_runs":          _agg([m["h_runs"]           for m in game_metrics]),
        "run_rate":        _agg([m["run_rate"]         for m in game_metrics]),
        "pas":             _agg([m["total_pa"]         for m in game_metrics]),
        "top_pas":         _agg([m["top_pa"]           for m in game_metrics]),
        "bot_pas":         _agg([m["bot_pa"]           for m in game_metrics]),
        "stays":           _agg([m["stays"]            for m in game_metrics]),
        "v_stays":         _agg([m["v_stays"]          for m in game_metrics]),
        "h_stays":         _agg([m["h_stays"]          for m in game_metrics]),
        "joker_insertions":_agg([m["joker_insertions"] for m in game_metrics]),
        "joker_power":     _agg([m["joker_power"]      for m in game_metrics]),
        "joker_speed":     _agg([m["joker_speed"]      for m in game_metrics]),
        "joker_contact":   _agg([m["joker_contact"]    for m in game_metrics]),
        "wh_changes":      _agg([m["wh_changes"]       for m in game_metrics]),
        "comm_changes":    _agg([m["comm_changes"]     for m in game_metrics]),
        "spell_lengths":   _agg(all_spells),
        "super_inning_pct": super_count / n * 100.0,
        "super_count":      super_count,
    }


# ---------------------------------------------------------------------------
# Metrics printer
# ---------------------------------------------------------------------------

_W = 60


def _hr(c: str = "─") -> str:
    return c * _W


def _row(label: str, value: str, target: str = "", flag: str = "") -> str:
    flag_str = f" {flag}" if flag else ""
    if target:
        return f"  {label:<34} {value:<14} {target}{flag_str}"
    return f"  {label:<34} {value}"


def _flag(actual: float, lo: float, hi: float, tol: float = 0.005) -> str:
    """Return ✓ if actual is within [lo, hi], with a display-unit tolerance.

    tol=0.005 is half of one 2-decimal-place display unit, so a mean that
    prints as "6.00" is never spuriously flagged even if the float is 5.9996.
    """
    return "✓" if lo - tol <= actual <= hi + tol else "!"


def print_metrics(agg: dict) -> None:
    n = agg["n_games"]
    print()
    print(_hr("═"))
    print(f"  O27v2 BATCH METRICS  —  {n} games  (seeds 0–{n - 1})")
    print(_hr("═"))

    print(f"\n  SCORING")
    print(_hr())
    r  = agg["runs"]
    fl = _flag(r["mean"], v2cfg.TARGET_RUNS_LO, v2cfg.TARGET_RUNS_HI)
    print(_row("Avg total runs/game",   f"{r['mean']:.2f}", "target 22–26", fl))
    print(_row("  Median/Std/Min/Max",
               f"{r['median']:.1f} / {r['std']:.1f} / {int(r['min'])} / {int(r['max'])}"))
    print(_row("  Avg visitors runs/game", f"{agg['v_runs']['mean']:.2f}"))
    print(_row("  Avg home     runs/game", f"{agg['h_runs']['mean']:.2f}"))

    print(f"\n  STAYS")
    print(_hr())
    st = agg["stays"]
    fl = _flag(st["mean"], v2cfg.TARGET_STAYS_LO, v2cfg.TARGET_STAYS_HI)
    print(_row("Avg stays/game",         f"{st['mean']:.3f}", "target 1.0–2.5", fl))
    print(_row("  Median/Std/Min/Max",
               f"{st['median']:.1f} / {st['std']:.2f} / {int(st['min'])} / {int(st['max'])}"))

    print(f"\n  JOKER INSERTIONS")
    print(_hr())
    jk = agg["joker_insertions"]
    fl = _flag(jk["mean"], v2cfg.TARGET_JOKER_LO, v2cfg.TARGET_JOKER_HI)
    print(_row("Avg joker insertions/game", f"{jk['mean']:.2f}", "target 5–9", fl))
    print(_row("  power  / speed / contact",
               f"{agg['joker_power']['mean']:.2f} / "
               f"{agg['joker_speed']['mean']:.2f} / "
               f"{agg['joker_contact']['mean']:.2f}"))

    print(f"\n  PITCHING CHANGES")
    print(_hr())
    wh = agg["wh_changes"]
    cm = agg["comm_changes"]
    fl_wh = _flag(wh["mean"], v2cfg.TARGET_WH_CHANGES_LO,   v2cfg.TARGET_WH_CHANGES_HI)
    fl_cm = _flag(cm["mean"], v2cfg.TARGET_COMM_CHANGES_LO, v2cfg.TARGET_COMM_CHANGES_HI)
    print(_row("Avg workhorse changes/game",  f"{wh['mean']:.2f}", "target 2–4",  fl_wh))
    print(_row("Avg committee changes/game",  f"{cm['mean']:.2f}", "target 6–10", fl_cm))
    sp = agg["spell_lengths"]
    print(_row("Avg pitcher spell BF",        f"{sp['mean']:.2f} (med {sp['median']:.0f})"))

    print(f"\n  SUPER-INNING")
    print(_hr())
    si  = agg["super_inning_pct"]
    fl  = "✓" if si < v2cfg.TARGET_SUPER_PCT_MAX else "!"
    print(_row("Super-inning frequency",
               f"{si:.2f}%  ({agg['super_count']}/{n})",
               f"target <{v2cfg.TARGET_SUPER_PCT_MAX:.0f}%", fl))

    print(f"\n  PLATE APPEARANCES")
    print(_hr())
    pa = agg["pas"]
    print(_row("Avg PAs/game",           f"{pa['mean']:.1f}  (ref ~79)"))
    print(_row("  Avg PAs/half  top",    f"{agg['top_pas']['mean']:.1f}"))
    print(_row("  Avg PAs/half  bottom", f"{agg['bot_pas']['mean']:.1f}"))

    print()
    print(_hr("═"))
    _TOL = 0.005  # half a 2-dp display unit — avoids false failures on 5.9996
    targets_ok = all([
        v2cfg.TARGET_RUNS_LO        - _TOL <= agg["runs"]["mean"]             <= v2cfg.TARGET_RUNS_HI        + _TOL,
        v2cfg.TARGET_STAYS_LO       - _TOL <= agg["stays"]["mean"]            <= v2cfg.TARGET_STAYS_HI       + _TOL,
        v2cfg.TARGET_JOKER_LO       - _TOL <= agg["joker_insertions"]["mean"] <= v2cfg.TARGET_JOKER_HI       + _TOL,
        agg["super_inning_pct"]            <  v2cfg.TARGET_SUPER_PCT_MAX      + _TOL,
        v2cfg.TARGET_WH_CHANGES_LO  - _TOL <= agg["wh_changes"]["mean"]       <= v2cfg.TARGET_WH_CHANGES_HI  + _TOL,
        v2cfg.TARGET_COMM_CHANGES_LO- _TOL <= agg["comm_changes"]["mean"]     <= v2cfg.TARGET_COMM_CHANGES_HI+ _TOL,
    ])
    status = "ALL PRD v2 TARGETS MET ✓" if targets_ok else "SOME TARGETS OUTSIDE RANGE — see ! flags"
    print(f"  {status}")
    print(_hr("═"))
    print()


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_batch(n_games: int) -> list[dict]:
    defs    = _get_team_defs()
    n_defs  = len(defs)
    results = []
    for seed in range(n_games):
        rng      = random.Random(seed)
        v_idx    = seed % n_defs
        h_idx    = (seed + n_defs // 2) % n_defs
        visitors = _make_team(v_idx, "visitors")
        home     = _make_team(h_idx, "home")
        state    = GameState(visitors=visitors, home=home)
        state.current_pitcher_id = _find_pitcher(home)
        renderer = FastRenderer()
        provider = ProbabilisticProvider(rng)
        state, _log = run_game(state, provider, renderer=renderer)
        results.append(_collect_metrics(state, renderer))

        if (seed + 1) % 50 == 0 or (seed + 1) == n_games:
            pct = (seed + 1) / n_games * 100
            print(f"  ... {seed + 1}/{n_games} games ({pct:.0f}%)", flush=True)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="O27v2 batch simulator and PRD v2 metrics checker (Phase 8)"
    )
    parser.add_argument(
        "--games", type=int, default=500,
        help="Number of games to simulate (default: 500)",
    )
    args = parser.parse_args()
    n = args.games
    print(f"\nRunning {n} O27v2 games …")
    metrics = run_batch(n)
    agg     = aggregate(metrics)
    print_metrics(agg)


if __name__ == "__main__":
    main()
