# EventForge v0.1.0 HookLedger update

Copy the files in this update into the existing EventForge repository root, preserving paths.

Then, from Windows Command Prompt at `C:\Users\himal\Desktop\Development`:

```bat
docker compose exec -T db psql -U eventforge -d eventforge -v ON_ERROR_STOP=1 < database\migrations\0002_hookledger.sql
docker compose up -d --build
docker compose exec api python -m pytest -q
curl http://localhost:8000/
curl http://localhost:8000/ready
```

Expected API version: `0.1.0`.

Create a test project using local HTTP Basic authentication:

```bat
curl -u eventforge:eventforge_dev_only_admin -H "Content-Type: application/json" -d "{\"name\":\"Local Lab\"}" http://localhost:8000/projects
```

Use the returned project ID to create a webhook endpoint:

```bat
curl -u eventforge:eventforge_dev_only_admin -H "Content-Type: application/json" -d "{\"name\":\"github-main\",\"source\":\"github\"}" http://localhost:8000/projects/PROJECT_ID/webhook-endpoints
```

The endpoint creation response returns `endpoint_token` once. Keep it for the next command.

First delivery:

```bat
curl -i -X POST -H "Content-Type: application/json" -H "X-GitHub-Event: push" -H "X-GitHub-Delivery: demo-delivery-001" -d "{\"hello\":\"eventforge\"}" http://localhost:8000/ingest/ENDPOINT_TOKEN
```

Repeat the exact command. Both responses should be HTTP 202; the second response should contain `"duplicate":true` and the same `event_id`.

Inspect the event history:

```bat
curl -u eventforge:eventforge_dev_only_admin http://localhost:8000/projects/PROJECT_ID/events
```
