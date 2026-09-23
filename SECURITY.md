# EventForge security notes

EventForge is still pre-1.0 and local-first. Do not expose the development
configuration directly to the public internet.

For production-like testing, use `EVENTFORGE_ENV=production`, replace the
default administrator password and webhook master secret, use signed webhook
endpoints, issue project-scoped API keys to automation, and leave private HTTP
actions disabled.

Secrets that appear in terminal transcripts, screenshots, issue trackers, or
chat logs should be treated as exposed and rotated before nonlocal use.

Report security issues privately to the repository owner rather than opening a
public proof-of-concept issue containing live credentials or endpoint tokens.


## v0.9 public edge

The production Compose topology publishes only Caddy. Do not publish PostgreSQL,
FastAPI, the worker, or the static web container directly.

`.env.production` contains live database/admin/webhook secrets and is gitignored.
Generate it with `tools/generate_production_env.py`, store a protected copy
outside the repository, and never paste it into release logs.

Production Compose forces signed webhooks and disables private/insecure FlowTrace
HTTP targets. The administrator interface still uses one Basic credential, so
v0.9 is an operator release candidate rather than a multi-user IAM system.
