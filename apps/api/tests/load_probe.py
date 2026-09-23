from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from app.db import db_connect

BASE_URL = os.getenv("EVENTFORGE_LOAD_BASE_URL", "http://api:8000").rstrip("/")
ADMIN_USER = os.getenv("EVENTFORGE_ADMIN_USER", "eventforge")
ADMIN_PASSWORD = os.getenv("EVENTFORGE_ADMIN_PASSWORD", "eventforge_dev_only_admin")
REQUESTS = max(1, int(os.getenv("EVENTFORGE_LOAD_REQUESTS", "200")))
CONCURRENCY = max(1, min(int(os.getenv("EVENTFORGE_LOAD_CONCURRENCY", "20")), 100))
DUPLICATE_REQUESTS = max(2, int(os.getenv("EVENTFORGE_LOAD_DUPLICATES", "20")))


def request_json(path: str, *, method: str = "GET", body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    req_headers = dict(headers or {})
    if payload is not None:
        req_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(BASE_URL + path, data=payload, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body_value = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body_value = raw.decode(errors="replace")
        return exc.code, body_value


def admin_headers() -> dict[str, str]:
    token = base64.b64encode(f"{ADMIN_USER}:{ADMIN_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def ingest(endpoint_token: str, delivery_id: str, index: int) -> tuple[int, dict[str, Any]]:
    status, body = request_json(
        "/ingest",
        method="POST",
        headers={
            "X-EventForge-Endpoint-Token": endpoint_token,
            "X-EventForge-Delivery-ID": delivery_id,
            "X-EventForge-Event-Type": "reliability.load",
        },
        body={"index": index, "probe": "v0.8"},
    )
    return status, body


def main() -> int:
    suffix = uuid.uuid4().hex[:12]
    project_id: str | None = None
    try:
        status, project = request_json(
            "/projects",
            method="POST",
            headers=admin_headers(),
            body={"name": f"v08-load-probe-{suffix}"},
        )
        if status != 201:
            raise RuntimeError(f"project creation failed: HTTP {status} {project}")
        project_id = project["id"]

        status, endpoint = request_json(
            f"/projects/{project_id}/webhook-endpoints",
            method="POST",
            headers=admin_headers(),
            body={"name": f"load-{suffix}", "source": "load-probe"},
        )
        if status != 201:
            raise RuntimeError(f"endpoint creation failed: HTTP {status} {endpoint}")
        endpoint_token = endpoint["endpoint_token"]

        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
            unique = list(
                executor.map(
                    lambda i: ingest(endpoint_token, f"load-{suffix}-{i}", i),
                    range(REQUESTS),
                )
            )
        elapsed = time.perf_counter() - started
        if any(code != 202 for code, _ in unique):
            failures = [(code, body) for code, body in unique if code != 202][:5]
            raise RuntimeError(f"unique ingest failures: {failures}")

        duplicate_delivery = f"duplicate-{suffix}"
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(CONCURRENCY, DUPLICATE_REQUESTS)) as executor:
            duplicates = list(
                executor.map(
                    lambda i: ingest(endpoint_token, duplicate_delivery, i),
                    range(DUPLICATE_REQUESTS),
                )
            )
        if any(code != 202 for code, _ in duplicates):
            raise RuntimeError("duplicate burst returned a non-202 response")
        duplicate_bodies = [body for _, body in duplicates]
        if len({body["event_id"] for body in duplicate_bodies}) != 1:
            raise RuntimeError("duplicate burst produced multiple event IDs")
        if sum(body["duplicate"] is False for body in duplicate_bodies) != 1:
            raise RuntimeError("duplicate burst did not produce exactly one original event")

        drained = False
        health: dict[str, Any] = {}
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status, health = request_json(
                f"/projects/{project_id}/queue-health",
                headers=admin_headers(),
            )
            if status != 200:
                raise RuntimeError(f"queue health failed: HTTP {status} {health}")
            if health["pending"] == 0 and health["running"] == 0 and health["retry_wait"] == 0:
                drained = True
                break
            time.sleep(0.25)

        print(
            json.dumps(
                {
                    "project_id": project_id,
                    "unique_requests": REQUESTS,
                    "duplicate_requests": DUPLICATE_REQUESTS,
                    "concurrency": CONCURRENCY,
                    "unique_elapsed_seconds": round(elapsed, 3),
                    "unique_requests_per_second": round(REQUESTS / elapsed, 2) if elapsed else None,
                    "duplicate_event_id": duplicate_bodies[0]["event_id"],
                    "queue_drained_within_30s": drained,
                    "queue_health": health,
                },
                default=str,
                separators=(",", ":"),
            )
        )
        return 0 if drained and health.get("dead_lettered", 0) == 0 else 2
    finally:
        if project_id is not None:
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))


if __name__ == "__main__":
    raise SystemExit(main())
