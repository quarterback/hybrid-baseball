"""o27audio CLI — Stage 1 orchestrator.

  python -m o27audio.cli narrate-game <game_id>
  python -m o27audio.cli narrate-game <id> --stub-script --stub-tts   # offline
  python -m o27audio.cli narrate-game <id> --dry-run                  # inspect prompt

DB path follows the app: set O27V2_DB_PATH (or use the active save).
OpenAI key is read from the Fly secret `OpenAI`; Anthropic from ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import argparse

from . import config, pipeline, script as script_mod


def cmd_narrate_game(args: argparse.Namespace) -> int:
    game = pipeline.gather(args.game_id)
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

    r = pipeline.produce(
        game,
        save_key=pipeline.current_save_key(),
        stub_script=args.stub_script,
        stub_tts=args.stub_tts,
        model=args.model,
        max_pbp_chars=args.max_pbp_chars,
        gap_ms=args.gap_ms,
    )
    print(f"[script] {r['model']}: {r['n_turns']} turns")
    print(f"[tts] {'stub tones' if args.stub_tts else config.TTS_MODEL}: "
          f"{r['char_count']} chars")
    print(f"\n✓ wrote {r['wav_path']}")
    if r["mp3_path"]:
        print(f"✓ wrote {r['mp3_path']}")
    print(f"  duration ≈ {r['duration_s']:.1f}s ({r['duration_s']/60:.1f} min), "
          f"{r['n_turns']} turns, {r['char_count']} chars")
    cost_note = "approx" if not args.stub_tts else "would-be (stub)"
    print(f"  estimated cost: ${r['est_cost_usd']:.4f} ({cost_note})")
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
