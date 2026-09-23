from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

from app.db import db_connect
from app.jobs import claim_job

BASE_URL = os.getenv("EVENTFORGE_RECOVERY_BASE_URL", "http://api:8000").rstrip("/")
ADMIN_USER = os.getenv("EVENTFORGE_ADMIN_USER", "eventforge")
ADMIN_PASSWORD = os.getenv("EVENTFORGE_ADMIN_PASSWORD", "eventforge_dev_only_admin")


def admin_headers() -> dict[str, str]:
    token = base64.b64encode(f"{ADMIN_USER}:{ADMIN_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def request_json(path: str, *, method: str = "GET", body: Any = None) -> tuple[int, Any]:
    payload = None if body is None else json.dumps(body).encode()
    headers = admin_headers()
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE_URL + path, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else None


def main() -> int:
    suffix = uuid.uuid4().hex[:12]
    project_id: str | None = None
    try:
        status, project = request_json("/projects", method="POST", body={"name": f"v08-recovery-probe-{suffix}"})
        if status != 201:
            raise RuntimeError(f"project creation failed: {status} {project}")
        project_id = project["id"]

        status, endpoint = request_json(
            f"/projects/{project_id}/webhook-endpoints",
            method="POST",
            body={"name": f"recovery-{suffix}", "source": "recovery-probe"},
        )
        if status != 201:
            raise RuntimeError(f"endpoint creation failed: {status} {endpoint}")

        request = urllib.request.Request(
            BASE_URL + "/ingest",
            data=b'{"probe":"recovery"}',
            headers={
                "Content-Type": "application/json",
                "X-EventForge-Endpoint-Token": endpoint["endpoint_token"],
                "X-EventForge-Delivery-ID": f"recovery-{suffix}",
                "X-EventForge-Event-Type": "reliability.recovery",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            event = json.loads(response.read())
        event_id = event["event_id"]

        kind = f"RELIABILITY_PROBE_{suffix}"
        job_id = uuid.uuid4()
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO jobs(id, project_id, event_id, kind, status, max_attempts)
                    VALUES (%s, %s, %s, %s, 'PENDING', 3)
                    """,
                    (job_id, project_id, event_id, kind),
                )
        claimed = claim_job("v08-recovery-probe", kinds=(kind,), lease_seconds=30)
        if claimed is None:
            raise RuntimeError("probe job was not claimable")
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
                    (job_id,),
                )

        status, recovery = request_json("/operations/recover-jobs", method="POST")
        if status != 200:
            raise RuntimeError(f"recovery endpoint failed: {status} {recovery}")
        status, health = request_json(f"/projects/{project_id}/queue-health")
        if status != 200:
            raise RuntimeError(f"queue health failed: {status} {health}")
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, recovery_count, last_recovered_at FROM jobs WHERE id = %s",
                    (job_id,),
                )
                row = cur.fetchone()
        if row is None or row["status"] != "PENDING" or row["recovery_count"] != 1:
            raise RuntimeError(f"unexpected recovered job state: {row}")

        print(json.dumps({"recovery": recovery, "job": row, "queue_health": health}, default=str, separators=(",", ":")))
        return 0
    finally:
        if project_id is not None:
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))


if __name__ == "__main__":
    raise SystemExit(main())
