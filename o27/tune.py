"""
O27 Batch Runner and Metrics Aggregator — Phase 5 Tuning Tool.

Usage:
    python o27/tune.py --games 500
    python -m o27.tune --games 500

Runs N games with seeds 0..N-1, suppresses per-game transcript output,
and prints a metrics table comparing actuals to PRD targets.

All tunable parameters live in o27/config.py.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import os

_workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

from o27.engine.state import GameState
from o27.engine.game import run_game
from o27.engine.prob import ProbabilisticProvider
from o27.render.render import Renderer, _NON_PA_EVENTS
from o27.main import make_foxes, make_bears


# ---------------------------------------------------------------------------
# FastRenderer — stat-tracking without Jinja2 template rendering
# ---------------------------------------------------------------------------

class FastRenderer(Renderer):
    """
    Renderer subclass that accumulates per-batter stats but skips all
    Jinja2 template rendering.  Used by tune.py for high-speed batch runs.

    All stats (stays, multi-hit ABs, PAs, etc.) are faithfully tracked
    via the inherited _update_stats() machinery; only the text output is
    suppressed (methods return empty lists / strings).
    """

    def render_event(self, event: dict, ctx: dict, state_after) -> list[str]:
        batter = ctx["batter"]
        etype = event["type"]
        is_pa_event = etype not in _NON_PA_EVENTS
        if is_pa_event and batter.player_id != self._current_pa_batter_id:
            self._on_new_pa(batter)
            self._current_pa_batter_id = batter.player_id
        disp = self._build_disp(event, ctx, state_after)
        self._update_stats(event, ctx, state_after, disp)
        return []

    def render_half_header(self, state) -> str:
        return ""

    def render_halftime(self, state) -> list[str]:
        return []

    def render_half_summary(self, state, which: str) -> list[str]:
        return []

    def render_box_score(self, state) -> list[str]:
        return []

    def render_partnership_log(self, state) -> list[str]:
        return []

    def render_spell_log(self, state) -> list[str]:
        return []

    def render_super_inning_log(self, state) -> list[str]:
        return []

    def render_super_inning_tie(self) -> list[str]:
        return []

    def render_super_inning_round_header(self, state, round_num, v5, h5) -> list[str]:
        return []

    def render_super_inning_round_summary(self, state, round_num, v_runs, h_runs) -> list[str]:
        return []

    def render_game_over(self, state) -> list[str]:
        return []

    # ------------------------------------------------------------------
    # Aggregation helpers (called after each game)
    # ------------------------------------------------------------------

    def total_stays(self) -> int:
        """Sum of all stay at-bats across every tracked batter."""
        return sum(s.sty for s in self._batter_stats.values())

    def total_multi_hit_abs(self) -> int:
        """Sum of all multi-hit at-bats across every tracked batter."""
        return sum(s.multi_hit_abs for s in self._batter_stats.values())


# ---------------------------------------------------------------------------
# Per-game metrics extraction
# ---------------------------------------------------------------------------

def _collect_game_metrics(state: GameState, renderer: FastRenderer) -> dict:
    """Extract one game's worth of metrics from the final state + renderer."""
    v_runs = state.score.get("visitors", 0)
    h_runs = state.score.get("home", 0)
    total_runs = v_runs + h_runs

    # Run rate = total runs / 54 outs (27 per half, two regulation halves).
    run_rate = total_runs / 54.0

    # PAs from spell log: sum of all BF across regulation halves only.
    reg_halves = {"top", "bottom"}
    total_pa = sum(
        spell.batters_faced
        for spell in state.spell_log
        if spell.half in reg_halves
    )

    # Spell lengths from regulation halves (for avg spell length metric).
    spell_lengths = [
        spell.batters_faced
        for spell in state.spell_log
        if spell.half in reg_halves
    ]

    # Joker insertions from state event log.
    joker_insertions = sum(
        1 for e in state.events
        if e.get("type") == "joker_insertion"
    )

    # Stays and multi-hit ABs from FastRenderer.
    stays = renderer.total_stays()
    multi_hit_abs = renderer.total_multi_hit_abs()

    # Super-inning triggered?
    had_super = state.super_inning_number > 0

    return {
        "v_runs": v_runs,
        "h_runs": h_runs,
        "total_runs": total_runs,
        "run_rate": run_rate,
        "total_pa": total_pa,
        "stays": stays,
        "multi_hit_abs": multi_hit_abs,
        "had_super": had_super,
        "joker_insertions": joker_insertions,
        "spell_lengths": spell_lengths,
    }


