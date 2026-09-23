-- EventForge v0.3.0: ReplayDB execution records and replay jobs.
-- Replays reference immutable original events/payloads instead of rewriting them.

CREATE TABLE IF NOT EXISTS replay_executions (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    replay_of uuid NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
    workflow_version_mode text NOT NULL
        CHECK (workflow_version_mode IN ('original', 'current')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS replay_executions_event_idx
    ON replay_executions(replay_of, created_at DESC);

CREATE INDEX IF NOT EXISTS replay_executions_project_idx
    ON replay_executions(project_id, created_at DESC);

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS replay_execution_id uuid
        REFERENCES replay_executions(id) ON DELETE CASCADE;

-- v0.2 used UNIQUE(event_id, kind), which would allow only one replay job per
-- source event. ReplayDB must allow independent repeated executions of the same
-- historical event, while still preserving exactly one initial PROCESS_EVENT job.
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_event_id_kind_key;

CREATE UNIQUE INDEX IF NOT EXISTS jobs_initial_process_event_uidx
    ON jobs(event_id, kind)
    WHERE kind = 'PROCESS_EVENT';

CREATE UNIQUE INDEX IF NOT EXISTS jobs_replay_execution_uidx
    ON jobs(replay_execution_id)
    WHERE replay_execution_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS jobs_replay_execution_idx
    ON jobs(replay_execution_id);

INSERT INTO schema_migrations(version)
VALUES ('0004_replaydb')
ON CONFLICT (version) DO NOTHING;
