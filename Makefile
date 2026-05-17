.PHONY: test-invariants test-invariants-fresh almanac-build almanac-serve

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

# The almanac is normally served live by the o27v2 web app at /almanac
# (see o27.almanac.blueprint). These targets are for standalone export
# / preview only — useful for ad-hoc snapshots.
almanac-build:
	python3 -m o27.almanac build \
		--source $${ALMANAC_SOURCE:-o27v2/o27v2.db} \
		--out    $${ALMANAC_OUT:-site}

almanac-serve:
	python3 -m o27.almanac build \
		--source $${ALMANAC_SOURCE:-o27v2/o27v2.db} \
		--out    site
	python3 -m o27.almanac serve --out site
