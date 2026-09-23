# ADR-002: PostgreSQL-backed reliable job queue

## Status

Accepted for EventForge v0.2.0.

## Context

HookLedger v0.1 persists an immutable event and a `PENDING` processing job in the
same PostgreSQL transaction before returning HTTP 202. v0.2 must make those jobs
safe to execute with multiple workers, process crashes, retries and exhausted
attempt budgets.

The engineering plan explicitly calls for jobs, leases, attempts, retries,
dead-letter state and worker claiming with `FOR UPDATE SKIP LOCKED`. It also
keeps PostgreSQL as the initial queue so queue semantics are learned before an
external broker is introduced.

## Decision

EventForge v0.2 uses the existing `jobs` table as the durable queue and adds:

- a 1-based `attempt_count` and configurable `max_attempts`;
- lease owner, opaque lease token and lease expiry fields;
- immutable `job_attempts` history for every execution attempt;
- `PENDING -> RUNNING -> SUCCESS` for successful work;
- `RUNNING -> RETRY_WAIT -> PENDING` for retryable failure;
- `RUNNING -> DEAD_LETTERED` after the attempt budget is exhausted;
- expired-lease recovery so work abandoned by a dead worker becomes retryable;
- exponential backoff with jitter;
- `FOR UPDATE SKIP LOCKED` when selecting queue work.

Workers may complete or fail a job only while they still own a live lease token.
A stale worker cannot overwrite a job after its lease expires and another worker
takes over.

## Initial worker

v0.2 runs a Python worker as a separate Docker Compose process. For
`PROCESS_EVENT`, it currently validates that the event and payload are durable
and then acknowledges the job. Workflow actions and external side effects are
introduced in later milestones; this version is deliberately about queue and
failure semantics.

## Retry timing

For a failed 1-based attempt `a`, the pre-jitter delay is:

`min(max_delay, base_delay * 2^(a-1))`

and non-negative random jitter is added. Defaults are 2 seconds initial delay
and 600 seconds maximum pre-jitter delay.

## Consequences

The system gains inspectable attempt history and crash recovery without adding
Kafka, RabbitMQ, Redis or NATS. PostgreSQL remains a learning-scale queue, not a
claim that it is the correct broker for every future throughput target.
