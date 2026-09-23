# ADR-011: Lease heartbeats and explicit recovery observability

## Status

Accepted for EventForge v0.8.0.

## Context

EventForge has used PostgreSQL leases since v0.2. A worker claims a job with an
expiry and another worker can recover that job after the lease expires. That
protects work from a crashed process, but a handler that legitimately runs near
or beyond the lease duration could otherwise be mistaken for a dead worker.

The existing recovery path also changed job state without exposing a compact,
project-scoped view of recovery activity to the v0.7 operations dashboard.

## Decision

While a job handler is executing, the worker runs a lightweight heartbeat that
renews the lease before expiry. The heartbeat uses the existing lease token, so
it cannot renew work after ownership has already been lost.

Persist three operational fields on `jobs`:

```text
last_heartbeat_at
recovery_count
last_recovered_at
```

Every lease recovery increments `recovery_count` and records
`last_recovered_at`. Claims and heartbeat renewals update `last_heartbeat_at`.

Expose project queue state through:

```text
GET /projects/{project_id}/queue-health
```

and provide an administrator-only bounded recovery trigger:

```text
POST /operations/recover-jobs
```

The automatic worker recovery loop remains the primary mechanism; the endpoint
is an operational escape hatch and verification tool, not a second queue.

## Consequences

Long handlers are less likely to be falsely reclaimed, queue recovery becomes
auditable, and the dashboard can identify expired leases and historical
recoveries without scanning raw tables.

The design remains at-least-once for external side effects. A lease heartbeat
cannot provide distributed exactly-once delivery to remote HTTP systems.
