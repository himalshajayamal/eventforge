# ADR-012: Single-node production edge for the v0.9 release candidate

## Status

Accepted for EventForge v0.9.0.

## Context

Through v0.8, EventForge is validated as a local Docker Compose system. v0.9
must make the same HookLedger + ReplayDB + FlowTrace platform deployable on the
public web without Kubernetes, a new database, microservices, or a fourth
product.

## Decision

Use one Linux Docker host for the v0.9 release candidate.

Caddy is the only internet-facing container. It terminates HTTPS and routes:

```text
/                 -> static React web
/api/*            -> FastAPI control plane
/ingest[/...]     -> FastAPI webhook ingress
/health, /ready   -> FastAPI probes
```

React is built with Node and served by Nginx. Production browser requests use
same-origin `/api`, so FastAPI does not need to be published directly.

PostgreSQL lives only on an internal Docker network. API and worker reach it
there. API and worker retain outbound connectivity because FlowTrace validates
and calls permitted public HTTPS destinations.

Critical production values are generated into gitignored `.env.production`.
Blank required values make Compose interpolation fail.

The stack includes a one-shot PostgreSQL backup profile and a provider-independent
smoke-test script.

## Consequences

The deployment is simple and close to the local architecture. Caddy manages
certificate acquisition/renewal while only ports 80/443 are public.

The host remains a single failure domain, so v0.9 requires off-host backups and
does not claim high availability.

Outbound HTTP actions remain at-least-once. v0.9 also retains the single
administrator credential instead of multi-user IAM/RBAC.
