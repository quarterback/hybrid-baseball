"""
O27 entry point — delegates to the o27 package.

Usage:
    python main.py [--seed N] [--output FILE]
    pnpm run o27

Runs `python -m o27.main` which treats o27 as a proper package
and avoids any sys.path workarounds.
"""

import subprocess
import sys

if __name__ == "__main__":
    sys.exit(subprocess.call(
        [sys.executable, "-m", "o27.main"] + sys.argv[1:]
    ))
