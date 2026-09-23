# ADR-004 — EventForge v1.0 contains three product modules

## Status

Accepted for the v1.0 roadmap.

## Decision

EventForge v1.0 will expose exactly three primary product modules:

1. HookLedger — webhook/event ingestion and reliable queueing.
2. ReplayDB — immutable historical event replay.
3. FlowTrace — durable automation workflows.

The wider engineering report originally described additional modules such as
QuotaGate, TracePocket, FaultMesh, ChangeWire, DriftLens, SyncForge and
MigrationSentinel. Those remain possible post-v1 work, but they are no longer
v1.0 release requirements.

Cross-cutting capabilities needed by the three products — authentication,
logging, security controls, basic tracing fields, deployment and testing — may
still be implemented internally. They do not become additional v1 products.

## Why

The three selected products form one coherent event lifecycle:

```text
HookLedger receives and stores an event
        |
        v
FlowTrace executes a durable workflow
        |
        v
ReplayDB reproduces an execution from history
```

This narrower stable-release scope leaves the remaining 0.x milestones for
integration, security, frontend completion, failure testing, deployment and
release hardening instead of continuously adding unrelated product surface.

## Consequences

- v0.4 is the third and final new product module before v1.0.
- later 0.x releases harden these three products.
- the README and release documentation must not promise all ten original ideas
  in v1.0.
- future modules can be proposed after the three-product v1 platform is stable.
