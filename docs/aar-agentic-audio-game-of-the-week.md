# After-Action Report — Agentic Audio: in-app "Game of the Week" broadcasts

**Date completed:** 2026-06-05
**Branch:** `claude/agentic-audio-sports-app-2o2jM` (PR #212)
**Status:** **Stage 1 shipped** — a working, in-app two-host audio broadcast of
any played O27 game. Built as a separate `o27audio/` service wired into the live
web app. Live API calls (Claude + OpenAI TTS) are first exercised on deploy;
everything around them is verified offline.

---

## TL;DR

The ask: an "MLB-TV-for-the-ears" companion — audio-only content narrating the
leagues. Two flavors wanted: (1) a **Game of the Week** play-by-play call, and
(2) a league **"sports radio" roundup**. It must run as a *separate instance*,
not be baked into the sim, and — critically, surfaced mid-project — must be
usable **entirely from the app UI on a phone, with no command line.**

Three findings:

1. **It's plumbing, not research.** O27 games are seed-deterministic and already
   persist everything needed: `game_pbp.pbp_text` (full pitch-by-pitch, ~52k
   chars/game, ending in a box score), plus `game_pa_log`, box stats, and
   `game_scoring_events`. The audio app **never simulates** — it reads existing
   data → Claude writes a script → OpenAI voices it → stitch to one clip.

2. **Cost is modest and TTS-dominated.** ~**$0.50–$1 per game** on the cheap
   baseline (OpenAI `gpt-4o-mini-tts`); Claude is the small part (~$0.15–$0.50).
   No GPUs, no always-on infra. The dominant lever is the TTS provider, not the
   LLM.

3. **Shipped Stage 1 (Game of the Week) end-to-end as an in-app button.** A
   "🎧 Listen" button on each game page generates the broadcast on a background
   thread and plays it in the browser. The roundup show is deferred.

---

## What was asked for

> "technical feasibility of building a separate app … agentic audio … sports
> radio narrating what happened across the leagues … game-of-the-week style
> play-by-play … what tools to use … how overly expensive or trivial it is …
> wired in but a separate instance."

Then, decisively, mid-build:

> "I don't use the command line for this I do everything inside the apps … I'm
> on a phone."

That second message reshaped the deliverable: a CLI was not enough; the trigger
and playback had to live in the web UI.

Confirmed product choices (via Q&A): **pre-rendered files** (not live
streaming), **two-host booth**, **both** content types (game + roundup),
**self-built** pipeline (Claude + a TTS API) over an all-in-one podcast API.

## The tools (the actual stack)

| Need | Tool | Notes |
| --- | --- | --- |
| Script | **Anthropic Claude** (`anthropic` SDK) | Opus 4.8; structured outputs → clean speaker turns; prompt caching on the system prompt |
| Voices | **OpenAI `gpt-4o-mini-tts`** (`openai` SDK) | two voice IDs = the booth; per-host tone via `instructions` |
| Stitch | stdlib **`wave`** + **ffmpeg** | WAV concat needs no ffmpeg to *build*; ffmpeg only transcodes to a phone-friendly MP3 |
| Data | existing **SQLite** via `o27v2.db` | read-only; no schema change to produce |
| Storage | Fly volume `/data/audio` + sidecar `manifest.db` | survives restarts; separate from the game DB |

## Architecture (where each piece lives)

A new, self-contained `o27audio/` package — nothing baked into the sim or core
schema:

```
sources.py    load_game(id)        read pbp + box + scoring events (o27v2.db, read-only)
script.py     generate_script()    Claude → [{speaker: pbp|color, text}] (structured outputs)
                                    + stub_script() — deterministic, offline, no keys
tts.py        synth_turns()        OpenAI gpt-4o-mini-tts, two voices → WAV/turn
                                    + offline tone stub (distinct pitch per host)
render.py     concat_wavs()        stdlib `wave` stitch → WAV (+ MP3 if ffmpeg present)
manifest.py                        sidecar index: status, paths, duration, chars, est cost
pipeline.py   gather()/produce()   fast in-request DB read + slow background render; shared by CLI + web
blueprint.py  /audio               generate (bg thread) · status (poll) · audio (range) · mobile player page
cli.py        narrate-game <id>    --dry-run / --stub-script / --stub-tts (dev only)
```

**Web touch points (additive):**
- `o27v2/web/app.py` — `register_blueprint(audio_bp)` (alongside gazette/almanac).
- `o27v2/web/templates/game.html` — the "🎧 Listen" button.
- `Dockerfile` — install `ffmpeg`; `o27/requirements.txt` — add `openai`.
- `.gitignore` — ignore generated `o27audio/out/`.

### Design decisions worth recording

- **Background thread, not synchronous request.** A full game is dozens of TTS
  calls (~a minute); rendering in-request would time out on mobile. `generate`
  spawns a daemon thread (the same pattern as the almanac cache warmer) and the
  page polls `status` until `ok`. An in-flight guard prevents double-taps.
- **Gather-in-request / produce-in-thread split.** DB reads happen synchronously
  *before* the thread starts, so switching the active league mid-render can't
  corrupt the read.
- **WAV-first stitching.** Stitching with the stdlib `wave` module avoids a hard
  ffmpeg/pydub dependency to *build* the clip; ffmpeg is used only to emit a
  smaller MP3 for delivery. OpenAI TTS and the offline stub both emit 24 kHz/
  mono/16-bit WAV, so real and stub segments concatenate identically.
- **Multi-league safe.** Clips are namespaced by the active save
  (`save_key:game_id`) in both the manifest and on disk, so game IDs from
  different leagues don't collide.
- **Persistence.** When `O27V2_SAVES_DIR` is set (Fly), clips write to
  `/data/audio` on the persistent volume; otherwise to `o27audio/out/`.

## Cost (recorded per clip in the manifest)

Per Game of the Week (~30k-char script ≈ 12–18 spoken min):
- Script: ~$0.15–$0.50 (Sonnet→Opus; Batch API halves it).
- TTS: ~$0.45–$0.50 (OpenAI). ElevenLabs would be ~$1.50 (Flash) to ~$9 (premium).
- **All-in ≈ $0.50–$1** on this baseline. A 100-game backfill ≈ $50–$100.

The manifest stores `char_count` and `est_cost_usd` per clip to reconcile
against provider dashboards before any bulk run.

## Validation (what was actually verified, and how)

This sandbox has **no network and no Flask/SDKs installed** (the app can't boot
here — a known sandbox limitation). So validation was offline:

- **Data layer.** `initdb` + `sim 5` into a throwaway DB; confirmed `load_game`
  pulls pbp (51,802 chars on the test game), 61 scoring events, 6 batting stars,
  pitching lines, and resolves team/player names. `--dry-run` shows a 56k-char
  payload assembled correctly.
- **Full pipeline, offline.** `--stub-script --stub-tts` ran gather → script →
  TTS → stitch → manifest, producing a valid **68.3s, 24 kHz mono 16-bit WAV**
  from 20 alternating turns, with a manifest row. Second game rendered the same.
- **Web flow, offline.** With a minimal in-process `flask` shim (real `jinja2`),
  drove the blueprint view functions: `none → generating →` (background thread)
  `→ ok` with duration/turns/cost, `audio()` resolved the file, the player page
  rendered (title correct), and a repeat `generate` no-ops as `ok`.
- **Safety/UX.** Missing-key and missing-SDK paths return friendly one-line
  errors; bad game IDs 404; generated audio is git-ignored; `app.py` compiles
  with the new import.

## The honest caveats

- ⚠️ **The two live API calls are unproven from here.** Claude script generation
  and OpenAI TTS could not run without network/keys. They are standard SDK calls
  exercised with stubs around them, but **first real contact is on deploy.**
  Voice quality, persona steering, and the script's handling of O27-specific
  mechanics (Second Chance, Walk-Back, Super-Innings) need a real listen and
  likely prompt tuning.
- ⚠️ **Requires an `ANTHROPIC_API_KEY` Fly secret.** Only `OpenAI` was set; the
  scripting step errors without the Claude key (surfaced on the page, not a crash).
- ⚠️ **Latency is real.** First render of a game is ~a minute; the UI says
  "Calling the game…" and polls. Subsequent plays are instant (cached on volume).
- ⚠️ **Cost-estimate is approximate.** TTS cost is estimated from characters at a
  configurable per-million rate (`gpt-4o-mini-tts` is token-priced); reconcile
  against the dashboard before trusting it for a backfill.
- ⚠️ **`max_pbp_chars` cap (60k).** Most games fit; unusually long games are
  truncated before the box-score tail. Fine for a highlights call, but a very
  long game loses its final innings of detail unless the cap is raised.

## What I did NOT do

- Did **not** build the league **roundup "sports radio" show** (Stage 2) — only
  Game of the Week shipped.
- Did **not** add an **auto-generate worker** (narrate new games automatically);
  generation is tap-to-start. The polling/worker scaffolding is in place to add it.
- Did **not** run the live Claude/OpenAI calls (no sandbox network) — voice
  quality and prompt tuning are the first post-deploy task.
- Did **not** modify the sim, engine, or core schema. The audio service is a
  read-only consumer with its own sidecar manifest.
- Did **not** add an automated test under `tests/` — validation was manual
  (offline harness). A small test that exercises `pipeline.produce(..., stub=True)`
  would be a cheap follow-up.

## Recommended next steps

1. **Deploy + listen.** Set `ANTHROPIC_API_KEY`, merge #212, tap Listen on a real
   game. Tune the two voices and the booth personas from the first sample.
2. **A/B the TTS provider.** The provider sits behind one interface; compare
   OpenAI vs ElevenLabs on a clip and pick by ear vs cost.
3. **Auto-generate worker.** Daemon that renders each newly-played game and the
   periodic roundup; Batch API for backfills.
4. **Stage 2 — roundup show.** `roundup --since` over standings/leaders/
   `transactions`, same script→TTS→stitch path.