# ---------------------------------------------------------------------------
# Metrics aggregation
# ---------------------------------------------------------------------------

def _agg(values: list[float]) -> dict:
    """Return mean, median, std dev, min, max for a numeric list."""
    if not values:
        return {"mean": 0.0, "median": 0.0, "std": 0.0, "min": 0, "max": 0}
    return {
        "mean":   statistics.mean(values),
        "median": statistics.median(values),
        "std":    statistics.pstdev(values),
        "min":    min(values),
        "max":    max(values),
    }


def aggregate_metrics(game_metrics: list[dict]) -> dict:
    """Compute aggregate statistics across all games."""
    n = len(game_metrics)
    if n == 0:
        return {}

    total_runs   = [m["total_runs"]       for m in game_metrics]
    v_runs       = [m["v_runs"]           for m in game_metrics]
    h_runs       = [m["h_runs"]           for m in game_metrics]
    run_rates    = [m["run_rate"]          for m in game_metrics]
    pas          = [m["total_pa"]          for m in game_metrics]
    stays        = [m["stays"]             for m in game_metrics]
    multi_hits   = [m["multi_hit_abs"]     for m in game_metrics]
    jokers       = [m["joker_insertions"]  for m in game_metrics]
    super_count  = sum(1 for m in game_metrics if m["had_super"])
    all_spells   = [sl for m in game_metrics for sl in m["spell_lengths"]]

    return {
        "n_games":          n,
        "runs":             _agg(total_runs),
        "v_runs":           _agg(v_runs),
        "h_runs":           _agg(h_runs),
        "run_rate":         _agg(run_rates),
        "pas":              _agg(pas),
        "stays":            _agg(stays),
        "multi_hit_abs":    _agg(multi_hits),
        "joker_insertions": _agg(jokers),
        "spell_lengths":    _agg(all_spells),
        "super_inning_pct": super_count / n * 100.0,
        "super_count":      super_count,
    }


# ---------------------------------------------------------------------------
# Metrics printer
# ---------------------------------------------------------------------------

_COL_W = 56   # total line width

def _hr(char: str = "─") -> str:
    return char * _COL_W


def _row(label: str, value: str, target: str = "", flag: str = "") -> str:
    flag_str = f" {flag}" if flag else ""
    if target:
        return f"  {label:<32} {value:<12} {target}{flag_str}"
    return f"  {label:<32} {value}"


def _flag(actual: float, lo: float, hi: float) -> str:
    """Return '✓' when in range, '!' when outside."""
    return "✓" if lo <= actual <= hi else "!"


