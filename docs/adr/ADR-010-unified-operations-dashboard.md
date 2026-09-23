# ADR-010: One project-scoped operations dashboard

## Status

Accepted for EventForge v0.7.0.

## Context

HookLedger, ReplayDB, and FlowTrace already share one PostgreSQL event model,
queue, control API, authorization layer, and worker runtime. Presenting them as
three unrelated browser applications would hide the cross-product event flow
that the architecture is designed to expose.

The v0.6 control plane also had no administrator endpoint for listing projects
and no compact project-level operational summary, which would force a dashboard
to either know project UUIDs out of band or download large historical lists only
to compute counters in the browser.

## Decision

Build one React application with one selected `project_id` context and five
navigation surfaces: Overview, HookLedger, ReplayDB, FlowTrace, and Security.

Add only two read projections to the FastAPI control plane:

```text
GET /projects
GET /projects/{project_id}/summary
```

The global project list remains administrator-only. The summary route remains
subject to the v0.6 project authorization middleware and therefore works with
an administrator or the matching project API key.

The frontend uses the existing API directly. It does not add a browser-only
backend, separate product databases, or duplicate authorization rules.

Credentials and one-time secrets are not persisted by the frontend. They live
in page memory and are discarded when the page is closed or disconnected.

## Consequences

A user can follow one event from HookLedger ingestion through ReplayDB and
FlowTrace without changing applications. The dashboard remains aligned with the
modular-monolith architecture and keeps the API usable independently by CLI and
automation clients.

The dashboard is still a pre-v1 engineering console. Multi-user identity,
organization membership, fine-grained RBAC, and billing remain outside v0.7.
