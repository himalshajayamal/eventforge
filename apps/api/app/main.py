import hashlib
import os
import secrets
import uuid
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

APP_VERSION = "0.2.0"

from app.db import db_connect

ADMIN_USER = os.getenv("EVENTFORGE_ADMIN_USER", "eventforge")
ADMIN_PASSWORD = os.getenv("EVENTFORGE_ADMIN_PASSWORD", "eventforge_dev_only_admin")
MAX_INGEST_BYTES = int(os.getenv("EVENTFORGE_MAX_INGEST_BYTES", "1048576"))

app = FastAPI(
    title="EventForge Control API",
    version=APP_VERSION,
    description="v0.2 HookLedger reliable jobs and retries",
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
        "status": "reliable-jobs",
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
                    SELECT id, project_id, event_id, kind, status,
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
                    SELECT id, project_id, event_id, kind, status,
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
                SELECT id, project_id, event_id, kind, status,
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
