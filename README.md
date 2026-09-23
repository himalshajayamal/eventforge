# EventForge v0.9.0 — Public Deployment Release Candidate

EventForge v1.0 remains intentionally limited to three user-facing products:

```text
HookLedger  -> receive and durably process webhooks/events
ReplayDB    -> replay immutable historical events
FlowTrace   -> execute durable structured workflows
```

v0.9.0 does not add a fourth product. It packages the v0.8 reliability-hardened
system for a real public release-candidate deployment.

## v0.9 additions

- production-only Docker Compose topology;
- Caddy HTTPS/reverse-proxy edge;
- static React production build served by Nginx;
- same-origin browser control API under `/api`;
- production secret generator that does not print secrets;
- provider-independent production smoke test;
- on-demand PostgreSQL backup profile;
- public release-candidate gates and ADR.

The frozen v0.8 reliability baseline includes the lease-clock freshness fix and
39 backend tests.

## Development mode

```bat
docker compose up -d --build
docker compose run --rm --build test
```

Expected: `39 passed`.

Development stays on `http://localhost:5173` (web) and
`http://localhost:8000` (API).

## Local production-mode staging

```bat
docker run --rm -v "%cd%:/repo" -w /repo python:3.14.7-slim python tools/generate_production_env.py --host http://localhost
docker compose --env-file .env.production -f compose.production.yaml config > NUL
docker compose --env-file .env.production -f compose.production.yaml up -d --build
docker run --rm -v "%cd%:/repo" -w /repo python:3.14.7-slim python tools/production_smoke.py --base-url http://host.docker.internal:8080

The smoke tool automatically sends `Host: localhost` for this local Docker-to-Windows path so Caddy matches the local staging site.
```

## Public paths

```text
https://eventforge.example.com/           dashboard
https://eventforge.example.com/api/       control API
https://eventforge.example.com/ingest     webhook ingress
https://eventforge.example.com/ready      readiness
https://eventforge.example.com/docs       API docs
```

Only the edge proxy should publish host ports.

## Release documentation

Read `DEPLOYMENT.md`, `RELEASE_CANDIDATE.md`, `UPGRADE_V0.9.md`,
`RELIABILITY.md`, `SECURITY.md`, and ADR-012.

Do not tag v0.9.0 until local production-mode validation, public HTTPS smoke,
and a signed public webhook delivery all pass.

## API identity

```json
{"name":"EventForge","version":"0.9.0","status":"release-candidate"}
```

## Boundaries

Production forces signed webhooks and strict public-only HTTPS targets for
FlowTrace HTTP actions. v0.9 still has one administrator Basic credential, not
multi-user IAM/RBAC. External HTTP effects remain at-least-once.
