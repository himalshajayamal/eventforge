# Upgrade to EventForge v0.9.0

v0.9.0 is the public-deployment and release-candidate milestone for HookLedger,
ReplayDB, and FlowTrace.

## Database

There is no `0009` migration. The schema remains at `0008_reliability`.

## Identity

```json
{"name":"EventForge","version":"0.9.0","status":"release-candidate"}
```

## New production assets

- `compose.production.yaml`
- `.env.production.example`
- `infrastructure/caddy/Caddyfile`
- `apps/web/Dockerfile.prod`
- `apps/web/nginx.conf`
- `tools/generate_production_env.py`
- `tools/production_smoke.py`
- `DEPLOYMENT.md`
- `RELEASE_CANDIDATE.md`
- `docs/adr/ADR-012-single-node-production-edge.md`

The development Compose stack remains available.

## Validation order

```bat
docker compose run --rm --build test
docker compose --profile reliability run --rm --build recovery-probe
docker compose --profile reliability run --rm --build load-probe
docker compose exec web npm run build
docker run --rm -v "%cd%:/repo" -w /repo python:3.14.7-slim python tools/generate_production_env.py --host http://localhost
docker compose --env-file .env.production -f compose.production.yaml config > NUL
docker compose --env-file .env.production -f compose.production.yaml up -d --build
docker run --rm -v "%cd%:/repo" -w /repo python:3.14.7-slim python tools/production_smoke.py --base-url http://host.docker.internal:8080

The smoke tool automatically sends `Host: localhost` for this local Docker-to-Windows path so Caddy matches the local staging site.
```

Do not commit `.env.production`.

Follow `DEPLOYMENT.md` and `RELEASE_CANDIDATE.md` before creating the v0.9 tag.
