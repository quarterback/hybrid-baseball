.PHONY: test-invariants test-invariants-fresh almanac almanac-serve

# Run the o27v2 stat-invariant suite against the default DB
# (o27v2/o27v2.db). Override the target via env var:
#   O27V2_DB_PATH=/path/to/other.db make test-invariants
test-invariants:
	python3 -m pytest tests/test_stat_invariants.py -v

# Same, but scoped to a comma-separated list of game ids — useful for
# verifying the harness against a freshly-simulated subset without
# re-simming the entire historical backlog. Example:
#   O27V2_INVARIANTS_GAMES=1391,1362 make test-invariants-fresh
test-invariants-fresh:
	python3 -m pytest tests/test_stat_invariants.py -v

# Build the static stats almanac into o27v2/web/static/almanac/ so the
# running web app serves it at /static/almanac/index.html (linked from
# the topbar nav). Re-run after each sim batch to refresh the data.
#   ALMANAC_SOURCE=/path/to/other.db make almanac
almanac:
	python3 -m o27.almanac build \
		--source $${ALMANAC_SOURCE:-o27v2/o27v2.db} \
		--out    o27v2/web/static/almanac

# Build + serve the almanac standalone on a local port for preview.
almanac-serve:
	python3 -m o27.almanac build \
		--source $${ALMANAC_SOURCE:-o27v2/o27v2.db} \
		--out    site
	python3 -m o27.almanac serve --out site
