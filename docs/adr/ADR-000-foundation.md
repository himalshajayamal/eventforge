# ADR-000: Local-first engineering foundation

Status: Accepted

## Context

EventForge needs a reproducible base before any project module is implemented.

## Decision

Use Docker Compose for the initial application stack, PostgreSQL as the first durable service,
FastAPI as the control API, React/TypeScript for the UI, C17 for the low-level library baseline,
and C++20 for the future data plane.

Do not introduce Kubernetes, Kafka, or a microservice fleet at v0.0.

## Consequences

The project is easy to run locally, simple enough to debug, and already contains the language
boundaries needed by later milestones.
