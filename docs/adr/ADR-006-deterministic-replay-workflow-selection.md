# ADR-006: Freeze replay workflow selections at replay request time

## Status

Accepted for EventForge v0.5.0.

## Context

ReplayDB stores whether a replay should use `original` or `current` FlowTrace
workflow versions. In v0.4, `current` was resolved when the replay worker later
processed the job. A workflow activation occurring between replay creation and
job execution could therefore change what the replay meant.

## Decision

When a replay is created, EventForge stores one row per selected workflow in
`replay_workflow_selections`.

- `original` snapshots the versions that processed the original event.
- `current` snapshots active matching versions at replay-request time.
- Replay processing consumes the snapshot rather than resolving active versions
  again.
- Older replay rows without snapshots retain the v0.4 fallback behavior.

## Consequences

Replay intent becomes deterministic and auditable. A later workflow activation
does not alter an existing replay. The database gains another immutable linkage
that must be retained with replay history.
