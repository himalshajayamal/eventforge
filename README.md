# EventForge v0.8.0 — Reliability and Recovery Hardening

EventForge v1.0 remains limited to the same three user-facing products:

```text
HookLedger  -> receive and persist events
ReplayDB    -> replay immutable historical events
FlowTrace   -> execute durable structured workflows
```

v0.8.0 does **not** add a fourth product. It hardens the shared PostgreSQL job
runtime against worker crashes, lease expiry, retry/recovery races, and
concurrent duplicate ingress.

## Reliability additions

### Worker lease heartbeat

A claimed job now records `last_heartbeat_at`. While its handler is running, the
worker periodically renews the same lease token. Defaults:

```text
lease:      30 seconds
heartbeat:  10 seconds
```

The interval is configurable with `EVENTFORGE_JOB_HEARTBEAT_SECONDS` and must be
shorter than the lease duration.

### Recovery audit metadata

Jobs now retain:

```text
recovery_count
last_recovered_at
```

Expired leases still follow the existing attempt budget: they return to the
retry path when attempts remain or become dead-lettered when the budget is
exhausted.

### Queue health projection

```text
GET /projects/{project_id}/queue-health
```

returns project-scoped counts for pending, running, retry-wait, success,
dead-lettered, expired leases, recovered jobs, total recoveries, the newest
running heartbeat, and the age of the oldest actionable job.

The v0.7 Overview dashboard now includes this reliability state.

### Explicit recovery pass

Administrators can trigger one bounded pass with:

```text
POST /operations/recover-jobs
```

It recovers expired leases and promotes due retries. Project API keys cannot use
the global operations route.

## Failure and load validation

The canonical isolated test command is:

```bat
docker compose run --rm --build test
```

v0.8 contains 38 API tests. New coverage includes lease renewal, recovery audit
metadata, queue-health authorization, administrator recovery, and eight-way
concurrent duplicate ingress proving one immutable event and one
`PROCESS_EVENT` job are created for one delivery identity.

Two temporary-project probes are also available:

```bat
docker compose --profile reliability run --rm --build recovery-probe
docker compose --profile reliability run --rm --build load-probe
```

The load probe defaults to 200 unique ingests at concurrency 20 plus a
20-request duplicate burst, then waits for the queue to drain. Both probes
delete their temporary project before exiting.

## API identity

```json
{"name":"EventForge","version":"0.8.0","status":"reliability-hardened"}
```

## Delivery guarantee boundary

The internal queue is durable and lease-based, but EventForge still does not
claim exactly-once external side effects. A remote HTTP action can succeed just
before a worker crashes and before local success is committed. Integrations
should use destination-side idempotency where supported.

## Milestone boundary

v0.8 is reliability hardening for HookLedger, ReplayDB, and FlowTrace only.
Deployment packaging, public hosting, production secrets, reverse proxy/TLS,
and the release-candidate process remain v0.9 work.
