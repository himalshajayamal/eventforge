from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import os
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from app.db import db_connect

HTTP_RESPONSE_LIMIT = int(os.getenv("EVENTFORGE_HTTP_ACTION_MAX_RESPONSE_BYTES", "1048576"))
ALLOW_PRIVATE_HTTP = os.getenv("EVENTFORGE_ALLOW_PRIVATE_HTTP_ACTIONS", "0") == "1"
ALLOW_INSECURE_HTTP = os.getenv("EVENTFORGE_ALLOW_INSECURE_HTTP_ACTIONS", "0") == "1"


class WorkflowActionError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _json_context_for_event(event_id: uuid.UUID) -> dict[str, Any]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.body, p.content_type, p.sha256
                FROM event_payloads AS p
                WHERE p.event_id = %s
                """,
                (event_id,),
            )
            payload = cur.fetchone()
    if payload is None:
        raise WorkflowActionError("event payload is missing")

    body = bytes(payload["body"])
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "event_payload_sha256": payload["sha256"],
            "event_payload_content_type": payload["content_type"],
            "event_payload_size": len(body),
        }

    if isinstance(decoded, dict):
        return decoded
    return {"value": decoded}


def _get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = [part for part in path.split(".") if part]
    if not parts:
        raise WorkflowActionError("json_transform path cannot be blank")
    current: dict[str, Any] = target
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def _is_disallowed_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_http_target(url: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    allowed_schemes = {"https"}
    if ALLOW_INSECURE_HTTP:
        allowed_schemes.add("http")
    if parsed.scheme.lower() not in allowed_schemes:
        raise WorkflowActionError("http_request requires https")
    if not parsed.hostname:
        raise WorkflowActionError("http_request URL requires a hostname")
    if parsed.username or parsed.password:
        raise WorkflowActionError("userinfo is not allowed in http_request URLs")

    if not ALLOW_PRIVATE_HTTP:
        try:
            addresses = {
                result[4][0]
                for result in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
            }
        except socket.gaierror as exc:
            raise WorkflowActionError("http_request hostname could not be resolved") from exc
        if not addresses:
            raise WorkflowActionError("http_request hostname did not resolve")
        for address in addresses:
            if _is_disallowed_ip(address):
                raise WorkflowActionError("http_request target resolves to a private or local address")

    return parsed


def execute_http_request(config: dict[str, Any], context: dict[str, Any], step_run_id: uuid.UUID) -> dict[str, Any]:
    method = str(config.get("method", "POST")).upper()
    if method not in {"GET", "POST"}:
        raise WorkflowActionError("http_request method must be GET or POST")

    url = str(config.get("url", ""))
    validate_http_target(url)
    timeout_seconds = float(config.get("timeout_seconds", 5.0))
    if timeout_seconds <= 0 or timeout_seconds > 10:
        raise WorkflowActionError("http_request timeout must be between 0 and 10 seconds")

    headers = {
        "User-Agent": "EventForge/0.5.0",
        "Accept": "application/json, */*;q=0.8",
        "Idempotency-Key": str(step_run_id),
    }
    body: bytes | None = None
    if method == "POST":
        body = json.dumps(context, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            data = response.read(HTTP_RESPONSE_LIMIT + 1)
            if len(data) > HTTP_RESPONSE_LIMIT:
                raise WorkflowActionError("http_request response exceeded size limit")
            if not 200 <= response.status < 300:
                raise WorkflowActionError(f"http_request returned status {response.status}")
            return {
                "status_code": response.status,
                "response_size": len(data),
                "content_type": response.headers.get("content-type"),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
    except urllib.error.HTTPError as exc:
        raise WorkflowActionError(f"http_request returned status {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise WorkflowActionError(f"http_request failed: {exc.reason}") from exc


def snapshot_replay_workflow_selections(
    cur,
    *,
    project_id: uuid.UUID,
    event_id: uuid.UUID,
    replay_execution_id: uuid.UUID,
    mode: str,
) -> int:
    """Freeze ReplayDB -> FlowTrace workflow-version resolution at replay request time.

    `original` reuses the workflow versions that processed the original event.
    `current` snapshots the active matching workflow versions visible when the
    replay is requested. Later workflow activation changes therefore cannot
    silently change the meaning of an already-created replay.
    """
    if mode not in {"original", "current"}:
        raise ValueError("mode must be current or original")

    if mode == "original":
        cur.execute(
            """
            SELECT DISTINCT wr.workflow_id, wr.workflow_version_id
            FROM workflow_runs AS wr
            WHERE wr.project_id = %s
              AND wr.event_id = %s
              AND wr.replay_execution_id IS NULL
            ORDER BY wr.workflow_id, wr.workflow_version_id
            """,
            (project_id, event_id),
        )
    else:
        cur.execute(
            """
            SELECT w.id AS workflow_id, v.id AS workflow_version_id
            FROM events AS e
            JOIN workflows AS w ON w.project_id = e.project_id
            JOIN workflow_versions AS v ON v.id = w.active_version_id
            WHERE e.id = %s
              AND e.project_id = %s
              AND w.enabled = true
              AND (v.trigger_source = e.source OR v.trigger_source = '*')
              AND (v.trigger_type = e.type OR v.trigger_type = '*')
            ORDER BY w.created_at, w.id
            """,
            (event_id, project_id),
        )

    rows = list(cur.fetchall())
    for row in rows:
        cur.execute(
            """
            INSERT INTO replay_workflow_selections(
                replay_execution_id, project_id, workflow_id, workflow_version_id
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (replay_execution_id, workflow_id) DO NOTHING
            """,
            (
                replay_execution_id,
                project_id,
                row["workflow_id"],
                row["workflow_version_id"],
            ),
        )
    return len(rows)


def _candidate_versions(
    project_id: uuid.UUID,
    event_id: uuid.UUID,
    *,
    replay_execution_id: uuid.UUID | None,
    mode: str,
) -> list[dict[str, Any]]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            if replay_execution_id is not None:
                cur.execute(
                    """
                    SELECT workflow_selection_snapshot_at
                    FROM replay_executions
                    WHERE id = %s AND project_id = %s
                    """,
                    (replay_execution_id, project_id),
                )
                replay_row = cur.fetchone()
                if replay_row is None:
                    raise WorkflowActionError("replay execution not found")

                cur.execute(
                    """
                    SELECT s.workflow_id, s.workflow_version_id, v.definition
                    FROM replay_workflow_selections AS s
                    JOIN workflow_versions AS v ON v.id = s.workflow_version_id
                    WHERE s.replay_execution_id = %s
                      AND s.project_id = %s
                    ORDER BY s.created_at, s.workflow_id
                    """,
                    (replay_execution_id, project_id),
                )
                selected = list(cur.fetchall())
                if replay_row["workflow_selection_snapshot_at"] is not None:
                    return selected

                # Compatibility for replay rows created before v0.5.0. Those
                # replays predate selection snapshots, so retain v0.4 behavior.
                if mode == "original":
                    cur.execute(
                        """
                        SELECT DISTINCT wr.workflow_id, wr.workflow_version_id,
                               v.definition
                        FROM workflow_runs AS wr
                        JOIN workflow_versions AS v ON v.id = wr.workflow_version_id
                        WHERE wr.event_id = %s
                          AND wr.project_id = %s
                          AND wr.replay_execution_id IS NULL
                        ORDER BY wr.workflow_id, wr.workflow_version_id
                        """,
                        (event_id, project_id),
                    )
                    return list(cur.fetchall())

            cur.execute(
                """
                SELECT w.id AS workflow_id, v.id AS workflow_version_id,
                       v.definition
                FROM events AS e
                JOIN workflows AS w ON w.project_id = e.project_id
                JOIN workflow_versions AS v ON v.id = w.active_version_id
                WHERE e.id = %s
                  AND e.project_id = %s
                  AND w.enabled = true
                  AND (v.trigger_source = e.source OR v.trigger_source = '*')
                  AND (v.trigger_type = e.type OR v.trigger_type = '*')
                ORDER BY w.created_at, w.id
                """,
                (event_id, project_id),
            )
            return list(cur.fetchall())

def schedule_workflows_for_event(
    project_id: uuid.UUID,
    event_id: uuid.UUID,
    *,
    replay_execution_id: uuid.UUID | None = None,
    mode: str = "current",
    first_job_kind: str = "RUN_WORKFLOW_STEP",
) -> list[uuid.UUID]:
    if mode not in {"current", "original"}:
        raise ValueError("mode must be current or original")

    context = _json_context_for_event(event_id)
    versions = _candidate_versions(
        project_id,
        event_id,
        replay_execution_id=replay_execution_id,
        mode=mode,
    )
    created_runs: list[uuid.UUID] = []

    for candidate in versions:
        definition = candidate["definition"]
        if not isinstance(definition, dict):
            raise WorkflowActionError("workflow definition is invalid")
        steps = definition.get("steps")
        if not isinstance(steps, list) or not steps:
            raise WorkflowActionError("workflow version has no executable steps")

        run_id = uuid.uuid4()
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO workflow_runs(
                        id, project_id, workflow_id, workflow_version_id,
                        event_id, replay_execution_id, context
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    (
                        run_id,
                        project_id,
                        candidate["workflow_id"],
                        candidate["workflow_version_id"],
                        event_id,
                        replay_execution_id,
                        Jsonb(context),
                    ),
                )
                inserted = cur.fetchone()
                if inserted is None:
                    continue

                step_ids: list[uuid.UUID] = []
                for index, step in enumerate(steps):
                    if not isinstance(step, dict) or "type" not in step:
                        raise WorkflowActionError("workflow step definition is invalid")
                    step_id = uuid.uuid4()
                    step_ids.append(step_id)
                    cur.execute(
                        """
                        INSERT INTO step_runs(
                            id, project_id, workflow_run_id, step_index,
                            step_type, config, status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, 'PENDING')
                        """,
                        (
                            step_id,
                            project_id,
                            run_id,
                            index,
                            step["type"],
                            Jsonb(step),
                        ),
                    )

                cur.execute(
                    """
                    INSERT INTO jobs(
                        id, project_id, event_id, workflow_run_id,
                        step_run_id, kind, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'PENDING')
                    """,
                    (
                        uuid.uuid4(),
                        project_id,
                        event_id,
                        run_id,
                        step_ids[0],
                        first_job_kind,
                    ),
                )
        created_runs.append(run_id)

    return created_runs


def _emit_event(
    cur,
    *,
    run: dict[str, Any],
    step: dict[str, Any],
    context: dict[str, Any],
) -> uuid.UUID:
    cur.execute(
        "SELECT id FROM events WHERE emitted_by_step_run_id = %s",
        (step["id"],),
    )
    existing = cur.fetchone()
    if existing is not None:
        return existing["id"]

    event_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    payload = json.dumps(context, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    config = step["config"]
    source = str(config.get("source", "flowtrace"))
    event_type = str(config.get("event_type", "workflow.emitted"))

    cur.execute(
        "SELECT trace_id FROM events WHERE id = %s",
        (run["event_id"],),
    )
    source_event = cur.fetchone()
    if source_event is not None:
        trace_id = source_event["trace_id"]

    cur.execute(
        """
        INSERT INTO events(
            id, project_id, endpoint_id, source, type,
            provider_delivery_id, trace_id, headers,
            parent_event_id, emitted_by_step_run_id
        )
        VALUES (%s, %s, NULL, %s, %s, NULL, %s, %s, %s, %s)
        """,
        (
            event_id,
            run["project_id"],
            source,
            event_type,
            trace_id,
            Jsonb({"eventforge_internal": "flowtrace"}),
            run["event_id"],
            step["id"],
        ),
    )
    cur.execute(
        """
        INSERT INTO event_payloads(
            event_id, project_id, content_type, body, size_bytes, sha256
        )
        VALUES (%s, %s, 'application/json', %s, %s, %s)
        """,
        (
            event_id,
            run["project_id"],
            payload,
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        ),
    )
    cur.execute(
        """
        INSERT INTO jobs(id, project_id, event_id, kind, status)
        VALUES (%s, %s, %s, 'PROCESS_EVENT', 'PENDING')
        ON CONFLICT DO NOTHING
        """,
        (uuid.uuid4(), run["project_id"], event_id),
    )
    return event_id


def process_workflow_step_job(job: dict[str, Any]) -> None:
    workflow_run_id = job.get("workflow_run_id")
    step_run_id = job.get("step_run_id")
    if workflow_run_id is None or step_run_id is None:
        raise WorkflowActionError("workflow step job is missing run identity")

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sr.id, sr.project_id, sr.workflow_run_id, sr.step_index,
                       sr.step_type, sr.config, sr.status,
                       wr.workflow_id, wr.workflow_version_id, wr.event_id,
                       wr.replay_execution_id, wr.context, wr.status AS run_status
                FROM step_runs AS sr
                JOIN workflow_runs AS wr ON wr.id = sr.workflow_run_id
                WHERE sr.id = %s AND wr.id = %s
                FOR UPDATE
                """,
                (step_run_id, workflow_run_id),
            )
            row = cur.fetchone()
            if row is None:
                raise WorkflowActionError("workflow step run not found")
            if row["status"] in {"SUCCESS", "SKIPPED"}:
                return
            if row["run_status"] in {"SUCCESS", "FAILED"}:
                return

            cur.execute(
                """
                UPDATE workflow_runs
                SET status = 'RUNNING',
                    started_at = COALESCE(started_at, now())
                WHERE id = %s
                """,
                (workflow_run_id,),
            )
            cur.execute(
                """
                UPDATE step_runs
                SET status = 'RUNNING',
                    started_at = COALESCE(started_at, now()),
                    error = NULL
                WHERE id = %s
                """,
                (step_run_id,),
            )
            step = dict(row)

    context = copy.deepcopy(step["context"] if isinstance(step["context"], dict) else {})
    config = step["config"]
    step_type = step["step_type"]
    output: dict[str, Any] = {}
    stop_after_condition = False
    delay_seconds = 0.0

    if step_type == "conditional":
        actual = _get_path(context, str(config.get("path", "")))
        expected = config.get("equals")
        matched = actual == expected
        output = {"matched": matched, "actual": actual, "expected": expected}
        stop_after_condition = not matched
    elif step_type == "json_transform":
        updates = config.get("set", {})
        if not isinstance(updates, dict):
            raise WorkflowActionError("json_transform set must be an object")
        for path, value in updates.items():
            _set_path(context, str(path), value)
        output = {"updated_paths": list(updates.keys())}
    elif step_type == "delay":
        delay_seconds = float(config.get("seconds", 0))
        if delay_seconds < 0 or delay_seconds > 3600:
            raise WorkflowActionError("delay seconds must be between 0 and 3600")
        output = {"delay_seconds": delay_seconds}
    elif step_type == "http_request":
        output = execute_http_request(config, context, step_run_id)
    elif step_type == "emit_event":
        output = {}
    else:
        raise WorkflowActionError(f"unsupported workflow step type: {step_type}")

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, event_id, status, context
                FROM workflow_runs
                WHERE id = %s
                FOR UPDATE
                """,
                (workflow_run_id,),
            )
            run = cur.fetchone()
            if run is None:
                raise WorkflowActionError("workflow run disappeared")

            cur.execute(
                """
                SELECT id, step_index, step_type, config, status
                FROM step_runs
                WHERE id = %s
                FOR UPDATE
                """,
                (step_run_id,),
            )
            current_step = cur.fetchone()
            if current_step is None:
                raise WorkflowActionError("workflow step disappeared")
            if current_step["status"] in {"SUCCESS", "SKIPPED"}:
                return

            if step_type == "emit_event":
                emitted_id = _emit_event(cur, run=run, step=current_step, context=context)
                output = {"event_id": str(emitted_id)}

            cur.execute(
                """
                UPDATE step_runs
                SET status = 'SUCCESS',
                    output = %s,
                    error = NULL,
                    completed_at = now()
                WHERE id = %s
                """,
                (Jsonb(output), step_run_id),
            )
            cur.execute(
                "UPDATE workflow_runs SET context = %s WHERE id = %s",
                (Jsonb(context), workflow_run_id),
            )

            if stop_after_condition:
                cur.execute(
                    """
                    UPDATE step_runs
                    SET status = 'SKIPPED', completed_at = now()
                    WHERE workflow_run_id = %s
                      AND step_index > %s
                      AND status = 'PENDING'
                    """,
                    (workflow_run_id, current_step["step_index"]),
                )
                cur.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'SUCCESS', completed_at = now()
                    WHERE id = %s
                    """,
                    (workflow_run_id,),
                )
                return

            cur.execute(
                """
                SELECT id, step_index
                FROM step_runs
                WHERE workflow_run_id = %s
                  AND step_index > %s
                  AND status = 'PENDING'
                ORDER BY step_index
                LIMIT 1
                """,
                (workflow_run_id, current_step["step_index"]),
            )
            next_step = cur.fetchone()
            if next_step is None:
                cur.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'SUCCESS', completed_at = now()
                    WHERE id = %s
                    """,
                    (workflow_run_id,),
                )
                return

            cur.execute(
                """
                INSERT INTO jobs(
                    id, project_id, event_id, workflow_run_id,
                    step_run_id, kind, status, available_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, 'PENDING',
                    now() + (%s * interval '1 second')
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    uuid.uuid4(),
                    run["project_id"],
                    run["event_id"],
                    workflow_run_id,
                    next_step["id"],
                    str(job.get("kind") or "RUN_WORKFLOW_STEP"),
                    delay_seconds,
                ),
            )


def record_workflow_job_failure(job: dict[str, Any], state: str, error: str) -> None:
    step_run_id = job.get("step_run_id")
    workflow_run_id = job.get("workflow_run_id")
    if step_run_id is None or workflow_run_id is None:
        return
    error = (error or "workflow step failed")[:4000]

    with db_connect() as conn:
        with conn.cursor() as cur:
            if state == "RETRY_WAIT":
                cur.execute(
                    """
                    UPDATE step_runs
                    SET status = 'PENDING', error = %s
                    WHERE id = %s AND status <> 'SUCCESS'
                    """,
                    (error, step_run_id),
                )
            elif state == "DEAD_LETTERED":
                cur.execute(
                    """
                    UPDATE step_runs
                    SET status = 'FAILED', error = %s, completed_at = now()
                    WHERE id = %s AND status <> 'SUCCESS'
                    """,
                    (error, step_run_id),
                )
                cur.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'FAILED', error = %s, completed_at = now()
                    WHERE id = %s AND status <> 'SUCCESS'
                    """,
                    (error, workflow_run_id),
                )
