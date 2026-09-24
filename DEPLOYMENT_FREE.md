# EventForge v0.9 Free Public Demo Deployment

This deployment path preserves the three-product v1 scope and uses no paid
runtime resources:

- Neon Free: PostgreSQL 18
- Render Free Web Service: FastAPI + EventForge worker in one container
- GitHub Pages: React dashboard (configured later)

The Render service uses repository-root build context and
`apps/api/Dockerfile.render`. The image includes the canonical SQL migrations,
runs them before application startup, then supervises both the API and durable
worker in the same free instance.

## Required Render variables

Runtime:

- `DATABASE_URL`: Neon pooled connection string
- `MIGRATION_DATABASE_URL`: Neon direct (non-pooler) connection string
- `EVENTFORGE_ENV=production`
- `EVENTFORGE_ADMIN_USER=eventforge`
- `EVENTFORGE_ADMIN_PASSWORD=<generated secret>`
- `EVENTFORGE_WEBHOOK_MASTER_SECRET=<generated secret>`
- `EVENTFORGE_REQUIRE_SIGNED_WEBHOOKS=1`
- `EVENTFORGE_MAX_INGEST_BYTES=1048576`
- `EVENTFORGE_HTTP_ACTION_MAX_RESPONSE_BYTES=1048576`
- `EVENTFORGE_ALLOW_PRIVATE_HTTP_ACTIONS=0`
- `EVENTFORGE_ALLOW_INSECURE_HTTP_ACTIONS=0`
- `EVENTFORGE_HTTP_ACTION_ALLOWED_PORTS=443`
- `EVENTFORGE_WORKER_POLL_SECONDS=0.5`
- `EVENTFORGE_JOB_LEASE_SECONDS=30`
- `EVENTFORGE_JOB_HEARTBEAT_SECONDS=10`
- `EVENTFORGE_RETRY_BASE_SECONDS=2`
- `EVENTFORGE_RETRY_MAX_SECONDS=600`
- `PYTHONDONTWRITEBYTECODE=1`

After GitHub Pages is created, set:

- `EVENTFORGE_CORS_ORIGINS=https://<github-user>.github.io`

Do not put database passwords or generated EventForge secrets in Git.
