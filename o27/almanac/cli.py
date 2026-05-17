"""
o27.almanac.cli — argparse entry points.

Subcommands:

  build   — render the static site from a data source.
  serve   — quick local preview of a built site (wraps http.server).
  ingest  — validate a season-bundle JSON file and report what it
            contains (no rendering).
"""
from __future__ import annotations

import argparse
import http.server
import os
import socketserver
import sys
import time

from . import loader, compute, render, export


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="o27.almanac",
        description="O27 stats almanac — static-site generator.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="Render the static almanac site.")
    pb.add_argument("--source", default="live",
                    help="Data source: 'live' (default — uses the o27v2 DB), "
                         "a SQLite path, or a season-bundle JSON path.")
    pb.add_argument("--out", default="site",
                    help="Output directory (default: ./site)")
    pb.add_argument("--title", default="O27 Almanac",
                    help="Site title (header brand text).")
    pb.add_argument("--subtitle", default="Hybrid Baseball — Season Stats",
                    help="Header subtitle (right of the brand).")

    ps = sub.add_parser("serve", help="Preview a built site over HTTP.")
    ps.add_argument("--out", default="site", help="Directory to serve.")
    ps.add_argument("--port", type=int, default=8765,
                    help="Local port (default 8765).")

    pi = sub.add_parser("ingest", help="Validate a data source and print a summary.")
    pi.add_argument("--source", required=True)

    args = p.parse_args(argv)

    if args.cmd == "build":
        return _cmd_build(args)
    if args.cmd == "serve":
        return _cmd_serve(args)
    if args.cmd == "ingest":
        return _cmd_ingest(args)
    p.error(f"unknown command: {args.cmd}")
    return 2


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _cmd_build(args: argparse.Namespace) -> int:
    t0 = time.time()
    print(f"[almanac] loading source: {args.source}", flush=True)
    dataset = loader.load(args.source)

    print(f"[almanac]   teams={dataset['meta']['team_count']}  "
          f"players={dataset['meta']['player_count']}  "
          f"games={dataset['meta']['game_count']}", flush=True)

    if dataset["meta"]["game_count"] == 0:
        print("[almanac] WARNING: dataset contains 0 played games. The "
              "site will render but every leaderboard will be empty.",
              file=sys.stderr)

    views = compute.compute_views(dataset)
    print(f"[almanac] computed {len(views.batting_season)} batter lines, "
          f"{len(views.pitching_season)} pitcher lines", flush=True)

    out_dir = os.path.abspath(args.out)
    print(f"[almanac] writing exports → {out_dir}/exports", flush=True)
    manifest = export.write_exports(views, dataset, out_dir)

    print(f"[almanac] rendering HTML → {out_dir}", flush=True)
    n_pages = render.render_site(
        views, dataset, out_dir,
        site_title=args.title,
        subtitle=args.subtitle,
        export_manifest=manifest,
    )

    dt = time.time() - t0
    print(f"[almanac] done: {n_pages} HTML pages, "
          f"{len(manifest)} export files, "
          f"in {dt:.2f}s", flush=True)
    print(f"[almanac] open file://{out_dir}/index.html "
          f"or `python -m o27.almanac serve --out {args.out}`",
          flush=True)
    return 0


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

def _cmd_serve(args: argparse.Namespace) -> int:
    out_dir = os.path.abspath(args.out)
    if not os.path.isdir(out_dir):
        print(f"[almanac] no such directory: {out_dir}. "
              f"Run `o27.almanac build` first.", file=sys.stderr)
        return 2

    handler_cls = http.server.SimpleHTTPRequestHandler
    os.chdir(out_dir)
    with socketserver.TCPServer(("", args.port), handler_cls) as httpd:
        print(f"[almanac] serving {out_dir} at http://localhost:{args.port}/",
              flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[almanac] stopped.", flush=True)
    return 0


# ---------------------------------------------------------------------------
# ingest (validation only)
# ---------------------------------------------------------------------------

def _cmd_ingest(args: argparse.Namespace) -> int:
    dataset = loader.load(args.source)
    meta = dataset["meta"]
    print(f"source        : {meta.get('source')}")
    print(f"source_kind   : {meta.get('source_kind')}")
    print(f"schema_version: {meta.get('schema_version')}")
    print(f"season        : {meta.get('season')}")
    print(f"teams         : {len(dataset['teams'])}")
    print(f"players       : {len(dataset['players'])}")
    print(f"games         : {len(dataset['games'])}")
    print(f"batting rows  : {len(dataset['batting'])}")
    print(f"pitching rows : {len(dataset['pitching'])}")
    print(f"awards        : {len(dataset.get('awards') or [])}")
    return 0
