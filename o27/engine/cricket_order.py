"""
Cricket Batting Order — optional league rule.

A cricket nod to O27's batting order. In cricket the order is not a fixed
carousel; here we approximate that churn by *flipping* the lineup at the end of
each trip through the order — the 1-9 order becomes 9-1 for the next cycle, so
the tail rotates up to the top and the openers drop to the bottom.

The flip is gated on the manager NOT having deployed a joker during the trip
just completed. Deploying a joker "locks" the order for that cycle (no flip).
That makes the rule a genuine tactical lever: a joker insertion buys you a
high-leverage pinch-hitter NOW but forfeits the order churn this cycle, while a
manager who holds his jokers lets the order keep flipping. A side that never
uses a joker sees its order invert every single trip; one that burns a joker
every cycle keeps the order it started with.

When enabled (per-league opt-in stamped on the Team by sim.py, or the global
cfg.CRICKET_BATTING_ORDER_ENABLED default surfaced on the Engine Settings
dashboard) the flip fires at every cycle boundary, in regulation, Declared
Seconds, and super-innings alike — the order persists across phases exactly as
it does today, the rule just churns it.

With the rule off (the default) advance_lineup is byte-for-byte unchanged: the
gate short-circuits before touching the lineup.

This module imports only `config` — never `state` — so it can be imported from
state.py without a circular dependency. It operates on the Team via duck typing
(getattr), so it has no hard dependency on the Team dataclass shape.
"""
from __future__ import annotations

from typing import Optional

from o27 import config as cfg


def cricket_order_on(team) -> bool:
    """True if the optional rule is active for this team's game.

    Per-game override (team.cricket_order_enabled, stamped by sim.py from the
    per-league flag) wins when set; otherwise fall back to the global config
    toggle (surfaced on the Engine Settings dashboard via engine_config). The
    two controls thus compose as "per-league opt-in OR global default" — the
    same shape as power_play_on().
    """
    override = getattr(team, "cricket_order_enabled", None)
    if override is not None:
        return bool(override)
    return bool(getattr(cfg, "CRICKET_BATTING_ORDER_ENABLED", False))


def maybe_invert_on_cycle(team) -> Optional[str]:
    """Flip the batting order at a cycle boundary, if the rule fires.

    Call this from Team.advance_lineup at the moment the lineup wraps to the
    top of the order, BEFORE jokers_used_this_cycle is reset — that set is the
    record of whether a joker was deployed during the trip just completed.

    Returns a play-by-play log line when the order was inverted (so callers can
    surface it), or None when nothing changed (rule off, a joker locked the
    order, or there is no lineup to flip).
    """
    if not cricket_order_on(team):
        return None
    # A joker deployed during this trip locks the order — no flip this cycle.
    if getattr(team, "jokers_used_this_cycle", None):
        return None
    lineup = getattr(team, "lineup", None)
    if not lineup or len(lineup) < 2:
        return None
    lineup.reverse()
    new_leadoff = getattr(lineup[0], "name", "?")
    return f"  Cricket order flips (joker-free trip) — {new_leadoff} now leads off."
