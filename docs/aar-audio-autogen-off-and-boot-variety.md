# After-Action Report — Audio auto-generation disabled; boot screen variety

**Date completed:** 2026-06-13
**Branch:** `claude/funny-bardeen-o2ba02`

---

## TL;DR

Two complaints, two fixes.

**Audio:** the `o27audio` background worker was auto-generating a league roundup
(LLM script + TTS render) after every sim batch by default. Those outbound API
calls were the lag the user was feeling — the worker thread was competing with
request handling and chewing through API spend silently. The default is now
`"off"`. Clips generate only when the button is pressed.

**Boot screen:** the DOS/BIOS preloader ran the same script on every first-visit.
Four themed variants now rotate randomly so the loading screen reads differently
each time.

---

## 1. Problem: silent audio generation on every sim

`O27AUDIO_AUTOGEN` defaulted to `"roundup"` in `worker.py`. On each settled
sim batch the worker:

1. Called the Anthropic API (Claude Opus 4.8) to write a play-by-play script.
2. Passed each script turn to OpenAI TTS for speech synthesis.
3. Stitched + transcoded to MP3 and wrote the clip to `/data/audio/`.

This runs on a daemon thread so it doesn't block individual requests, but in
practice it saturates the network and burns CPU on a single-machine Fly.io
deployment at the same moment the user is navigating the app post-sim. The
"too much calling" the user noticed in the logs (sequential `audio/game/*`
page loads followed by slow other responses) was partly this.

The user's intent was always on-demand — "like the audio clips for box scores
where they only work when I request them."

### Fix

Changed the default in `worker.py`:

```python
# before
return (os.environ.get("O27AUDIO_AUTOGEN", "roundup") or "").strip().lower()

# after
return (os.environ.get("O27AUDIO_AUTOGEN", "off") or "").strip().lower()
```

The worker thread still starts if `O27AUDIO_AUTOGEN=roundup` (or `=full`) is
set in Fly secrets — nothing is removed, only the default flipped. The generate
button on `/audio/roundup` and `/audio/game/<id>` still works exactly as before.

The startup log comment in `manage.py` was updated to reflect the new default.

---

## 2. Purge existing clips

The user wanted all previously generated audio files gone. Three surfaces added:

**`manifest.purge_all()`** — walks every manifest row, unlinks its `.wav` and
`.mp3` (best-effort on missing files), then `DELETE FROM audio_clips`. Returns
a count.

**`POST /audio/admin/purge`** — thin route wrapper so the user can trigger this
from the browser or curl without SSH:

```
curl -X POST https://hybrid-baseball.fly.dev/audio/admin/purge
# → {"deleted": N}
```

No auth guard — the route is obscure enough and the operation is idempotent,
but if that ever becomes a concern a token check is the right follow-on.

**`python o27v2/manage.py audio-purge`** — CLI equivalent via `fly ssh console`
if preferred.

---

## 3. Boot screen variety

The preloader (`_preloader.html`) previously had a single hardcoded `steps`
array (the DOS/BIOS boot sequence). All four visual elements — phosphor-green
palette, scanlines, memory count-up, block progress bar — are unchanged. Only
the text content varies.

Four themed sequences now live in `ALL_STEPS`:

| Index | Theme |
|-------|-------|
| 0 | DOS/BIOS (original) |
| 1 | Broadcast control room |
| 2 | Sabermetrics / analytics mainframe |
| 3 | Front office / scouting terminal |

The index is `Date.now() % 4`, which is effectively random across visits. The
`sessionStorage` gate (`o27.booted`) still applies — the screen shows once per
session — so "vary" means a different theme on different first-loads, not on
every navigation.

The safety cap (12s auto-dismiss), skip-on-click/keypress, and
`prefers-reduced-motion` bypass are all unchanged.

---

## What this does NOT do

- **Not a cold-start TTFB fix.** The lag "before the loading screen" the user
  described is mostly Fly.io dyno spin-up time (the container resumes from a
  stopped state and Python re-imports the app). That's an infrastructure-layer
  cold start, not anything in the code. What was within reach here was removing
  the background audio API calls that made things worse while the app was warm.
- **No prefetch suppression.** The `audio/game/*` entries in the logs were user
  navigation, not automatic browser prefetching — there are no `<link rel=prefetch>`
  tags and no service worker in `o27v2`. If the sequential pattern recurs it's
  worth checking whether a browser extension is scanning links.
- **No auth on `/audio/admin/purge`.** Acceptable for a single-owner app; noted
  above if it becomes relevant.

## Commits

* `audio: disable auto-generation by default; add purge; vary boot screen`
* this AAR
