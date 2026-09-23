# ADR-009: Signed webhooks and pinned outbound HTTP destinations

## Status

Accepted for EventForge v0.6.0.

## Context

HookLedger accepts internet-originated payloads and FlowTrace can initiate
outbound HTTP requests. Those are the two strongest network trust boundaries in
the current three-product platform.

## Decision

For HookLedger, support endpoint HMAC verification with server-derived signing
secrets. EventForge-format signatures include a timestamp and enforce a bounded
freshness window. GitHub `X-Hub-Signature-256` is supported for GitHub endpoints.
Rejected signatures are audited without storing signature material.

For FlowTrace, require HTTPS and port 443 by default, reject userinfo/fragments,
reject local/private/link-local/metadata destinations, validate every DNS answer,
and pin the actual TCP connection to one of the validated addresses. Redirects
are rejected rather than followed.

## Consequences

- Endpoint signing secrets are not stored as plaintext database values.
- Rotating an endpoint secret increments its derivation version and immediately
  invalidates the old provider secret.
- Rotating the server master secret is a coordinated operation because all
  derived endpoint secrets change.
- GitHub signatures lack EventForge's timestamp freshness property; delivery-id
  idempotency still protects logical-event duplication but is not equivalent to
  timestamp replay protection.
- Private/insecure HTTP overrides remain development/operator escape hatches and
  are disabled by default.
