# EventForge v0.4.0 — FlowTrace

EventForge is a local-first developer infrastructure platform. v0.4.0 adds the
third v1 product module: **FlowTrace**, durable structured automation workflows.

## v1.0 product scope

EventForge v1.0 is intentionally limited to three user-facing products:

```text
HookLedger  -> receive/store events
ReplayDB    -> replay historical events
FlowTrace   -> execute durable workflows
```

The additional project ideas in the original engineering report are deferred
until after v1.0. The remaining 0.x releases after FlowTrace focus on hardening
these three products instead of adding more product modules.

## FlowTrace persistence model

```text
workflow
   |
   v
workflow_version     immutable definition
   |
   v
workflow_run         exact event + exact version
   |
   v
step_run             durable state for each step
   |
   v
PostgreSQL job       lease / retry / recovery
```

A successful step is committed before the next step becomes runnable. A worker
restart therefore resumes from durable step state rather than rebuilding the
workflow from memory.

## Initial structured step types

```text
conditional
json_transform
delay
http_request
emit_event
```

Arbitrary user Python or JavaScript is intentionally not supported.

`delay` is durable: the worker does not sleep. It completes the delay step and
creates the next step job with a future `available_at` timestamp.

`emit_event` creates another immutable EventForge event with a `parent_event_id`
and an `emitted_by_step_run_id`, then queues normal `PROCESS_EVENT` handling.

HTTP actions are HTTPS-only by default, reject private/local target addresses,
disable redirects, bound timeout/response size and send the step-run UUID as an
`Idempotency-Key`. This is not a claim of production-complete SSRF protection;
additional destination/DNS hardening remains part of the pre-v1 security work.

## ReplayDB integration

ReplayDB now resolves its persisted mode to actual workflow versions:

- `original` -> versions that ran for the original event.
- `current` -> current active matching versions.

## Main API additions

```text
POST /projects/{project_id}/workflows
GET  /projects/{project_id}/workflows
GET  /workflows/{workflow_id}
GET  /workflows/{workflow_id}/versions
POST /workflows/{workflow_id}/versions
POST /workflows/{workflow_id}/versions/{version_number}/activate

GET  /projects/{project_id}/workflow-runs
GET  /workflow-runs/{run_id}
GET  /workflow-runs/{run_id}/steps
POST /events/{event_id}/workflow-runs
```

The last endpoint is an explicit/manual scheduling hook that is useful for local
engineering and historical-event testing. Normal new webhook events are
scheduled by the worker's `PROCESS_EVENT` path.

## Runtime

```text
HookLedger event
      |
      v
PROCESS_EVENT job
      |
      v
matching active workflow version
      |
      v
workflow_run
      |
      v
RUN_WORKFLOW_STEP jobs
      |
      v
step_run history
```

## Local verification

```bat
docker compose exec api python -m pytest -q
curl http://localhost:8000/
curl http://localhost:8000/ready
docker compose exec web npm run build
```

Expected API identity:

```json
{"name":"EventForge","version":"0.4.0","status":"flowtrace"}
```

## Milestone boundary

v0.4 establishes durable workflow/version/run/step semantics, structured action
execution, replay version resolution and worker restart recovery. It is still a
local-first engineering milestone. Production authentication, destination
registration, stronger SSRF/DNS controls, polished frontend workflows and cloud
release hardening remain later pre-v1 work.
