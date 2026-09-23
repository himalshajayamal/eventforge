# Upgrade EventForge v0.2.0 -> v0.3.0

This overlay implements the ReplayDB milestone.

## What changes

- adds `database/migrations/0004_replaydb.sql`
- adds immutable `replay_executions` records
- allows repeated replay jobs for one historical event
- adds `replay_execution_id` to jobs
- adds `POST /events/{event_id}/replays`
- adds replay history/query endpoints
- records `workflow_version_mode` as `original` or `current`
- extends the worker to process `REPLAY_EVENT` jobs
- keeps source events and payloads unchanged in PostgreSQL
- updates the API version to `0.3.0`

## Apply on the existing local database

From Windows Command Prompt at:

```text
C:\Users\himal\Desktop\Development
```

run:

```bat
docker compose exec -T db psql -U eventforge -d eventforge -v ON_ERROR_STOP=1 < database\migrations\0004_replaydb.sql
docker compose up -d --build
```

The Compose database initialization already mounts the complete migrations
directory, so a fresh empty PostgreSQL volume will apply `0001` through `0004`
in filename order.

## Verify

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT * FROM schema_migrations ORDER BY version;"
docker compose exec api python -m pytest -q
curl http://localhost:8000/
curl http://localhost:8000/ready
docker compose exec web npm run build
```

Expected API identity:

```json
{"name":"EventForge","version":"0.3.0","status":"replaydb"}
```

## Manual replay using the existing v0.1 demonstration event

The previously verified logical event is:

```text
8aa167d2-e692-4b67-866c-943995b67b44
```

Create an execution that records the original-workflow-version choice:

```bat
curl -u eventforge:eventforge_dev_only_admin -H "Content-Type: application/json" -d "{\"workflow_version_mode\":\"original\"}" http://localhost:8000/events/8aa167d2-e692-4b67-866c-943995b67b44/replays
```

Create another independent execution using the current-workflow-version choice:

```bat
curl -u eventforge:eventforge_dev_only_admin -H "Content-Type: application/json" -d "{\"workflow_version_mode\":\"current\"}" http://localhost:8000/events/8aa167d2-e692-4b67-866c-943995b67b44/replays
```

Inspect both executions:

```bat
curl -u eventforge:eventforge_dev_only_admin http://localhost:8000/events/8aa167d2-e692-4b67-866c-943995b67b44/replays
```

Verify the original logical event still exists exactly once:

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT id, source, type, provider_delivery_id, received_at FROM events WHERE id = '8aa167d2-e692-4b67-866c-943995b67b44';"
```

Inspect replay jobs and worker results:

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT r.id, r.replay_of, r.workflow_version_mode, j.id AS job_id, j.kind, j.status, j.attempt_count FROM replay_executions r JOIN jobs j ON j.replay_execution_id = r.id ORDER BY r.created_at DESC LIMIT 20;"
docker compose logs worker --tail=50
```

## Milestone boundary

The engineering plan says the user can choose original versus current workflow
version. Because workflow/version tables are introduced in the following
FlowTrace milestone, v0.3 persists this choice but does not resolve it to a
workflow row yet. This keeps the implementation aligned with the development
order instead of adding v0.4 concepts early.
