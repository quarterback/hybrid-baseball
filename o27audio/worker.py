"""Auto-generate worker — narrates the league without anyone tapping a button.

A daemon thread that watches the active save for newly-played games and, once a
sim batch has settled, generates the **league roundup** for the latest game-day
(and, in ``full`` mode, the single Game of the Week for that day).

Cost-aware by design:
  - Mode via ``O27AUDIO_AUTOGEN``:  ``roundup`` (default) | ``full`` | ``off``.
  - One roundup per *new* game-day (not per game); never regenerates a date.
  - ``full`` adds exactly one game per day (the most broadcast-worthy one).
  - A failed render (e.g. missing key) advances the watermark so it doesn't
    hot-loop — the error is visible in the manifest.

Started from manage.py's runserver (so it only runs when actually serving).
"""
from __future__ import annotations

import os
import threading
import time

from . import config, manifest, pipeline, sources


def _mode() -> str:
    return (os.environ.get("O27AUDIO_AUTOGEN", "roundup") or "").strip().lower()


def _interval() -> int:
    try:
        return max(20, int(os.environ.get("O27AUDIO_AUTOGEN_INTERVAL", "90")))
    except ValueError:
        return 90


_started = False
_guard = threading.Lock()


def start() -> bool:
    """Start the worker if enabled. Idempotent; returns True if it launched."""
    global _started
    mode = _mode()
    if mode in ("", "off", "0", "false", "none"):
        return False
    with _guard:
        if _started:
            return False
        _started = True
    threading.Thread(target=_loop, args=(mode,), name="o27audio-autogen",
                     daemon=True).start()
    return True


def _loop(mode: str) -> None:
    seen: dict[str, str] = {}   # save_key -> latest date observed last tick
    done: dict[str, str] = {}   # save_key -> latest date already handled
    interval = _interval()
    while True:
        try:
            _tick(mode, seen, done)
        except Exception:
            pass
        time.sleep(interval)


def _tick(mode: str, seen: dict[str, str], done: dict[str, str]) -> None:
    save_key = pipeline.current_save_key()
    latest = sources.latest_played_date()
    if not latest:
        return

    prev = seen.get(save_key)
    seen[save_key] = latest
    # Debounce: only act once the latest game-day is stable across one full
    # interval — i.e. a sim batch has finished landing.
    if latest != prev:
        return
    if done.get(save_key) == latest:
        return

    ref = f"{save_key}:{latest}"
    if (manifest.get("roundup", ref) or {}).get("status") == "ok":
        done[save_key] = latest
        return

    # Roundup for the latest game-day.
    try:
        rd = pipeline.gather_roundup(latest)
        pipeline.produce_roundup(rd, save_key=save_key)
    except Exception:
        pass  # failure is recorded in the manifest; advance anyway

    # full mode: also narrate the single most broadcast-worthy game.
    if mode == "full":
        gid = sources.pick_game_of_the_day(latest)
        if gid is not None:
            gref = f"{save_key}:{gid}"
            if (manifest.get("game", gref) or {}).get("status") != "ok":
                try:
                    game = pipeline.gather(gid)
                    pipeline.produce(game, save_key=save_key)
                except Exception:
                    pass

    # Daily radio is ephemeral: once the new game-day's clips exist, drop every
    # earlier day's audio for this save so the /data/audio dir doesn't grow
    # without bound across a season. Best-effort — never let it break the loop.
    try:
        _purge_old_days(save_key, latest)
    except Exception:
        pass

    done[save_key] = latest


def _purge_old_days(save_key: str, keep_date: str) -> None:
    """Delete generated audio (files + manifest rows) for every game-day other
    than ``keep_date`` within this save. Roundup refs carry the date directly;
    game refs are mapped to their game_date via the save's DB. Clips whose date
    can't be resolved are left alone (fail-safe — we never delete blindly)."""
    for row in manifest.list_for_save(save_key):
        ref = row["ref_id"]
        tail = ref.rsplit(":", 1)[-1]
        if row["kind"] == "roundup":
            clip_date = tail
        else:  # 'game' — resolve game_id -> its game_date
            try:
                clip_date = sources.load_game(int(tail)).game_date
            except Exception:
                clip_date = None
        if clip_date is not None and clip_date != keep_date:
            manifest.delete_clip(row["kind"], ref)
