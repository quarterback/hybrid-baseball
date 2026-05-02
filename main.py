"""
O27 entry point — forwards to the game engine in o27/main.py.

Usage:
    python main.py [--seed N] [--output FILE]
"""

import subprocess
import sys

if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "o27/main.py"] + sys.argv[1:]))
