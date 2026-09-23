# ADR-007: Isolate pytest from the live development worker

## Status

Accepted for EventForge v0.5.0.

## Context

Earlier integration tests used the same PostgreSQL database as the running
Compose worker. Retry-promotion and lease-recovery tests could race with the
worker even when production behavior was correct.

## Decision

The canonical Compose test workflow uses a disposable `eventforge_test`
database. A dedicated test service recreates that database, applies all SQL
migrations, and launches pytest with a test-only `DATABASE_URL`.

The normal API and worker continue to use the `eventforge` database.

## Consequences

Queue/lease tests become deterministic without stopping the normal worker.
Tests exercise the real migrations against PostgreSQL, while test data no
longer pollutes the development database. The test database is disposable and
must never be treated as durable application state.
