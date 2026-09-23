# ADR-001 — HookLedger v0.1 ingestion semantics

## Status
Accepted for v0.1.0.

## Context
EventForge needs its first durable event source. The engineering baseline requires webhook ingestion to persist an immutable event and a processing job in one PostgreSQL transaction, then return HTTP 202 only after commit. Duplicate provider deliveries must not create duplicate logical events when an idempotency identity is supplied.

## Decisions

- PostgreSQL remains the v0.1 source of truth for event metadata, payload bytes, and pending jobs.
- Each webhook endpoint receives a cryptographically random URL token. Only the SHA-256 hash is stored in PostgreSQL; the plaintext token is returned once at endpoint creation.
- Control-plane project, endpoint, and history routes use HTTP Basic authentication in the local-only v0.1 environment. This is intentionally temporary; project-scoped hashed API keys belong in a later security/authentication milestone.
- Provider delivery identity is selected in this order: `X-GitHub-Delivery`, `X-EventForge-Delivery-Id`, then `Idempotency-Key`.
- Database uniqueness on `(endpoint_id, provider_delivery_id)` is the final idempotency guard.
- If no idempotency identity is supplied, repeated requests are independent events.
- Every accepted inbound request is recorded in `ingress_attempts` as either `ACCEPTED` or `DUPLICATE`.
- The event row, payload row, and initial `PROCESS_EVENT` job are created in one transaction.
- Jobs remain `PENDING` in v0.1. Worker claiming, leases, retries, backoff, and dead-letter handling are v0.2 work.
- Payloads remain in PostgreSQL for the initial implementation. Object archival is a later deployment/storage step.
- The ingestion body limit defaults to 1 MiB and is configurable through `EVENTFORGE_MAX_INGEST_BYTES`.
- Only a safe subset of request headers is persisted; authorization/cookie secrets and full payloads are not written to application logs.

## Consequences

The v0.1 exit criterion is testable with a real duplicate request: two requests carrying the same provider delivery ID return HTTP 202 and reference the same logical event, while only one event and one processing job exist.