def print_metrics(agg: dict) -> None:
    """Print a formatted metrics table with target comparisons."""
    n = agg["n_games"]
    print()
    print(_hr("═"))
    print(f"  O27 SIMULATION METRICS  —  {n} games  (seeds 0–{n - 1})")
    print(_hr("═"))

    # --- Scoring ---
    print(f"\n  {'SCORING':}")
    print(_hr())
    r = agg["runs"]
    flag = _flag(r["mean"], 22.0, 24.0)
    print(_row("Avg total runs/game",
               f"{r['mean']:.2f}",
               "target 22–24",
               flag))
    print(_row("  Median / Std / Min / Max",
               f"{r['median']:.1f} / {r['std']:.1f} / {int(r['min'])} / {int(r['max'])}"))
    vr = agg["v_runs"]
    hr_ = agg["h_runs"]
    print(_row("Avg visitors runs/game",  f"{vr['mean']:.2f}"))
    print(_row("Avg home runs/game",      f"{hr_['mean']:.2f}"))

    # --- Run rate ---
    print(f"\n  {'RUN RATE':}")
    print(_hr())
    rr = agg["run_rate"]
    flag = _flag(rr["mean"], 0.38, 0.48)
    print(_row("Avg run rate (R/out)",
               f"{rr['mean']:.4f}",
               "target ~0.43",
               flag))
    print(_row("  Median / Std",
               f"{rr['median']:.4f} / {rr['std']:.4f}"))

    # --- Plate appearances ---
    print(f"\n  {'PLATE APPEARANCES':}")
    print(_hr())
    pa = agg["pas"]
    flag = _flag(pa["mean"], 72.0, 86.0)
    print(_row("Avg PAs/game (reg halves)",
               f"{pa['mean']:.1f}",
               "ref ~79",
               flag))
    print(_row("  Median / Std / Min / Max",
               f"{pa['median']:.0f} / {pa['std']:.1f} / {int(pa['min'])} / {int(pa['max'])}"))

    # --- Stays ---
    print(f"\n  {'STAY MECHANIC':}")
    print(_hr())
    st = agg["stays"]
    flag = _flag(st["mean"], 0.3, 1.0)
    print(_row("Avg stays/game",
               f"{st['mean']:.3f}",
               "target 0.3–1.0",
               flag))
    print(_row("  Median / Std / Min / Max",
               f"{st['median']:.1f} / {st['std']:.2f} / {int(st['min'])} / {int(st['max'])}"))
    mh = agg["multi_hit_abs"]
    print(_row("Avg multi-hit ABs/game",  f"{mh['mean']:.3f}"))

    # --- Super-inning ---
    print(f"\n  {'SUPER-INNING':}")
    print(_hr())
    si_pct = agg["super_inning_pct"]
    flag = _flag(si_pct, 0.0, 5.0)
    print(_row("Super-inning frequency",
               f"{si_pct:.2f}%  ({agg['super_count']}/{n})",
               "target <5%",
               flag))

    # --- Manager activity ---
    print(f"\n  {'MANAGER ACTIVITY':}")
    print(_hr())
    jk = agg["joker_insertions"]
    print(_row("Avg joker insertions/game", f"{jk['mean']:.2f}"))
    sp = agg["spell_lengths"]
    print(_row("Avg pitcher spell length (BF)",
               f"{sp['mean']:.2f}  (med {sp['median']:.0f})"))

    # --- Summary ---
    print()
    print(_hr("═"))
    targets_met = all([
        22.0 <= agg["runs"]["mean"] <= 24.0,
        0.3  <= agg["stays"]["mean"] <= 1.0,
        agg["super_inning_pct"] < 5.0,
        0.38 <= agg["run_rate"]["mean"] <= 0.48,
        72.0 <= agg["pas"]["mean"] <= 86.0,
    ])
    status = "ALL PRD TARGETS MET ✓" if targets_met else "SOME TARGETS OUTSIDE RANGE — see ! flags"
    print(f"  {status}")
    print(_hr("═"))
    print()


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_batch(n_games: int) -> list[dict]:
    """
    Run n_games games with seeds 0..n_games-1.

    Returns a list of per-game metric dicts.
    """
    results: list[dict] = []
    for seed in range(n_games):
        rng = random.Random(seed)
        renderer = FastRenderer()
        foxes = make_foxes()
        bears = make_bears()
        state = GameState(visitors=foxes, home=bears)
        provider = ProbabilisticProvider(rng)
        state, _ = run_game(state, provider, renderer=renderer)
        metrics = _collect_game_metrics(state, renderer)
        results.append(metrics)

        # Progress indicator every 50 games.
        if (seed + 1) % 50 == 0 or (seed + 1) == n_games:
            pct = (seed + 1) / n_games * 100
            print(f"  ... {seed + 1}/{n_games} games ({pct:.0f}%)", flush=True)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="O27 batch simulator and metrics aggregator (Phase 5 tuning)"
    )
    parser.add_argument(
        "--games", type=int, default=500,
        help="Number of games to simulate (default: 500)",
    )
    args = parser.parse_args()

    n = args.games
    print(f"\nRunning {n} games …")
    game_metrics = run_batch(n)
    agg = aggregate_metrics(game_metrics)
    print_metrics(agg)


if __name__ == "__main__":
    main()
