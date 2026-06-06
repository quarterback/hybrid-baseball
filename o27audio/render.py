"""Stage 4 — Stitch & publish. Concatenate WAV turns into one clip.

Uses the stdlib ``wave`` module (no ffmpeg / pydub). If ffmpeg happens to be on
PATH we also emit an MP3 for convenience, but the WAV is always the source of
truth so the pipeline never hard-depends on ffmpeg.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import wave

from . import config


def _silence_frames(ms: int) -> bytes:
    n = int(config.WAV_SAMPLE_RATE * ms / 1000)
    return b"\x00\x00" * n * config.WAV_CHANNELS


def concat_wavs(segments: list[bytes], gap_ms: int | None = None) -> bytes:
    """Concatenate WAV byte-blobs (all same format) into one WAV blob, with a
    short silence between turns."""
    if not segments:
        raise ValueError("no audio segments to stitch")
    gap = config.GAP_MS if gap_ms is None else gap_ms
    out = io.BytesIO()
    writer: wave.Wave_write | None = None
    try:
        for i, seg in enumerate(segments):
            with wave.open(io.BytesIO(seg), "rb") as r:
                params = r.getparams()
                data = r.readframes(r.getnframes())
            if writer is None:
                writer = wave.open(out, "wb")
                writer.setnchannels(params.nchannels)
                writer.setsampwidth(params.sampwidth)
                writer.setframerate(params.framerate)
            if i:
                writer.writeframes(_silence_frames(gap))
            writer.writeframes(data)
    finally:
        if writer is not None:
            writer.close()
    return out.getvalue()


def wav_duration_secs(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as r:
        return r.getnframes() / float(r.getframerate())


def write_clip(wav_bytes: bytes, league: str, basename: str) -> dict[str, str | None]:
    """Write the WAV (and an MP3 if ffmpeg is available). Returns paths."""
    safe_league = "".join(c if c.isalnum() else "_" for c in (league or "default"))
    out_dir = os.path.join(config.OUT_DIR, safe_league)
    os.makedirs(out_dir, exist_ok=True)
    wav_path = os.path.join(out_dir, basename + ".wav")
    with open(wav_path, "wb") as fh:
        fh.write(wav_bytes)

    mp3_path: str | None = None
    if shutil.which("ffmpeg"):
        mp3_path = os.path.join(out_dir, basename + ".mp3")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path,
                 "-codec:a", "libmp3lame", "-qscale:a", "4", mp3_path],
                check=True,
            )
        except (subprocess.CalledProcessError, OSError):
            mp3_path = None
    return {"wav": wav_path, "mp3": mp3_path}
