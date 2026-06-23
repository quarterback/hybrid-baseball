# AAR — Cascading pitching: no one goes the distance

## The problem

O27's continuous 27-out half is structurally punishing to a single arm, but the
manager was still riding starters toward complete games. Two mechanisms caused
it:

1. **The pull decision was batters-faced only.** `should_change_pitcher` pulled
   on `pitcher_spell_count` against role thresholds whose targets were
   explicitly pre-1980s / Japanese-usage deep (workhorse ≈ 32 BF ≈ 22 outs).
2. **A guard actively *forced* a tiring starter to stay in.**
   `RELIEVER_ENTRY_OUTS_MIN = 18` blocked any starter pull before out 18 unless
   the pitcher had already given up 8+ runs — so a workhorse who wasn't being
   shelled was *required* to ride toward a complete game.

Ron's earlier commits (`Add pitch-count stamina fatigue` /
`Lower pitch-count fatigue threshold`, PRs #274/#275) added a pitch-count
fatigue *ramp* — a pitcher past his stamina budget pitches worse — but nothing
consumed that signal for the *pull*. So a gassed starter just threw badly
instead of being taken out.

Measured baseline (60-game sim, the model as it stood on `main`):

| | mean | median | max |
|---|---|---|---|
| Starter pitches | 53 | 51 | 114 |
| Starter outs | 9.9 | 9 | 27 |
| Pitchers / team-game | 3.8 | 3.5 | 10 |

14% of starts still reached 18+ outs; ~2% were 27-out complete games.

## The shift

The brief: not a hard pitch cap, but the stamina/fatigue/skill model
interacting with ratings to decide *emergently* how long a pitcher lasts — and
a fundamental move to **cascading pitchers** (no complete games), with starts
landing around **half** of where they were.

What changed:

- **Shared the fatigue signal.** Extracted `prob.pitch_fatigue_level(pitcher,
  pitch_count, weather)` — the exact ramp the outcome model already applied,
  refactored so `_pitch_probs` calls it (behaviour-preserving) and the manager
  can read it. Stamina sets the budget; grit and a low release slow accrual;
  weather speeds it. Emergent from ratings, not a flat cap.
- **Fatigue-reactive hook.** `should_change_pitcher` now pulls any arm whose
  fatigue crosses `PULL_FATIGUE_TOLERANCE + skill·PULL_FATIGUE_SKILL_LEASH`,
  regardless of batters faced — so a long-count / high-stress spell ends early,
  and a better arm earns a slightly longer leash.
- **Killed the forced complete game.** The `RELIEVER_ENTRY_OUTS_MIN` guard is no
  longer consulted; the workhorse branch now pulls on its threshold like every
  other role. (Constant kept for legacy import/save compatibility.)
- **Halved the BF thresholds.** Role tiers (stamina → workhorse/classical/
  opener, skill → leash) are retained but retuned down: workhorse 28+8·skill →
  6+4·skill, classical 10+20 → 4+6, opener 7+3 → 4+2, reliever 12+6 → 6+3.

## Validation

80-game headless sim after the change (`o27v2 initdb` + `sim 80`):

| | before | after |
|---|---|---|
| Starter pitches (mean) | 53 | **29** |
| Starter outs (mean / max) | 9.9 / 27 | **5.3 / 10** |
| Reliever outs (mean) | 5.2 | 4.0 |
| Pitchers / team-game (mean) | 3.8 | **5.9** |
| Starts ≥ 18 outs | 14% | **0%** |
| Complete games | ~2% | **0%** |

Starter workload is ~half its old self, the half is now a 6-arm relay, and no
pitcher goes the distance — exactly the target. Total runs/game (~25, both
teams) is unchanged from the pre-existing O27 environment: pulling tiring arms
*sooner* means fresher pitchers, so offense did not inflate.

Tests: `o27/tests` (178) green, `tests/test_stat_invariants.py` (12) green, plus
a new `o27/tests/test_pitch_count_pull.py` (5) covering the fatigue hook
(gassed-but-low-BF pull, fresh arm stays, high- vs low-stamina divergence on
the same pitch count, workhorse no longer extended, and
`pitch_fatigue_level` monotonicity). The pitch-count *fatigue* refactor is
covered by the existing `test_pitch_count_fatigue.py` (unchanged, still green).

## What I did NOT change

- **The fatigue budget / outcome ramp** (`PITCH_FATIGUE_*`) is untouched — that
  governs how a pitcher *degrades*, which Ron just tuned. The pull leans on it
  as a signal but doesn't move it, so league scoring stays put.
- **Cross-game rotation / rest** (`o27v2/rotation.py`). Staffs carry 19+ arms,
  so ~6 pitchers/game is sustainable across a 162-game season without exhausting
  bullpens; I did not touch availability or rest logic.
- **Stat definitions.** ⚠️ One follow-up: `Workhorse Start %` (WS%, ≥18 outs)
  is now effectively unreachable and will read ~0 league-wide. It should be
  rescaled (e.g. a "long start" at ≥12 outs) or retired, but I left the public
  leaderboard stat alone pending a call on the new threshold.
