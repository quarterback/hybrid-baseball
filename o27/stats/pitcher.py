"""
Pitcher stat accumulator for O27 (Phase 4).

PitcherStats aggregates per-pitcher totals across all spells in a game.
Spell-by-spell breakdown lives in state.spell_log (SpellRecord objects).
Hit, BB, K, and HBP counts are tracked by the Renderer (which sees each
event) and merged in via merge_render_stats().
"""
from dataclasses import dataclass, field


@dataclass
class PitcherStats:
    player_id: str
    name: str
    batters_faced: int = 0
    outs_recorded: int = 0
    hits_allowed: int = 0
    runs_allowed: int = 0
    bb: int = 0
    k: int = 0
    hbp: int = 0
    hr_allowed: int = 0
    pitches_thrown: int = 0
    spell_count: int = 0        # number of separate spells pitched
    max_spell: int = 0          # longest single spell (consecutive BF)

    @classmethod
    def from_spell_log(cls, spell_log: list, pitcher_id: str, name: str) -> "PitcherStats":
        """Build aggregate PitcherStats from all fields in spell_log entries."""
        s = cls(player_id=pitcher_id, name=name)
        for rec in spell_log:
            if rec.pitcher_id == pitcher_id:
                s.batters_faced  += rec.batters_faced
                s.outs_recorded  += rec.outs_recorded
                s.runs_allowed   += rec.runs_allowed
                s.hits_allowed   += rec.hits_allowed
                s.bb             += rec.bb
                s.k              += rec.k
                s.hbp            += rec.hbp
                s.hr_allowed     += getattr(rec, "hr_allowed", 0)
                s.pitches_thrown += getattr(rec, "pitches_thrown", 0)
                s.spell_count    += 1
                if rec.batters_faced > s.max_spell:
                    s.max_spell = rec.batters_faced
        return s

    def merge_render_stats(self, h: int, bb: int, k: int, hbp: int) -> None:
        """Merge H/BB/K/HBP tracked by the Renderer into this aggregate."""
        self.hits_allowed += h
        self.bb  += bb
        self.k   += k
        self.hbp += hbp
