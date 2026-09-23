# ADR-005 — FlowTrace persists workflow versions, runs and step runs

## Status

Accepted for EventForge v0.4.0.

## Context

FlowTrace must survive worker/process loss without losing the whole workflow.
It also has to integrate with ReplayDB, which distinguishes replaying an event
with its original workflow version from replaying it with the current version.

## Decision

Persist four layers:

```text
workflow
  -> workflow_version
       -> workflow_run
            -> step_run
```

Workflow versions are immutable. A workflow points at its active version.
Every workflow run stores the exact version that created it.

Each executable step receives its own PostgreSQL-backed job. The existing job
lease/retry machinery therefore applies to a workflow step exactly as it does to
other EventForge work.

After a step succeeds, FlowTrace commits its `step_run` result and workflow
context before the next step is eligible. Delay steps do not sleep a worker;
they schedule the next step with a future `available_at` timestamp.

## Initial action types

v0.4 supports the safe structured action model from the engineering plan:

- `conditional`
- `json_transform`
- `delay`
- `http_request`
- `emit_event`

Arbitrary Python/JavaScript execution is not supported.

Outbound HTTP is deliberately conservative: HTTPS by default, bounded timeout
and response size, no redirects, an idempotency key derived from the step-run
identity, and private/local address rejection by default. This is still an early
local-first implementation; later security hardening must include stronger DNS
rebinding defenses and destination policy.

## Crash semantics

If a worker dies, its job lease expires and the existing reliable-job recovery
moves the step back into the retry path. Previously committed steps remain
`SUCCESS` and are not erased.

External HTTP side effects remain at-least-once. A crash after a remote service
accepts a request but before EventForge records success can cause the request to
be repeated. FlowTrace sends a stable `Idempotency-Key` where possible, but does
not claim exactly-once external side effects.

## Replay integration

- `original` replay mode selects workflow versions that actually ran for the
  original event.
- `current` replay mode selects each matching workflow's active version.

This turns the v0.3 ReplayDB mode flag into executable version semantics.
