from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db import db_connect
from app.jobs import (
    LostLeaseError,
    claim_job,
    complete_job,
    fail_job,
    promote_due_retries,
    recover_expired_leases,
)

POLL_SECONDS = float(os.getenv("EVENTFORGE_WORKER_POLL_SECONDS", "0.5"))
LEASE_SECONDS = int(os.getenv("EVENTFORGE_JOB_LEASE_SECONDS", "30"))
RETRY_BASE_SECONDS = float(os.getenv("EVENTFORGE_RETRY_BASE_SECONDS", "2"))
RETRY_MAX_SECONDS = float(os.getenv("EVENTFORGE_RETRY_MAX_SECONDS", "600"))
WORKER_ID = os.getenv(
    "EVENTFORGE_WORKER_ID",
    f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}",
)

stop_event = threading.Event()


def log_record(level: str, operation: str, result: str, **fields: Any) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "severity": level,
        "service": "eventforge-worker",
        "worker_id": WORKER_ID,
        "operation": operation,
        "result": result,
        **fields,
    }
    print(json.dumps(record, default=str, separators=(",", ":")), flush=True)


def process_event_job(job: dict[str, Any]) -> None:
    """v0.2 processor: validate that the durable event/payload exists.

    Actual workflow actions arrive in later milestones. v0.2 focuses on reliable
    queue ownership, leases, retries and attempt history.
    """
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, p.event_id AS payload_event_id
                FROM events AS e
                JOIN event_payloads AS p ON p.event_id = e.id
                WHERE e.id = %s AND e.project_id = %s
                """,
                (job["event_id"], job["project_id"]),
            )
            if cur.fetchone() is None:
                raise RuntimeError("event payload is missing")


def process_replay_job(job: dict[str, Any]) -> None:
    """Validate that a replay execution can read the immutable source payload.

    v0.3 intentionally does not execute workflow steps yet; FlowTrace arrives in
    v0.4. Completing this job proves that a distinct replay execution can be
    scheduled from a historical event without rewriting the event or payload.
    """
    replay_execution_id = job.get("replay_execution_id")
    if replay_execution_id is None:
        raise RuntimeError("replay job is missing replay_execution_id")

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.replay_of, r.workflow_version_mode, p.sha256
                FROM replay_executions AS r
                JOIN events AS e ON e.id = r.replay_of
                JOIN event_payloads AS p ON p.event_id = e.id
                WHERE r.id = %s
                  AND r.project_id = %s
                  AND r.replay_of = %s
                """,
                (replay_execution_id, job["project_id"], job["event_id"]),
            )
            if cur.fetchone() is None:
                raise RuntimeError("replay source event or payload is missing")


def process_job(job: dict[str, Any]) -> None:
    if job["kind"] == "PROCESS_EVENT":
        process_event_job(job)
        return
    if job["kind"] == "REPLAY_EVENT":
        process_replay_job(job)
        return
    raise RuntimeError(f"unsupported job kind: {job['kind']}")


def request_stop(signum: int, _frame: Any) -> None:
    log_record("INFO", "worker_signal", "stopping", signal=signum)
    stop_event.set()


def run() -> None:
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    log_record("INFO", "worker_start", "ok", lease_seconds=LEASE_SECONDS)

    while not stop_event.is_set():
        recovery = recover_expired_leases()
        if recovery["requeued"] or recovery["dead_lettered"]:
            log_record("WARN", "recover_expired_leases", "recovered", **recovery)

        promoted = promote_due_retries()
        if promoted:
            log_record("INFO", "promote_due_retries", "promoted", count=promoted)

        job = claim_job(WORKER_ID, lease_seconds=LEASE_SECONDS, kinds=("PROCESS_EVENT", "REPLAY_EVENT"))
        if job is None:
            stop_event.wait(POLL_SECONDS)
            continue

        base_fields = {
            "project_id": job["project_id"],
            "event_id": job["event_id"],
            "job_id": job["id"],
            "attempt": job["attempt_count"],
        }
        if job.get("replay_execution_id") is not None:
            base_fields["replay_execution_id"] = job["replay_execution_id"]
        log_record("INFO", "job_claim", "claimed", **base_fields)

        try:
            process_job(job)
            complete_job(job["id"], job["lease_token"])
            log_record("INFO", "job_execute", "success", **base_fields)
        except LostLeaseError as exc:
            log_record("WARN", "job_execute", "lost_lease", error=str(exc), **base_fields)
        except Exception as exc:  # worker boundary: persist failure instead of crashing the loop
            try:
                state = fail_job(
                    job["id"],
                    job["lease_token"],
                    str(exc),
                    base_delay_seconds=RETRY_BASE_SECONDS,
                    max_delay_seconds=RETRY_MAX_SECONDS,
                )
                log_record("ERROR", "job_execute", state.lower(), error=str(exc), **base_fields)
            except LostLeaseError as lease_exc:
                log_record(
                    "WARN",
                    "job_execute",
                    "lost_lease",
                    error=str(lease_exc),
                    **base_fields,
                )

    log_record("INFO", "worker_stop", "ok")


if __name__ == "__main__":
    run()
