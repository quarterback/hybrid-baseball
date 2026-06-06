"""
Cricket Batting Order — optional league rule.

A cricket nod to O27's batting order. In cricket the order is not a fixed
carousel; here we approximate that churn by *flipping* the lineup — the 1-9
order becomes 9-1 for the next cycle, so the tail rotates up to the top and the
openers drop to the bottom.

The flip is an **earned, use-or-lose decision**, not an automatic event:

  * EARN  — completing a trip through the order WITHOUT deploying a joker earns
            one flip opportunity (Team.pending_flip, set in advance_lineup).
            It does not bank or aggregate; you get at most one pending flip.
  * USE   — at the top of the new cycle the batting manager decides whether to
            spend it (manager.should_use_flip, driven by his flip-aggression
            persona and the game situation — score and where in the out-arc he
            is). The decision is made once and the opportunity is consumed
            whether he uses it or loses it.

Because deploying a joker forfeits the chance to EARN a flip, the two levers
trade off: a flip-minded manager holds his jokers (and builds an order that
reads well in both directions); a joker-happy manager rarely earns a flip; a
situational manager weighs joker-now vs. flip-next by the score and the arc.

The flip is **regulation-only** — the use decision declines in super-innings and
Declared Seconds frames (gated in the provider).

With the rule off (the default) advance_lineup never arms pending_flip, so the
engine is byte-for-byte unchanged.

This module imports only `config` — never `state` — so it can be imported from
state.py without a circular dependency. It operates on the Team via duck typing.
"""
from __future__ import annotations

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


def can_flip(team) -> bool:
    """A flip is physically possible only with a 2+ batter lineup."""
    lineup = getattr(team, "lineup", None)
    return bool(lineup) and len(lineup) >= 2


def flip_line(team) -> str:
    """Play-by-play line describing the flip that WOULD happen, without
    mutating. After the reversal the current last batter leads off, so name
    him here (computed pre-reversal so the provider can stamp it on the event).
    """
    if not can_flip(team):
        return ""
    new_leadoff = getattr(team.lineup[-1], "name", "?")
    return f"  Cricket order flips (joker-free trip) — {new_leadoff} now leads off."


def apply_flip(team) -> None:
    """Reverse the batting order in place (1-9 -> 9-1). Called from apply_event
    when a cricket_flip event is processed, so the reversal lands before the
    next plate appearance's context is captured."""
    if can_flip(team):
        team.lineup.reverse()
