#!/usr/bin/env python3
"""PreToolUse guard: block edits to the dead legacy web app `o27/web/`.

`o27/web/` is the old Flask app — it is never served (Dockerfile / .replit run
`o27v2/manage.py`) and never imported by `o27v2`. Web routes, templates, and
UI features belong in `o27v2/web/`. The rest of `o27/` (engine, render, stats,
almanac, gazette, config) is the SHARED CORE and is intentionally editable —
this guard only fires on the `o27/web/` subtree.

Exit code 2 + a message on stderr tells Claude Code to block the call and
shows the message to the model so it redirects to `o27v2/web/`.
"""
import json
import sys


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0  # Never break tool use on a parse hiccup.

    tool_input = data.get("tool_input") or {}
    # Edit/Write/MultiEdit all carry the target as file_path.
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    norm = "/" + str(path).replace("\\", "/").lstrip("/")

    if "/o27/web/" in norm:
        sys.stderr.write(
            "BLOCKED: o27/web/ is the DEAD legacy Flask app. It is never served "
            "(Dockerfile and .replit run o27v2/manage.py) and never imported by "
            "o27v2.\n"
            "→ Put web routes, templates, and UI features in o27v2/web/ instead.\n"
            "(The shared engine/render/stats under o27/engine, o27/render, "
            "o27/stats ARE editable — only o27/web/ is off-limits. See CLAUDE.md.)\n"
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
