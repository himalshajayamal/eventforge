#!/bin/sh
set -eu

DB_URL="${MIGRATION_DATABASE_URL:-${DATABASE_URL:?DATABASE_URL is required}}"

attempt=1
while [ "$attempt" -le 30 ]; do
    if psql "$DB_URL" -X -v ON_ERROR_STOP=1 -Atqc "SELECT 1" >/dev/null 2>&1; then
        break
    fi
    if [ "$attempt" -eq 30 ]; then
        echo "eventforge_migration_database_unavailable" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 2
done

cat > /tmp/eventforge-migrate.sql <<'SQL'
\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(2026092409);

\echo applying 0001_foundation
\i /app/migrations/0001_foundation.sql

SELECT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '0002_hookledger'
) AS applied \gset
\if :applied
  \echo skipping 0002_hookledger
\else
  \echo applying 0002_hookledger
  \i /app/migrations/0002_hookledger.sql
\endif

SELECT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '0003_reliable_jobs'
) AS applied \gset
\if :applied
  \echo skipping 0003_reliable_jobs
\else
  \echo applying 0003_reliable_jobs
  \i /app/migrations/0003_reliable_jobs.sql
\endif

SELECT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '0004_replaydb'
) AS applied \gset
\if :applied
  \echo skipping 0004_replaydb
\else
  \echo applying 0004_replaydb
  \i /app/migrations/0004_replaydb.sql
\endif

SELECT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '0005_flowtrace'
) AS applied \gset
\if :applied
  \echo skipping 0005_flowtrace
\else
  \echo applying 0005_flowtrace
  \i /app/migrations/0005_flowtrace.sql
\endif

SELECT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '0006_integration'
) AS applied \gset
\if :applied
  \echo skipping 0006_integration
\else
  \echo applying 0006_integration
  \i /app/migrations/0006_integration.sql
\endif

SELECT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '0007_security'
) AS applied \gset
\if :applied
  \echo skipping 0007_security
\else
  \echo applying 0007_security
  \i /app/migrations/0007_security.sql
\endif

SELECT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '0008_reliability'
) AS applied \gset
\if :applied
  \echo skipping 0008_reliability
\else
  \echo applying 0008_reliability
  \i /app/migrations/0008_reliability.sql
\endif

COMMIT;
SQL

psql "$DB_URL" -X -v ON_ERROR_STOP=1 -f /tmp/eventforge-migrate.sql
rm -f /tmp/eventforge-migrate.sql
echo "eventforge_migrations_ready"
