# EventForge v0.2.0 upgrade — Reliable Jobs and Retries

This overlay upgrades a working v0.1.0 checkout in place. Do not create a second
EventForge repository.

## What changes

- migration `0003_reliable_jobs.sql`;
- job leases and attempt budgets;
- `job_attempts` history;
- retry wait and dead-letter state;
- exponential backoff with jitter;
- expired-lease recovery;
- PostgreSQL `FOR UPDATE SKIP LOCKED` claiming;
- separate Python worker process in Docker Compose;
- job/attempt inspection API endpoints;
- CI now applies SQL migrations before Python tests;
- API version becomes `0.2.0`.

## Apply on Windows Command Prompt

From `C:\Users\himal\Desktop\Development` extract the contents of the overlay
ZIP directly into the repository root and overwrite matching files.

Apply the migration to the existing database:

```bat
docker compose exec -T db psql -U eventforge -d eventforge -v ON_ERROR_STOP=1 < database\migrations\0003_reliable_jobs.sql
```

Rebuild and start all processes:

```bat
docker compose up -d --build
```

The Compose project should now include `db`, `api`, `worker`, and `web`.

## Verify

```bat
docker compose ps
docker compose exec db psql -U eventforge -d eventforge -c "SELECT * FROM schema_migrations ORDER BY version;"
docker compose exec api python -m pytest -q
curl http://localhost:8000/
curl http://localhost:8000/ready
docker compose logs worker --tail=50
```

Expected migration history includes `0003_reliable_jobs`, and the API root
reports version `0.2.0` with status `reliable-jobs`.

Inspect the queue and attempt history:

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT id, kind, status, attempt_count, max_attempts, available_at, lease_owner, last_error FROM jobs ORDER BY created_at DESC LIMIT 20;"
docker compose exec db psql -U eventforge -d eventforge -c "SELECT job_id, attempt_number, worker_id, outcome, started_at, finished_at, retry_at FROM job_attempts ORDER BY started_at DESC LIMIT 20;"
```

Existing `PENDING` PROCESS_EVENT jobs from v0.1 should be claimed by the worker
and normally become `SUCCESS`.

## Release checkpoint

After all tests pass:

```bat
git status
git add .
git commit -m "feat: add reliable jobs and retries for EventForge v0.2.0"
git tag -a v0.2.0 -m "EventForge v0.2.0 - Reliable Jobs and Retries"
git push origin main
git push origin v0.2.0
```

Do not use force for an ordinary forward release. If remote history was
intentionally rewritten, prefer `--force-with-lease` after inspecting the
remote state.
