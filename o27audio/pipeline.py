"""Shared pipeline used by both the CLI and the web blueprint.

Split into a fast synchronous **gather** (DB reads) and a slow **produce**
(Claude + TTS + stitch). The web layer does ``gather`` inside the request and
runs ``produce`` on a background thread — so a long render never blocks the
request, and switching the active league mid-render can't corrupt the read.
"""
from __future__ import annotations

from typing import Any

from . import config, manifest, render, script as script_mod, tts
from .sources import GameData, RoundupData, load_game, load_roundup


def current_save_key() -> str:
    """Namespace key for the active league/save, so game IDs from different
    saves don't collide in the manifest or on disk."""
    try:
        from o27v2 import saves
        return saves.get_active_id() or "default"
    except Exception:
        return "default"


def estimate_cost(char_count: int, usage: dict | None) -> float:
    tts_usd = char_count * config.TTS_COST_PER_M_CHARS / 1_000_000
    llm_usd = 0.0
    if usage:
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        cached = usage.get("cache_read_input_tokens", 0)
        llm_usd = (
            (inp - cached) * config.LLM_INPUT_COST_PER_M / 1_000_000
            + cached * config.LLM_INPUT_COST_PER_M * 0.1 / 1_000_000
            + out * config.LLM_OUTPUT_COST_PER_M / 1_000_000
        )
    return round(tts_usd + llm_usd, 4)


def gather(game_id: int) -> GameData:
    """Fast, synchronous DB read. Raises ValueError for missing/legacy games."""
    return load_game(game_id)


def produce(
    game: GameData,
    *,
    save_key: str,
    stub_script: bool = False,
    stub_tts: bool = False,
    model: str | None = None,
    max_pbp_chars: int = 60000,
    gap_ms: int | None = None,
) -> dict[str, Any]:
    """Run script → TTS → stitch → manifest for an already-gathered game.

    Records progress in the manifest (``begin`` before, ``record``/``fail``
    after). Returns the success dict; re-raises on failure after marking it.
    """
    ref = f"{save_key}:{game.game_id}"
    used_model = "stub" if stub_script else (model or config.SCRIPT_MODEL)
    manifest.begin("game", ref, league=save_key, model=used_model)
    try:
        # Stage 2 — script
        usage = None
        if stub_script:
            turns = script_mod.stub_script(game)
        else:
            turns, usage = script_mod.generate_script(game, model, max_pbp_chars)
        if not turns:
            raise RuntimeError("no script turns produced")

        # Stage 3 — voice
        segments, char_count = tts.synth_turns(turns, stub=stub_tts)

        # Stage 4 — stitch & publish (namespaced by save so IDs don't collide)
        wav_bytes = render.concat_wavs(segments, gap_ms)
        duration = render.wav_duration_secs(wav_bytes)
        paths = render.write_clip(wav_bytes, save_key, f"game_{game.game_id}")

        est_cost = estimate_cost(char_count, usage)
        result = {
            "ref": ref, "wav_path": paths["wav"], "mp3_path": paths["mp3"],
            "duration_s": round(duration, 1), "n_turns": len(turns),
            "char_count": char_count, "est_cost_usd": est_cost,
            "model": used_model, "usage": usage,
        }
        manifest.record(
            "game", ref, league=save_key, wav_path=paths["wav"],
            mp3_path=paths["mp3"], duration_s=result["duration_s"],
            n_turns=len(turns), char_count=char_count, est_cost_usd=est_cost,
            model=used_model, status="ok", script=turns,
        )
        return result
    except Exception as e:  # noqa: BLE001 — mark then re-raise for the caller
        manifest.fail("game", ref, f"{type(e).__name__}: {e}")
        raise


def generate_game_audio(game_id: int, **kwargs) -> dict[str, Any]:
    """Convenience: gather + produce in one call (used by the CLI)."""
    save_key = kwargs.pop("save_key", None) or current_save_key()
    game = gather(game_id)
    return produce(game, save_key=save_key, **kwargs)


# ---------------------------------------------------------------------------
# Roundup ("sports radio") — Stage 2
# ---------------------------------------------------------------------------

def gather_roundup(date: str | None = None) -> RoundupData:
    return load_roundup(date)


def produce_roundup(
    roundup: RoundupData,
    *,
    save_key: str,
    stub_script: bool = False,
    stub_tts: bool = False,
    model: str | None = None,
    gap_ms: int | None = None,
) -> dict[str, Any]:
    """Run script → TTS → stitch → manifest for a roundup show."""
    ref = f"{save_key}:{roundup.date}"
    used_model = "stub" if stub_script else (model or config.SCRIPT_MODEL)
    manifest.begin("roundup", ref, league=save_key, model=used_model)
    try:
        usage = None
        if stub_script:
            turns = script_mod.stub_roundup_script(roundup)
        else:
            turns, usage = script_mod.generate_roundup_script(roundup, model)
        if not turns:
            raise RuntimeError("no roundup turns produced")

        segments, char_count = tts.synth_turns(turns, stub=stub_tts)
        wav_bytes = render.concat_wavs(segments, gap_ms)
        duration = render.wav_duration_secs(wav_bytes)
        paths = render.write_clip(wav_bytes, save_key, f"roundup_{roundup.date}")

        est_cost = estimate_cost(char_count, usage)
        result = {
            "ref": ref, "wav_path": paths["wav"], "mp3_path": paths["mp3"],
            "duration_s": round(duration, 1), "n_turns": len(turns),
            "char_count": char_count, "est_cost_usd": est_cost,
            "model": used_model, "usage": usage,
        }
        manifest.record(
            "roundup", ref, league=save_key, wav_path=paths["wav"],
            mp3_path=paths["mp3"], duration_s=result["duration_s"],
            n_turns=len(turns), char_count=char_count, est_cost_usd=est_cost,
            model=used_model, status="ok", script=turns,
        )
        return result
    except Exception as e:  # noqa: BLE001
        manifest.fail("roundup", ref, f"{type(e).__name__}: {e}")
        raise
