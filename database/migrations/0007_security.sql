-- EventForge v0.6.0: project-scoped API keys and signed-webhook security metadata.

CREATE TABLE IF NOT EXISTS project_api_keys (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name text NOT NULL,
    key_prefix text NOT NULL,
    key_hash char(64) NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at timestamptz,
    UNIQUE (project_id, name)
);

CREATE INDEX IF NOT EXISTS project_api_keys_project_idx
    ON project_api_keys(project_id, created_at DESC);

ALTER TABLE ingress_endpoints
    ADD COLUMN IF NOT EXISTS verify_signature boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS signing_secret_version integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS signature_tolerance_seconds integer NOT NULL DEFAULT 300;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ingress_endpoints_signing_secret_version_check'
    ) THEN
        ALTER TABLE ingress_endpoints
            ADD CONSTRAINT ingress_endpoints_signing_secret_version_check
            CHECK (signing_secret_version > 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ingress_endpoints_signature_tolerance_check'
    ) THEN
        ALTER TABLE ingress_endpoints
            ADD CONSTRAINT ingress_endpoints_signature_tolerance_check
            CHECK (signature_tolerance_seconds BETWEEN 30 AND 3600);
    END IF;
END
$$;

DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'ingress_attempts'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%outcome%'
    LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE ingress_attempts DROP CONSTRAINT %I', constraint_name);
    END IF;
END
$$;

ALTER TABLE ingress_attempts
    ADD CONSTRAINT ingress_attempts_outcome_check
    CHECK (outcome IN ('ACCEPTED', 'DUPLICATE', 'REJECTED_SIGNATURE'));

INSERT INTO schema_migrations(version)
VALUES ('0007_security')
ON CONFLICT (version) DO NOTHING;
