# o27audio — agentic audio companion (Stage 1)

A **separate** service that turns O27 games into a two-host radio call. It reads
the live game DB **read-only**, has Claude write a play-by-play + color script,
voices it with OpenAI TTS, and stitches the turns into one audio file. Nothing
here is baked into the sim or the core schema — produced clips live under
`o27audio/out/` and are indexed in a sidecar `manifest.db`.

This is the **Game of the Week** spike. The league "sports radio" roundup show
is the planned next stage.

## Pipeline

```
sources.py  → load_game(id)         read pbp + box + scoring events (o27v2.db, read-only)
script.py   → generate_script()     Claude → [{speaker: pbp|color, text}]  (structured outputs)
tts.py      → synth_turns()          OpenAI gpt-4o-mini-tts, two voices → WAV per turn
render.py   → concat_wavs()          stdlib `wave` stitch → one WAV (+ MP3 if ffmpeg present)
manifest.py → record()               sidecar index: paths, duration, chars, est cost
```

## Run

```bash
# DB path follows the app (active save), or pin one:
export O27V2_DB_PATH=/path/to/o27v2.db

# Keys — OpenAI key is the Fly secret named `OpenAI`; Anthropic is standard:
#   fly secrets set OpenAI=sk-...        ANTHROPIC_API_KEY=sk-ant-...

python -m o27audio.cli narrate-game 2          # full pipeline → out/<league>/game_2.wav
python -m o27audio.cli narrate-game 2 --dry-run # inspect the assembled prompt only
```

### Offline plumbing test (no keys, no network)

```bash
python -m o27audio.cli narrate-game 2 --stub-script --stub-tts
```

`--stub-script` builds a deterministic script from the data; `--stub-tts`
synthesises distinct tones per host. Lets you exercise gather → stitch → manifest
without any API access (the two hosts are different pitches so you can hear the
turn alternation).

## Stage 2 — League roundup ("sports radio")

A daily "around the league" show: the slate, division leaders, HR/RBI/K leaders,
and the transactions wire → a two-host radio rundown.

- In-app: **📻 Radio** in the Games nav → `/audio/roundup` (latest game-day; or
  `/audio/roundup?date=YYYY-MM-DD`).
- CLI: `python -m o27audio.cli roundup [--date YYYY-MM-DD] [--dry-run] [--stub-script --stub-tts]`

## Auto-generate worker

`o27audio/worker.py` runs a daemon thread (started by `manage.py runserver`) that
watches the active save and, once a sim batch settles, auto-generates audio — so
you never have to tap a button.

| `O27AUDIO_AUTOGEN` | Behavior |
|---|---|
| `roundup` (default) | One roundup per **new** game-day. Cheap (~$0.20–$0.60). |
| `full` | Also narrates that day's single most broadcast-worthy game. |
| `off` | Worker disabled (button-only). |

`O27AUDIO_AUTOGEN_INTERVAL` (default 90s) sets the poll/debounce interval. The
worker never regenerates a date and advances its watermark even on failure, so a
missing key won't hot-loop — the error shows in the manifest.

## Routes (blueprint)

```
/audio/game/<id>              player page (🎧 Listen button targets this)
/audio/game/<id>/generate     POST — start a background render
/audio/game/<id>/status       GET  — poll JSON
/audio/game/<id>/audio        GET  — the clip (range-enabled)
/audio/roundup[?date=]        player page (📻 Radio)
/audio/roundup/generate|status|audio
```

## Config (env)

| Var | Default | Purpose |
|---|---|---|
| `OpenAI` / `OPENAI_API_KEY` | — | OpenAI TTS key (Fly secret is `OpenAI`) |
| `ANTHROPIC_API_KEY` | — | Claude key for scripting |
| `O27AUDIO_SCRIPT_MODEL` | `claude-opus-4-8` | Script model (use sonnet/haiku for volume) |
| `O27AUDIO_TTS_MODEL` | `gpt-4o-mini-tts` | OpenAI TTS model (cheap baseline) |
| `O27AUDIO_VOICE_PBP` / `_COLOR` | `onyx` / `nova` | The two booth voices |
| `O27AUDIO_OUT_DIR` | `o27audio/out` | Output + manifest location |

## Cost (approximate, recorded per clip in the manifest)

~$0.50–$1 per game with this baseline (OpenAI TTS dominates; Claude is the cheap
part). The manifest stores `char_count` and `est_cost_usd` so you can reconcile
against provider dashboards before any backfill.
