# EventForge v0.9 Production Deployment

v0.9 packages the existing HookLedger + ReplayDB + FlowTrace system for a
single-node public release candidate. It does not add a fourth product.

## Production topology

```text
Internet
   |
   v
Caddy 2.11.4
  |-- /, frontend assets ----------> Nginx 1.30.5 + React build
  |-- /api/* ----------------------> FastAPI
  |-- /ingest and /ingest/* ------> FastAPI
  |-- /health and /ready ---------> FastAPI
                                     |
                                     +---- PostgreSQL 18.6
                                     |
Worker ------------------------------+
  |
  +---- outbound HTTPS actions only
```

Only Caddy publishes host ports. PostgreSQL, the API container, the worker, and
the static web container remain Docker-internal.

## Generate production secrets

Do not hand-copy development credentials into production.

Local production-mode staging from Windows CMD:

```bat
docker run --rm -v "%cd%:/repo" -w /repo python:3.14.7-slim python tools/generate_production_env.py --host http://localhost
```

The command creates `.env.production`, does not print generated secrets, and
uses host ports 8080/8443 for local staging.

For a public DNS name on the Linux deployment host:

```sh
python3 tools/generate_production_env.py --host eventforge.example.com
```

The public form uses ports 80/443 so Caddy can obtain and renew HTTPS
certificates automatically.

`.env.production` is gitignored. Back it up as a secret outside the repository.

## Local production-mode release gate

```bat
docker compose --env-file .env.production -f compose.production.yaml config > NUL
docker compose --env-file .env.production -f compose.production.yaml up -d --build
docker compose --env-file .env.production -f compose.production.yaml ps
curl http://localhost:8080/api/
curl http://localhost:8080/ready
```

Smoke-test through the same edge routing used by a public deployment:

```bat
docker run --rm -v "%cd%:/repo" -w /repo python:3.14.7-slim python tools/production_smoke.py --base-url http://host.docker.internal:8080

The smoke tool automatically sends `Host: localhost` for this local Docker-to-Windows path so Caddy matches the local staging site.
```

Expected API identity:

```json
{"name":"EventForge","version":"0.9.0","status":"release-candidate"}
```

## Public host requirements

Use a Linux host with Docker Engine + Compose v2, persistent disk, DNS pointing
at the host, inbound TCP 80 and TCP/UDP 443, plus outbound DNS/HTTPS.

Do not publish ports 5432, 8000, or the web container's port directly.

```sh
docker compose --env-file .env.production -f compose.production.yaml up -d --build
docker compose --env-file .env.production -f compose.production.yaml ps
python3 tools/production_smoke.py --base-url https://eventforge.example.com
```

Public control-plane URLs live under `/api`. Webhook ingress intentionally keeps
its historical public paths:

```text
POST https://eventforge.example.com/ingest
POST https://eventforge.example.com/ingest/{legacy-token}
```

## Backups

```sh
mkdir -p backups
chmod 700 backups
docker compose --env-file .env.production -f compose.production.yaml --profile ops run --rm backup
```

Copy backups off the deployment host. A backup only on the database host is not
a disaster-recovery copy.

## Upgrade and rollback boundary

v0.9 adds no database migration. The production stack still includes migrations
0001 through 0008 for a new database.

Before every deployment: create an off-host backup, record the current tag,
deploy, run the smoke test, and inspect logs/queue health.

Application rollback is a Git/image rollback. Database rollback is not implied;
restore a verified backup only when a schema/data rollback is actually required.

## Security boundary

Production mode refuses known development secrets. Production Compose forces
signed webhooks, disables private/insecure FlowTrace HTTP targets, and exposes
only the edge proxy.

v0.9 still uses one administrator Basic credential rather than multi-user
IAM/RBAC. External HTTP workflow actions remain at-least-once.
