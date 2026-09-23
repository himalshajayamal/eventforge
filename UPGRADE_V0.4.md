# Upgrade EventForge v0.3.0 -> v0.4.0

This overlay implements FlowTrace and records the revised three-product v1
scope.

## What changes

- adds `database/migrations/0005_flowtrace.sql`
- adds `workflows`, `workflow_versions`, `workflow_runs`, `step_runs`
- links workflow step jobs into the existing PostgreSQL reliable queue
- adds workflow/version/run/step API endpoints
- adds structured conditional, transform, delay, HTTP and emit-event steps
- resolves ReplayDB `original` / `current` modes to actual workflow versions
- allows internal FlowTrace-emitted events without a webhook endpoint
- adds parent/emitting-step lineage to internally emitted events
- updates the worker to claim `RUN_WORKFLOW_STEP` jobs
- adds crash/recovery coverage for workflow steps
- updates the API version to `0.4.0`
- records the decision that EventForge v1.0 contains only HookLedger, ReplayDB
  and FlowTrace as primary products

## Apply to the existing database

From Windows Command Prompt at:

```text
C:\Users\himal\Desktop\Development
```

run:

```bat
docker compose exec -T db psql -U eventforge -d eventforge -v ON_ERROR_STOP=1 < database\migrations\0005_flowtrace.sql
docker compose up -d --build
```

A fresh empty volume continues to mount the complete migrations directory, so
PostgreSQL applies `0001` through `0005` in filename order.

## Verify

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT * FROM schema_migrations ORDER BY version;"
docker compose exec api python -m pytest -q
curl http://localhost:8000/
curl http://localhost:8000/ready
docker compose exec web npm run build
```

The v0.4 overlay contains 20 Python tests. The expected API identity is:

```json
{"name":"EventForge","version":"0.4.0","status":"flowtrace"}
```

## Manual durable-resume experiment

The already verified historical event is:

```text
8aa167d2-e692-4b67-866c-943995b67b44
```

and its project is:

```text
56cbfef3-ff55-4288-96aa-45f005cc79d1
```

Create a three-step workflow with a 30-second durable delay:

```bat
curl -u eventforge:eventforge_dev_only_admin -H "Content-Type: application/json" -d "{\"name\":\"flowtrace-resume-demo\",\"trigger\":{\"source\":\"github\",\"type\":\"push\"},\"steps\":[{\"type\":\"json_transform\",\"set\":{\"flowtrace.status\":\"started\"}},{\"type\":\"delay\",\"seconds\":30},{\"type\":\"emit_event\",\"source\":\"flowtrace\",\"event_type\":\"demo.completed\"}]}" http://localhost:8000/projects/56cbfef3-ff55-4288-96aa-45f005cc79d1/workflows
```

Explicitly schedule the historical event against the current workflow version:

```bat
curl -u eventforge:eventforge_dev_only_admin -X POST http://localhost:8000/events/8aa167d2-e692-4b67-866c-943995b67b44/workflow-runs
```

Inspect the latest run and steps:

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT id, workflow_version_id, event_id, status, context, created_at, completed_at FROM workflow_runs WHERE project_id = '56cbfef3-ff55-4288-96aa-45f005cc79d1' ORDER BY created_at DESC LIMIT 5;"
docker compose exec db psql -U eventforge -d eventforge -c "SELECT sr.workflow_run_id, sr.step_index, sr.step_type, sr.status, j.status AS job_status, j.available_at FROM step_runs sr LEFT JOIN jobs j ON j.step_run_id = sr.id WHERE sr.workflow_run_id = (SELECT id FROM workflow_runs WHERE project_id = '56cbfef3-ff55-4288-96aa-45f005cc79d1' ORDER BY created_at DESC LIMIT 1) ORDER BY sr.step_index;"
```

After the first two steps are successful and the third step job is waiting for
its future `available_at`, stop the worker:

```bat
docker compose stop worker
```

Wait until the scheduled time has passed, then restart it:

```bat
docker compose start worker
```

Inspect again:

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT id, status, context, started_at, completed_at FROM workflow_runs WHERE project_id = '56cbfef3-ff55-4288-96aa-45f005cc79d1' ORDER BY created_at DESC LIMIT 1;"
docker compose exec db psql -U eventforge -d eventforge -c "SELECT step_index, step_type, status, output, error, started_at, completed_at FROM step_runs WHERE workflow_run_id = (SELECT id FROM workflow_runs WHERE project_id = '56cbfef3-ff55-4288-96aa-45f005cc79d1' ORDER BY created_at DESC LIMIT 1) ORDER BY step_index;"
docker compose logs worker --tail=80
```

The run should end `SUCCESS`. The first two step records must remain committed
across worker downtime, and the final step must execute after restart.

## Release rule

Do not tag `v0.4.0` until migration, 20/20 tests, API/readiness, frontend build
and the durable-resume experiment pass.
