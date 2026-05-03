# Deploying

The production deployment lives on Fly.io as the **`hybrid-baseball`** app
(not `o27`, even though `fly.toml` still has `app = "o27"` for historical
reasons — all `flyctl` commands must pass `-a hybrid-baseball`).

## Required volume

The app mounts a persistent SQLite volume at `/data`. The volume must be
named **`o27v2_data`** and live in region **`ams`** (the only region this
single-machine app runs in). Fly does not auto-create volumes — without it,
deploys fail with:

```
machine in group 'app' needs an unattached volume named 'o27v2_data' in region 'ams'
```

If the volume is ever missing (e.g. fresh app, or it was destroyed), recreate
it before the next deploy:

```
flyctl volume create o27v2_data -r ams -n 1 -a hybrid-baseball
```

The default 1 GB size is plenty — the live SQLite DB is on the order of
~250 KB after a week of sims. Increase via `--size N` (GB) only if needed.

## Deploying

```
flyctl deploy -a hybrid-baseball \
  --image registry.fly.io/hybrid-baseball:deployment-<sha> \
  --depot-scope=app \
  --config fly.toml
```

`fly.toml` already wires:
- `O27V2_DB_PATH=/data/o27v2.db`
- `[[mounts]] source = "o27v2_data" destination = "/data"`
- `min_machines_running = 1` (no idle stop/start cycling)
- `/api/health` http check

After deploy, sanity-check:
```
flyctl status   -a hybrid-baseball
flyctl volumes list -a hybrid-baseball   # o27v2_data should be ATTACHED
curl https://hybrid-baseball.fly.dev/api/health   # → {"status":"ok"}
```
