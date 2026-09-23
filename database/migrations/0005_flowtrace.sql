-- EventForge v0.4.0: FlowTrace durable workflow definitions, versions, runs and steps.

CREATE TABLE IF NOT EXISTS workflows (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    active_version_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

CREATE INDEX IF NOT EXISTS workflows_project_idx
    ON workflows(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS workflow_versions (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_id uuid NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    version_number integer NOT NULL CHECK (version_number > 0),
    trigger_source text NOT NULL,
    trigger_type text NOT NULL,
    definition jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workflow_id, version_number)
);

CREATE INDEX IF NOT EXISTS workflow_versions_project_idx
    ON workflow_versions(project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS workflow_versions_trigger_idx
    ON workflow_versions(project_id, trigger_source, trigger_type);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'workflows_active_version_fk'
    ) THEN
        ALTER TABLE workflows
            ADD CONSTRAINT workflows_active_version_fk
            FOREIGN KEY (active_version_id)
            REFERENCES workflow_versions(id)
            ON DELETE SET NULL;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS workflow_runs (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_id uuid NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    workflow_version_id uuid NOT NULL REFERENCES workflow_versions(id) ON DELETE RESTRICT,
    event_id uuid NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
    replay_execution_id uuid REFERENCES replay_executions(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED')),
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS workflow_runs_original_uidx
    ON workflow_runs(workflow_id, event_id)
    WHERE replay_execution_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS workflow_runs_replay_uidx
    ON workflow_runs(workflow_id, replay_execution_id)
    WHERE replay_execution_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS workflow_runs_project_idx
    ON workflow_runs(project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS workflow_runs_event_idx
    ON workflow_runs(event_id, created_at DESC);

CREATE TABLE IF NOT EXISTS step_runs (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_run_id uuid NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    step_index integer NOT NULL CHECK (step_index >= 0),
    step_type text NOT NULL,
    config jsonb NOT NULL,
    status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'SKIPPED', 'FAILED')),
    output jsonb,
    error text,
    started_at timestamptz,
    completed_at timestamptz,
    UNIQUE (workflow_run_id, step_index)
);

CREATE INDEX IF NOT EXISTS step_runs_run_idx
    ON step_runs(workflow_run_id, step_index);

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS workflow_run_id uuid
        REFERENCES workflow_runs(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS step_run_id uuid
        REFERENCES step_runs(id) ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS jobs_step_run_uidx
    ON jobs(step_run_id)
    WHERE step_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS jobs_workflow_run_idx
    ON jobs(workflow_run_id, created_at DESC);

-- FlowTrace can emit internal EventForge events. Those events are not tied to a
-- webhook ingress endpoint, so endpoint_id becomes optional for internal events.
ALTER TABLE events
    ALTER COLUMN endpoint_id DROP NOT NULL;

ALTER TABLE events
    ADD COLUMN IF NOT EXISTS parent_event_id uuid REFERENCES events(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS emitted_by_step_run_id uuid REFERENCES step_runs(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS events_emitted_step_uidx
    ON events(emitted_by_step_run_id)
    WHERE emitted_by_step_run_id IS NOT NULL;

INSERT INTO schema_migrations(version)
VALUES ('0005_flowtrace')
ON CONFLICT (version) DO NOTHING;
