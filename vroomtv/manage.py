#!/usr/bin/env python3
import os
import sys

def runserver():
    from app import app
    port = int(os.environ.get("PORT", 6000))
    app.run(host="0.0.0.0", port=port, debug=False)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "runserver"
    if cmd == "runserver":
        runserver()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
