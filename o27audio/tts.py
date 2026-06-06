"""Stage 3 — Voice. Render each dialogue turn to WAV via OpenAI TTS.

WAV (not MP3) so the stitch stage can use the Python stdlib ``wave`` module and
we depend on neither ffmpeg nor pydub. ``stub=True`` synthesises a local tone
instead of calling OpenAI, so the pipeline runs fully offline.
"""
from __future__ import annotations

import io
import math
import struct
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config

Turn = dict[str, str]


def _voice_for(speaker: str) -> str:
    return config.VOICE_COLOR if speaker == "color" else config.VOICE_PBP


def _synth_openai(text: str, speaker: str, client) -> bytes:
    kwargs = dict(
        model=config.TTS_MODEL,
        voice=_voice_for(speaker),
        input=text,
        response_format="wav",
    )
    # `instructions` (tone steering) is supported on gpt-4o-* TTS models only.
    if config.TTS_MODEL.startswith("gpt-4o"):
        kwargs["instructions"] = config.VOICE_INSTRUCTIONS.get(speaker, "")
    resp = client.audio.speech.create(**kwargs)
    # openai>=1.x returns a binary response with `.content` (and `.read()`).
    return getattr(resp, "content", None) or resp.read()


def _synth_stub(text: str, speaker: str) -> bytes:
    """A speaker-distinct tone, length ~proportional to the text. No network."""
    sr = config.WAV_SAMPLE_RATE
    secs = min(8.0, max(0.6, len(text) * 0.045))
    freq = 180.0 if speaker == "pbp" else 130.0  # distinct pitch per host
    n = int(sr * secs)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(config.WAV_CHANNELS)
        w.setsampwidth(config.WAV_SAMPLE_WIDTH)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            # gentle amplitude envelope so turns don't click
            env = min(1.0, i / (sr * 0.05), (n - i) / (sr * 0.05))
            val = int(9000 * env * math.sin(2 * math.pi * freq * (i / sr)))
            frames += struct.pack("<h", val)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def synth_turns(turns: list[Turn], stub: bool = False) -> tuple[list[bytes], int]:
    """Synthesise every turn. Returns ``(wav_segments, total_chars)`` with
    segments in turn order. Real TTS calls run concurrently (they're
    independent) — the main render-speed lever."""
    total_chars = sum(len(t["text"]) for t in turns)
    if stub:
        return [_synth_stub(t["text"], t["speaker"]) for t in turns], total_chars

    key = config.openai_key()
    if not key:
        raise RuntimeError(
            "No OpenAI key. This project stores it as the Fly secret 'OpenAI' "
            "(env var `OpenAI`). Use --stub-tts to test the pipeline offline."
        )
    try:
        from openai import OpenAI  # lazy import
    except ImportError as e:
        raise RuntimeError(
            "openai SDK not installed — run "
            "`pip install -r o27audio/requirements.txt` "
            "(or use --stub-tts for an offline test)."
        ) from e
    client = OpenAI(api_key=key)

    workers = min(config.TTS_CONCURRENCY, len(turns)) or 1
    if workers == 1:
        segments = [_synth_openai(t["text"], t["speaker"], client) for t in turns]
        return segments, total_chars

    # Parallel: submit all turns, place each result at its index so the stitched
    # order matches the script. The OpenAI client is thread-safe.
    slots: list[bytes | None] = [None] * len(turns)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_synth_openai, t["text"], t["speaker"], client): i
            for i, t in enumerate(turns)
        }
        for fut in as_completed(futures):
            slots[futures[fut]] = fut.result()  # raises on first failure
    return [s for s in slots if s is not None], total_chars
