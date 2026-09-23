# Upgrade to EventForge v0.7.0

v0.7.0 adds the unified React operations dashboard and two read-only API
projections used by that dashboard. There is **no database migration** in this
milestone.

## Upgrade steps

From the repository root:

```bat
docker compose up -d --build
```

Verify the API:

```bat
curl http://localhost:8000/
curl http://localhost:8000/ready
```

Expected identity:

```json
{"name":"EventForge","version":"0.7.0","status":"unified-dashboard"}
```

Run the isolated backend suite with an explicit image rebuild:

```bat
docker compose run --rm --build test
```

Expected result:

```text
32 passed
```

Then build the browser application:

```bat
docker compose exec web npm run build
```

Open:

```text
http://localhost:5173
```

Administrator development bootstrap defaults remain controlled by the existing
`EVENTFORGE_ADMIN_USER` and `EVENTFORGE_ADMIN_PASSWORD` environment variables.
The frontend does not persist the entered credentials.

## No migration

Do not rerun `0007_security.sql`. v0.7.0 introduces no `0008` migration because
its backend additions are read-only queries over the existing v0.6 schema.
