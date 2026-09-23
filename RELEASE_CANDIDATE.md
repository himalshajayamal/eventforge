# EventForge v0.9 Release-Candidate Gates

v0.9 is complete only after both local production-mode validation and a real
public deployment pass.

## Gate A — reliability baseline

```bat
docker compose run --rm --build test
docker compose --profile reliability run --rm --build recovery-probe
docker compose --profile reliability run --rm --build load-probe
```

Expected: `39 passed`; recovery without dead-lettering; load queue fully drains.

## Gate B — production configuration

```bat
docker compose --env-file .env.production -f compose.production.yaml config > NUL
```

Expected: exit code 0 and no missing-variable error.

## Gate C — production-mode local stack

```bat
docker compose --env-file .env.production -f compose.production.yaml up -d --build
docker compose --env-file .env.production -f compose.production.yaml ps
```

Expected: db healthy; API and web healthy; worker and Caddy running.

## Gate D — edge routing and authorization

```bat
docker run --rm -v "%cd%:/repo" -w /repo python:3.14.7-slim python tools/production_smoke.py --base-url http://host.docker.internal:8080

The smoke tool automatically sends `Host: localhost` for this local Docker-to-Windows path so Caddy matches the local staging site.
```

Expected: frontend 200, v0.9.0 release-candidate API, ready database, and a 401
for an unauthenticated control-plane request.

## Gate E — network exposure

Only Caddy may publish host ports. Database, API, worker, and web stay internal.

## Gate F — backup

```sh
docker compose --env-file .env.production -f compose.production.yaml --profile ops run --rm backup
```

Expected: a non-empty custom-format dump. Copy at least one RC backup off-host.

## Gate G — public TLS

```sh
python3 tools/production_smoke.py --base-url https://eventforge.example.com
```

Expected: Gate D passes over public HTTPS and HTTP redirects to HTTPS.

## Gate H — public signed webhook

Deliver one temporary signed webhook to:

```text
https://eventforge.example.com/ingest
```

Expected: HTTP 202, one durable event, one initial PROCESS_EVENT job, and no
signature material persisted in event headers. Disable the endpoint afterward.

## Gate I — release hygiene

```bat
git diff --check
git add .
git diff --cached --check
git status --short
git diff --cached --name-only
```

No `.env.production`, backup dump, terminal transcript, or secret-bearing file
may be staged.

## Gate J — tag boundary

Do not tag v0.9.0 until the real public HTTPS smoke test and signed webhook test
have both passed.
