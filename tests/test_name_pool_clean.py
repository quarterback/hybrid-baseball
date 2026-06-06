"""
Guard: the committed name pools stay free of scraped junk.

The pools were seeded from scraped sports data, which dragged in club mascots,
city names, and misfiled name parts (see scripts/scrub_name_pools.py). That
produced player names like "Hyun-soo Knights" and "Red Young-pyo". The scrubber
cleans them; this test makes sure they don't creep back in on the next refresh.

The scrubber is idempotent, so running it in dry-run mode against the committed
files must report ZERO removals. If this fails, someone re-seeded a pool with
junk — re-run `python scripts/scrub_name_pools.py`.
"""
from __future__ import annotations

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_scrubber():
    path = os.path.join(_ROOT, "scripts", "scrub_name_pools.py")
    spec = importlib.util.spec_from_file_location("scrub_name_pools", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_name_pools_have_no_residual_junk():
    scrub = _load_scrubber()
    report = scrub.scrub(dry_run=True)
    leftover = {
        pool: buckets for pool, buckets in report.items() if buckets
    }
    assert not leftover, (
        "Name pools still contain scraped junk — re-run "
        "scripts/scrub_name_pools.py. Offenders: "
        + "; ".join(
            f"{pool}.{key}={vals}"
            for pool, buckets in leftover.items()
            for key, vals in buckets.items()
        )
    )
