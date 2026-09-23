from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.db import db_connect

EVENTFORGE_ENV = os.getenv("EVENTFORGE_ENV", "development").strip().lower()
ADMIN_USER = os.getenv("EVENTFORGE_ADMIN_USER", "eventforge")
ADMIN_PASSWORD = os.getenv("EVENTFORGE_ADMIN_PASSWORD", "eventforge_dev_only_admin")
WEBHOOK_MASTER_SECRET = os.getenv(
    "EVENTFORGE_WEBHOOK_MASTER_SECRET",
    "eventforge_dev_only_webhook_master_secret_change_me",
)
REQUIRE_SIGNED_WEBHOOKS = (
    EVENTFORGE_ENV == "production"
    or os.getenv("EVENTFORGE_REQUIRE_SIGNED_WEBHOOKS", "0") == "1"
)

DEV_ADMIN_PASSWORD = "eventforge_dev_only_admin"
DEV_WEBHOOK_MASTER_SECRET = "eventforge_dev_only_webhook_master_secret_change_me"


@dataclass(frozen=True)
class AuthContext:
    kind: str
    project_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"


def validate_runtime_security(
    environment: str = EVENTFORGE_ENV,
    admin_password: str = ADMIN_PASSWORD,
    webhook_master_secret: str = WEBHOOK_MASTER_SECRET,
) -> None:
    """Reject known-development credentials when EventForge runs as production."""
    env = environment.strip().lower()
    if env not in {"development", "test", "production"}:
        raise RuntimeError("EVENTFORGE_ENV must be development, test, or production")
    if env != "production":
        return
    if len(admin_password) < 16 or secrets.compare_digest(admin_password, DEV_ADMIN_PASSWORD):
        raise RuntimeError("production requires a non-default EVENTFORGE_ADMIN_PASSWORD of at least 16 characters")
    if len(webhook_master_secret) < 32 or secrets.compare_digest(
        webhook_master_secret, DEV_WEBHOOK_MASTER_SECRET
    ):
        raise RuntimeError("production requires a non-default EVENTFORGE_WEBHOOK_MASTER_SECRET of at least 32 characters")


validate_runtime_security()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_project_api_key() -> str:
    return "efk_" + secrets.token_urlsafe(32)


def derive_webhook_signing_secret(endpoint_id: uuid.UUID, version: int) -> str:
    message = f"eventforge:webhook:{endpoint_id}:{version}".encode("utf-8")
    digest = hmac.new(WEBHOOK_MASTER_SECRET.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _decode_basic(header: str) -> tuple[str, str] | None:
    try:
        encoded = header.split(" ", 1)[1]
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
        return username, password
    except (ValueError, UnicodeDecodeError, base64.binascii.Error, IndexError):
        return None


def _authenticate_basic(header: str) -> AuthContext | None:
    decoded = _decode_basic(header)
    if decoded is None:
        return None
    username, password = decoded
    username_ok = secrets.compare_digest(username, ADMIN_USER)
    password_ok = secrets.compare_digest(password, ADMIN_PASSWORD)
    if username_ok and password_ok:
        return AuthContext(kind="admin")
    return None


def _authenticate_bearer(header: str) -> AuthContext | None:
    try:
        token = header.split(" ", 1)[1].strip()
    except IndexError:
        return None
    if not token.startswith("efk_") or len(token) < 24 or len(token) > 200:
        return None
    digest = token_hash(token)
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id
                FROM project_api_keys
                WHERE key_hash = %s
                  AND revoked_at IS NULL
                """,
                (digest,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                """
                UPDATE project_api_keys
                SET last_used_at = now()
                WHERE id = %s
                  AND (last_used_at IS NULL OR last_used_at < now() - interval '5 minutes')
                """,
                (row["id"],),
            )
    return AuthContext(kind="project_api_key", project_id=row["project_id"], api_key_id=row["id"])


def authenticate_request(request: Request) -> AuthContext | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        return _authenticate_basic(header)
    if header.lower().startswith("bearer "):
        return _authenticate_bearer(header)
    return None


_PROJECT_PATH = re.compile(r"^/projects/([0-9a-fA-F-]{36})(?:/|$)")
_OBJECT_PATHS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^/events/([0-9a-fA-F-]{36})(?:/|$)"), "events"),
    (re.compile(r"^/jobs/([0-9a-fA-F-]{36})(?:/|$)"), "jobs"),
    (re.compile(r"^/replays/([0-9a-fA-F-]{36})(?:/|$)"), "replay_executions"),
    (re.compile(r"^/workflows/([0-9a-fA-F-]{36})(?:/|$)"), "workflows"),
    (re.compile(r"^/workflow-runs/([0-9a-fA-F-]{36})(?:/|$)"), "workflow_runs"),
    (re.compile(r"^/webhook-endpoints/([0-9a-fA-F-]{36})(?:/|$)"), "ingress_endpoints"),
)


def _object_project_id(table: str, object_id: uuid.UUID) -> uuid.UUID | None:
    allowed_tables = {
        "events",
        "jobs",
        "replay_executions",
        "workflows",
        "workflow_runs",
        "ingress_endpoints",
    }
    if table not in allowed_tables:
        raise RuntimeError("unsupported authorization table")
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT project_id FROM {table} WHERE id = %s", (object_id,))
            row = cur.fetchone()
    return None if row is None else row["project_id"]


def authorize_project_scope(request: Request, auth: AuthContext) -> bool:
    if auth.is_admin:
        return True
    if auth.project_id is None:
        return False

    path = request.url.path
    project_match = _PROJECT_PATH.match(path)
    if project_match:
        try:
            requested_project = uuid.UUID(project_match.group(1))
        except ValueError:
            return False
        return requested_project == auth.project_id

    for pattern, table in _OBJECT_PATHS:
        match = pattern.match(path)
        if not match:
            continue
        try:
            object_id = uuid.UUID(match.group(1))
        except ValueError:
            return False
        return _object_project_id(table, object_id) == auth.project_id

    # A project key never gets ambient/global access to an unscoped control-plane route.
    return False


def _is_public(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True
    path = request.url.path
    if path in {"/", "/health", "/ready", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}:
        return True
    return path == "/ingest" or path.startswith("/ingest/")


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "authentication required"},
        headers={"WWW-Authenticate": 'Basic realm="EventForge", Bearer'},
    )


def _not_found() -> JSONResponse:
    # 404 intentionally avoids revealing whether a cross-project object exists.
    return JSONResponse(status_code=404, content={"detail": "resource not found"})


async def security_middleware(request: Request, call_next):
    if not _is_public(request):
        auth = authenticate_request(request)
        if auth is None:
            return _unauthorized()
        request.state.eventforge_auth = auth
        if not authorize_project_scope(request, auth):
            # Admins already returned True. Project keys only continue when the route resolves to their project.
            return _not_found()

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if EVENTFORGE_ENV == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def require_control_access(request: Request) -> None:
    if not hasattr(request.state, "eventforge_auth"):
        raise HTTPException(status_code=401, detail="authentication required")


def require_admin(request: Request) -> None:
    auth: Any = getattr(request.state, "eventforge_auth", None)
    if auth is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if not auth.is_admin:
        raise HTTPException(status_code=403, detail="administrator credentials required")
