"""
o27.almanac — Standalone static-site generator for O27 season data.

Reads from either a live o27v2 SQLite DB or a season-archive JSON file
and emits a self-contained Fangraphs-style stats almanac (HTML + CSS +
JS + downloadable CSV/JSON exports). No Flask runtime required to serve
the output — just open index.html or `python -m http.server` it.

Entry point: `python -m o27.almanac build --source <path> --out site/`
"""
from __future__ import annotations

__version__ = "0.1.0"
