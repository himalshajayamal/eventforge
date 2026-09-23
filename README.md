# EventForge v0.3.0 — ReplayDB

EventForge is a local-first developer infrastructure platform. The v0.3.0
milestone adds ReplayDB: a historical event can create a new replay execution
without changing the original event or payload.

## Replay model

```text
original event ABC
      |
      | immutable historical record
      v
replay request
      |
      v
replay execution XYZ
      |
      +-- replay_of = ABC
      +-- workflow_version_mode = original | current
      +-- REPLAY_EVENT job
```

Each replay gets a distinct `replay_executions.id` and its own reliable job.
Repeated replays of the same source event are therefore independent executions.
The source `events` row and `event_payloads` row are not cloned or rewritten.

The engineering plan distinguishes replaying with the original workflow version
from replaying with the current workflow version. v0.3 records that choice as
`workflow_version_mode`. Workflow definitions and workflow versions do not exist
until the next milestone, so v0.3 deliberately does not invent them early.

Payloads remain in PostgreSQL in v0.3. The custom C event store is a later
milestone.

## API

Create a replay:

```text
POST /events/{event_id}/replays
```

Body:

```json
{
  "workflow_version_mode": "original"
}
```

The other valid mode is `current`.

Inspect replay history:

```text
GET /events/{event_id}/replays
GET /projects/{project_id}/replays
GET /replays/{replay_id}
```

Control-plane replay endpoints use the same Basic authentication as the existing
project/event administration endpoints.

## Runtime

```text
Browser / webhook sender
        |
        v
React/Vite :5173     FastAPI :8000
                         |
                         v
                   PostgreSQL :5432
                         ^
                         |
                    Python worker
                         |
             PROCESS_EVENT / REPLAY_EVENT
```

## Local start

On Windows Command Prompt:

```bat
copy .env.example .env
docker compose up -d --build
```

Verify:

```bat
curl http://localhost:8000/health
curl http://localhost:8000/ready
docker compose ps
docker compose exec api python -m pytest -q
docker compose exec web npm run build
```

## Replay inspection

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT r.id, r.replay_of, r.workflow_version_mode, j.id AS job_id, j.status FROM replay_executions r JOIN jobs j ON j.replay_execution_id = r.id ORDER BY r.created_at DESC LIMIT 20;"
docker compose logs worker --tail=50
```

## Current milestone boundary

v0.3 implements replay execution identity, replay history, original/current
workflow-version intent, durable replay jobs and reuse of the immutable source
payload. It does not yet implement workflow definitions/actions, tracing, the
C++ gateway or the C event store.
