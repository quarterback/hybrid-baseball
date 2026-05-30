"""
Jinja2 play-by-play and box score renderer for O27.

The Renderer class:
  - Loads Jinja2 templates from render/templates/
  - Captures pre-event game state snapshots
  - Renders each event via play_by_play.j2
  - Tracks per-batter stats as a side effect of rendering (for box score)
  - Provides render_halftime(), render_half_summary(), render_box_score(),
    render_game_over() for structural output sections

Usage (see engine/game.py for integration):
    renderer = Renderer()
    ctx = renderer.capture_context(state)          # BEFORE apply_event
    apply_event(state, event)
    lines = renderer.render_event(event, ctx, state)  # AFTER apply_event
"""

from __future__ import annotations

import os
from typing import Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from o27.stats.batter import BatterStats
from o27.stats.pitcher import PitcherStats
from o27.stats.team import TeamStats
from o27.engine.pa import _pick_walk_back_sponsor
from o27.engine import power_play as _power_play


def _power_play_line(state) -> Optional[str]:
    """Box-score `Powerplays:` line (None when the optional rule is off)."""
    return _power_play.format_powerplays_line(state)

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

# Jinja2 Environments are reusable and their compiled-template cache is the
# expensive part to build. A fresh Renderer is created per game (see
# o27v2/sim.py), so without sharing, every game re-parses and re-compiles
# every template — the dominant cost when simulating a whole month/season.
# Cache one Environment per template dir; Renderer instances keep their own
# per-game state but share the (immutable, thread-safe) template cache.
_ENV_CACHE: dict[str, "Environment"] = {}


def _get_environment(tdir: str) -> "Environment":
    env = _ENV_CACHE.get(tdir)
    if env is None:
        env = Environment(
            loader=FileSystemLoader(tdir),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
            # Templates are static at runtime. auto_reload (default True)
            # re-stats the template file on every get_template() call, which
            # is wasteful given render_event() fetches a template per pitch.
            auto_reload=False,
            cache_size=-1,
        )
        _ENV_CACHE[tdir] = env
    return env

# Manager / between-pitch event types that do NOT start a new plate appearance.
_NON_PA_EVENTS = frozenset(
    {"joker_insertion", "pitching_change", "pinch_hit",
     "stolen_base_attempt", "pickoff_attempt", "balk",
     "wild_pitch", "passed_ball",
     "defensive_sub", "tactical_def_swap", "pinch_runner",
     "joker_to_field", "phase_transition_swap"}
)

# Maps internal hit_type strings → human-readable prose for the transcript.
_HIT_TYPE_DISPLAY: dict[str, str] = {
    "single":          "single",
    "infield_single":  "infield single",
    "double":          "double",
    "triple":          "triple",
    "hr":              "HOME RUN",
    "home_run":        "HOME RUN",
    "ground_out":      "ground out",
    "fly_out":         "fly out",
    "line_out":        "line out",
    "fielders_choice": "fielder's choice",
    "double_play":     "double play",
    "triple_play":     "triple play",
    "stay_ground":     "ground ball (stay)",
    "stay_fly_no_catch": "fly ball (stay)",
    "error":           "error",
    "itp_out":         "deep drive — thrown out at home",
}

# Sentinel `runner_from_base` values, distinct from the real base indices
# 0/1/2 (1B/2B/3B). BATTER_HR_FROM_BASE = batter's own home-run run ("HR");
# OTHER_FROM_BASE = a run with no starting base (Walk-Back bonus / phantom),
# rendered as "—".
BATTER_HR_FROM_BASE = 3
OTHER_FROM_BASE = 4


