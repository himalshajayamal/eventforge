# EventForge Reliability Verification — v0.8.0

This document defines the release gates for the v0.8 reliability milestone.
The automated suite is necessary but not sufficient; the manual checks verify
behavior across real Docker process boundaries.

## Gate A — isolated correctness

```bat
docker compose run --rm --build test
```

Expected on the v0.9 branch: `39 passed`.

## Gate B — recovery probe

```bat
docker compose --profile reliability run --rm --build recovery-probe
```

Expected: exit code `0`, one expired synthetic lease recovered, recovery metadata
recorded, and the temporary project removed.

## Gate C — ingress/load probe

```bat
docker compose --profile reliability run --rm --build load-probe
```

Expected: exit code `0`, all unique ingests accepted, the duplicate burst maps to
one event ID, no dead-lettered job, and the temporary project queue drains within
30 seconds.

## Gate D — worker interruption

Create or select a workflow containing a delay step, start a run, then stop the
worker after at least one step has committed:

```bat
docker compose stop worker
```

Wait until the next scheduled step is due, verify it remains durable in
PostgreSQL, then restart:

```bat
docker compose start worker
```

Expected: the persisted run resumes from the pending step; completed steps are
not re-created.

## Gate E — database restart

With the application stack running:

```bat
docker compose restart db
```

After PostgreSQL becomes healthy, verify:

```bat
curl http://localhost:8000/ready
docker compose ps
```

Expected: readiness returns to `{"status":"ready","database":"ok"}` and the
worker continues processing durable work without rebuilding state from memory.

## Gate F — queue observability

For a selected project:

```text
GET /projects/{project_id}/queue-health
```

Verify `expired_leases == 0` after steady-state recovery and inspect
`recovery_count`/`last_recovered_at` on any recovered job.

## Boundary

These checks establish durable at-least-once processing and recoverability. They
do not establish exactly-once external HTTP effects.
