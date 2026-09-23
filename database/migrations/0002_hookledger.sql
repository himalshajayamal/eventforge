CREATE TABLE IF NOT EXISTS ingress_endpoints (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name text NOT NULL,
    source text NOT NULL DEFAULT 'generic',
    token_prefix text NOT NULL,
    token_hash char(64) NOT NULL UNIQUE,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_received_at timestamptz,
    UNIQUE (project_id, name)
);

CREATE INDEX IF NOT EXISTS ingress_endpoints_project_idx
    ON ingress_endpoints(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    endpoint_id uuid NOT NULL REFERENCES ingress_endpoints(id) ON DELETE RESTRICT,
    source text NOT NULL,
    type text NOT NULL,
    provider_delivery_id text,
    trace_id uuid NOT NULL,
    headers jsonb NOT NULL DEFAULT '{}'::jsonb,
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS events_endpoint_delivery_uidx
    ON events(endpoint_id, provider_delivery_id)
    WHERE provider_delivery_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS events_project_received_idx
    ON events(project_id, received_at DESC);

CREATE INDEX IF NOT EXISTS events_trace_idx
    ON events(trace_id);

CREATE TABLE IF NOT EXISTS event_payloads (
    event_id uuid PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    content_type text,
    body bytea NOT NULL,
    size_bytes integer NOT NULL CHECK (size_bytes >= 0),
    sha256 char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (octet_length(body) = size_bytes)
);

CREATE INDEX IF NOT EXISTS event_payloads_project_idx
    ON event_payloads(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    kind text NOT NULL DEFAULT 'PROCESS_EVENT',
    status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'RETRY_WAIT', 'DEAD_LETTERED')),
    available_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (event_id, kind)
);

CREATE INDEX IF NOT EXISTS jobs_claim_idx
    ON jobs(status, available_at, created_at);

-- v0.1 records every inbound webhook delivery attempt. Outbound delivery
-- execution/retry semantics are intentionally implemented in v0.2.
CREATE TABLE IF NOT EXISTS ingress_attempts (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    endpoint_id uuid NOT NULL REFERENCES ingress_endpoints(id) ON DELETE CASCADE,
    event_id uuid REFERENCES events(id) ON DELETE SET NULL,
    provider_delivery_id text,
    request_id uuid NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('ACCEPTED', 'DUPLICATE')),
    response_code integer NOT NULL CHECK (response_code BETWEEN 100 AND 599),
    received_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ingress_attempts_project_idx
    ON ingress_attempts(project_id, received_at DESC);

INSERT INTO schema_migrations(version)
VALUES ('0002_hookledger')
ON CONFLICT (version) DO NOTHING;
