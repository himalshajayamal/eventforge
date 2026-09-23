-- EventForge v0.8.0: worker lease heartbeat and recovery observability.

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS last_heartbeat_at timestamptz,
    ADD COLUMN IF NOT EXISTS recovery_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_recovered_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_recovery_count_nonnegative'
    ) THEN
        ALTER TABLE jobs
            ADD CONSTRAINT jobs_recovery_count_nonnegative
            CHECK (recovery_count >= 0);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS jobs_project_status_created_idx
    ON jobs(project_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS jobs_project_actionable_idx
    ON jobs(project_id, available_at, created_at, id)
    WHERE status IN ('PENDING', 'RETRY_WAIT');

INSERT INTO schema_migrations(version)
VALUES ('0008_reliability')
ON CONFLICT (version) DO NOTHING;
