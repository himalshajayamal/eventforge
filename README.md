# EventForge v0.2.0 — Reliable Jobs and Retries

EventForge is a local-first developer infrastructure platform. The v0.2.0
milestone extends HookLedger with a durable PostgreSQL-backed job queue,
worker leases, attempt history, retries, exponential backoff with jitter and
dead-letter state.

## Current runtime

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
```

A webhook is acknowledged only after its event and initial job commit. The
worker then claims eligible jobs using PostgreSQL locking and records every
attempt.

## Job lifecycle

```text
PENDING
   |
   v
RUNNING
  |   \
  |    \
  v     v
SUCCESS RETRY_WAIT
          |
          v
       PENDING
          |
          v
   DEAD_LETTERED  (when the attempt budget is exhausted)
```

A RUNNING job is owned through a time-bounded lease. If the worker disappears,
another worker can recover the expired lease without allowing the stale worker
to complete the job later.

## Local start

On Windows Command Prompt:

```bat
copy .env.example .env
docker compose up -d --build
```

Open:

- Frontend: http://localhost:5173
- API: http://localhost:8000
- OpenAPI: http://localhost:8000/docs

Verify:

```bat
curl http://localhost:8000/health
curl http://localhost:8000/ready
docker compose ps
docker compose exec api python -m pytest -q
```

## Queue inspection

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT id, kind, status, attempt_count, max_attempts FROM jobs ORDER BY created_at DESC LIMIT 20;"
docker compose exec db psql -U eventforge -d eventforge -c "SELECT job_id, attempt_number, worker_id, outcome FROM job_attempts ORDER BY started_at DESC LIMIT 20;"
docker compose logs worker --tail=50
```

## Native C/C++ build under WSL

Keep Linux build artifacts on the native WSL filesystem:

```bash
cd /mnt/c/Users/himal/Desktop/Development
cmake -S . -B ~/eventforge-build -G Ninja
cmake --build ~/eventforge-build
ctest --test-dir ~/eventforge-build --output-on-failure
```

## Current milestone boundary

v0.2 implements reliable queue ownership and retry mechanics. It does not yet
implement workflow actions, outbound side effects, ReplayDB, the C++ gateway or
the C event store; those remain later milestones in the engineering plan.
