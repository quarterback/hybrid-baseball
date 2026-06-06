"""Configuration for o27audio — all env-driven, matching the repo convention.

Secrets come from environment variables (on Fly, set via `fly secrets set`):

  - OpenAI key:    env ``OpenAI`` (the name used in this project's Fly secrets),
                   falling back to the conventional ``OPENAI_API_KEY``.
  - Anthropic key: env ``ANTHROPIC_API_KEY`` (or ``ANTHROPIC``).

Nothing here imports the SDKs — keep this module import-safe in bare sandboxes.
"""
from __future__ import annotations

import os

# --- API keys -------------------------------------------------------------
def _first_env(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def openai_key() -> str | None:
    """The OpenAI key. This project stores the Fly secret as ``OpenAI``."""
    return _first_env("OpenAI", "OPENAI_API_KEY", "OPENAI", "openai")


def anthropic_key() -> str | None:
    """The Anthropic key. Accepts the conventional names plus the project's
    capitalized style (e.g. ``Anthropic``, mirroring the ``OpenAI`` secret)."""
    return _first_env(
        "ANTHROPIC_API_KEY", "ANTHROPIC", "Anthropic", "anthropic",
        "ANTHROPIC_KEY", "CLAUDE_API_KEY", "Claude",
    )


# --- Models ---------------------------------------------------------------
# Flagship Game of the Week → Opus for the best broadcast voice. Override via
# env for the high-volume roundup show (e.g. claude-sonnet-4-6 / haiku).
SCRIPT_MODEL = os.environ.get("O27AUDIO_SCRIPT_MODEL", "claude-opus-4-8")

# OpenAI TTS. gpt-4o-mini-tts is the cheap baseline and supports per-voice
# tone steering via `instructions` (perfect for a two-host booth).
TTS_MODEL = os.environ.get("O27AUDIO_TTS_MODEL", "gpt-4o-mini-tts")

# --- The booth: two distinct voices ---------------------------------------
# OpenAI voice names. pbp = energetic play-by-play; color = analytical foil.
VOICE_PBP = os.environ.get("O27AUDIO_VOICE_PBP", "onyx")
VOICE_COLOR = os.environ.get("O27AUDIO_VOICE_COLOR", "nova")

# Tone steering passed to gpt-4o-mini-tts per speaker.
VOICE_INSTRUCTIONS = {
    "pbp": (
        "You are a high-energy baseball play-by-play announcer calling the action "
        "live. Crisp, urgent, building excitement on big moments; let scoring plays "
        "ring out. Conversational hand-offs to your broadcast partner."
    ),
    "color": (
        "You are the color commentator — a former player. Warm, wry, analytical. "
        "You react to the play-by-play, add insight and a little humor, and never "
        "rush. Relaxed cadence, lower energy than your partner."
    ),
}

# --- Cost estimation (approximate, for the manifest) ----------------------
# Per-million-character TTS rate used only to estimate cost in the manifest.
# tts-1 ≈ $15/1M chars; gpt-4o-mini-tts is roughly comparable per minute.
TTS_COST_PER_M_CHARS = float(os.environ.get("O27AUDIO_TTS_COST_PER_M_CHARS", "15.0"))
# Claude Opus 4.8 list price ($/1M tokens) for the script-cost estimate.
LLM_INPUT_COST_PER_M = float(os.environ.get("O27AUDIO_LLM_IN_COST_PER_M", "5.0"))
LLM_OUTPUT_COST_PER_M = float(os.environ.get("O27AUDIO_LLM_OUT_COST_PER_M", "25.0"))

# --- Output ---------------------------------------------------------------
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))


def _default_out_dir() -> str:
    """Where clips + the manifest live.

    Precedence: O27AUDIO_OUT_DIR, else — on Fly — alongside the saves volume
    (``<dir of O27V2_SAVES_DIR>/audio`` → ``/data/audio``) so clips survive
    restarts/redeploys, else the package-local ``o27audio/out``.
    """
    explicit = os.environ.get("O27AUDIO_OUT_DIR")
    if explicit:
        return explicit
    saves_dir = os.environ.get("O27V2_SAVES_DIR")
    if saves_dir:
        return os.path.join(os.path.dirname(os.path.abspath(saves_dir)), "audio")
    return os.path.join(_PKG_DIR, "out")


OUT_DIR = _default_out_dir()

# Audio format produced by OpenAI TTS / our stub (must match for stitching).
WAV_SAMPLE_RATE = 24000
WAV_CHANNELS = 1
WAV_SAMPLE_WIDTH = 2  # 16-bit
GAP_MS = 280  # silence inserted between dialogue turns