class Renderer:
    """Jinja2 renderer for O27 play-by-play and structured output."""

    def __init__(self, template_dir: Optional[str] = None) -> None:
        tdir = template_dir or _TEMPLATE_DIR
        self._env = _get_environment(tdir)
        self._batter_stats: dict[str, BatterStats] = {}
        self._current_pa_batter_id: Optional[str] = None
        # Task #58: end-of-phase cumulative snapshots used to derive
        # per-phase batter rows after the game finishes. game.py calls
        # end_phase(N) after each phase (regulation = 0, SI round N >= 1).
        self._phase_end_snapshots: dict[int, dict[str, BatterStats]] = {}
        # Phase 11D — per-PA event log (ball_in_play only). Each entry is
        # a dict ready to insert into game_pa_log. Per-batter tracking of
        # which AB they're currently in + which swing within that AB. AB
        # boundary is detected by comparing s.ab+1 to the batter's last
        # observed in-progress AB number (changes when prior AB completed).
        self._pa_log: list[dict] = []
        self._batter_current_ab: dict = {}      # batter_id -> in-progress ab number
        # Power Play pitcher accumulator (keyed by pitcher player_id). Window
        # counters (pp_*) accrue only while the pitcher's defense had its nickel
        # deployed behind him; total BIP counters accrue
        # always, so sim.py can build the BABIP split (with-nickel vs without).
        self._pp_pitcher: dict[str, dict] = {}
        self._batter_swing_idx: dict = {}       # batter_id -> swing_idx within current ab
        # Pesäpallo-style per-PA advancement tracking. At PA start we
        # snapshot which runners were on which bases; during the PA we
        # accumulate which of those runners got out (FC, TOOTBLAN, etc.);
        # at PA end (= next PA start) we diff against current bases to
        # credit each per-base advancement (1B→higher, 2B→higher, 3B→home).
        # Snapshots are tuples so they don't track in-place list mutations.
        self._last_bases: tuple = (None, None, None)   # bases at END of last event
        self._last_score_v: int = 0   # visitors' score at END of last event
        self._last_score_h: int = 0   # home's score at END of last event
        self._last_half: str = "top"  # half at END of last event
        self._last_outs: int = 0      # outs at END of last event
        self._pa_start_bases: tuple = (None, None, None)  # bases at START of current PA
        self._pa_runners_out: set = set()       # runner_ids retired during current PA
        # Pesäpallo-style scoring log — one entry per run that crosses the
        # plate. Captures batter, runner, runner's starting base in the
        # PA, the score-after-this-run, outs, and half. Populated at PA
        # boundary in _credit_pa_advancement; persisted by sim.py at game
        # end via the game_scoring_events table.
        self._scoring_log: list[dict] = []
        # Per-game running count of joker insertions per batting team, keyed
        # by team_id. Surfaced in the joker play-by-play line as a usage tally.
        self._joker_insertions: dict[str, int] = {}
        # Structured per-PA records for downstream consumers (the SVG
        # scorecard, future analytics). One entry per completed plate
        # appearance. Derived from BatterStats diffs at PA boundary so the
        # outcome is whatever the engine actually credited, not parsed from
        # text. Populated by _finalize_pa_record / _start_pa_record.
        self.pa_records: list[dict] = []
        self._pa_record_in_progress: dict | None = None
        # Pitcher arc records: one entry per pitcher's appearance window
        # (start_out, end_out, pitcher name, half). Closed when the next
        # pitcher comes in or at game end.
        self.pitcher_arc: dict[str, list[dict]] = {"top": [], "bot": []}
        self._current_arc_segment: dict | None = None

    # -----------------------------------------------------------------------
    # Public API — called by the game loop
    # -----------------------------------------------------------------------

    def capture_context(self, state) -> dict:
        """
        Snapshot the game state BEFORE an event is applied.
        Returns a plain dict (no live state references) so it stays valid
        after state mutation.
        """
        batter = state.current_batter
        pitcher = state.get_current_pitcher()
        batting_tid = state.batting_team.team_id
        return {
            "batter": batter,
            "pitcher": pitcher,
            "outs": state.outs,
            "count_balls": state.count.balls,
            "count_strikes": state.count.strikes,
            "count_fouls": state.count.fouls,
            "count": str(state.count),
            "bases": state.bases_summary(),
            "bases_list": list(state.bases),          # copy — safe after mutation
            "score": dict(state.score),               # copy
            "batting_team_id": batting_tid,
            "phase": getattr(state, "phase_number", 0) or getattr(state, "super_inning_number", 0),
            "batting_team_name": state.batting_team.name,
            "fielding_team_name": state.fielding_team.name,
            "visitors_name": state.visitors.name,
            "home_name": state.home.name,
            "half": state.half,
            "spell_count": state.pitcher_spell_count,
            "is_super": state.is_super_inning,
            "at_bat_hits_before": state.current_at_bat_hits,
        }

    def render_event(self, event: dict, ctx: dict, state_after) -> list[str]:
        """
        Render one game event as a list of play-by-play text lines.
        Also updates internal per-batter stats used by render_box_score().
        """
        batter = ctx["batter"]
        etype = event["type"]
        lines: list[str] = []

        # Detect new plate appearance (batter change), ignoring manager /
        # between-pitch events that fire without changing the batter.
        is_pa_event = etype not in _NON_PA_EVENTS
        if is_pa_event and batter.player_id != self._current_pa_batter_id:
            # PA boundary — credit the previous batter for per-base
            # advancement that occurred during their PA. self._last_bases
            # holds bases at end of the previous PA's terminal event;
            # diff vs self._pa_start_bases plus self._pa_runners_out tells
            # us which starting-base runners advanced vs were retired.
            if self._current_pa_batter_id is not None:
                self._credit_pa_advancement(self._current_pa_batter_id)
                self._finalize_pa_record(ctx)
            # Reset PA-scoped state for the incoming batter. The new PA's
            # starting bases = the bases standing right now (this event's
            # pre-event snapshot). Within a half this equals the previous
            # PA's end bases; across a half/super-inning boundary the engine
            # has cleared the bases, so this correctly resets to empty rather
            # than leaking the prior half's stranded runners into this PA.
            self._pa_start_bases = tuple(ctx.get("bases_list") or (None, None, None))
            self._pa_runners_out = set()
            self._on_new_pa(batter)
            self._start_pa_record(batter, ctx)
            lines.append(self._batter_intro(batter))
            self._current_pa_batter_id = batter.player_id

        # Build the template context dict (all display values pre-computed).
        disp = self._build_disp(event, ctx, state_after)

        # Update batter stats. When the batting team is facing an active nickel
        # window (short-handed), mirror whatever pa/ab/hits this event credits
        # into the batter's short-handed counters via a before/after delta —
        # avoids instrumenting every outcome branch in _update_stats.
        _sh = bool(getattr(state_after, "power_play_sh_active", False))
        _sh_s = self._get_stats(batter) if _sh else None
        _sh_before = ((_sh_s.pa, _sh_s.ab, _sh_s.hits, _sh_s.k, _sh_s.bb,
                       _sh_s.outs_recorded) if _sh else None)
        self._update_stats(event, ctx, state_after, disp)
        if _sh:
            _sh_s.sh_pa   += _sh_s.pa   - _sh_before[0]
            _sh_s.sh_ab   += _sh_s.ab   - _sh_before[1]
            _sh_s.sh_hits += _sh_s.hits - _sh_before[2]
        # Power Play pitcher: when short-handed is active, ctx["pitcher"] IS the
        # fielding pitcher with the nickel deployed behind him.
        # Window counters come from the same batter deltas (BF = pa delta, etc.);
        # total BIP counters accrue on every ball-in-play regardless of window so
        # the BABIP split can be computed downstream.
        self._credit_pp_pitcher(event, ctx, disp, _sh,
                                _sh_before, _sh_s)

        # Render via Jinja2 template.
        tmpl = self._env.get_template("play_by_play.j2")
        rendered = tmpl.render(**disp).rstrip("\n")
        if rendered:
            lines.append(rendered)

        # Append runner advancement narrative computed from state delta.
        runner_lines = self._compute_runner_lines(ctx, state_after, etype, disp, event)
        lines.extend(runner_lines)

        # Per-PA advancement bookkeeping — accumulate runners retired during
        # this PA (any outcome with runner_out_idx / extra_runner_outs), and
        # snapshot bases / score / half / outs at end of this event so the
        # next PA boundary can diff against them.
        if is_pa_event:
            self._accumulate_pa_runners_out(event, ctx)
        self._last_bases    = tuple(state_after.bases)
        self._last_score_v  = int(state_after.score.get("visitors", 0) or 0)
        self._last_score_h  = int(state_after.score.get("home", 0) or 0)
        self._last_half     = str(getattr(state_after, "half", "top") or "top")
        self._last_outs     = int(getattr(state_after, "outs", 0) or 0)

        return lines

    def render_half_header(self, state) -> str:
        """Return the half-inning header line."""
        half_labels = {
            "top": "TOP HALF",
            "bottom": "BOTTOM HALF",
            "super_top": f"SUPER-INNING R{state.super_inning_number} — VISITORS",
            "super_bottom": f"SUPER-INNING R{state.super_inning_number} — HOME",
        }
        label = half_labels.get(state.half, state.half.upper())
        batting = state.batting_team.name
        return f"\n{'─' * 60}\n{label} | {batting} batting\n{'─' * 60}"

    def render_halftime(self, state) -> list[str]:
        """Render the halftime break announcement."""
        # Close the last PA of the top half (no next-batter event will fire
        # to trigger _finalize_pa_record).
        if self._pa_record_in_progress is not None:
            self._finalize_pa_record({"outs": state.outs})
            self._current_pa_batter_id = None
        v_score = state.score["visitors"]
        target_runs = v_score + 1
        required_rr = target_runs / 27
        tmpl = self._env.get_template("halftime.j2")
        rendered = tmpl.render(
            visitors_name=state.visitors.name,
            home_name=state.home.name,
            visitors_score=v_score,
            target_runs=target_runs,
            required_rr=required_rr,
        ).rstrip("\n")
        return rendered.split("\n") if rendered else []

    def render_half_summary(self, state, which: str) -> list[str]:
        """Render the end-of-half summary including runs, hits, outs, and run rate."""
        team = state.visitors if which == "top" else state.home
        runs = state.score[team.team_id]
        outs = state.outs
        rr = runs / max(outs, 1)
        hits = sum(
            s.hits
            for pid, s in self._batter_stats.items()
            if any(p.player_id == pid for p in team.roster)
        )
        stays = sum(
            s.sty
            for pid, s in self._batter_stats.items()
            if any(p.player_id == pid for p in team.roster)
        )
        half_label = "top" if which == "top" else "bottom"
        return [
            "",
            (
                f"End of {half_label} half — {team.name}: "
                f"{runs} run(s), {hits} hit(s), {outs} out(s) | "
                f"Run rate: {rr:.3f}"
            ),
        ]

    def _line_score_phases(self, state) -> list[dict]:
        """Collapsed per-team line-score columns: 1 (regulation), 2 (a
        team's seconds round if any), S (a team's super-inning round if
        any). Each team has at most one cell in 2 and at most one in S.

        Returns a list of column rows in display order. Each row:
          {label, visitors_runs, visitors_outs, visitors_played,
                  home_runs, home_outs, home_played}
        """
        seconds_count = (1 if state.visitors.seconds_used else 0) \
                      + (1 if state.home.seconds_used else 0)
        v_ids = {p.player_id for p in state.visitors.roster}
        h_ids = {p.player_id for p in state.home.roster}

        def _per_phase_runs_outs(ph: int):
            # batter_stats_for_phase returns the per-phase delta — for
            # phase 0 that's the regulation totals (no prev snapshot),
            # for phase>0 it's cumulative[phase] - cumulative[phase-1].
            stats = self.batter_stats_for_phase(ph)
            v_r = v_o = h_r = h_o = 0
            for pid, s in stats.items():
                if pid in v_ids:
                    v_r += int(getattr(s, "runs", 0) or 0)
                    v_o += int(getattr(s, "outs_recorded", 0) or 0)
                elif pid in h_ids:
                    h_r += int(getattr(s, "runs", 0) or 0)
                    h_o += int(getattr(s, "outs_recorded", 0) or 0)
            return v_r, v_o, h_r, h_o

        all_phases = sorted(set(self.phases_seen()) | {0})

        # Bucket phases: 0 = regulation, 1..seconds_count = seconds rounds,
        # rest = SI rounds. Collapse each bucket into ONE column.
        reg_v_r, reg_v_o, reg_h_r, reg_h_o = _per_phase_runs_outs(0)
        rows: list[dict] = [{
            "label": "1",
            "visitors_runs": reg_v_r, "visitors_outs": reg_v_o,
            "visitors_played": (reg_v_o > 0 or reg_v_r > 0),
            "home_runs": reg_h_r, "home_outs": reg_h_o,
            "home_played": (reg_h_o > 0 or reg_h_r > 0),
        }]

        # Seconds column: sum across the seconds-bucket phases. Each team
        # batted in at most one seconds round, so summing is a safe collapse.
        seconds_phases = [p for p in all_phases if 0 < p <= seconds_count]
        if seconds_phases:
            v_r = v_o = h_r = h_o = 0
            for p in seconds_phases:
                a, b, c, d = _per_phase_runs_outs(p)
                v_r += a; v_o += b; h_r += c; h_o += d
            rows.append({
                "label": "2",
                "visitors_runs": v_r, "visitors_outs": v_o,
                "visitors_played": (v_o > 0 or v_r > 0),
                "home_runs": h_r, "home_outs": h_o,
                "home_played": (h_o > 0 or h_r > 0),
            })

        # SI column: sum across SI-bucket phases (rounds where BOTH teams bat).
        si_phases = [p for p in all_phases if p > seconds_count]
        if si_phases:
            v_r = v_o = h_r = h_o = 0
            for p in si_phases:
                a, b, c, d = _per_phase_runs_outs(p)
                v_r += a; v_o += b; h_r += c; h_o += d
            rows.append({
                "label": "S",
                "visitors_runs": v_r, "visitors_outs": v_o,
                "visitors_played": (v_o > 0 or v_r > 0),
                "home_runs": h_r, "home_outs": h_o,
                "home_played": (h_o > 0 or h_r > 0),
            })
        return rows

    def _flush_final_pa(self) -> None:
        """Credit the game's final plate appearance's advancement metrics.

        `_credit_pa_advancement` normally fires at the *next* batter's PA
        boundary, but the last PA of the game (frequently a walk-off) has no
        successor, so its per-base advancement (adv_op / rad) would otherwise
        be dropped. (Run scoring-log rows are emitted per-event in
        _credit_runs, so they don't depend on this flush.) Flushed once at
        game end.
        """
        if self._current_pa_batter_id is not None:
            self._credit_pa_advancement(self._current_pa_batter_id)
            self._current_pa_batter_id = None

    def render_box_score(self, state) -> list[str]:
        """Render the full dual-team box score, including pitcher lines and required RR."""
        self._flush_final_pa()

        def _rows(team):
            return [
                self._batter_stats.get(
                    p.player_id,
                    BatterStats(player_id=p.player_id, name=p.name),
                )
                for p in team.roster
            ]

        def _totals(rows: list[BatterStats]) -> BatterStats:
            t = BatterStats(player_id="TOTALS", name="TOTALS")
            for r in rows:
                t.pa           += r.pa
                t.ab           += r.ab
                t.runs         += r.runs
                t.hits         += r.hits
                t.doubles      += r.doubles
                t.triples      += r.triples
                t.hr           += r.hr
                t.rbi          += r.rbi
                t.bb           += r.bb
                t.ibb          += r.ibb
                t.k            += r.k
                t.hbp          += r.hbp
                t.sty          += r.sty
                t.stay_rbi     += r.stay_rbi
                t.stay_hits    += r.stay_hits
                t.multi_hit_abs += r.multi_hit_abs
                t.adv_op_1b    += r.adv_op_1b
                t.adv_adv_1b   += r.adv_adv_1b
                t.adv_op_2b    += r.adv_op_2b
                t.adv_adv_2b   += r.adv_adv_2b
                t.adv_op_3b    += r.adv_op_3b
                t.adv_adv_3b   += r.adv_adv_3b
                t.rad_1b       += r.rad_1b
                t.rad_2b       += r.rad_2b
                t.rad_3b       += r.rad_3b
            return t

        # Build per-pitcher aggregates from spell_log (includes H/BB/K/HBP).
        pitcher_map: dict[str, PitcherStats] = {}
        for spell in state.spell_log:
            pid = spell.pitcher_id
            if pid not in pitcher_map:
                pitcher_map[pid] = PitcherStats(
                    player_id=pid, name=spell.pitcher_name
                )
            ps = pitcher_map[pid]
            ps.batters_faced += spell.batters_faced
            ps.outs_recorded += spell.outs_recorded
            ps.runs_allowed  += spell.runs_allowed
            ps.hits_allowed  += spell.hits_allowed
            ps.bb            += spell.bb
            ps.k             += spell.k
            ps.hbp           += spell.hbp
            ps.spell_count   += 1

        # Split pitcher aggregates by team (pitcher pitches for the FIELDING team,
        # so a visitors-roster pitcher pitched against the home side → listed under visitors).
        v_pitcher_ids = {p.player_id for p in state.visitors.roster}
        h_pitcher_ids = {p.player_id for p in state.home.roster}
        v_pitchers = [ps for pid, ps in pitcher_map.items() if pid in v_pitcher_ids]
        h_pitchers = [ps for pid, ps in pitcher_map.items() if pid in h_pitcher_ids]

        v_rows = _rows(state.visitors)
        h_rows = _rows(state.home)
        v_runs = state.score["visitors"]
        h_runs = state.score["home"]

        # Use TeamStats for required run rate footer (full-game projection).
        target_runs: Optional[int] = None
        required_rr: Optional[float] = None
        if state.target_score is not None:
            home_ts = TeamStats(
                team_name=state.home.name,
                runs=h_runs,
                outs=27,
                target_runs=state.target_score + 1,
            )
            target_runs = home_ts.target_runs
            required_rr = home_ts.required_run_rate_full

        # IBB notes (MLB box-score convention: footnote with player names
        # and counts, NOT a column on the batting line). Format per team:
        # "Last, F. (2); Other, P." — same shape as 2B/3B/HR notes lines.
        def _ibb_note(rows):
            parts = []
            for r in rows:
                n = int(getattr(r, "ibb", 0) or 0)
                if n <= 0:
                    continue
                if n == 1:
                    parts.append(r.name)
                else:
                    parts.append(f"{r.name} ({n})")
            return "; ".join(parts)

        tmpl = self._env.get_template("box_score.j2")
        rendered = tmpl.render(
            visitors_name=state.visitors.name,
            home_name=state.home.name,
            visitors_rows=v_rows,
            home_rows=h_rows,
            visitors_totals=_totals(v_rows),
            home_totals=_totals(h_rows),
            visitors_runs=v_runs,
            home_runs=h_runs,
            visitors_rr=v_runs / 27,
            home_rr=h_runs / 27,
            visitors_stays=sum(s.sty for s in v_rows),
            home_stays=sum(s.sty for s in h_rows),
            visitors_ibb_note=_ibb_note(v_rows),
            home_ibb_note=_ibb_note(h_rows),
            visitors_pitchers=v_pitchers,
            home_pitchers=h_pitchers,
            required_rr=required_rr,
            target_runs=target_runs,
            # Declared Seconds — surface each team's declaration under the
            # line score. Score values are the runs FOR/AGAINST that team at
            # the moment of declaration, captured in evaluate_declaration.
            visitors_declared_at=state.visitors.declared_at_out,
            visitors_declare_score_for=int(state.visitors.declare_score_for or 0),
            visitors_declare_score_against=int(state.visitors.declare_score_against or 0),
            home_declared_at=state.home.declared_at_out,
            home_declare_score_for=int(state.home.declare_score_for or 0),
            home_declare_score_against=int(state.home.declare_score_against or 0),
            home_bats_first=bool(getattr(state, "home_bats_first", False)),
            # Per-phase runs/outs for the line score. Each phase>0 row is
            # rendered as `runs(outs)` so the seconds / SI rounds carry their
            # banked-outs context. Phase 0 always renders just runs.
            line_phases=self._line_score_phases(state),
            # Power Play (optional rule) — surface each team's nickel window(s)
            # under the line score, mirroring the Declared Seconds line.
            powerplays_line=_power_play_line(state),
        ).rstrip("\n")
        return rendered.split("\n") if rendered else []

    def render_partnership_log(self, state) -> list[str]:
        """Render the full partnership log after the box score."""
        partnerships = state.partnership_log
        if not partnerships:
            return []
        total_runs = sum(p.runs for p in partnerships)
        count = len(partnerships)
        avg_rpp = f"{total_runs / count:.2f}" if count else "0.00"
        tmpl = self._env.get_template("partnership_log.j2")
        rendered = tmpl.render(
            partnerships=partnerships,
            avg_rpp=avg_rpp,
        ).rstrip("\n")
        return rendered.split("\n") if rendered else []

    def render_spell_log(self, state) -> list[str]:
        """Render the pitcher spell log after the partnership log."""
        spells = state.spell_log
        if not spells:
            return []
        tmpl = self._env.get_template("spell_log.j2")
        rendered = tmpl.render(spells=spells).rstrip("\n")
        return rendered.split("\n") if rendered else []

    def render_super_inning_log(self, state) -> list[str]:
        """Render the end-of-game super-inning summary block (final_log mode)."""
        rounds = state.super_inning_rounds
        if not rounds:
            return []
        # Pair visitor + home rounds by super-inning number.
        # Rounds are appended in pairs: v_round, h_round, v_round, h_round ...
        round_pairs = []
        for i in range(0, len(rounds), 2):
            v = rounds[i]
            h = rounds[i + 1] if i + 1 < len(rounds) else None
            rn = i // 2 + 1
            round_pairs.append({
                "round_num": rn,
                "v_name": v.team_name,
                "v_runs": v.runs,
                "h_name": h.team_name if h else "—",
                "h_runs": h.runs if h else 0,
            })
        winner_name = ""
        if state.winner:
            t = state.visitors if state.winner == "visitors" else state.home
            winner_name = t.name
        tmpl = self._env.get_template("super_inning.j2")
        rendered = tmpl.render(
            mode="final_log",
            winner_name=winner_name,
            round_pairs=round_pairs,
        ).rstrip("\n")
        return rendered.split("\n") if rendered else []

    def render_super_inning_tie(self) -> list[str]:
        return ["\n=== TIE — SUPER-INNING TIEBREAKER ==="]

    def render_super_inning_round_header(self, state, round_num: int) -> list[str]:
        tmpl = self._env.get_template("super_inning.j2")
        rendered = tmpl.render(
            mode="header",
            round_num=round_num,
            visitors_name=state.visitors.name,
            home_name=state.home.name,
        ).rstrip("\n")
        return rendered.split("\n") if rendered else []

    def render_super_inning_round_summary(self, state, round_num: int,
                                          v_runs: int, h_runs: int) -> list[str]:
        tmpl = self._env.get_template("super_inning.j2")
        rendered = tmpl.render(
            mode="summary",
            round_num=round_num,
            visitors_name=state.visitors.name,
            home_name=state.home.name,
            visitors_runs=v_runs,
            home_runs=h_runs,
        ).rstrip("\n")
        return rendered.split("\n") if rendered else []

    def render_game_over(self, state) -> list[str]:
        """Render the final game-over banner."""
        # Close the trailing PA if the game ended mid-half (walk-off, etc.)
        if self._pa_record_in_progress is not None:
            self._finalize_pa_record({"outs": state.outs})
            self._current_pa_batter_id = None
        self._populate_pitcher_arc(state)
        winner = state.winner
        sep = "=" * 60
        if not winner:
            # Regular-season tie after the SI round cap. Both halves are still
            # closed out properly above; this just emits the banner.
            v_score = state.score.get("visitors", 0)
            h_score = state.score.get("home", 0)
            return [
                f"\n{sep}",
                f"GAME OVER (tie after SI cap): {state.visitors.name} {v_score}, "
                f"{state.home.name} {h_score}",
                sep,
            ]
        other = "home" if winner == "visitors" else "visitors"
        w_team = state.visitors if winner == "visitors" else state.home
        o_team = state.home if winner == "visitors" else state.visitors
        w_score = state.score[winner]
        o_score = state.score[other]
        suffix = " (super-inning)" if state.super_inning_number > 0 else ""
        return [
            f"\n{sep}",
            f"GAME OVER{suffix}: {w_team.name.upper()} WIN {w_score}–{o_score}",
            sep,
            f"Final score: {w_team.name} {w_score}, {o_team.name} {o_score}",
        ]

    # -----------------------------------------------------------------------
    # Super-inning per-batter outcome helpers
    # -----------------------------------------------------------------------

    def _on_new_pa(self, batter) -> None:
        # PA increment moved to per-event in _update_stats: each contact
        # event (run-chosen, stay-chosen, foul-out, K, walk, HBP) is its
        # own PA. A single AB can contain up to 3 PAs (max 3 stays from
        # 0-0). This hook stays as a "first time we see this batter at
        # the plate" marker — the actual stat increment happens elsewhere.
        return

    # Halves where the visitors are batting (so opposing pitchers face them
    # and show up on the visitors' scorecard arc).
    _TOP_HALVES = {"top", "seconds_first", "supreg_top", "super_top"}

    def _populate_pitcher_arc(self, state) -> None:
        """Build pitcher_arc[bucket] from state.spell_log at game end. Each
        segment is {start_out, end_out, pitcher, half}. Out positions run on
        a single continuous ruler per side."""
        top_cursor = 0
        bot_cursor = 0
        for spell in state.spell_log:
            is_top = spell.half in self._TOP_HALVES
            bucket = "top" if is_top else "bot"
            cursor = top_cursor if is_top else bot_cursor
            seg_end = cursor + spell.outs_recorded
            self.pitcher_arc[bucket].append({
                "start_out": cursor,
                "end_out": seg_end,
                "pitcher": spell.pitcher_name,
                "half": spell.half,
            })
            if is_top:
                top_cursor = seg_end
            else:
                bot_cursor = seg_end

    def _stats_snapshot(self, player_id: str) -> dict:
        """Return a flat snapshot of the batter's accumulator fields."""
        s = self._batter_stats.get(player_id)
        if s is None:
            return {"pa": 0, "ab": 0, "hits": 0, "doubles": 0, "triples": 0,
                    "hr": 0, "bb": 0, "ibb": 0, "k": 0, "hbp": 0, "sty": 0,
                    "outs_recorded": 0}
        return {"pa": s.pa, "ab": s.ab, "hits": s.hits, "doubles": s.doubles,
                "triples": s.triples, "hr": s.hr, "bb": s.bb, "ibb": s.ibb,
                "k": s.k, "hbp": s.hbp, "sty": s.sty,
                "outs_recorded": s.outs_recorded}

    def _start_pa_record(self, batter, ctx) -> None:
        """Open a PA record for the incoming batter."""
        self._pa_record_in_progress = {
            "batter_id": batter.player_id,
            "batter_name": batter.name,
            "is_joker": bool(getattr(batter, "is_joker", False)),
            "joker_id": self._joker_insertions.get(ctx.get("batting_team_id", ""), 0) or None,
            "pitcher_name": ctx["pitcher"].name if ctx.get("pitcher") else "",
            "half": ctx.get("half", "top"),
            "outs_at_start": ctx.get("outs", 0),
            "phase": ctx.get("phase", 0),
            "pre": self._stats_snapshot(batter.player_id),
        }

    def _finalize_pa_record(self, ctx_after) -> None:
        """Close the in-progress PA record when the next PA starts (or the
        half ends). Derives the outcome from the BatterStats delta so it
        reflects what the engine actually credited, not a text parse."""
        pa = self._pa_record_in_progress
        if pa is None:
            return
        post = self._stats_snapshot(pa["batter_id"])
        pre = pa["pre"]
        d = {k: post[k] - pre[k] for k in post}

        # Outcome derivation from the diff.
        outcome, is_out = "OUT", True
        if d["hr"] > 0:
            outcome, is_out = "HR", False
        elif d["triples"] > 0:
            outcome, is_out = "3B", False
        elif d["doubles"] > 0:
            outcome, is_out = "2B", False
        elif d["hits"] > 0:
            outcome, is_out = "1B", False
        elif d["ibb"] > 0:
            outcome, is_out = "IBB", False
        elif d["bb"] > 0:
            outcome, is_out = "BB", False
        elif d["hbp"] > 0:
            outcome, is_out = "HBP", False
        elif d["k"] > 0:
            outcome, is_out = "K", True
        elif d["outs_recorded"] > 0:
            outcome, is_out = "OUT", True
        else:
            # No definite outcome (FC where the batter advances on a runner
            # being put out, sac fly, etc.). Mark as a generic out only if
            # the half-game outs ticked up across this PA.
            outs_now = ctx_after.get("outs", pa["outs_at_start"])
            is_out = outs_now > pa["outs_at_start"]
            outcome = "FC" if is_out else "?"

        self.pa_records.append({
            "batter": pa["batter_name"],
            "batter_id": pa["batter_id"],
            "pitcher": pa["pitcher_name"],
            "half": pa["half"],
            "phase": pa["phase"],
            "outs_at_start": pa["outs_at_start"],
            "outs_at_end": pa["outs_at_start"] + (1 if is_out else 0),
            "is_out": is_out,
            "outcome": outcome,
            "stays": d["sty"],
            "is_joker": pa["is_joker"],
            "joker_id": pa["joker_id"],
            "is_walk_back": outcome == "HR",
        })
        self._pa_record_in_progress = None

    def _accumulate_pa_runners_out(self, event, ctx) -> None:
        """Track runners retired during the current PA. Called per-event
        AFTER stat updates so the data is available to the credit logic
        at the next PA boundary."""
        outcome = event.get("outcome")
        if not isinstance(outcome, dict):
            return
        out_idxs: list[int] = []
        if outcome.get("runner_out_idx") is not None:
            out_idxs.append(outcome["runner_out_idx"])
        out_idxs.extend(outcome.get("extra_runner_outs") or [])
        if not out_idxs:
            return
        # ctx.bases_list is the PRE-event snapshot — the runners standing
        # on the bases that out_idxs reference. Resolve idx → runner_id
        # and add to the per-PA out set.
        pre_bases = ctx.get("bases_list") or [None, None, None]
        for idx in out_idxs:
            if 0 <= idx < 3 and pre_bases[idx] is not None:
                self._pa_runners_out.add(pre_bases[idx])

    def _credit_pa_advancement(self, batter_id: str) -> None:
        """Credit per-base advancement at PA end. Pesäpallo-style: each
        runner standing on 1B/2B/3B at PA start = opportunity; runner ending
        at a HIGHER base OR scored (= departed without being recorded as
        retired during this PA) = successful advancement.

        Run scoring-log rows are emitted authoritatively in _credit_runs;
        this method only computes the per-base advancement metrics.
        """
        s = self._stats_for_id(batter_id)
        start_bases = self._pa_start_bases or (None, None, None)
        end_bases   = self._last_bases or (None, None, None)
        for src_idx in (0, 1, 2):
            runner_id = start_bases[src_idx]
            if runner_id is None:
                continue
            # Opportunity.
            if s is not None:
                if src_idx == 0:   s.adv_op_1b += 1
                elif src_idx == 1: s.adv_op_2b += 1
                else:              s.adv_op_3b += 1
            # Three terminal cases per starting-base runner:
            #   - still on bases at higher idx → advanced N-src_idx bases
            #   - departed and NOT in retired-set → scored → gained (3-src_idx) bases
            #   - departed and in retired-set → out → no advancement, no bases
            advanced = False
            bases_gained = 0
            if runner_id in end_bases:
                new_idx = end_bases.index(runner_id)
                if new_idx > src_idx:
                    advanced = True
                    bases_gained = new_idx - src_idx
            elif runner_id not in self._pa_runners_out:
                advanced = True
                bases_gained = 3 - src_idx
            if advanced and s is not None:
                if src_idx == 0:   s.adv_adv_1b += 1
                elif src_idx == 1: s.adv_adv_2b += 1
                else:              s.adv_adv_3b += 1
            if bases_gained and s is not None:
                # RAD — graded total advancement bases gained by this
                # starting-base runner. Mirrors MLB Total Bases for batters,
                # applied to runner movement instead.
                if src_idx == 0:   s.rad_1b += bases_gained
                elif src_idx == 1: s.rad_2b += bases_gained
                else:              s.rad_3b += bases_gained

    def _stats_for_id(self, batter_id: str):
        """Look up an existing BatterStats by player_id. Returns None if
        the batter never accumulated any stats this game (no PA, no
        defensive credit, no pinch role)."""
        return self._batter_stats.get(batter_id)

    def _credit_fielder(self, fielder_id, state_after, attr: str) -> None:
        """Increment a per-fielder stat (po / e) for the player who made
        the play. Creates a BatterStats entry if one doesn't exist yet —
        defensive players need stat rows even if they haven't batted yet
        in the half.
        """
        if not fielder_id:
            return
        if fielder_id not in self._batter_stats:
            # Look the player up in the fielding team's roster to grab
            # their name; fall back to the id if absent.
            name = fielder_id
            for team in (state_after.fielding_team, state_after.batting_team):
                p = team.get_player(fielder_id)
                if p is not None:
                    name = p.name
                    break
            self._batter_stats[fielder_id] = BatterStats(
                player_id=fielder_id, name=name
            )
        setattr(self._batter_stats[fielder_id], attr,
                getattr(self._batter_stats[fielder_id], attr) + 1)

    def _get_stats(self, player) -> BatterStats:
        if player.player_id not in self._batter_stats:
            self._batter_stats[player.player_id] = BatterStats(
                player_id=player.player_id, name=player.name
            )
        return self._batter_stats[player.player_id]

    # Hit types that count as a ball-in-play hit for pitcher BABIP (HR excluded).
    _PP_BIP_HITS = frozenset(("single", "infield_single", "double", "triple"))
    # All ball-in-play hit_types (hits + outs on contact), i.e. the BABIP
    # denominator. HR and errors are excluded from BABIP by convention.
    _PP_BIP_ALL = frozenset(("single", "infield_single", "double", "triple",
                             "ground_out", "fly_out", "line_out",
                             "fielders_choice"))

    def _credit_pp_pitcher(self, event, ctx, disp, sh, sh_before, sh_s) -> None:
        """Accumulate Power Play pitcher counters keyed by the fielding pitcher.

        Window counters (pp_*) only accrue while `sh` is True (the nickel was
        deployed behind him); BF/K/BB/outs are read from the batter's stat
        deltas captured around _update_stats. Total BIP counters (tot_*) accrue
        on every ball-in-play regardless of window, so the BABIP split
        (with-nickel vs without) is computable in sim.py.
        """
        pitcher = ctx.get("pitcher")
        if pitcher is None:
            return
        rec = self._pp_pitcher.setdefault(pitcher.player_id, {
            "pp_bf": 0, "pp_outs": 0, "pp_k": 0, "pp_bb": 0,
            "pp_bip": 0, "pp_bip_hits": 0, "tot_bip": 0, "tot_bip_hits": 0,
        })
        # Ball-in-play classification (independent of the window) for BABIP.
        if event.get("type") == "ball_in_play":
            ht = disp.get("hit_type", "")
            if ht in self._PP_BIP_ALL:
                rec["tot_bip"] += 1
                if ht in self._PP_BIP_HITS:
                    rec["tot_bip_hits"] += 1
                if sh:
                    rec["pp_bip"] += 1
                    if ht in self._PP_BIP_HITS:
                        rec["pp_bip_hits"] += 1
        # Window counting stats from the batter deltas (BF = a completed PA).
        if sh and sh_before is not None:
            rec["pp_bf"]   += sh_s.pa            - sh_before[0]
            rec["pp_k"]    += sh_s.k             - sh_before[3]
            rec["pp_bb"]   += sh_s.bb            - sh_before[4]
            rec["pp_outs"] += sh_s.outs_recorded - sh_before[5]

    def _pick_dp_pivot(self, state_after, exclude_id):
        """Pick an infielder distinct from `exclude_id` to credit the
        DP / TP pivot (extra A + extra PO). Walks the fielding team's
        lineup and prefers the standard middle-infield pivots (2B, SS).
        Returns the player_id or None if no suitable fielder found.
        """
        team = getattr(state_after, "fielding_team", None)
        if team is None:
            return None
        lineup = getattr(team, "lineup", None) or getattr(team, "roster", None) or []
        # Standard pivot priority: 2B → SS → 3B → 1B → C → anyone else.
        priority = ("2B", "SS", "3B", "1B", "C")
        by_position: dict[str, list] = {pos: [] for pos in priority}
        any_others: list = []
        for p in lineup:
            pid = getattr(p, "player_id", None)
            if not pid or pid == exclude_id:
                continue
            pos = (getattr(p, "position", "") or "").upper()
            if pos in by_position:
                by_position[pos].append(pid)
            else:
                any_others.append(pid)
        for pos in priority:
            if by_position[pos]:
                return by_position[pos][0]
        return any_others[0] if any_others else None

    def _resolve_player_name(self, state, player_id) -> str:
        """Look up a player's display name by id across both teams' rosters.
        Falls back to the id string if the player can't be found."""
        if not player_id:
            return ""
        for team in (getattr(state, "visitors", None), getattr(state, "home", None)):
            if team is None:
                continue
            getter = getattr(team, "get_player", None)
            p = getter(player_id) if getter else None
            if p is not None:
                return p.name
        return str(player_id)

    def _batter_intro(self, batter) -> str:
        tag = " [P]" if batter.is_pitcher else ""
        return f"--- Now batting: {batter.name}{tag} ---"

    # -----------------------------------------------------------------------
    # Internal: runner advancement narrative
    # -----------------------------------------------------------------------

    def _compute_runner_lines(
        self, ctx: dict, state_after, etype: str, disp: dict, event: dict
    ) -> list[str]:
        """
        Compute human-readable runner advancement narrative by comparing the
        bases snapshot (before event) with the post-event bases.  Returns a
        list of indented lines suitable for appending to the event output.

        This covers contact plays, walk force-advances, HBP force-advances,
        and HR clearances, without depending on the raw apply_event() log.
        """
        # Only meaningful for contact and pitch events that move runners.
        if etype not in (
            "ball_in_play", "ball", "hit_by_pitch",
            "balk", "wild_pitch", "passed_ball",
        ):
            return []

        bases_before = ctx["bases_list"]
        bases_after = list(state_after.bases)
        base_names = ["1B", "2B", "3B"]
        hit_type = disp.get("hit_type", "")
        runs_scored = disp.get("runs_scored", 0)
        lines: list[str] = []

        # --- HOME RUN: everyone scores ---
        if etype == "ball_in_play" and hit_type in ("hr", "home_run"):
            inside_park = bool((event.get("outcome") or {}).get("inside_park"))
            for i in range(2, -1, -1):
                if bases_before[i] is not None:
                    lines.append(f"  Runner scores.")
            if inside_park:
                lines.append("  Batter scores — INSIDE-THE-PARK HOME RUN!")
            else:
                lines.append("  Batter scores (HR).")
            # Walk-Back caption. The HR PA places the hitter on 3B as a live
            # bonus runner (state.walk_back_runner_ids); the raw-log path in
            # o27/engine/pa.py emits these lines, but render_event bypasses
            # that log, so mirror them here.
            if ctx['batter'].player_id in getattr(state_after, "walk_back_runner_ids", set()):
                lines.append(
                    f"  [Walk-Back — {ctx['batter'].name} retreats to 3B "
                    f"as the bonus runner.]"
                )
                sponsor = _pick_walk_back_sponsor(state_after)
                if sponsor:
                    lines.append(f"  [The Walk-Back is brought to you by {sponsor}.]")
            return lines

        # --- Identify runner thrown out (fielder's choice / stay play) ---
        thrown_out_base: Optional[int] = None
        if etype == "ball_in_play":
            outcome = event.get("outcome", {})
            runner_out_idx = outcome.get("runner_out_idx")
            if runner_out_idx is not None and bases_before[runner_out_idx] is not None:
                thrown_out_base = runner_out_idx

        # --- Track each runner who was on base before the play ---
        after_id_set = {pid for pid in bases_after if pid is not None}
        for i in range(2, -1, -1):  # process 3B → 2B → 1B (score order)
            old_pid = bases_before[i]
            if old_pid is None:
                continue

            # Runner thrown out on this play.
            if thrown_out_base is not None and i == thrown_out_base:
                lines.append(f"  Runner at {base_names[i]} thrown out.")
                continue

            if old_pid in after_id_set:
                # Runner is still on a base — find which one.
                for j in range(3):
                    if bases_after[j] == old_pid and j != i:
                        lines.append(
                            f"  Runner advances from {base_names[i]} to {base_names[j]}."
                        )
                        break
            else:
                # Runner left the bases without being thrown out → scored.
                lines.append(f"  Runner scores from {base_names[i]}.")

        # --- Walk / HBP force-advance scoring (no specific runner narrative needed
        #     if we already handled it above, but catch the case where no runner
        #     was on a scored-from base because they were forced through empty bases) ---
        if etype in ("ball", "hit_by_pitch") and runs_scored > 0:
            runner_lines_count = sum(1 for ln in lines if "scores" in ln)
            shortfall = runs_scored - runner_lines_count
            for _ in range(shortfall):
                lines.append("  Runner scores (forced).")

        # --- Wild pitch / passed ball / balk: no base-by-base narration,
        #     but note runs scored if any ---
        if etype in ("wild_pitch", "passed_ball", "balk"):
            if runs_scored > 0:
                lines.append(f"  {runs_scored} run(s) score.")

        return lines

    # -----------------------------------------------------------------------
    # Internal: display context builder
    # -----------------------------------------------------------------------

    def _build_disp(self, event: dict, ctx: dict, state_after) -> dict:
        """Build the full Jinja2 template context dict from event + snapshots."""
        batter = ctx["batter"]
        pitcher = ctx["pitcher"]
        etype = event["type"]
        batting_tid = ctx["batting_team_id"]
        score_before = ctx["score"].get(batting_tid, 0)
        score_after = state_after.score.get(batting_tid, 0)
        runs_scored = max(0, score_after - score_before)

        # --- Base context (all keys always present to satisfy StrictUndefined) ---
        d: dict = {
            "event_type": etype,
            "display_type": etype.upper().replace("_", " "),
            "outs": ctx["outs"],
            "count": ctx["count"],
            "bases": ctx["bases"],
            "batter_name": batter.name,
            "batter_is_joker": False,
            "batter_is_pitcher": batter.is_pitcher,
            "pitcher_name": pitcher.name if pitcher else "—",
            "visitors_name": ctx["visitors_name"],
            "home_name": ctx["home_name"],
            "score_visitors": ctx["score"].get("visitors", 0),
            "score_home": ctx["score"].get("home", 0),
            "runs_scored": runs_scored,
            # Flags (event-specific; default False)
            "is_walk": False,
            "is_strikeout": False,
            "is_foul_out": False,
            "swinging": False,
            "stay_valid": False,
            "stay_batter_out": False,
            "stay_hit_credited": False,
            "steal_success": False,
            "steal_home": False,
            "pickoff_success": False,
            # String placeholders
            "ball_number": 0,
            "new_count": str(state_after.count),
            "hit_type": "",
            "hit_type_display": "",
            "choice": "run",
            "batter_safe": True,
            "new_bases": state_after.bases_summary(),
            "at_bat_hits": state_after.current_at_bat_hits,
            "steal_to": "",
            "pickoff_base": "",
            "joker_name": "",
            "joker_count": 0,
            "batting_team_name": ctx["batting_team_name"],
            "fielding_team_name": ctx["fielding_team_name"],
            "new_pitcher_name": "",
            "old_pitcher_name": pitcher.name if pitcher else "—",
            "old_spell_count": ctx["spell_count"],
            "replacement_name": "",
            "replaced_name": batter.name,
            # Substitution display fields (defensive_sub / pinch_runner /
            # joker_to_field / tactical_def_swap). All default empty so
            # StrictUndefined is satisfied even on non-sub events.
            "sub_in_name": "",
            "sub_out_name": "",
            "sub_position": "",
            "sub_base": "",
            # Phase-transition swap — comma-joined name lists for the
            # multi-player line.
            "sub_in_list": "",
            "sub_out_list": "",
        }

        # --- Event-specific overrides ---

        if etype == "ball":
            is_walk = ctx["count_balls"] == 3
            d["is_walk"] = is_walk
            d["ball_number"] = 4 if is_walk else state_after.count.balls
            d["new_count"] = str(state_after.count)

        elif etype in ("called_strike", "swinging_strike"):
            d["is_strikeout"] = ctx["count_strikes"] == 2
            d["swinging"] = (etype == "swinging_strike")
            d["new_count"] = str(state_after.count)

        elif etype == "foul":
            # O27 foul-out rule: 3 fouls in an at-bat = OUT (FO).
            # ctx["count_fouls"] is the foul count BEFORE this foul, so 2
            # means this foul makes it 3 → foul-out.
            d["is_foul_out"] = ctx.get("count_fouls", 0) >= 2
            d["new_count"] = str(state_after.count)
            if d["is_foul_out"]:
                d["display_type"] = "FOUL OUT"

        elif etype == "ball_in_play":
            outcome = event.get("outcome", {})
            choice = event.get("choice", "run")
            hit_type = outcome.get("hit_type", "")
            batter_safe = outcome.get("batter_safe", True)
            caught_fly = outcome.get("caught_fly", False)

            # PRD §2.6: HR overrides stay → run.
            if hit_type in ("hr", "home_run") and choice == "stay":
                choice = "run"

            d["choice"] = choice
            d["hit_type"] = hit_type
            d["hit_type_display"] = _HIT_TYPE_DISPLAY.get(
                hit_type, hit_type.replace("_", " ")
            )
            d["batter_safe"] = batter_safe
            d["new_bases"] = state_after.bases_summary()

            if choice == "stay":
                # The engine's stay rule (see o27/engine/stay.py) treats a
                # caught fly as the ONLY thing that retires the batter on a
                # stay — a 2-strike stay still credits a hit and burns the
                # final strike but does NOT make a batter-out. Mirror that
                # here so the renderer doesn't over-charge batter OR for
                # 2-strike stays that the engine never recorded as outs.
                stay_out = bool(caught_fly)
                d["stay_batter_out"] = stay_out
                d["stay_valid"] = not stay_out
                if not stay_out:
                    bases_before = ctx["bases_list"]
                    bases_after = state_after.bases
                    runner_advanced = runs_scored > 0 or any(
                        bases_after[i] is not None
                        and bases_after[i] != bases_before[i]
                        for i in range(3)
                    )
                    d["stay_hit_credited"] = runner_advanced
                    d["at_bat_hits"] = state_after.current_at_bat_hits

        elif etype == "stolen_base_attempt":
            base_idx = event.get("base_idx", 0)
            success = event.get("success", True)
            to_names = ["2B", "3B", "home"]
            d["steal_success"] = success
            d["steal_to"] = to_names[base_idx] if base_idx < 3 else "?"
            d["steal_home"] = (base_idx == 2 and success)

        elif etype == "pickoff_attempt":
            base_idx = event.get("base_idx", 0)
            success = event.get("success", False)
            base_names = ["1B", "2B", "3B"]
            d["pickoff_success"] = success
            d["pickoff_base"] = base_names[base_idx] if base_idx < 3 else "?"

        elif etype == "pitching_change":
            new_p = event.get("new_pitcher")
            d["new_pitcher_name"] = new_p.name if new_p else "?"

        elif etype == "pinch_hit":
            replacement = event.get("replacement")
            d["display_type"] = "PINCH HITTER"
            d["replacement_name"] = replacement.name if replacement else "?"
            # Mark the replacement as a PH and record who they came in for.
            # The replaced player is the team's CURRENT batter at this point
            # in the event stream (pinch_hit fires before the replacement
            # has actually batted).
            replaced = ctx.get("batter")
            if replacement is not None:
                rs = self._get_stats(replacement)
                rs.entry_type = "PH"
                if replaced is not None:
                    rs.replaced_player_id = str(replaced.player_id)
                # Inning = state.outs // 3 + 1. Stamped only on first
                # entry (no-reentry — preserves original inning).
                if not rs.entered_inning:
                    rs.entered_inning = state_after.outs // 3 + 1

        elif etype in ("joker_inserted", "joker_insertion"):
            # Joker entered for one PA. They get game_position="J" elsewhere;
            # here we mark entry_type so the box score can group them.
            #
            # The provider emits {"type": "joker_insertion", "joker": Player};
            # the engine's insert_joker also appends a "joker_inserted" event
            # to state.events with joker_id/joker_name keys. Handle both
            # shapes here for back-compat.
            joker = event.get("joker")
            inning = state_after.outs // 3 + 1
            # Plumb the incoming joker's name into the template (the bug:
            # this was never set, so the log read "inserts  (joker)").
            # The provider intent carries a Player under "joker"; the legacy
            # state.events form carries a "joker_name" string instead.
            d["joker_name"] = joker.name if joker is not None else event.get("joker_name", "")
            # Per-game running joker-insertion count for this batting team.
            # O27 jokers are uncapped by design (any number per game), so this
            # is a usage tally for readability, NOT a cap denominator.
            tid = ctx["batting_team_id"]
            self._joker_insertions[tid] = self._joker_insertions.get(tid, 0) + 1
            d["joker_count"] = self._joker_insertions[tid]
            if joker is not None:
                stats_obj = self._get_stats(joker)
                stats_obj.entry_type = "joker"
                if not stats_obj.entered_inning:
                    stats_obj.entered_inning = inning
            else:
                joker_id = event.get("joker_id")
                joker_name = event.get("joker_name", "")
                if joker_id and joker_id in self._batter_stats:
                    js = self._batter_stats[joker_id]
                    js.entry_type = "joker"
                    if not js.entered_inning:
                        js.entered_inning = inning
                elif joker_id:
                    self._batter_stats[joker_id] = BatterStats(
                        player_id=str(joker_id), name=joker_name,
                        entry_type="joker", entered_inning=inning,
                    )

        elif etype == "defensive_sub":
            # Mid-game defensive substitution. The substitute takes the
            # outgoing player's lineup slot — they may bat later. Mark them
            # with entry_type="DEF" so the box score indents them under the
            # player they replaced.
            #
            # The provider intent emits Player objects under
            # `player_in` / `player_out`. (state.events later carries an
            # `in_id` / `out_id` form, but the renderer sees the provider
            # intent — those keys are unused here.)
            player_in  = event.get("player_in")
            player_out = event.get("player_out")
            d["display_type"] = "DEFENSIVE SUB"
            if player_in is not None:
                d["sub_in_name"] = player_in.name
                stats_obj = self._get_stats(player_in)
                stats_obj.entry_type = "DEF"
                if player_out is not None:
                    d["sub_out_name"] = player_out.name
                    d["sub_position"] = (getattr(player_out, "game_position", "")
                                         or getattr(player_out, "position", "") or "")
                    stats_obj.replaced_player_id = str(player_out.player_id)
                if not stats_obj.entered_inning:
                    stats_obj.entered_inning = state_after.outs // 3 + 1

        elif etype == "pinch_runner":
            # Pinch runner takes over for the runner on `base_idx`. They
            # don't get a PA/AB unless they later come up to bat (their
            # lineup slot replaces the outgoing player). For box-score
            # purposes mark with entry_type="PR".
            #
            # Provider intent: {runner_in: Player, base_idx: int}. The
            # outgoing player is whoever held `base_idx` BEFORE the sub —
            # we read it off state_after.bases (the engine already
            # advanced it to runner_in.player_id, so we recover the
            # outgoing id by inspecting the substitution_log).
            runner_in = event.get("runner_in")
            base_idx  = event.get("base_idx")
            d["display_type"] = "PINCH RUNNER"
            base_names = ["1B", "2B", "3B"]
            if base_idx is not None and 0 <= base_idx < 3:
                d["sub_base"] = base_names[base_idx]
            if runner_in is not None:
                d["sub_in_name"] = runner_in.name
                stats_obj = self._get_stats(runner_in)
                stats_obj.entry_type = "PR"
                if not stats_obj.entered_inning:
                    stats_obj.entered_inning = state_after.outs // 3 + 1
                # Recover the replaced runner from the just-appended
                # substitution_log entry — keyed on this in_player_id.
                log = getattr(state_after, "substitution_log", None) or []
                for rec in reversed(log):
                    if (rec.kind == "pinch_run"
                            and rec.in_player_id == runner_in.player_id):
                        stats_obj.replaced_player_id = str(rec.out_player_id)
                        d["sub_out_name"] = self._resolve_player_name(
                            state_after, rec.out_player_id)
                        break

        elif etype == "tactical_def_swap":
            # Mid-batting-half offensive→defensive swap. Reuses pinch_hit
            # semantics in the engine but is logged separately so the box
            # score can distinguish a leverage-driven PH from a strategic
            # def-swap. Provider intent: {replacement: Player}; the
            # outgoing player is the current scheduled batter (same as
            # pinch_hit). Mark entry_type="DEF" so the row reads as a
            # defensive insertion rather than a PH.
            replacement = event.get("replacement")
            d["display_type"] = "DEFENSIVE SWAP"
            # Outgoing player is the currently scheduled batter (same as PH).
            d["sub_out_name"] = batter.name
            if replacement is not None:
                d["sub_in_name"] = replacement.name
                stats_obj = self._get_stats(replacement)
                # tactical_def_swap is a defensive intent; the player is
                # in the lineup permanently from here on, just like PH,
                # but tagged DEF for the box-score's purposes.
                if stats_obj.entry_type in ("", "starter"):
                    stats_obj.entry_type = "DEF"
                # Record who they came in for — the scheduled batter, same
                # as pinch_hit. Without this the box score can't pair or
                # name the sub and the footnote reads "Replaced — at ...".
                # First-entry only (no-reentry preserves the original).
                if batter is not None and not stats_obj.replaced_player_id:
                    stats_obj.replaced_player_id = str(batter.player_id)
                if not stats_obj.entered_inning:
                    stats_obj.entered_inning = state_after.outs // 3 + 1

        elif etype == "phase_transition_swap":
            # Wholesale offensive→defensive unit swap. Provider intent:
            # {swaps: [{player_in: Player, player_out: Player}, ...]}.
            # Build comma-joined incoming/outgoing name lists for the
            # single multi-player line, and tag every incoming player DEF.
            d["display_type"] = "PHASE TRANSITION"
            swaps = event.get("swaps") or []
            ins, outs = [], []
            inning = state_after.outs // 3 + 1
            for sw in swaps:
                player_in  = sw.get("player_in")
                player_out = sw.get("player_out")
                if player_in is not None:
                    ins.append(player_in.name)
                    stats_obj = self._get_stats(player_in)
                    if stats_obj.entry_type in ("", "starter"):
                        stats_obj.entry_type = "DEF"
                    if player_out is not None:
                        stats_obj.replaced_player_id = str(player_out.player_id)
                    if not stats_obj.entered_inning:
                        stats_obj.entered_inning = inning
                if player_out is not None:
                    outs.append(player_out.name)
            d["sub_in_list"]  = ", ".join(ins)
            d["sub_out_list"] = ", ".join(outs)

        elif etype == "declaration":
            # Declared Seconds — surface a play-by-play line via the template.
            # The team that just declared is the BATTING team at this point;
            # the score values come from the team object (stamped in
            # evaluate_declaration at the moment of declaration).
            team_obj = state_after.batting_team
            d["declaring_team_name"] = team_obj.name
            d["declared_at"] = int(event.get("at_out", state_after.outs))
            d["declare_score_for"] = int(getattr(team_obj, "declare_score_for", 0) or 0)
            d["declare_score_against"] = int(getattr(team_obj, "declare_score_against", 0) or 0)

        elif etype == "joker_to_field":
            # Rare: a joker is moved from the bench (DH-pool) into a
            # fielding slot to replace a fielder (injury, leverage). The
            # joker now occupies that fielding position for the rest of
            # the game; the team's joker-pool count drops by 1. Mark the
            # joker with entry_type="joker_field" so the box score lists
            # them with the position they took, separately from the
            # tactical-PH joker pool.
            #
            # Provider intent: {joker: Player, player_out: Player}.
            joker      = event.get("joker")
            player_out = event.get("player_out")
            d["display_type"] = "JOKER TO FIELD"
            if joker is not None:
                d["joker_name"] = joker.name
                stats_obj = self._get_stats(joker)
                stats_obj.entry_type = "joker_field"
                if player_out is not None:
                    d["sub_out_name"] = player_out.name
                    d["sub_position"] = (getattr(player_out, "game_position", "")
                                         or getattr(player_out, "position", "") or "")
                    stats_obj.replaced_player_id = str(player_out.player_id)
                if not stats_obj.entered_inning:
                    stats_obj.entered_inning = state_after.outs // 3 + 1

        return d

    # -----------------------------------------------------------------------
    # Internal: per-batter stats accumulation
    # -----------------------------------------------------------------------

    def _update_stats(self, event: dict, ctx: dict, state_after, disp: dict) -> None:
        """Update BatterStats based on the rendered event display context."""
        batter = ctx["batter"]
        s = self._get_stats(batter)
        etype = event["type"]
        runs_scored = disp.get("runs_scored", 0)
        ab_hits_before = ctx.get("at_bat_hits_before", 0)
        # Task #49: snapshot OR before per-event credits so we can charge the
        # responsible batter for any leftover engine-recorded outs (CS, FC,
        # pickoffs, runner thrown out on a ground out / stay, etc.).
        _or_before = s.outs_recorded

        # O27 multi-hit AB: at-bats (not walks/HBP) with 2+ credited hits.
        # Credited hits = stay hits accumulated prior to this event (ab_hits_before)
        # PLUS the terminal running hit, if this event is a run-chosen safety hit.
        _SAFETY_HITS = frozenset(
            ("single", "infield_single", "double", "triple", "hr", "home_run")
        )

        def _check_multi_hit(terminal_hit: bool = False) -> None:
            """
            Called only when the at-bat ends AND s.ab was just incremented.
            terminal_hit=True when the final event also credits a safety hit to batter.
            """
            total = ab_hits_before + (1 if terminal_hit else 0)
            if total >= 2:
                s.multi_hit_abs += 1

        if etype == "ball" and disp["is_walk"]:
            # Walk: 1 PA, NOT an at-bat. No multi_hit_abs.
            s.pa += 1
            s.bb += 1
            s.rbi += runs_scored

        elif etype == "intentional_walk":
            # Manager-issued IBB. Counts as a walk AND as an IBB (subset).
            s.pa += 1
            s.bb += 1
            s.ibb += 1
            s.rbi += runs_scored

        elif etype == "foul_tip_caught":
            # Foul-tip K: 1 PA, 1 AB, 1 K, 1 out.
            s.pa += 1
            s.ab += 1
            s.k += 1
            s.outs_recorded += 1
            _check_multi_hit()

        elif etype == "foul" and disp.get("is_foul_out"):
            # O27 foul-out: 1 PA, 1 AB, 1 out (FO label, not K).
            s.pa += 1
            s.ab += 1
            s.fo += 1
            s.outs_recorded += 1
            _check_multi_hit()

        elif etype == "stolen_base_attempt":
            # Credit the RUNNER (not the current batter) with SB or CS.
            # ctx["bases_list"] holds pre-event base state so we can recover
            # the runner ID. The runner already has a BatterStats entry from
            # whatever PA put them on base.
            base_idx = event.get("base_idx", 0)
            bases_before = ctx.get("bases_list") or [None, None, None]
            runner_id = bases_before[base_idx] if 0 <= base_idx < 3 else None
            success = bool(event.get("success", False))
            if runner_id is not None and runner_id in self._batter_stats:
                rs = self._batter_stats[runner_id]
                if success:
                    rs.sb += 1
                else:
                    rs.cs += 1
                    rs.outs_recorded += 1
                    # CS = baserunner erased without scoring → LOB.
                    bt = ctx.get("batting_team_id")
                    if bt == "visitors" or bt == "home":
                        tm = state_after.visitors if bt == "visitors" else state_after.home
                        tm.lob = int(getattr(tm, "lob", 0) or 0) + 1
            # Don't fall through to the leftover-out reconciliation below —
            # the at-bat is still in progress and the only out (if any) was
            # already charged to the runner above. Falling through would
            # double-charge the current batter for the runner's CS out.
            if runs_scored > 0:
                self._credit_runs(ctx, state_after, runs_scored, etype, disp)
            return

        elif etype in ("called_strike", "swinging_strike") and disp["is_strikeout"]:
            # K: 1 PA, 1 AB, 1 K, 1 out.
            s.pa += 1
            s.ab += 1
            s.k += 1
            s.outs_recorded += 1
            _check_multi_hit()

        elif etype == "hit_by_pitch":
            # HBP: 1 PA, NOT an at-bat. No multi_hit_abs.
            s.pa += 1
            s.hbp += 1
            s.rbi += runs_scored

        elif etype == "ball_in_play":
            choice = disp.get("choice", "run")
            hit_type = disp.get("hit_type", "")
            is_safety_hit = hit_type in _SAFETY_HITS

            # Phase 11D — append a per-event row to _pa_log for diagnostic
            # swing-split analysis. Detect AB boundary: in-progress AB number
            # is `s.ab + 1` (s.ab counts COMPLETED ABs prior to this event).
            # When that changes vs the last observed value for this batter,
            # swing_idx resets to 1; otherwise it increments.
            bid = batter.player_id
            in_progress_ab = (s.ab or 0) + 1
            if self._batter_current_ab.get(bid) != in_progress_ab:
                # New AB started — reset swing_idx
                self._batter_current_ab[bid] = in_progress_ab
                self._batter_swing_idx[bid] = 1
            else:
                self._batter_swing_idx[bid] += 1
            swing_idx = self._batter_swing_idx[bid]
            outcome = event.get("outcome", {})
            quality = outcome.get("quality")
            team_id = ctx.get("batting_team_id")
            pitcher = ctx.get("pitcher")

            # SABR analytics: stamp pre/post game-state on every BIP event
            # so RE24 / leverage / WPA can be computed without replaying the
            # engine. Bases encoded as a 3-bit mask (bit0=1B, bit1=2B,
            # bit2=3B), score_diff is batting_score − fielding_score.
            def _bases_mask(seq):
                return sum((1 << i) for i, r in enumerate(seq or [None, None, None]) if r is not None)

            outs_before = ctx.get("outs", 0) or 0
            bases_before_mask = _bases_mask(ctx.get("bases_list"))
            score_before_dict = ctx.get("score") or {}
            bat_before = score_before_dict.get(team_id, 0)
            fld_before = sum(v for k, v in score_before_dict.items() if k != team_id)
            score_diff_before = bat_before - fld_before

            outs_after = getattr(state_after, "outs", outs_before) or 0
            bases_after_mask = _bases_mask(list(getattr(state_after, "bases", [None, None, None])))
            score_after_dict = dict(getattr(state_after, "score", {}) or {})
            bat_after = score_after_dict.get(team_id, bat_before)
            fld_after = sum(v for k, v in score_after_dict.items() if k != team_id)
            score_diff_after = bat_after - fld_after

            self._pa_log.append({
                "team_id": team_id,
                "batter_id": bid,
                "pitcher_id": pitcher.player_id if pitcher else None,
                "phase": ctx.get("phase", 0),
                "ab_seq": in_progress_ab,
                "swing_idx": swing_idx,
                "choice": choice,
                "quality": quality,
                "hit_type": hit_type,
                "pitch_type": event.get("pitch_type"),
                "exit_velocity": event.get("exit_velocity"),
                "launch_angle":  event.get("launch_angle"),
                "spray_angle":   event.get("spray_angle"),
                # was_stay = 1 only on VALID 2C events (matches s.sty); invalid
                # stays (auto-out caught fly) don't count as 2C events.
                "was_stay": 1 if (choice == "stay" and disp.get("stay_valid")) else 0,
                "stay_credited": 1 if (choice == "stay" and disp.get("stay_hit_credited")) else 0,
                "runs_scored": runs_scored,
                "rbi_credited": runs_scored,
                "outs_before": outs_before,
                "bases_before": bases_before_mask,
                "score_diff_before": score_diff_before,
                "outs_after": outs_after,
                "bases_after": bases_after_mask,
                "score_diff_after": score_diff_after,
            })

            if choice == "stay":
                if disp.get("stay_valid"):
                    # Stay event: credit the stay attempt (sty), the hit
                    # if applicable, and runner-movement opportunities.
                    # PA increments ONLY when the AB actually ends (i.e.,
                    # the stay pushes strikes to 3 and terminates the AB),
                    # so the standard identity PA == AB + BB + HBP holds —
                    # intermediate stays accumulate sty without counting
                    # as a separate plate appearance each.
                    s.sty += 1
                    if disp.get("stay_hit_credited"):
                        s.hits += 1
                        s.stay_hits += 1
                    # stay_rbi: credit RBI for runs that score on a valid stay.
                    if runs_scored > 0:
                        s.stay_rbi += runs_scored
                    s.rbi += runs_scored

                    # 2C moved-runner stats. For each runner on base BEFORE
                    # this 2C event, record an opportunity; if the same
                    # runner ended up on a higher base or scored cleanly,
                    # record a successful move. Runners thrown out (FC/
                    # TOOTBLAN) count as opportunities but not moves.
                    bases_before = ctx.get("bases_list") or [None, None, None]
                    bases_after = state_after.bases
                    outcome = event.get("outcome", {})
                    out_idxs = []
                    if outcome.get("runner_out_idx") is not None:
                        out_idxs.append(outcome["runner_out_idx"])
                    out_idxs.extend(outcome.get("extra_runner_outs") or [])
                    for src_idx in (0, 1, 2):
                        runner_id = bases_before[src_idx]
                        if runner_id is None:
                            continue
                        if src_idx == 0:   s.c2_op_1b += 1
                        elif src_idx == 1: s.c2_op_2b += 1
                        else:              s.c2_op_3b += 1
                        # Did the runner advance?
                        moved = False
                        for dst_idx in range(src_idx + 1, 3):
                            if bases_after[dst_idx] == runner_id:
                                moved = True
                                break
                        if (not moved
                                and runner_id not in bases_after
                                and src_idx not in out_idxs):
                            moved = True   # scored cleanly
                        if moved:
                            if src_idx == 0:   s.c2_adv_1b += 1
                            elif src_idx == 1: s.c2_adv_2b += 1
                            else:              s.c2_adv_3b += 1
                    # If the stay's strike-credit pushed the count to 3
                    # strikes, the AB ends — count as an AB (max-hits stay
                    # sequence terminates the AB without a batter-out) and
                    # credit the PA at the terminal event.
                    if state_after.count.strikes >= 3:
                        s.pa += 1
                        s.ab += 1
                        _check_multi_hit(terminal_hit=disp.get("stay_hit_credited", False))
                    # Otherwise AB CONTINUES — do NOT check multi_hit_abs yet
                    # and do NOT credit a PA (intermediate stay only).
                elif disp.get("stay_batter_out"):
                    # Stay results in out → AB ends. The only path that
                    # reaches here now is caught_fly (the rule was simplified
                    # so 2-strike stays don't out the batter).
                    s.pa += 1
                    s.ab += 1
                    s.rbi += runs_scored
                    s.outs_recorded += 1
                    _check_multi_hit(terminal_hit=False)
            else:
                # Run chosen — AB ends. 1 PA, 1 AB.
                s.pa += 1
                s.ab += 1
                if is_safety_hit:
                    s.hits += 1
                elif hit_type == "error":
                    # Reached on error: AB credited, NO hit, NO out, ROE++.
                    # Pitcher H allowed does NOT increment (errors aren't
                    # hits in MLB scoring); pa.py already handled that.
                    s.roe += 1
                    # Charge the error to the responsible fielder (E++).
                    self._credit_fielder(
                        (event.get("outcome") or {}).get("fielder_id"),
                        state_after, "e",
                    )
                elif not disp.get("batter_safe", True):
                    # Batter retired (ground out, fly out, line out, DP etc.)
                    s.outs_recorded += 1
                    # Credit the putout to the responsible fielder (PO++).
                    # Caught flies still credit a PO (the fielder caught it).
                    fielder_id_v = (event.get("outcome") or {}).get("fielder_id")
                    self._credit_fielder(fielder_id_v, state_after, "po")
                    # Assist credit on throwing outs. Caught flies and pure
                    # unassisted putouts don't get an A; ground outs, DPs,
                    # fielder's choices, and triple plays do. On DP/TP the
                    # chain credits an extra A to a derived pivot infielder
                    # (approximate — no spray-angle lookup).
                    if hit_type in ("ground_out", "fielders_choice",
                                    "double_play", "triple_play",
                                    "infield_out"):
                        self._credit_fielder(fielder_id_v, state_after, "a")
                    if hit_type in ("double_play", "triple_play"):
                        # Extra assist + extra putout for the pivot. Pull a
                        # different infielder from the fielding team's
                        # lineup — we approximate by walking the lineup and
                        # picking the first non-fielder_id infield slot.
                        pivot = self._pick_dp_pivot(state_after, fielder_id_v)
                        if pivot:
                            self._credit_fielder(pivot, state_after, "a")
                            self._credit_fielder(pivot, state_after, "po")
                if hit_type == "double":
                    s.doubles += 1
                elif hit_type == "triple":
                    s.triples += 1
                elif hit_type in ("hr", "home_run"):
                    s.hr += 1
                elif hit_type == "double_play":
                    s.gidp += 1
                elif hit_type == "triple_play":
                    s.gitp += 1
                s.rbi += runs_scored
                # Terminal running hit counts toward multi-hit AB. Errors
                # don't count toward multi-hit (they aren't hits).
                _check_multi_hit(terminal_hit=is_safety_hit)

        # TOA (thrown out advancing) — credit the RUNNER who was nailed
        # on the bases, not the batter at the plate. The advancement-table
        # outs from prob.runner_advances_for_hit propagate here via the
        # outcome's toa_runner_idxs list. Each TOA marks its runner with
        # outs_recorded += 1 AND toa += 1; we tally these so the leftover-
        # out reconciliation below doesn't double-charge the batter for
        # them. Also: every TOA is a baserunner erased without scoring →
        # increment the batting team's LOB by the same count, plus count
        # any other base-outs the play recorded (FC lead-runner, GIDP-
        # runner, pickoff caught mid-PA — anyone who was on base and is
        # now an out without crossing the plate).
        toa_credited = 0
        toa_charged_stats: list = []   # runner stats objects credited a TOA out
        if etype == "ball_in_play":
            outcome = event.get("outcome") or {}
            toa_idxs = outcome.get("toa_runner_idxs") or []
            non_toa_out_idxs: list[int] = []
            if outcome.get("runner_out_idx") is not None:
                non_toa_out_idxs.append(int(outcome["runner_out_idx"]))
            non_toa_out_idxs.extend(int(i) for i in (outcome.get("extra_runner_outs") or []))
            non_toa_out_idxs = [i for i in non_toa_out_idxs if i not in toa_idxs]
            if toa_idxs:
                bases_before = ctx.get("bases_list") or [None, None, None]
                for idx in toa_idxs:
                    if 0 <= idx < 3:
                        runner_pid = bases_before[idx]
                        if runner_pid is not None and runner_pid in self._batter_stats:
                            rs = self._batter_stats[runner_pid]
                            rs.outs_recorded += 1
                            rs.toa += 1
                            toa_credited += 1
                            toa_charged_stats.append(rs)
            # All base-runner erasures on this play (TOA + FC + GIDP-runner
            # + pickoff caught here) count toward LOB — they were on base
            # and won't cross the plate.
            erased_total = len(set(toa_idxs)) + len(set(non_toa_out_idxs))
            if erased_total:
                bt = ctx.get("batting_team_id")
                if bt in ("visitors", "home"):
                    tm = state_after.visitors if bt == "visitors" else state_after.home
                    tm.lob = int(getattr(tm, "lob", 0) or 0) + erased_total

        # Task #49: reconcile this event's per-batter out charges to the
        # engine's ground-truth out count. state.outs is the single source of
        # truth for both ledgers (renderer batter OR and engine pitcher outs);
        # the per-event structured branches and the TOA loop above only express
        # the renderer's *intended* attribution and can diverge from what the
        # engine actually recorded:
        #   - under-count (CS / pickoff / FC / DP-trail runner outs not credited
        #     by a structured branch) → top the current batter up.
        #   - over-count (a TOA / structured out the engine never recorded —
        #     e.g. the engine's stay path retires only the lead runner, or a
        #     multi-out play truncated at the phase out-cap) → trim the excess
        #     so the OR column still sums to the engine's outs per phase.
        # Trim the batter's own structured charge first (down to its pre-event
        # value), then peel back TOA runner credits LIFO — never below what was
        # charged this event, so no per-player count goes negative.
        # A Declared Seconds declaration ends the half by jumping state.outs
        # straight to the cap (pa.py: state.outs = 27) WITHOUT recording any
        # real out — the team banked the remaining outs for a rebuttal round.
        # The engine never calls _record_out, so the pitcher ledger correctly
        # shows only the real outs; treat the artificial jump as zero outs here
        # so the batter ledger doesn't get charged the banked count.
        if etype == "declaration":
            engine_outs_delta = 0
        else:
            engine_outs_delta = (state_after.outs or 0) - (ctx.get("outs") or 0)
        batter_charged = s.outs_recorded - _or_before
        diff = engine_outs_delta - (batter_charged + toa_credited)
        if diff > 0:
            s.outs_recorded += diff
        elif diff < 0:
            excess = -diff
            trim_batter = min(excess, batter_charged)
            s.outs_recorded -= trim_batter
            excess -= trim_batter
            for rs in reversed(toa_charged_stats):
                if excess <= 0:
                    break
                rs.outs_recorded -= 1
                rs.toa -= 1
                excess -= 1

        # Credit runs-scored (R) to the players who left the bases.
        if runs_scored > 0:
            self._credit_runs(ctx, state_after, runs_scored, etype, disp)

    def _credit_runs(self, ctx: dict, state_after, runs_scored: int,
                     etype: str, disp: dict) -> None:
        """Credit the 'R' stat to the players who scored AND emit one
        scoring-log row per run.

        This is the single authoritative run-attribution path (every run
        flows through here exactly once), so deriving the scoring log from
        the same `runs_scored` count guarantees the log reconciles exactly
        to the final score — no phantom over-counts, no missed runs.

        Each scorer is paired with the base they scored from (0/1/2 = 1B/2B/3B,
        BATTER_HR_FROM_BASE for the batter's own home-run run). Any run that
        can't be matched to a starting-base runner or a HR (e.g. a Walk-Back
        bonus run, or a phantom-attribution edge) is credited to the batter
        and tagged OTHER_FROM_BASE.

        Edge cases handled: same pid on multiple bases; lineup-wrap (batter
        was also a runner); HR batter; runner pid missing from _batter_stats.
        """
        from collections import Counter

        bases_before = ctx["bases_list"]
        bases_after = list(state_after.bases)
        before_count = Counter(p for p in bases_before if p is not None)
        after_count  = Counter(p for p in bases_after  if p is not None)
        batter_pid   = ctx["batter"].player_id

        # Ordered (from_base, pid) for each runner who left a base and crossed.
        # 3B → 2B → 1B so the furthest-along runner is credited first; `seen`
        # prevents double-counting a pid that occupies multiple bases.
        scorers: list[tuple[int, str]] = []
        seen: set[str] = set()
        for i in (2, 1, 0):
            pid = bases_before[i]
            if pid is None or pid in seen:
                continue
            seen.add(pid)
            scored = max(0, before_count[pid] - after_count.get(pid, 0))
            for _ in range(scored):
                scorers.append((i, pid))

        # Lineup-wrap case: batter was on base before, still on base after
        # with the same multiplicity → the runner-instance of them crossed.
        if (batter_pid in before_count
                and before_count[batter_pid] == after_count.get(batter_pid, 0)):
            wrap_base = bases_before.index(batter_pid)
            scorers.append((wrap_base, batter_pid))

        # HR: the batter scores himself (unless already counted via wrap).
        hit_type = disp.get("hit_type", "")
        if etype == "ball_in_play" and hit_type in ("hr", "home_run"):
            if not any(pid == batter_pid for _, pid in scorers):
                scorers.append((BATTER_HR_FROM_BASE, batter_pid))

        # Take exactly `runs_scored` runs; pad any shortfall with batter-
        # attributed "other" runs (Walk-Back / phantom) so the count is exact.
        runs: list[tuple[int, str]] = list(scorers[:runs_scored])
        while len(runs) < runs_scored:
            runs.append((OTHER_FROM_BASE, batter_pid))

        # Credit R and emit a scoring-log row per run, ticking the batting
        # team's score up by one each run (other team's score is constant).
        bt = ctx.get("batting_team_id")
        before_bt = int(ctx.get("score", {}).get(bt, 0) or 0)
        other = "home" if bt == "visitors" else "visitors"
        other_score = int(state_after.score.get(other, 0) or 0)
        for i, (from_base, pid) in enumerate(runs):
            target = pid if pid in self._batter_stats else batter_pid
            if target in self._batter_stats:
                self._batter_stats[target].runs += 1
            bt_score = before_bt + i + 1
            self._scoring_log.append({
                "seq":              len(self._scoring_log),
                "half":             ctx.get("half", "top"),
                "outs_before":      int(ctx.get("outs", 0) or 0),
                "batter_id":        batter_pid,
                "runner_id":        pid,
                "runner_from_base": from_base,
                "visitors_score":   bt_score if bt == "visitors" else other_score,
                "home_score":       bt_score if bt == "home" else other_score,
            })

    # -----------------------------------------------------------------------
    # Public accessor — structured stats for web display
    # -----------------------------------------------------------------------

    @property
    def batter_stats(self) -> dict:
        """Expose internal BatterStats dict (player_id → BatterStats)."""
        return self._batter_stats

    # -----------------------------------------------------------------------
    # Task #58: per-phase snapshots and delta extraction
    # -----------------------------------------------------------------------

    def end_phase(self, phase: int) -> None:
        """Snapshot cumulative batter stats at the end of a phase.

        Called by the game loop after each phase finishes:
          - phase 0 = end of regulation (after both halves)
          - phase N >= 1 = end of super-inning round N (after both halves)

        Per-phase delta rows are derived later by batter_stats_for_phase().
        """
        from dataclasses import replace
        self._phase_end_snapshots[phase] = {
            pid: replace(s) for pid, s in self._batter_stats.items()
        }

    def batter_stats_for_phase(self, phase: int) -> dict:
        """Per-phase BatterStats delta dict (player_id -> BatterStats).

        Computed as snapshot[phase] - snapshot[phase-1]. Phase 0's
        baseline is empty (game start). Players with zero activity in
        the phase are omitted.
        """
        end = self._phase_end_snapshots.get(phase, {})
        prev = self._phase_end_snapshots.get(phase - 1, {}) if phase > 0 else {}
        out: dict[str, BatterStats] = {}
        for pid, end_s in end.items():
            prev_s = prev.get(pid)
            d = self._stat_delta(end_s, prev_s)
            if self._has_activity(d):
                out[pid] = d
        return out

    def phases_seen(self) -> list[int]:
        """Sorted list of phases for which end_phase() was called."""
        return sorted(self._phase_end_snapshots.keys())

    @staticmethod
    def _stat_delta(end_s: BatterStats, prev_s: Optional[BatterStats]) -> BatterStats:
        d = BatterStats(player_id=end_s.player_id, name=end_s.name)
        # Identity tags (set once when the player entered the game, never
        # incremented) must be propagated as-is, not subtracted as deltas.
        # The for-loop below only handles counter fields; without these
        # three lines every PH/PR/DEF/joker stamp gets stripped between
        # the cumulative bstat and the per-phase delta that o27v2/sim.py
        # persists to game_batter_stats.
        d.entry_type         = end_s.entry_type
        d.replaced_player_id = end_s.replaced_player_id
        d.entered_inning     = end_s.entered_inning
        prev_get = (lambda f: getattr(prev_s, f)) if prev_s else (lambda f: 0)
        for f in ("pa", "ab", "runs", "hits", "doubles", "triples", "hr",
                  "rbi", "bb", "k", "hbp", "sty", "outs_recorded",
                  "stay_rbi", "stay_hits", "multi_hit_abs",
                  "sb", "cs", "fo", "roe",
                  "po", "a", "e",
                  "gidp", "gitp",
                  "c2_op_1b", "c2_adv_1b", "c2_op_2b", "c2_adv_2b",
                  "c2_op_3b", "c2_adv_3b",
                  "adv_op_1b", "adv_adv_1b", "adv_op_2b", "adv_adv_2b",
                  "adv_op_3b", "adv_adv_3b",
                  "rad_1b", "rad_2b", "rad_3b"):
            setattr(d, f, getattr(end_s, f) - prev_get(f))
        return d

    @staticmethod
    def _has_activity(d: BatterStats) -> bool:
        return any(getattr(d, f) for f in (
            "pa", "ab", "runs", "hits", "bb", "k", "hbp", "sty", "outs_recorded"
        ))
