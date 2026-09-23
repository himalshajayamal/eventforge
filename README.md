# EventForge v0.5.0 — Integrated Core

EventForge v1.0 remains intentionally limited to three user-facing products:

```text
HookLedger  -> receive and persist events
ReplayDB    -> replay immutable historical events
FlowTrace   -> execute durable structured workflows
```

v0.5.0 does **not** add a fourth product. It hardens the links between those
three products and the development/test process around them.

## Integrated event path

```text
Webhook
  |
  v
HookLedger event
  |
  v
PROCESS_EVENT job
  |
  v
FlowTrace workflow version
  |
  v
workflow_run / step_runs
  |
  +---- emit_event ----> new immutable EventForge event
  |
  v
ReplayDB replay request
  |
  v
frozen workflow-version selection
  |
  v
replay workflow_run
```

New webhook events already flow through the normal `PROCESS_EVENT` worker path.
v0.5 adds explicit integration tests for this path instead of testing each
module only in isolation.

## Deterministic replay selection

A replay now snapshots the exact FlowTrace workflow versions it means to run
**when the replay request is created**.

```text
original
  -> versions that processed the historical event

current
  -> active matching versions at replay-request time
```

Those selections are stored in:

```text
replay_workflow_selections
```

This closes a race from v0.4 where a workflow could be activated after a replay
was requested but before its worker job was claimed, silently changing the
meaning of `current`.

Pre-v0.5 replay rows remain readable. If an older replay has no selection
snapshot, the scheduler retains the previous v0.4 resolution behavior.

## Cross-product event view

v0.5 adds:

```text
GET /events/{event_id}/integration
```

The response connects one event to its jobs, FlowTrace runs, ReplayDB replays,
and FlowTrace-emitted child events.

Replay snapshots can be inspected through:

```text
GET /replays/{replay_id}/workflow-selections
```

## Isolated integration tests

The live development worker must not mutate the same queue rows that pytest is
asserting against. v0.5 therefore adds a dedicated Compose test service using a
separate PostgreSQL database:

```text
eventforge        -> normal development database
eventforge_test   -> disposable pytest database
```

Run the complete suite with:

```bat
docker compose run --rm test
```

The test runner:

1. recreates `eventforge_test`,
2. applies every migration in order,
3. sets `DATABASE_URL` only for the pytest subprocess,
4. runs the suite,
5. leaves the live `eventforge` database and worker untouched.

This is now the canonical Python test command for v0.5 and later local work.

## API identity

```json
{"name":"EventForge","version":"0.5.0","status":"integrated-core"}
```

## Main v0.5 additions

```text
database/migrations/0006_integration.sql
apps/api/tests/run_isolated.py
GET /events/{event_id}/integration
GET /replays/{replay_id}/workflow-selections
replay workflow-version snapshots
cross-product integration tests
```

## Milestone boundary

v0.5 is an integration milestone. Authentication remains the development Basic
Auth model, HTTP-action destination controls are still pre-v1 hardening work,
and the frontend is not yet the unified v1 experience.

The next milestone should continue hardening these same three products rather
than add another product module.
