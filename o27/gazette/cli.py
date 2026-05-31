"""o27.gazette.cli — run the Gazette standalone.

Examples:
    # Print the ready-to-paste prompt for the most recent slate (default voice)
    python -m o27.gazette

    # A specific date, in a specific writer's voice
    python -m o27.gazette 2026-04-17 --voice stathead

    # Dump the raw structured payload (to edit by hand, then feed back in)
    python -m o27.gazette 2026-04-17 --json > slate.json

    # Re-render a prompt from a hand-edited payload file (no DB needed)
    python -m o27.gazette --from-file slate.json --voice scribe

    # Point at a specific save DB, and list the available voices
    python -m o27.gazette --db /data/saves/abc123.db
    python -m o27.gazette --list-voices
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _load_payload(args) -> dict:
    """Either read a hand-edited payload file, or serialize fresh from the DB."""
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as fh:
            return json.load(fh)
    # DB-backed path: honour --db before importing anything that touches db.
    if args.db:
        os.environ["O27V2_DB_PATH"] = args.db
    from . import serialize
    slate_date = args.date or serialize.latest_slate_date()
    if not slate_date:
        sys.exit("No played games found — sim a day first, or pass --from-file.")
    return serialize.build_daily_payload(slate_date)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m o27.gazette",
        description="The O27 Gazette — structured slate + voiced LLM prompt.",
    )
    p.add_argument("date", nargs="?", default=None,
                   help="Slate date YYYY-MM-DD (default: most recent played).")
    p.add_argument("--voice", default=None,
                   help="Writer voice id (see --list-voices).")
    p.add_argument("--json", action="store_true",
                   help="Emit the raw Game Context payload instead of a prompt.")
    p.add_argument("--from-file", metavar="PATH", default=None,
                   help="Load a (possibly hand-edited) payload JSON instead of "
                        "querying the DB.")
    p.add_argument("--db", metavar="PATH", default=None,
                   help="Path to a save's SQLite DB (sets O27V2_DB_PATH).")
    p.add_argument("--list-voices", action="store_true",
                   help="List the available writer voices and exit.")
    args = p.parse_args(argv)

    if args.list_voices:
        from .voices import all_voices, DEFAULT_VOICE_ID
        for v in all_voices():
            star = " (default)" if v.id == DEFAULT_VOICE_ID else ""
            print(f"  {v.id:<10} {v.name}{star}\n             {v.blurb}")
        return 0

    payload = _load_payload(args)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    from .prompt import build_prompt
    print(build_prompt(payload, voice=args.voice))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
