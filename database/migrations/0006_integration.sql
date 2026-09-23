-- EventForge v0.5.0: deterministic ReplayDB/FlowTrace linkage for the integrated core.

ALTER TABLE replay_executions
    ADD COLUMN IF NOT EXISTS workflow_selection_snapshot_at timestamptz;

CREATE TABLE IF NOT EXISTS replay_workflow_selections (
    replay_execution_id uuid NOT NULL REFERENCES replay_executions(id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_id uuid NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    workflow_version_id uuid NOT NULL REFERENCES workflow_versions(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (replay_execution_id, workflow_id)
);

CREATE INDEX IF NOT EXISTS replay_workflow_selections_project_idx
    ON replay_workflow_selections(project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS replay_workflow_selections_version_idx
    ON replay_workflow_selections(workflow_version_id);

INSERT INTO schema_migrations(version)
VALUES ('0006_integration')
ON CONFLICT (version) DO NOTHING;
