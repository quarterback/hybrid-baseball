# Review Packet — O27 "Agentic Audio" Companion (`o27audio`)

**Branch / artifact:** `claude/agentic-audio-sports-app-2o2jM` · open PR #212 ·
AAR at `docs/aar-agentic-audio-game-of-the-week.md`
**Date:** 2026-06-05

---

## 1. What was your objective?
The brief started as a **feasibility question** — "is an MLB-TV-for-the-ears companion
for O27 viable, what tools, how expensive?" — and converted mid-stream into a **build**.
The settled objective: ship a separate, read-only audio service that turns O27 data into
broadcast-style audio, **usable entirely inside the deployed app from a phone** (the user
does not use a command line). Scope grew through the session to: per-game recaps, a daily
league roundup, an auto-generate worker, and a controllable broadcast voice.

## 2. What work did you complete?
- **A standalone `o27audio/` package:** `sources` (read-only DB pulls), `script` (Claude,
  structured outputs), `tts` (OpenAI, parallelized), `render` (stdlib-`wave` stitching +
  optional ffmpeg MP3), `manifest` (sidecar index), `pipeline` (shared gather/produce),
  `blueprint` (web UI), `worker` (auto-gen daemon), `cli`.
- **Three in-app surfaces:** 🎧 Game Recap button on box scores, 📻 Radio (daily roundup)
  in the nav, and a one-tap recap card on the dashboard.
- **Auto-generate worker** with cost guards (default cheap "roundup" mode, one per
  game-day, never regenerated).
- **Deploy plumbing:** ffmpeg + `openai` added to the image; clips written to the Fly
  `/data` volume; flexible API-key naming (`OpenAI`/`Anthropic` style).
- **Two refinements driven by user feedback:** parallel TTS (cut a render from ~2–4 min to
  under a minute) and a full **tone rewrite** (treat scores as normal, not novelty) plus a
  **user-editable lexicon/style editor** at `/audio/style`.
- An AAR and this review.

## 3. What decisions mattered most?
- **Pre-rendered files, not live streaming** — removed an entire class of
  cost/latency/sync complexity for negligible product loss (games are deterministic and
  already simmed).
- **Background-thread generation + status polling** — the single decision that made it
  work on a phone without request timeouts.
- **WAV stitching via the Python stdlib** — meant the pipeline had no hard ffmpeg
  dependency to *build*, so it ran and was verifiable in a bare sandbox.
- **Cost guards on the worker** (default roundup-only, one per date) — auto-generation on
  a sim-heavy app is a real money risk; this was the difference between "useful" and
  "dangerous."
- **Tone = real broadcast, score-is-never-the-story** — the decision that turned it from a
  gimmick into something that sounds like baseball. This one I got *wrong first* (see §5).

## 4. What obstacles did you encounter?
- **The sandbox has no network, no Flask, and no SDKs** — I could never actually run the
  live Claude/OpenAI calls or boot the app. I worked around it with stub modes and a
  hand-built Flask shim to drive the routes, but the live calls were validated by *the
  user*, not me.
- **Fly's ephemeral filesystem** — clips would vanish on restart; solved by writing to the
  mounted `/data` volume.
- **Long renders vs. phone request limits** — solved with the worker-thread/poll pattern.
- **Multi-league saves** — game IDs collide across leagues; solved by namespacing every
  clip by active save.

## 5. What mistakes did you make?
- **I built a CLI first, before establishing how the user actually uses the app.** They
  then told me they're phone-only and never touch a command line — a chunk of the first
  design was unusable until I pivoted to the web UI. I should have asked about the delivery
  surface before writing code.
- **The original script prompts leaned into novelty** — jokey, marveling at big scores,
  treating O27 as a curiosity. That was a genuine design miss, not a bug; it took user
  feedback to surface it, and only then did I research how real recaps sound. The right
  move was to ground the voice in real broadcast conventions *up front*.
- **I shipped "takes a minute" copy when a game actually took 2–4 minutes** — optimistic
  and slightly misleading until the user flagged the lag.
- **A refactor dropped `import sys`** (caught by my own re-read before it shipped) and I
  printed "AL AL Central" from a duplicated league/division label (caught in a dry-run).
  Minor, but both were self-inflicted.

## 6. What assumptions did you make?
- That OpenAI TTS WAV output is 24 kHz / mono / 16-bit (I matched my stub to it). **I never
  verified this against the real API** — if it differs, stitching could misbehave. The user
  ran it successfully, which is the only reason I now believe the assumption held.
- That the user would deploy by merging the PR from their phone, and that their Anthropic
  key followed the same naming style as `OpenAI`.
- That high scores are normal in O27 (correct — and central to the tone fix).
- That Opus 4.8 is the right default for script quality and that approximate per-character
  cost estimates are good enough for a manifest.

## 7. What would you do differently?
- **Ask two questions before building:** "how will you trigger and consume this?" and "what
  should it sound like?" Both reshaped the work *after* I'd already built against a wrong
  assumption.
- **Research the domain voice before writing the first prompt**, not after a complaint.
- **Verify the external API's real output format** (a single live call) rather than
  assuming it.
- **Add automated tests** (`pipeline.produce(..., stub=True)` is a cheap, durable guard)
  instead of relying entirely on manual offline runs.
- Surface latency/cost expectations honestly in the UI from the start.

## 8. What evidence supports your assessment?
- **The user's real-world confirmation:** "it works now without the lag," "the audio is
  fine, it did work, it's been a lot of fun" — the only evidence the *live* path works
  end-to-end.
- **Offline verification I can stand behind:** stub pipeline produced a valid 24 kHz/mono/
  16-bit WAV; the Flask shim drove generate→`ok`→audio-served for both game and roundup;
  the worker's tick logic (observe→debounce→generate→no-op) ran clean twice; parallel TTS
  preserved order under randomized completion; prompt assembly shows all the intended tone
  markers; templates Jinja-compile; all modules byte-compile.
- **The gap:** I have **no direct evidence** of voice quality or live API behavior — that's
  inherently the user's judgment and the deploy's.

## 9. What should a manager know that isn't obvious from the final output?
- **I never ran the two most important lines of code.** The Claude and OpenAI calls were
  validated by the user on Fly, not by me — the sandbox blocked it. Everything around them
  is tested; they are not (by me).
- **The first version's tone was wrong**, and it only got fixed because the user pushed
  back. The product's quality hinges on prompt/voice craft that is subjective and needs
  ongoing tuning — it is not "done," it's "good and steerable."
- **There's standing cost exposure.** The auto-worker spends real money per sim-day; it's
  guarded (roundup-only default, one-per-date, fails-safe on missing keys) but a
  misconfigured `O27AUDIO_AUTOGEN=full` on a heavy simmer adds up. Costs are external
  (Anthropic + OpenAI) and per-use.
- **The main tuning knob is now user-owned:** the `/audio/style` lexicon is injected into
  every render, so output quality will drift with what's typed there — that's powerful and
  also a support surface.
- **No automated tests yet**, and clips live on a single Fly volume (fine, but not backed
  up).
- Pushes occasionally report "[new branch]" — that's the ephemeral git proxy reconnecting,
  not lost history; PR #212 carries the full, intact chain.
