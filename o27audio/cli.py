"""o27audio CLI — Stage 1 orchestrator.

  python -m o27audio.cli narrate-game <game_id>
  python -m o27audio.cli narrate-game <id> --stub-script --stub-tts   # offline
  python -m o27audio.cli narrate-game <id> --dry-run                  # inspect prompt

DB path follows the app: set O27V2_DB_PATH (or use the active save).
OpenAI key is read from the Fly secret `OpenAI`; Anthropic from ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import argparse
import sys

from . import config, manifest, render, script as script_mod, tts
from .sources import load_game


def _estimate_cost(char_count: int, usage: dict | None) -> float:
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


def cmd_narrate_game(args: argparse.Namespace) -> int:
    game = load_game(args.game_id)
    label = (
        f"{game.away['name']} {game.away_score} @ "
        f"{game.home['name']} {game.home_score}"
    )
    print(f"Game {game.game_id}: {label}  ({game.game_date})")

    if args.dry_run:
        system, user_text = script_mod.build_messages(game, args.max_pbp_chars)
        print(f"\n[dry-run] system prompt: {len(system)} chars")
        print(f"[dry-run] user payload : {len(user_text)} chars "
              f"(pbp {len(game.pbp_text)} chars, capped at {args.max_pbp_chars})")
        print(f"[dry-run] scoring events: {len(game.scoring_events)}, "
              f"batting stars: {len(game.batting_stars)}")
        print("\n----- user payload (first 1200 chars) -----\n")
        print(user_text[:1200])
        return 0

    # Stage 2 — script
    usage = None
    if args.stub_script:
        turns = script_mod.stub_script(game)
        model = "stub"
        print(f"[script] stub: {len(turns)} turns")
    else:
        turns, usage = script_mod.generate_script(game, args.model, args.max_pbp_chars)
        model = args.model or config.SCRIPT_MODEL
        print(f"[script] {model}: {len(turns)} turns, "
              f"in={usage['input_tokens']} out={usage['output_tokens']} tokens")
    if not turns:
        print("No turns produced — aborting.", file=sys.stderr)
        return 1

    # Stage 3 — voice
    segments, char_count = tts.synth_turns(turns, stub=args.stub_tts)
    print(f"[tts] {'stub tones' if args.stub_tts else config.TTS_MODEL}: "
          f"{len(segments)} segments, {char_count} chars")

    # Stage 4 — stitch & publish
    wav_bytes = render.concat_wavs(segments, args.gap_ms)
    duration = render.wav_duration_secs(wav_bytes)
    paths = render.write_clip(wav_bytes, game.home["league"], f"game_{game.game_id}")

    est_cost = _estimate_cost(char_count, usage)
    manifest.record(
        "game", str(game.game_id),
        league=game.home["league"], wav_path=paths["wav"], mp3_path=paths["mp3"],
        duration_s=round(duration, 1), n_turns=len(turns), char_count=char_count,
        est_cost_usd=est_cost, model=model, status="ok", script=turns,
    )

    print(f"\n✓ wrote {paths['wav']}")
    if paths["mp3"]:
        print(f"✓ wrote {paths['mp3']}")
    print(f"  duration ≈ {duration:.1f}s ({duration/60:.1f} min), "
          f"{len(turns)} turns, {char_count} chars")
    cost_note = "approx" if not args.stub_tts else "would-be (stub)"
    print(f"  estimated cost: ${est_cost:.4f} ({cost_note})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="o27audio", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("narrate-game", help="Render a two-host call for one game")
    p.add_argument("game_id", type=int)
    p.add_argument("--model", default=None, help="Claude model (default: Opus 4.8)")
    p.add_argument("--max-pbp-chars", type=int, default=60000,
                   help="Cap on play-by-play chars sent to the model")
    p.add_argument("--gap-ms", type=int, default=None, help="Silence between turns")
    p.add_argument("--stub-script", action="store_true",
                   help="Skip Claude; build a deterministic script from data")
    p.add_argument("--stub-tts", action="store_true",
                   help="Skip OpenAI; synthesise local tones (offline test)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show the assembled prompt and exit (no API calls)")
    p.set_defaults(func=cmd_narrate_game)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
