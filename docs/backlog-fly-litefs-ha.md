# Backlog: High-availability on Fly via LiteFS (a second machine done right)

**Status:** Not started — parked by the owner on 2026-06-03 as "more complicated
than I thought right now."
**Goal:** Run the live app on **2+ Fly machines** for redundancy instead of the
single machine it has run on forever.
**Decision already made:** Use **LiteFS** (Fly's distributed SQLite), not a
naive machine-count bump and not (yet) a Postgres migration.

This doc exists so the next agent can pick the work up without re-discovering
everything. It records *why* the obvious approach is wrong, what the code audit
found, the concrete implementation plan, the open decisions, and the human-only
Fly steps.

---

## 1. Why you cannot just run `fly scale count 2`

The app stores **all** state as **SQLite files on a Fly volume**:

```toml
# fly.toml
[[mounts]]
  source = "o27v2_data"
  destination = "/data"

[env]
  O27V2_SAVES_DIR = "/data/saves"
```

**Fly volumes are per-machine, not shared.** A second machine gets its own,
independent volume with its own copy of the data. Fly's proxy round-robins
requests, so two machines would silently diverge:

- a season simmed (a write) lands on machine A's disk; B never sees it;
- a browser request hitting A then B shows inconsistent / "disappearing" data;
- two machines accepting writes = split-brain = data loss.

So a naive count bump makes reliability **worse**. The current `fly.toml`
comments ("Single-machine setup") already reflect that the design assumes one
writer. LiteFS is the fix: one **primary** owns writes, **replicas** stream a
read-only copy and can be promoted if the primary dies.

---

## 2. Code audit findings (already done — don't redo)

All references are to the live tree (`o27v2/`). Verified 2026-06-03.

### 2a. Data layout
- Each saved league is its own SQLite DB: `save_<id>.db` under
  `O27V2_SAVES_DIR` (default `/data/saves`). Path computed in
  `o27v2/saves.py:93-94` (`db_path_for`).
- WAL is on, so every DB has `-wal` and `-shm` sidecars
  (`o27v2/db.py:1083-1084`, set once in `init_db()`).
- Connections open in `o27v2/db.py:40-58` (`get_conn`), path resolved by
  `_resolve_path()` (`o27v2/db.py:22-37`): `O27V2_DB_PATH` override wins (tests),
  otherwise the active save's file via `saves.active_db_path()`.

### 2b. The saves registry — **this is wrinkle #1**
- `saves/registry.json` is a **plain JSON file**, not SQLite. Path:
  `o27v2/saves.py:39-40` (`_registry_path` → `<saves_dir>/registry.json`).
- Written atomically (temp + `os.replace`) in `save_registry()`
  (`o27v2/saves.py:62-69`), called from `new_save` (125), `set_active` (134),
  `rename_save` (143), `touch_save` (151), `delete_save` (178),
  `register_existing_file` (214).
- Schema: `{ active_id, saves: [{id, name, config_id, seed, created_at,
  last_played_at, filename}] }`.
- **LiteFS only replicates SQLite databases inside its FUSE mount. A plain JSON
  file will NOT replicate**, so a replica wouldn't know which saves exist or
  which is active. This must be solved (see §4).

### 2c. HTTP methods — **the good news**
- **Every** state-mutating route is declared `methods=["POST"]` or
  `["DELETE"]`. No route writes the DB or registry behind a `GET` (implicit or
  explicit). Confirmed across `o27v2/web/app.py` (saves ops ~9194-9283; the
  whole `/api/sim/*` family ~9315-9467; seasons, teams, trades, youth, college,
  worldcup, FA, economy ops).
- This means **LiteFS's built-in write-forwarding proxy works out of the box**:
  it forwards non-GET requests to the primary and serves GETs locally. No need
  to hand-instrument `fly-replay` per route.
- GET-only, no-write routes confirmed: `/api/health` (11340),
  `/api/sim/multi-season/status` (9490), `/api/history/presim/status` (9518),
  and all view routes.

### 2d. Startup writes — **this is wrinkle #2**
On boot, `o27v2/manage.py` `cmd_runserver()` (392-429) does, **before** Flask
serves:
1. one-time legacy adoption: if registry empty AND a legacy `/data/o27v2.db`
   exists → `saves.register_existing_file()` + `saves.set_active()` (both write
   `registry.json`) — `manage.py:396-415`;
2. `db.init_db()` (`manage.py:423`) which writes `PRAGMA journal_mode = WAL`
   and runs idempotent `ALTER TABLE` migrations (`o27v2/db.py:1065-1214`);
3. fresh-install seeding only if `teams` is empty (`manage.py:423-427`).

On a LiteFS **replica**, the FUSE mount is **read-only** until/unless it becomes
primary. These startup writes will fail there. Must be guarded (see §4).

### 2e. Health check
`/api/health` (`o27v2/web/app.py:11340`) returns `{"status":"ok"}`, does **not**
touch the DB — already ideal for Fly machine health checks.

---

## 3. Target architecture

```
        Fly proxy (round-robin)
        /                     \
  machine A (primary)     machine B (replica)
  own volume /var/lib/    own volume /var/lib/
  litefs (LiteFS data)    litefs (LiteFS data)
        |                       |
  litefs FUSE mount  <== stream ==  litefs FUSE mount (read-only)
  e.g. /litefs/saves/*.db          /litefs/saves/*.db
        |                       |
   gunicorn/flask          gunicorn/flask
   (writes OK)             (LiteFS proxy replays POSTs to A)
```

- App reads/writes SQLite under the **LiteFS FUSE dir** (e.g. `/litefs`), NOT
  directly on the volume. The volume holds LiteFS's *internal* data dir.
- Leader election needs a lease backend: **Fly Consul** (`fly consul attach`)
  is the standard choice.
- The **LiteFS proxy** sits in front of the app; non-GET → primary, GET → local.

---

## 4. Implementation plan

### Step A — relocate the SQLite tree under the LiteFS mount
- Decide the FUSE mount (say `/litefs`) and set `O27V2_SAVES_DIR=/litefs/saves`.
- Keep the Fly volume mounted at `/data` (or `/var/lib/litefs`) for LiteFS's
  internal `data.dir`. **Do not** point `O27V2_SAVES_DIR` at the raw volume any
  more — it must point at the FUSE mount so files replicate.
- Plan the data move for the existing live save(s): copy current
  `/data/saves/*` into the new LiteFS-managed location on the primary on first
  boot. (One-time; see migration note below.)

### Step B — solve wrinkle #1 (registry.json doesn't replicate)
Pick ONE (recommended first):
- **(Recommended) Move the registry into SQLite.** Create a tiny
  `registry.db` (or a `_registry` table in a dedicated control DB) under the
  LiteFS mount so it replicates. Rewrite `o27v2/saves.py` `load_registry` /
  `save_registry` to read/write that DB instead of JSON. Keep the same public
  API (`new_save`, `set_active`, …) so callers don't change. Add a one-time
  importer from the old `registry.json`.
- **(Alternative) Derive the registry from the directory.** LiteFS auto-creates
  replicated `save_<id>.db` files on replicas, so the list of saves can be
  recovered by scanning. But the metadata (name, active_id, timestamps) still
  needs a replicated home — so you'd still need a small SQLite store for that.
  This ends up being the same work as the recommended option; prefer the DB.

### Step C — solve wrinkle #2 (startup writes on a read-only replica)
- Gate the boot-time writes in `manage.py:cmd_runserver()` on "am I primary?".
  LiteFS exposes primacy via the `.primary` sentinel file in the FUSE dir
  (e.g. `/litefs/.primary` present ⇒ this node is NOT primary) and via the
  internal API. Run legacy adoption + seeding **only on the primary**.
- Make `db.init_db()` safe to call on a replica: skip the `PRAGMA
  journal_mode = WAL` and `ALTER TABLE` writes when the FS is read-only (the
  primary will have already applied them and they replicate). Simplest guard:
  catch the read-only error, or check primacy before writing.
- Note: LiteFS requires WAL DBs be created via LiteFS; confirm WAL handling in
  the current LiteFS version (it supports WAL, but verify sidecar streaming).

### Step D — add the LiteFS config + Dockerfile changes
- New `litefs.yml` (mount dir, data dir on the volume, Consul lease, and an
  `exec`/`proxy` block that runs the app and proxies HTTP). Sketch:
  ```yaml
  fuse:
    dir: "/litefs"
  data:
    dir: "/var/lib/litefs"
  proxy:
    addr: ":8080"          # Fly http_service points here
    target: "localhost:8081"  # gunicorn/flask listens here
    db: "..."              # primary-tracking DB if required by version
  lease:
    type: "consul"
    advertise-url: "http://${HOSTNAME}.vm.${FLY_APP_NAME}.internal:20202"
    candidate: ${FLY_REGION == PRIMARY_REGION}
    promote: true
    consul:
      url: "${FLY_CONSUL_URL}"
      key: "litefs/${FLY_APP_NAME}"
  exec:
    - cmd: "python o27v2/manage.py runserver"  # bind to :8081 (PORT)
  ```
  (Exact keys vary by LiteFS version — check current docs.)
- `Dockerfile`: copy the LiteFS binary (`COPY --from=flyio/litefs:0.5 /usr/local/bin/litefs /usr/local/bin/litefs`),
  install `fuse3`, set `ENTRYPOINT ["litefs", "mount"]`. App must now listen on
  the proxy's target port (e.g. 8081), and `http_service.internal_port` /
  LiteFS `proxy.addr` stays 8080.
- `fly.toml`: keep `min_machines_running` ≥ 1, keep `auto_stop_machines = false`
  for the primary; add the second machine (§5). Health check already fine.

### Step E — validation (see §6).

---

## 5. Human-only Fly steps (an agent in the sandbox cannot run these)

The remote sandbox has **no `fly` CLI / no Fly auth / no network to Fly**. The
owner must run, on first rollout:

```bash
# 1. Leader-election backend
fly consul attach

# 2. Deploy the LiteFS-enabled image (still 1 machine — verify it's primary)
fly deploy

# 3. Add the second machine + its own volume (same region or a 2nd region)
fly volume create o27v2_data --region iad   # creates volume for machine #2
fly scale count 2                            # or: fly machine clone <id>

# 4. Watch replication / promotion
fly logs
fly status
fly machine list
```

Notes:
- Back up the volume **before** the first LiteFS deploy
  (`fly volume snapshots ...`) — the data move (Step A) touches live save files.
- Each machine needs its **own** volume; `fly scale count` won't create the
  second volume automatically in all cases — verify, create explicitly if
  needed.
- Decide region strategy: same region (`iad`) = pure HA; second region = HA +
  read locality (but cross-region replication lag applies to fresh writes).

---

## 6. How to validate it actually works

1. On the primary, create/rename a save and sim a season.
2. Hit the replica directly (`fly machine list` → target one machine) and
   confirm the new save + standings appear (replication works, wrinkle #1
   solved).
3. From the replica, POST a sim (e.g. `/api/sim/today`) and confirm it
   succeeds — proves the LiteFS proxy forwarded the write to the primary
   (wrinkle: write-forwarding).
4. Reboot the replica and confirm it boots clean with a read-only FS (wrinkle
   #2 — no startup-write crash).
5. Stop the primary (`fly machine stop <primary>`) and confirm a replica is
   promoted and the site stays up + writable.
6. Run the engine tests locally to ensure the registry refactor didn't break
   anything: `pytest o27/tests o27v2/tests` (some o27v2 tests need flask/DB —
   environmental). The `saves.py` changes specifically should get unit coverage.

---

## 7. Open decisions for the owner / next agent

- **Registry storage:** confirm "move registry into SQLite" (recommended) vs.
  keep JSON + a separate replicated metadata DB. (§4 Step B)
- **Region strategy:** two machines in `iad`, or a second region? (§5)
- **When to cut over:** this changes the on-disk layout of live data; schedule a
  low-traffic window and snapshot first.
- **Postgres instead?** If multi-writer or heavier concurrency ever matters more
  than keeping the SQLite-per-save model, a Fly/managed Postgres migration is
  the bigger but cleaner alternative. Not chosen now.

---

## 8. Files this work will touch

- `fly.toml` — second machine, ports, mounts (volume → LiteFS data dir).
- `Dockerfile` — LiteFS binary + fuse3 + entrypoint; app listens on proxy target.
- `litefs.yml` — **new** file.
- `o27v2/saves.py` — registry → replicated SQLite (Step B); paths under FUSE.
- `o27v2/db.py` — make `init_db()` replica-safe (Step C).
- `o27v2/manage.py` — gate legacy adoption + seeding on "is primary" (Step C).

Nothing here was changed yet — this is purely a plan.
