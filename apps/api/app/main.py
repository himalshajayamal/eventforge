import hashlib
import os
import secrets
import uuid
from typing import Annotated, Any, Literal, Union

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

APP_VERSION = "0.5.0"

from app.db import db_connect
from app.workflows import schedule_workflows_for_event, snapshot_replay_workflow_selections

ADMIN_USER = os.getenv("EVENTFORGE_ADMIN_USER", "eventforge")
ADMIN_PASSWORD = os.getenv("EVENTFORGE_ADMIN_PASSWORD", "eventforge_dev_only_admin")
MAX_INGEST_BYTES = int(os.getenv("EVENTFORGE_MAX_INGEST_BYTES", "1048576"))

app = FastAPI(
    title="EventForge Control API",
    version=APP_VERSION,
    description="v0.5 integrated HookLedger + ReplayDB + FlowTrace core",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

basic_auth = HTTPBasic(auto_error=False)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class EndpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(default="generic", min_length=1, max_length=80)


class ReplayCreate(BaseModel):
    workflow_version_mode: Literal["original", "current"] = "original"


class WorkflowTrigger(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=120)


class ConditionalStep(BaseModel):
    type: Literal["conditional"]
    path: str = Field(min_length=1, max_length=200)
    equals: Any


class DelayStep(BaseModel):
    type: Literal["delay"]
    seconds: float = Field(ge=0, le=3600)


class JsonTransformStep(BaseModel):
    type: Literal["json_transform"]
    set: dict[str, Any] = Field(min_length=1)


class EmitEventStep(BaseModel):
    type: Literal["emit_event"]
    event_type: str = Field(min_length=1, max_length=120)
    source: str = Field(default="flowtrace", min_length=1, max_length=80)


class HttpRequestStep(BaseModel):
    type: Literal["http_request"]
    method: Literal["GET", "POST"] = "POST"
    url: str = Field(min_length=8, max_length=2048)
    timeout_seconds: float = Field(default=5.0, gt=0, le=10)


WorkflowStep = Annotated[
    Union[ConditionalStep, DelayStep, JsonTransformStep, EmitEventStep, HttpRequestStep],
    Field(discriminator="type"),
]


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    trigger: WorkflowTrigger
    steps: list[WorkflowStep] = Field(min_length=1, max_length=50)


class WorkflowVersionCreate(BaseModel):
    trigger: WorkflowTrigger
    steps: list[WorkflowStep] = Field(min_length=1, max_length=50)
    activate: bool = True



def require_admin(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_auth)],
) -> None:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    username_ok = secrets.compare_digest(credentials.username, ADMIN_USER)
    password_ok = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def safe_ingest_headers(request: Request) -> dict[str, str]:
    allowed = {
        "content-type",
        "user-agent",
        "x-github-event",
        "x-github-delivery",
        "x-eventforge-delivery-id",
        "x-eventforge-event-type",
        "idempotency-key",
    }
    return {key: value for key, value in request.headers.items() if key.lower() in allowed}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "EventForge",
        "version": APP_VERSION,
        "status": "integrated-core",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ready")
                value = cur.fetchone()
        if value != {"ready": 1}:
            raise RuntimeError("unexpected database readiness response")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database not ready") from exc

    return {"status": "ready", "database": "ok"}


