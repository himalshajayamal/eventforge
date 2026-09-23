# ADR-003: Replay executions reference immutable historical events

## Status

Accepted for EventForge v0.3.0.

## Context

ReplayDB must replay a previously received event while preserving the original
historical record. A replay is a new execution, not a mutation of the source
event. The engineering plan also requires a distinction between replaying with
the original workflow version and the current workflow version.

At v0.3, workflow definitions and workflow versions have not been introduced
yet, and event payloads still live in PostgreSQL.

## Decision

Create an immutable `replay_executions` record for every replay request. Each
record has its own UUID and references the source event through `replay_of`. It
also records `workflow_version_mode` as `original` or `current`.

Create one `REPLAY_EVENT` job per replay execution. `jobs.replay_execution_id`
links queue state to the replay identity. Multiple replay executions may refer
to the same source event. The original `PROCESS_EVENT` uniqueness rule remains
protected by a partial unique index.

The replay worker reads the existing PostgreSQL payload through the source event
reference. It does not create a replacement event or payload and does not update
the historical event.

## Consequences

- repeated replay requests create distinct execution identities
- replay jobs inherit the v0.2 lease/retry/dead-letter machinery
- the source event and payload remain unchanged
- replay history is queryable independently from event history
- original/current workflow intent is durable before workflow tables exist
- v0.4 can later resolve that intent against real workflow versions
- no C event-store dependency is introduced in v0.3

## Rejected alternatives

### Mutate the original event to indicate that it was replayed

Rejected because it damages historical immutability.

### Clone the event and payload for every replay

Rejected for v0.3 because the plan describes a new execution from an event, not
a rewritten historical event, and the immutable PostgreSQL payload can be
referenced directly.

### Add workflow/version tables in v0.3

Rejected because FlowTrace and workflow persistence are the following milestone.
The replay mode is recorded now and resolved later.
