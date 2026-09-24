# ADR-013: Free public-demo runtime on Neon + Render + GitHub Pages

## Status

Accepted for the EventForge v0.9 release-candidate public demonstration.

## Context

The v0.9 single-host Docker topology is valid for a VPS, but the public demo
must use free services without requiring a payment card. EventForge still needs
PostgreSQL, a public FastAPI process, and a continuously co-located durable job
worker whenever the free web instance is awake.

## Decision

Use Neon Free for PostgreSQL 18, one Render Free Web Service for FastAPI plus
the EventForge worker, and GitHub Pages for the static React dashboard.

Render's free tier does not provide a separate free background-worker service,
so the API image supervises both processes. The database remains the durability
boundary: Render process sleep/restart does not erase EventForge jobs.

The Render-specific image copies the canonical database migrations and applies
them before API/worker startup. Runtime traffic uses Neon's pooled connection;
migrations use the direct connection.

CORS is configured from `EVENTFORGE_CORS_ORIGINS`, allowing the exact GitHub
Pages origin rather than a wildcard.

## Consequences

The demo remains $0 and no-card, but Render free instances can spin down during
inactivity and cold-start on the next request. The public demo therefore does
not claim an industrial availability SLA.

Outbound HTTP side effects remain at-least-once.
