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

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

# Manager / between-pitch event types that do NOT start a new plate appearance.
_NON_PA_EVENTS = frozenset(
    {"joker_insertion", "pitching_change", "pinch_hit",
     "stolen_base_attempt", "pickoff_attempt", "balk",
     "wild_pitch", "passed_ball",
     "defensive_sub", "tactical_def_swap", "pinch_runner",
     "joker_to_field"}
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
}


class Renderer:
    """Jinja2 renderer for O27 play-by-play and structured output."""

    def __init__(self, template_dir: Optional[str] = None) -> None:
        tdir = template_dir or _TEMPLATE_DIR
        self._env = Environment(
            loader=FileSystemLoader(tdir),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )
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
        self._batter_swing_idx: dict = {}       # batter_id -> swing_idx within current ab

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
            "phase": getattr(state, "super_inning_number", 0),
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
            self._on_new_pa(batter)
            lines.append(self._batter_intro(batter))
            self._current_pa_batter_id = batter.player_id

        # Build the template context dict (all display values pre-computed).
        disp = self._build_disp(event, ctx, state_after)

        # Update batter stats.
        self._update_stats(event, ctx, state_after, disp)

        # Render via Jinja2 template.
        tmpl = self._env.get_template("play_by_play.j2")
        rendered = tmpl.render(**disp).rstrip("\n")
        if rendered:
            lines.append(rendered)

        # Append runner advancement narrative computed from state delta.
        runner_lines = self._compute_runner_lines(ctx, state_after, etype, disp, event)
        lines.extend(runner_lines)

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

    def render_box_score(self, state) -> list[str]:
        """Render the full dual-team box score, including pitcher lines and required RR."""
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
                t.k            += r.k
                t.hbp          += r.hbp
                t.sty          += r.sty
                t.stay_rbi     += r.stay_rbi
                t.stay_hits    += r.stay_hits
                t.multi_hit_abs += r.multi_hit_abs
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
            visitors_pitchers=v_pitchers,
            home_pitchers=h_pitchers,
            required_rr=required_rr,
            target_runs=target_runs,
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
                "v_dismissals": v.dismissals,
                "v_outcomes": v.batter_outcomes if v.batter_outcomes else [],
                "h_name": h.team_name if h else "—",
                "h_runs": h.runs if h else 0,
                "h_dismissals": h.dismissals if h else 0,
                "h_outcomes": h.batter_outcomes if h and h.batter_outcomes else [],
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

    def render_super_inning_round_header(self, state, round_num: int,
                                         v5, h5) -> list[str]:
        tmpl = self._env.get_template("super_inning.j2")
        rendered = tmpl.render(
            mode="header",
            round_num=round_num,
            visitors_name=state.visitors.name,
            home_name=state.home.name,
            visitors_lineup=", ".join(p.name for p in v5),
            home_lineup=", ".join(p.name for p in h5),
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
        winner = state.winner
        if not winner:
            return []
        other = "home" if winner == "visitors" else "visitors"
        w_team = state.visitors if winner == "visitors" else state.home
        o_team = state.home if winner == "visitors" else state.visitors
        w_score = state.score[winner]
        o_score = state.score[other]
        suffix = " (super-inning)" if state.super_inning_number > 0 else ""
        sep = "=" * 60
        return [
            f"\n{sep}",
            f"GAME OVER{suffix}: {w_team.name.upper()} WIN {w_score}–{o_score}",
            sep,
            f"Final score: {w_team.name} {w_score}, {o_team.name} {o_score}",
        ]

    # -----------------------------------------------------------------------
    # Super-inning per-batter outcome helpers
    # -----------------------------------------------------------------------

    def snapshot_batter_stats(self, player_ids: list[str]) -> dict:
        """
        Return a snapshot of key batter stat fields for the given player_ids.
        Call BEFORE a super-inning half; compare with batter_outcomes_since()
        AFTER the half to derive per-batter outcome strings.
        """
        snap = {}
        for pid in player_ids:
            s = self._batter_stats.get(pid)
            if s:
                snap[pid] = {
                    "hits": s.hits, "doubles": s.doubles, "triples": s.triples,
                    "hr": s.hr, "bb": s.bb, "k": s.k, "hbp": s.hbp, "sty": s.sty,
                }
            else:
                snap[pid] = {
                    "hits": 0, "doubles": 0, "triples": 0,
                    "hr": 0, "bb": 0, "k": 0, "hbp": 0, "sty": 0,
                }
        return snap

    def batter_outcomes_since(
        self, players: list, snapshot: dict
    ) -> list[str]:
        """
        Derive a brief outcome label for each player relative to the snapshot.
        Returns a list of "Name: outcome" strings, one per player.
        """
        results = []
        for p in players:
            pid = p.player_id
            pre = snapshot.get(pid, {})
            s = self._batter_stats.get(pid)
            post = {
                "hits": s.hits if s else 0,
                "doubles": s.doubles if s else 0,
                "triples": s.triples if s else 0,
                "hr": s.hr if s else 0,
                "bb": s.bb if s else 0,
                "k": s.k if s else 0,
                "hbp": s.hbp if s else 0,
                "sty": s.sty if s else 0,
            }
            label = self._outcome_label(pre, post)
            results.append(f"{p.name}: {label}")
        return results

    @staticmethod
    def _outcome_label(pre: dict, post: dict) -> str:
        """Derive a brief outcome string from the delta of two BatterStats snapshots."""
        dh   = post.get("hits", 0)     - pre.get("hits", 0)
        dbb  = post.get("bb", 0)       - pre.get("bb", 0)
        dhbp = post.get("hbp", 0)      - pre.get("hbp", 0)
        dk   = post.get("k", 0)        - pre.get("k", 0)
        dhr  = post.get("hr", 0)       - pre.get("hr", 0)
        d3b  = post.get("triples", 0)  - pre.get("triples", 0)
        d2b  = post.get("doubles", 0)  - pre.get("doubles", 0)
        dsty = post.get("sty", 0)      - pre.get("sty", 0)

        # Terminal outcome (in precedence order)
        if dbb > 0:
            term = "BB"
        elif dhbp > 0:
            term = "HBP"
        elif dhr > 0:
            term = "HR"
        elif d3b > 0:
            term = "3B"
        elif d2b > 0:
            term = "2B"
        elif dh - d2b - d3b - dhr > 0:
            term = "1B"
        elif dk > 0:
            term = "K"
        else:
            term = "out"

        # Annotate stay count when the batter accumulated stay hits before terminal
        prefix = f"{dsty}×stay, " if dsty > 0 else ""
        return f"{prefix}{term}"

    def _on_new_pa(self, batter) -> None:
        # PA increment moved to per-event in _update_stats: each contact
        # event (run-chosen, stay-chosen, foul-out, K, walk, HBP) is its
        # own PA. A single AB can contain up to 3 PAs (max 3 stays from
        # 0-0). This hook stays as a "first time we see this batter at
        # the plate" marker — the actual stat increment happens elsewhere.
        return

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
            for i in range(2, -1, -1):
                if bases_before[i] is not None:
                    lines.append(f"  Runner scores.")
            lines.append("  Batter scores (HR).")
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
            "batting_team_name": ctx["batting_team_name"],
            "fielding_team_name": ctx["fielding_team_name"],
            "new_pitcher_name": "",
            "old_pitcher_name": pitcher.name if pitcher else "—",
            "old_spell_count": ctx["spell_count"],
            "replacement_name": "",
            "replaced_name": batter.name,
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
                stay_out = (ctx["count_strikes"] == 2) or caught_fly
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

        elif etype == "joker_inserted":
            # Joker entered for one PA. They get game_position="J" elsewhere;
            # here we mark entry_type so the box score can group them.
            joker_id = event.get("joker_id")
            joker_name = event.get("joker_name", "")
            if joker_id and joker_id in self._batter_stats:
                self._batter_stats[joker_id].entry_type = "joker"
            elif joker_id:
                self._batter_stats[joker_id] = BatterStats(
                    player_id=str(joker_id), name=joker_name, entry_type="joker"
                )

        elif etype == "defensive_sub":
            # Mid-game defensive substitution. The substitute takes the
            # outgoing player's lineup slot — they may bat later. Mark them
            # with entry_type="DEF" so the box score indents them under the
            # player they replaced.
            in_id  = event.get("in_id")
            out_id = event.get("out_id")
            in_name = event.get("in_name", "")
            if in_id is not None:
                stats_obj = self._batter_stats.get(in_id) or BatterStats(
                    player_id=str(in_id), name=in_name
                )
                stats_obj.entry_type = "DEF"
                if out_id is not None:
                    stats_obj.replaced_player_id = str(out_id)
                self._batter_stats[in_id] = stats_obj

        elif etype == "pinch_runner":
            # Pinch runner takes over for `out_id` on the basepaths. They
            # don't get a PA/AB unless they later come up to bat (their
            # lineup slot replaces the outgoing player). For box-score
            # purposes mark with entry_type="PR".
            in_id  = event.get("in_id")
            out_id = event.get("out_id")
            in_name = event.get("in_name", "")
            if in_id is not None:
                stats_obj = self._batter_stats.get(in_id) or BatterStats(
                    player_id=str(in_id), name=in_name
                )
                stats_obj.entry_type = "PR"
                if out_id is not None:
                    stats_obj.replaced_player_id = str(out_id)
                self._batter_stats[in_id] = stats_obj

        elif etype == "joker_to_field":
            # Rare: a joker is moved from the bench (DH-pool) into a
            # fielding slot to replace a fielder (injury, leverage). The
            # joker now occupies that fielding position for the rest of
            # the game; the team's joker-pool count drops by 1. Mark the
            # joker with entry_type="joker_field" so the box score lists
            # them with the position they took, separately from the
            # tactical-PH joker pool.
            joker_id = event.get("joker_id")
            joker_name = event.get("joker_name", "")
            out_id = event.get("out_id")
            if joker_id is not None:
                stats_obj = self._batter_stats.get(joker_id) or BatterStats(
                    player_id=str(joker_id), name=joker_name
                )
                stats_obj.entry_type = "joker_field"
                if out_id is not None:
                    stats_obj.replaced_player_id = str(out_id)
                self._batter_stats[joker_id] = stats_obj

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
                    # Stay: 1 PA. At-bat MAY end if strikes hits 3 — engine
                    # tracks that. The hit / RBI credit happens here. AB
                    # increment only happens when the AB actually ends
                    # (signaled by strikes == 3 in the post-event count).
                    s.pa += 1
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
                    # sequence terminates the AB without a batter-out).
                    if state_after.count.strikes >= 3:
                        s.ab += 1
                        _check_multi_hit(terminal_hit=disp.get("stay_hit_credited", False))
                    # Otherwise AB CONTINUES — do NOT check multi_hit_abs yet.
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

        # Task #49: universal leftover-out charge. Any out the engine recorded
        # for this event that the per-event branches above didn't already
        # credit (CS, successful pickoff, FC runner out, DP runner-trail out,
        # runner thrown out on a stay, etc.) is charged to the current batter
        # so the per-batter OR column sums to 27 per half.
        engine_outs_delta = (state_after.outs or 0) - (ctx.get("outs") or 0)
        already_charged = s.outs_recorded - _or_before
        leftover = engine_outs_delta - already_charged
        if leftover > 0:
            s.outs_recorded += leftover

        # Credit runs-scored (R) to the players who left the bases.
        if runs_scored > 0:
            self._credit_runs(ctx, state_after, runs_scored, etype, disp)

    def _credit_runs(self, ctx: dict, state_after, runs_scored: int,
                     etype: str, disp: dict) -> None:
        """
        Approximately credit the 'R' stat to runners who scored.
        Identifies player_ids that were on base before the event and are no
        longer on base after (they either scored or were put out).  We prefer
        runners furthest along (3B → 2B → 1B) since they're most likely to
        have scored rather than been retired.
        """
        bases_before = ctx["bases_list"]
        bases_after = list(state_after.bases)

        # Collect player_ids that left the bases (3B → 2B → 1B order).
        after_set = {pid for pid in bases_after if pid is not None}
        left_ids: list[str] = []
        for i in (2, 1, 0):
            pid = bases_before[i]
            if pid is not None and pid not in after_set:
                left_ids.append(pid)

        # If batter hit a HR, they score too.
        hit_type = disp.get("hit_type", "")
        if etype == "ball_in_play" and hit_type in ("hr", "home_run"):
            batter_pid = ctx["batter"].player_id
            if batter_pid not in left_ids:
                left_ids.append(batter_pid)

        # Credit the first `runs_scored` departing players with a run.
        for pid in left_ids[:runs_scored]:
            if pid in self._batter_stats:
                self._batter_stats[pid].runs += 1

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
        prev_get = (lambda f: getattr(prev_s, f)) if prev_s else (lambda f: 0)
        for f in ("pa", "ab", "runs", "hits", "doubles", "triples", "hr",
                  "rbi", "bb", "k", "hbp", "sty", "outs_recorded",
                  "stay_rbi", "stay_hits", "multi_hit_abs",
                  "sb", "cs", "fo", "roe",
                  "po", "a", "e"):
            setattr(d, f, getattr(end_s, f) - prev_get(f))
        return d

    @staticmethod
    def _has_activity(d: BatterStats) -> bool:
        return any(getattr(d, f) for f in (
            "pa", "ab", "runs", "hits", "bb", "k", "hbp", "sty", "outs_recorded"
        ))