@app.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    project_id = uuid.uuid4()
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="project name cannot be blank")

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO projects(id, name)
                VALUES (%s, %s)
                RETURNING id, name, created_at
                """,
                (project_id, name),
            )
            project = cur.fetchone()

    assert project is not None
    return project


@app.post(
    "/projects/{project_id}/webhook-endpoints",
    status_code=status.HTTP_201_CREATED,
)
def create_webhook_endpoint(
    project_id: uuid.UUID,
    payload: EndpointCreate,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    endpoint_id = uuid.uuid4()
    endpoint_token = secrets.token_urlsafe(32)
    endpoint_token_hash = token_hash(endpoint_token)
    endpoint_name = payload.name.strip()
    source = payload.source.strip().lower()

    if not endpoint_name or not source:
        raise HTTPException(status_code=422, detail="name and source cannot be blank")

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="project not found")

            try:
                cur.execute(
                    """
                    INSERT INTO ingress_endpoints(
                        id, project_id, name, source, token_prefix, token_hash
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, project_id, name, source, token_prefix, enabled, created_at
                    """,
                    (
                        endpoint_id,
                        project_id,
                        endpoint_name,
                        source,
                        endpoint_token[:8],
                        endpoint_token_hash,
                    ),
                )
                endpoint = cur.fetchone()
            except psycopg.errors.UniqueViolation as exc:
                raise HTTPException(
                    status_code=409,
                    detail="an endpoint with this name already exists in the project",
                ) from exc

    assert endpoint is not None
    endpoint["endpoint_token"] = endpoint_token
    endpoint["ingest_path"] = f"/ingest/{endpoint_token}"
    endpoint["token_note"] = "This token is shown once. EventForge stores only its SHA-256 hash."
    return endpoint


@app.get("/projects/{project_id}/webhook-endpoints")
def list_webhook_endpoints(
    project_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> list[dict[str, Any]]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, name, source, token_prefix, enabled,
                       created_at, last_received_at
                FROM ingress_endpoints
                WHERE project_id = %s
                ORDER BY created_at DESC
                """,
                (project_id,),
            )
            return list(cur.fetchall())


@app.get("/projects/{project_id}/events")
def list_events(
    project_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
    limit: int = 50,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.project_id, e.endpoint_id, e.source, e.type,
                       e.provider_delivery_id, e.trace_id, e.received_at,
                       p.content_type, p.size_bytes, p.sha256
                FROM events AS e
                JOIN event_payloads AS p ON p.event_id = e.id
                WHERE e.project_id = %s
                ORDER BY e.received_at DESC
                LIMIT %s
                """,
                (project_id, limit),
            )
            return list(cur.fetchall())


@app.get("/events/{event_id}/ingress-attempts")
def list_ingress_attempts(
    event_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> list[dict[str, Any]]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, endpoint_id, event_id, provider_delivery_id,
                       request_id, outcome, response_code, received_at
                FROM ingress_attempts
                WHERE event_id = %s
                ORDER BY received_at ASC
                """,
                (event_id,),
            )
            return list(cur.fetchall())


@app.get("/events/{event_id}")
def get_event(
    event_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.project_id, e.endpoint_id, e.source, e.type,
                       e.provider_delivery_id, e.trace_id, e.headers, e.received_at,
                       p.content_type, p.size_bytes, p.sha256
                FROM events AS e
                JOIN event_payloads AS p ON p.event_id = e.id
                WHERE e.id = %s
                """,
                (event_id,),
            )
            event = cur.fetchone()

    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event


@app.get("/events/{event_id}/integration")
def get_event_integration(
    event_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    """Return one cross-product view for HookLedger, FlowTrace and ReplayDB."""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.project_id, e.endpoint_id, e.source, e.type,
                       e.provider_delivery_id, e.trace_id, e.parent_event_id,
                       e.emitted_by_step_run_id, e.received_at,
                       p.content_type, p.size_bytes, p.sha256
                FROM events AS e
                JOIN event_payloads AS p ON p.event_id = e.id
                WHERE e.id = %s
                """,
                (event_id,),
            )
            event = cur.fetchone()
            if event is None:
                raise HTTPException(status_code=404, detail="event not found")

            cur.execute(
                """
                SELECT id, kind, status, attempt_count, max_attempts,
                       replay_execution_id, workflow_run_id, step_run_id,
                       available_at, last_error, created_at, completed_at
                FROM jobs
                WHERE event_id = %s
                ORDER BY created_at
                """,
                (event_id,),
            )
            jobs = list(cur.fetchall())

            cur.execute(
                """
                SELECT wr.id, wr.workflow_id, w.name AS workflow_name,
                       wr.workflow_version_id, v.version_number,
                       wr.replay_execution_id, wr.status, wr.error,
                       wr.created_at, wr.started_at, wr.completed_at
                FROM workflow_runs AS wr
                JOIN workflows AS w ON w.id = wr.workflow_id
                JOIN workflow_versions AS v ON v.id = wr.workflow_version_id
                WHERE wr.event_id = %s
                ORDER BY wr.created_at
                """,
                (event_id,),
            )
            workflow_runs = list(cur.fetchall())

            cur.execute(
                """
                SELECT r.id, r.workflow_version_mode, r.workflow_selection_snapshot_at, r.created_at,
                       j.id AS job_id, j.status AS job_status,
                       COUNT(s.workflow_id)::integer AS workflow_selection_count
                FROM replay_executions AS r
                JOIN jobs AS j ON j.replay_execution_id = r.id
                LEFT JOIN replay_workflow_selections AS s
                       ON s.replay_execution_id = r.id
                WHERE r.replay_of = %s
                GROUP BY r.id, r.workflow_version_mode, r.workflow_selection_snapshot_at, r.created_at, j.id, j.status
                ORDER BY r.created_at
                """,
                (event_id,),
            )
            replays = list(cur.fetchall())

            cur.execute(
                """
                SELECT id, source, type, trace_id, emitted_by_step_run_id, received_at
                FROM events
                WHERE parent_event_id = %s
                ORDER BY received_at
                """,
                (event_id,),
            )
            emitted_events = list(cur.fetchall())

    return {
        "event": event,
        "jobs": jobs,
        "workflow_runs": workflow_runs,
        "replays": replays,
        "emitted_events": emitted_events,
    }


