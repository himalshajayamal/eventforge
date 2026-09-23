-- EventForge v0.2.0: reliable PostgreSQL-backed jobs, leases, attempts and retries.

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_attempts integer NOT NULL DEFAULT 5,
    ADD COLUMN IF NOT EXISTS lease_owner text,
    ADD COLUMN IF NOT EXISTS lease_token uuid,
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_error text,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS completed_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_attempt_count_nonnegative'
    ) THEN
        ALTER TABLE jobs
            ADD CONSTRAINT jobs_attempt_count_nonnegative
            CHECK (attempt_count >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_max_attempts_positive'
    ) THEN
        ALTER TABLE jobs
            ADD CONSTRAINT jobs_max_attempts_positive
            CHECK (max_attempts > 0);
    END IF;
END
$$;

DROP INDEX IF EXISTS jobs_claim_idx;

CREATE INDEX IF NOT EXISTS jobs_claim_idx
    ON jobs(status, available_at, created_at, id)
    WHERE status IN ('PENDING', 'RETRY_WAIT');

CREATE INDEX IF NOT EXISTS jobs_expired_lease_idx
    ON jobs(lease_expires_at, id)
    WHERE status = 'RUNNING';

CREATE TABLE IF NOT EXISTS job_attempts (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    worker_id text NOT NULL,
    lease_token uuid NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    outcome text NOT NULL DEFAULT 'RUNNING'
        CHECK (outcome IN ('RUNNING', 'SUCCESS', 'RETRY', 'DEAD_LETTERED', 'LEASE_EXPIRED')),
    error text,
    retry_at timestamptz,
    UNIQUE (job_id, attempt_number),
    UNIQUE (lease_token)
);

CREATE INDEX IF NOT EXISTS job_attempts_job_idx
    ON job_attempts(job_id, attempt_number);

CREATE INDEX IF NOT EXISTS job_attempts_project_idx
    ON job_attempts(project_id, started_at DESC);

INSERT INTO schema_migrations(version)
VALUES ('0003_reliable_jobs')
ON CONFLICT (version) DO NOTHING;
