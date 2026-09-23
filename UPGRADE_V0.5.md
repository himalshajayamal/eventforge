# Upgrade to EventForge v0.5.0

v0.5.0 integrates HookLedger, ReplayDB and FlowTrace more tightly and introduces
an isolated PostgreSQL test database.

## 1. Apply migration 0006 once

From the repository root in Windows Command Prompt:

```bat
docker compose exec -T db psql -U eventforge -d eventforge -v ON_ERROR_STOP=1 < database\migrations\0006_integration.sql
```

Do not rerun earlier migrations manually.

## 2. Rebuild the application images

```bat
docker compose up -d --build
```

Expected API identity:

```json
{"name":"EventForge","version":"0.5.0","status":"integrated-core"}
```

## 3. Use the isolated test service

```bat
docker compose run --rm test
```

This creates/recreates `eventforge_test`, applies all migrations and runs pytest
without sharing queue state with the normal worker.

## 4. Verify migration state

```bat
docker compose exec db psql -U eventforge -d eventforge -c "SELECT * FROM schema_migrations ORDER BY version;"
```

Expected latest row:

```text
0006_integration
```

## 5. Verify runtime

```bat
curl http://localhost:8000/
curl http://localhost:8000/ready
docker compose exec web npm run build
docker compose ps
```

## Replay behavior change

New replay requests now freeze the selected workflow versions at request time.
Existing replay rows created before v0.5 have no snapshot rows and continue to
use the previous resolution behavior for compatibility.