@app.get("/projects/{project_id}/jobs")
def list_jobs(
    project_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
    job_status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    if job_status is not None:
        job_status = job_status.strip().upper()
        allowed_statuses = {"PENDING", "RUNNING", "SUCCESS", "RETRY_WAIT", "DEAD_LETTERED"}
        if job_status not in allowed_statuses:
            raise HTTPException(status_code=400, detail="invalid job status")

    with db_connect() as conn:
        with conn.cursor() as cur:
            if job_status is None:
                cur.execute(
                    """
                    SELECT id, project_id, event_id, replay_execution_id, workflow_run_id, step_run_id, kind, status,
                           attempt_count, max_attempts, available_at,
                           lease_owner, lease_expires_at, last_error,
                           created_at, updated_at, completed_at
                    FROM jobs
                    WHERE project_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (project_id, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, project_id, event_id, replay_execution_id, workflow_run_id, step_run_id, kind, status,
                           attempt_count, max_attempts, available_at,
                           lease_owner, lease_expires_at, last_error,
                           created_at, updated_at, completed_at
                    FROM jobs
                    WHERE project_id = %s AND status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (project_id, job_status, limit),
                )
            return list(cur.fetchall())


@app.get("/jobs/{job_id}")
def get_job(
    job_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, event_id, replay_execution_id, workflow_run_id, step_run_id, kind, status,
                       attempt_count, max_attempts, available_at,
                       lease_owner, lease_token, lease_expires_at,
                       last_error, created_at, updated_at, completed_at
                FROM jobs
                WHERE id = %s
                """,
                (job_id,),
            )
            job = cur.fetchone()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.get("/jobs/{job_id}/attempts")
def list_job_attempts(
    job_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> list[dict[str, Any]]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, job_id, event_id, attempt_number,
                       worker_id, started_at, finished_at, outcome,
                       error, retry_at
                FROM job_attempts
                WHERE job_id = %s
                ORDER BY attempt_number ASC
                """,
                (job_id,),
            )
            return list(cur.fetchall())


@app.post(
    "/events/{event_id}/replays",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_replay(
    event_id: uuid.UUID,
    payload: ReplayCreate,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    """Create a replay and freeze its FlowTrace workflow-version selection.

    v0.5 makes replay meaning deterministic: ``original`` snapshots the versions
    that handled the historical event, while ``current`` snapshots the active
    matching versions at replay-request time. A later workflow activation cannot
    silently change an already-created replay.
    """
    replay_id = uuid.uuid4()
    job_id = uuid.uuid4()

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.project_id
                FROM events AS e
                JOIN event_payloads AS p ON p.event_id = e.id
                WHERE e.id = %s
                """,
                (event_id,),
            )
            source = cur.fetchone()
            if source is None:
                raise HTTPException(status_code=404, detail="event not found")

            cur.execute(
                """
                INSERT INTO replay_executions(
                    id, project_id, replay_of, workflow_version_mode
                )
                VALUES (%s, %s, %s, %s)
                RETURNING id, project_id, replay_of, workflow_version_mode, created_at
                """,
                (
                    replay_id,
                    source["project_id"],
                    event_id,
                    payload.workflow_version_mode,
                ),
            )
            replay = cur.fetchone()
            assert replay is not None

            workflow_selection_count = snapshot_replay_workflow_selections(
                cur,
                project_id=source["project_id"],
                event_id=event_id,
                replay_execution_id=replay_id,
                mode=payload.workflow_version_mode,
            )
            cur.execute(
                """
                UPDATE replay_executions
                SET workflow_selection_snapshot_at = now()
                WHERE id = %s
                """,
                (replay_id,),
            )

            cur.execute(
                """
                INSERT INTO jobs(
                    id, project_id, event_id, replay_execution_id, kind, status
                )
                VALUES (%s, %s, %s, %s, 'REPLAY_EVENT', 'PENDING')
                RETURNING id, status
                """,
                (job_id, source["project_id"], event_id, replay_id),
            )
            job = cur.fetchone()
            assert job is not None

    return {
        **replay,
        "job_id": job["id"],
        "job_status": job["status"],
        "workflow_selection_count": workflow_selection_count,
    }


@app.get("/events/{event_id}/replays")
def list_event_replays(
    event_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> list[dict[str, Any]]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM events WHERE id = %s", (event_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="event not found")

            cur.execute(
                """
                SELECT r.id, r.project_id, r.replay_of, r.workflow_version_mode,
                       r.workflow_selection_snapshot_at, r.created_at, j.id AS job_id, j.status AS job_status,
                       j.attempt_count, j.max_attempts, j.last_error, j.completed_at
                FROM replay_executions AS r
                JOIN jobs AS j ON j.replay_execution_id = r.id
                WHERE r.replay_of = %s
                ORDER BY r.created_at DESC
                """,
                (event_id,),
            )
            return list(cur.fetchall())


@app.get("/projects/{project_id}/replays")
def list_project_replays(
    project_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
    limit: int = 50,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.project_id, r.replay_of, r.workflow_version_mode,
                       r.workflow_selection_snapshot_at, r.created_at, j.id AS job_id, j.status AS job_status,
                       j.attempt_count, j.max_attempts, j.last_error, j.completed_at
                FROM replay_executions AS r
                JOIN jobs AS j ON j.replay_execution_id = r.id
                WHERE r.project_id = %s
                ORDER BY r.created_at DESC
                LIMIT %s
                """,
                (project_id, limit),
            )
            return list(cur.fetchall())


@app.get("/replays/{replay_id}")
def get_replay(
    replay_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.project_id, r.replay_of, r.workflow_version_mode,
                       r.workflow_selection_snapshot_at, r.created_at, j.id AS job_id, j.status AS job_status,
                       j.attempt_count, j.max_attempts, j.available_at,
                       j.lease_owner, j.lease_expires_at, j.last_error,
                       j.completed_at
                FROM replay_executions AS r
                JOIN jobs AS j ON j.replay_execution_id = r.id
                WHERE r.id = %s
                """,
                (replay_id,),
            )
            replay = cur.fetchone()
    if replay is None:
        raise HTTPException(status_code=404, detail="replay not found")
    return replay


@app.get("/replays/{replay_id}/workflow-selections")
def list_replay_workflow_selections(
    replay_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> list[dict[str, Any]]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM replay_executions WHERE id = %s", (replay_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="replay not found")
            cur.execute(
                """
                SELECT s.replay_execution_id, s.project_id, s.workflow_id,
                       w.name AS workflow_name, s.workflow_version_id,
                       v.version_number, v.trigger_source, v.trigger_type,
                       s.created_at
                FROM replay_workflow_selections AS s
                JOIN workflows AS w ON w.id = s.workflow_id
                JOIN workflow_versions AS v ON v.id = s.workflow_version_id
                WHERE s.replay_execution_id = %s
                ORDER BY w.created_at, w.id
                """,
                (replay_id,),
            )
            return list(cur.fetchall())


def _workflow_definition(trigger: WorkflowTrigger, steps: list[WorkflowStep]) -> dict[str, Any]:
    return {
        "trigger": trigger.model_dump(),
        "steps": [step.model_dump() for step in steps],
    }


@app.post("/projects/{project_id}/workflows", status_code=status.HTTP_201_CREATED)
def create_workflow(
    project_id: uuid.UUID,
    payload: WorkflowCreate,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    workflow_id = uuid.uuid4()
    version_id = uuid.uuid4()
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="workflow name cannot be blank")
    trigger_source = payload.trigger.source.strip().lower()
    trigger_type = payload.trigger.type.strip()
    if not trigger_source or not trigger_type:
        raise HTTPException(status_code=422, detail="workflow trigger cannot be blank")
    definition = _workflow_definition(
        WorkflowTrigger(source=trigger_source, type=trigger_type), payload.steps
    )

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="project not found")
            try:
                cur.execute(
                    """
                    INSERT INTO workflows(id, project_id, name)
                    VALUES (%s, %s, %s)
                    RETURNING id, project_id, name, enabled, created_at, updated_at
                    """,
                    (workflow_id, project_id, name),
                )
                workflow = cur.fetchone()
                cur.execute(
                    """
                    INSERT INTO workflow_versions(
                        id, project_id, workflow_id, version_number,
                        trigger_source, trigger_type, definition
                    )
                    VALUES (%s, %s, %s, 1, %s, %s, %s)
                    RETURNING id, version_number, trigger_source, trigger_type,
                              definition, created_at
                    """,
                    (
                        version_id,
                        project_id,
                        workflow_id,
                        trigger_source,
                        trigger_type,
                        Jsonb(definition),
                    ),
                )
                version = cur.fetchone()
                cur.execute(
                    """
                    UPDATE workflows
                    SET active_version_id = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (version_id, workflow_id),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise HTTPException(
                    status_code=409,
                    detail="a workflow with this name already exists in the project",
                ) from exc

    assert workflow is not None and version is not None
    return {**workflow, "active_version_id": version_id, "active_version": version}


@app.post("/workflows/{workflow_id}/versions", status_code=status.HTTP_201_CREATED)
def create_workflow_version(
    workflow_id: uuid.UUID,
    payload: WorkflowVersionCreate,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    version_id = uuid.uuid4()
    trigger_source = payload.trigger.source.strip().lower()
    trigger_type = payload.trigger.type.strip()
    definition = _workflow_definition(
        WorkflowTrigger(source=trigger_source, type=trigger_type), payload.steps
    )

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, project_id FROM workflows WHERE id = %s FOR UPDATE",
                (workflow_id,),
            )
            workflow = cur.fetchone()
            if workflow is None:
                raise HTTPException(status_code=404, detail="workflow not found")
            cur.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version FROM workflow_versions WHERE workflow_id = %s",
                (workflow_id,),
            )
            next_version = cur.fetchone()
            assert next_version is not None
            cur.execute(
                """
                INSERT INTO workflow_versions(
                    id, project_id, workflow_id, version_number,
                    trigger_source, trigger_type, definition
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, project_id, workflow_id, version_number,
                          trigger_source, trigger_type, definition, created_at
                """,
                (
                    version_id,
                    workflow["project_id"],
                    workflow_id,
                    next_version["next_version"],
                    trigger_source,
                    trigger_type,
                    Jsonb(definition),
                ),
            )
            version = cur.fetchone()
            if payload.activate:
                cur.execute(
                    "UPDATE workflows SET active_version_id = %s, updated_at = now() WHERE id = %s",
                    (version_id, workflow_id),
                )

    assert version is not None
    version["active"] = payload.activate
    return version


@app.post("/workflows/{workflow_id}/versions/{version_number}/activate")
def activate_workflow_version(
    workflow_id: uuid.UUID,
    version_number: int,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, workflow_id, version_number
                FROM workflow_versions
                WHERE workflow_id = %s AND version_number = %s
                """,
                (workflow_id, version_number),
            )
            version = cur.fetchone()
            if version is None:
                raise HTTPException(status_code=404, detail="workflow version not found")
            cur.execute(
                """
                UPDATE workflows
                SET active_version_id = %s, updated_at = now()
                WHERE id = %s
                RETURNING id, project_id, name, enabled, active_version_id, created_at, updated_at
                """,
                (version["id"], workflow_id),
            )
            workflow = cur.fetchone()
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return workflow


@app.get("/projects/{project_id}/workflows")
def list_workflows(
    project_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> list[dict[str, Any]]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT w.id, w.project_id, w.name, w.enabled, w.active_version_id,
                       v.version_number AS active_version_number,
                       v.trigger_source, v.trigger_type,
                       w.created_at, w.updated_at
                FROM workflows AS w
                LEFT JOIN workflow_versions AS v ON v.id = w.active_version_id
                WHERE w.project_id = %s
                ORDER BY w.created_at DESC
                """,
                (project_id,),
            )
            return list(cur.fetchall())


@app.get("/workflows/{workflow_id}")
def get_workflow(
    workflow_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT w.id, w.project_id, w.name, w.enabled, w.active_version_id,
                       v.version_number AS active_version_number,
                       v.trigger_source, v.trigger_type, v.definition,
                       w.created_at, w.updated_at
                FROM workflows AS w
                LEFT JOIN workflow_versions AS v ON v.id = w.active_version_id
                WHERE w.id = %s
                """,
                (workflow_id,),
            )
            workflow = cur.fetchone()
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return workflow


@app.get("/workflows/{workflow_id}/versions")
def list_workflow_versions(
    workflow_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> list[dict[str, Any]]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.id, v.project_id, v.workflow_id, v.version_number,
                       v.trigger_source, v.trigger_type, v.definition, v.created_at,
                       (w.active_version_id = v.id) AS active
                FROM workflow_versions AS v
                JOIN workflows AS w ON w.id = v.workflow_id
                WHERE v.workflow_id = %s
                ORDER BY v.version_number
                """,
                (workflow_id,),
            )
            return list(cur.fetchall())


@app.get("/projects/{project_id}/workflow-runs")
def list_workflow_runs(
    project_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
    limit: int = 50,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT wr.id, wr.project_id, wr.workflow_id, wr.workflow_version_id,
                       v.version_number, wr.event_id, wr.replay_execution_id,
                       wr.status, wr.error, wr.created_at, wr.started_at, wr.completed_at
                FROM workflow_runs AS wr
                JOIN workflow_versions AS v ON v.id = wr.workflow_version_id
                WHERE wr.project_id = %s
                ORDER BY wr.created_at DESC
                LIMIT %s
                """,
                (project_id, limit),
            )
            return list(cur.fetchall())


@app.get("/workflow-runs/{run_id}")
def get_workflow_run(
    run_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT wr.id, wr.project_id, wr.workflow_id, wr.workflow_version_id,
                       v.version_number, wr.event_id, wr.replay_execution_id,
                       wr.status, wr.context, wr.error,
                       wr.created_at, wr.started_at, wr.completed_at
                FROM workflow_runs AS wr
                JOIN workflow_versions AS v ON v.id = wr.workflow_version_id
                WHERE wr.id = %s
                """,
                (run_id,),
            )
            run = cur.fetchone()
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    return run


@app.get("/workflow-runs/{run_id}/steps")
def list_workflow_steps(
    run_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> list[dict[str, Any]]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, workflow_run_id, step_index, step_type,
                       config, status, output, error, started_at, completed_at
                FROM step_runs
                WHERE workflow_run_id = %s
                ORDER BY step_index
                """,
                (run_id,),
            )
            return list(cur.fetchall())


@app.post("/events/{event_id}/workflow-runs", status_code=status.HTTP_202_ACCEPTED)
def schedule_event_workflows(
    event_id: uuid.UUID,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, project_id FROM events WHERE id = %s", (event_id,))
            event = cur.fetchone()
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    run_ids = schedule_workflows_for_event(event["project_id"], event_id, mode="current")
    return {"event_id": event_id, "scheduled_run_ids": run_ids}


@app.post("/ingest/{endpoint_token}", status_code=status.HTTP_202_ACCEPTED)
async def ingest_webhook(endpoint_token: str, request: Request) -> dict[str, Any]:
    if len(endpoint_token) < 20 or len(endpoint_token) > 200:
        raise HTTPException(status_code=404, detail="endpoint not found")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_INGEST_BYTES:
                raise HTTPException(status_code=413, detail="payload too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid content-length") from None

    body = await request.body()
    if len(body) > MAX_INGEST_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")

    request_id = uuid.uuid4()
    candidate_event_id = uuid.uuid4()
    candidate_trace_id = uuid.uuid4()
    candidate_job_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    endpoint_token_hash = token_hash(endpoint_token)

    delivery_id = (
        request.headers.get("x-github-delivery")
        or request.headers.get("x-eventforge-delivery-id")
        or request.headers.get("idempotency-key")
    )
    if delivery_id is not None:
        delivery_id = delivery_id.strip()
        if not delivery_id or len(delivery_id) > 255:
            raise HTTPException(status_code=400, detail="invalid delivery id")

    event_type = (
        request.headers.get("x-github-event")
        or request.headers.get("x-eventforge-event-type")
        or "unknown"
    ).strip()
    if not event_type or len(event_type) > 120:
        raise HTTPException(status_code=400, detail="invalid event type")

    content_type = request.headers.get("content-type")
    payload_sha256 = hashlib.sha256(body).hexdigest()
    headers = safe_ingest_headers(request)

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, source
                FROM ingress_endpoints
                WHERE token_hash = %s AND enabled = true
                """,
                (endpoint_token_hash,),
            )
            endpoint = cur.fetchone()
            if endpoint is None:
                raise HTTPException(status_code=404, detail="endpoint not found")

            cur.execute(
                """
                INSERT INTO events(
                    id, project_id, endpoint_id, source, type,
                    provider_delivery_id, trace_id, headers
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id, trace_id
                """,
                (
                    candidate_event_id,
                    endpoint["project_id"],
                    endpoint["id"],
                    endpoint["source"],
                    event_type,
                    delivery_id,
                    candidate_trace_id,
                    Jsonb(headers),
                ),
            )
            inserted = cur.fetchone()

            if inserted is None:
                if delivery_id is None:
                    raise HTTPException(status_code=500, detail="event insert conflict")

                cur.execute(
                    """
                    SELECT id, trace_id
                    FROM events
                    WHERE endpoint_id = %s AND provider_delivery_id = %s
                    """,
                    (endpoint["id"], delivery_id),
                )
                existing = cur.fetchone()
                if existing is None:
                    raise HTTPException(status_code=500, detail="duplicate event lookup failed")

                cur.execute(
                    """
                    INSERT INTO ingress_attempts(
                        id, project_id, endpoint_id, event_id,
                        provider_delivery_id, request_id, outcome, response_code
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'DUPLICATE', 202)
                    """,
                    (
                        attempt_id,
                        endpoint["project_id"],
                        endpoint["id"],
                        existing["id"],
                        delivery_id,
                        request_id,
                    ),
                )
                cur.execute(
                    "UPDATE ingress_endpoints SET last_received_at = now() WHERE id = %s",
                    (endpoint["id"],),
                )
                result = {
                    "status": "accepted",
                    "duplicate": True,
                    "event_id": existing["id"],
                    "trace_id": existing["trace_id"],
                    "request_id": request_id,
                }
            else:
                cur.execute(
                    """
                    INSERT INTO event_payloads(
                        event_id, project_id, content_type, body, size_bytes, sha256
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        candidate_event_id,
                        endpoint["project_id"],
                        content_type,
                        body,
                        len(body),
                        payload_sha256,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO jobs(id, project_id, event_id, kind, status)
                    VALUES (%s, %s, %s, 'PROCESS_EVENT', 'PENDING')
                    """,
                    (candidate_job_id, endpoint["project_id"], candidate_event_id),
                )
                cur.execute(
                    """
                    INSERT INTO ingress_attempts(
                        id, project_id, endpoint_id, event_id,
                        provider_delivery_id, request_id, outcome, response_code
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'ACCEPTED', 202)
                    """,
                    (
                        attempt_id,
                        endpoint["project_id"],
                        endpoint["id"],
                        candidate_event_id,
                        delivery_id,
                        request_id,
                    ),
                )
                cur.execute(
                    "UPDATE ingress_endpoints SET last_received_at = now() WHERE id = %s",
                    (endpoint["id"],),
                )
                result = {
                    "status": "accepted",
                    "duplicate": False,
                    "event_id": candidate_event_id,
                    "trace_id": candidate_trace_id,
                    "request_id": request_id,
                }

    # psycopg commits when the connection context exits successfully. Returning
    # only here guarantees the 202 response is sent after durable DB commit.
    return result
