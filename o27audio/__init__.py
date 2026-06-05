"""o27audio — agentic audio companion for O27 (the "MLB-TV-for-the-ears" spike).

A *separate* service that reads the live O27 database read-only, has Claude
write a two-host broadcast script for a game, voices it with OpenAI TTS, and
stitches the turns into a single audio file. Nothing here is baked into the
sim or the core schema; produced clips are tracked in a sidecar manifest under
``o27audio/out/``.

Stage 1 (this spike): ``narrate-game <id>`` — Game of the Week play-by-play.

Run:  python -m o27audio.cli narrate-game <game_id>
Offline plumbing test (no keys/network):
      python -m o27audio.cli narrate-game <id> --stub-script --stub-tts
"""
