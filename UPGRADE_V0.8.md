# Upgrade to EventForge v0.8.0

v0.8.0 hardens failure, load, and recovery behavior across the existing
HookLedger + ReplayDB + FlowTrace platform. It does not add another product.

## What changes

- migration `0008_reliability.sql` adds job heartbeat/recovery metadata and
  queue-health indexes;
- workers renew live job leases while a handler is executing;
- expired-lease recovery records recovery counters/timestamps;
- `GET /projects/{project_id}/queue-health` exposes project-scoped queue health;
- `POST /operations/recover-jobs` lets an administrator trigger one bounded
  recovery/promote pass without waiting for the worker loop;
- the Overview dashboard shows queue recovery state;
- the isolated suite adds lease, recovery, authorization, and concurrent
  idempotency coverage;
- load and recovery probes can be run through the Compose `reliability` profile.

## Upgrade steps

From the repository root, apply **only** the new migration:

```bat
docker compose exec -T db psql -U eventforge -d eventforge -v ON_ERROR_STOP=1 < database\migrations\0008_reliability.sql
```

Verify migrations:

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT * FROM schema_migrations ORDER BY version;"
```

Rebuild the runtime:

```bat
docker compose up -d --build
```

Expected API identity:

```json
{"name":"EventForge","version":"0.8.0","status":"reliability-hardened"}
```

Run the isolated backend suite with an explicit rebuild:

```bat
docker compose run --rm --build test
```

Expected result:

```text
38 passed
```

Build the frontend:

```bat
docker compose exec web npm run build
```

## Reliability probes

The probes use temporary projects and delete them when they finish.

Recovery probe:

```bat
docker compose --profile reliability run --rm --build recovery-probe
```

Load/idempotency probe (defaults: 200 unique requests, 20 concurrent workers,
20 simultaneous duplicate deliveries):

```bat
docker compose --profile reliability run --rm --build load-probe
```

The load probe succeeds only if the queue drains within 30 seconds and no job
for the temporary project is dead-lettered.

## Lease settings

The default worker lease remains 30 seconds and the new heartbeat interval is
10 seconds:

```text
EVENTFORGE_JOB_LEASE_SECONDS=30
EVENTFORGE_JOB_HEARTBEAT_SECONDS=10
```

The heartbeat interval must be greater than zero and strictly less than the
lease duration.

## Delivery semantics

Lease heartbeats reduce accidental duplicate processing caused by long-running
handlers. They do **not** make external HTTP side effects exactly-once. A crash
can still occur after an external system accepts a request but before EventForge
commits local success, so integrations must continue to use idempotency where
available.
