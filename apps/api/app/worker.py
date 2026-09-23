from __future__ import annotations

import json
import os
import signal
import socket
import threading
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
    renew_lease,
)
from app.workflows import (
    process_workflow_step_job,
    record_workflow_job_failure,
    schedule_workflows_for_event,
)

POLL_SECONDS = float(os.getenv("EVENTFORGE_WORKER_POLL_SECONDS", "0.5"))
LEASE_SECONDS = int(os.getenv("EVENTFORGE_JOB_LEASE_SECONDS", "30"))
RETRY_BASE_SECONDS = float(os.getenv("EVENTFORGE_RETRY_BASE_SECONDS", "2"))
RETRY_MAX_SECONDS = float(os.getenv("EVENTFORGE_RETRY_MAX_SECONDS", "600"))
HEARTBEAT_SECONDS = float(os.getenv("EVENTFORGE_JOB_HEARTBEAT_SECONDS", "10"))
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
    """Validate the durable event/payload and schedule matching FlowTrace runs."""
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

    schedule_workflows_for_event(
        job["project_id"],
        job["event_id"],
        mode="current",
    )


def process_replay_job(job: dict[str, Any]) -> None:
    """Resolve ReplayDB original/current intent to durable FlowTrace runs."""
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
            replay = cur.fetchone()
            if replay is None:
                raise RuntimeError("replay source event or payload is missing")

    schedule_workflows_for_event(
        job["project_id"],
        job["event_id"],
        replay_execution_id=replay_execution_id,
        mode=replay["workflow_version_mode"],
    )


def process_job(job: dict[str, Any]) -> None:
    if job["kind"] == "PROCESS_EVENT":
        process_event_job(job)
        return
    if job["kind"] == "REPLAY_EVENT":
        process_replay_job(job)
        return
    if job["kind"] == "RUN_WORKFLOW_STEP":
        process_workflow_step_job(job)
        return
    raise RuntimeError(f"unsupported job kind: {job['kind']}")



def _heartbeat_job_lease(
    job: dict[str, Any],
    heartbeat_stop: threading.Event,
    heartbeat_lost: threading.Event,
) -> None:
    """Renew a claimed job lease while its handler is executing."""
    while not heartbeat_stop.wait(HEARTBEAT_SECONDS):
        try:
            renewed = renew_lease(
                job["id"],
                job["lease_token"],
                lease_seconds=LEASE_SECONDS,
            )
        except Exception as exc:
            log_record(
                "WARN",
                "job_heartbeat",
                "error",
                job_id=job["id"],
                job_kind=job["kind"],
                error=str(exc),
            )
            continue
        if not renewed:
            heartbeat_lost.set()
            log_record(
                "WARN",
                "job_heartbeat",
                "lost_lease",
                job_id=job["id"],
                job_kind=job["kind"],
            )
            return
        log_record(
            "INFO",
            "job_heartbeat",
            "renewed",
            job_id=job["id"],
            job_kind=job["kind"],
        )


def _stop_heartbeat(heartbeat_stop: threading.Event, thread: threading.Thread) -> None:
    heartbeat_stop.set()
    thread.join(timeout=max(1.0, min(HEARTBEAT_SECONDS + 1.0, 5.0)))


def request_stop(signum: int, _frame: Any) -> None:
    log_record("INFO", "worker_signal", "stopping", signal=signum)
    stop_event.set()


def run() -> None:
    if LEASE_SECONDS < 2:
        raise RuntimeError("EVENTFORGE_JOB_LEASE_SECONDS must be at least 2")
    if HEARTBEAT_SECONDS <= 0 or HEARTBEAT_SECONDS >= LEASE_SECONDS:
        raise RuntimeError("EVENTFORGE_JOB_HEARTBEAT_SECONDS must be > 0 and less than the lease duration")

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    log_record(
        "INFO",
        "worker_start",
        "ok",
        lease_seconds=LEASE_SECONDS,
        heartbeat_seconds=HEARTBEAT_SECONDS,
    )

    while not stop_event.is_set():
        recovery = recover_expired_leases()
        if recovery["requeued"] or recovery["dead_lettered"]:
            log_record("WARN", "recover_expired_leases", "recovered", **recovery)

        promoted = promote_due_retries()
        if promoted:
            log_record("INFO", "promote_due_retries", "promoted", count=promoted)

        job = claim_job(
            WORKER_ID,
            lease_seconds=LEASE_SECONDS,
            kinds=("PROCESS_EVENT", "REPLAY_EVENT", "RUN_WORKFLOW_STEP"),
        )
        if job is None:
            stop_event.wait(POLL_SECONDS)
            continue

        base_fields = {
            "project_id": job["project_id"],
            "event_id": job["event_id"],
            "job_id": job["id"],
            "job_kind": job["kind"],
            "attempt": job["attempt_count"],
        }
        if job.get("replay_execution_id") is not None:
            base_fields["replay_execution_id"] = job["replay_execution_id"]
        if job.get("workflow_run_id") is not None:
            base_fields["workflow_run_id"] = job["workflow_run_id"]
        if job.get("step_run_id") is not None:
            base_fields["step_run_id"] = job["step_run_id"]
        log_record("INFO", "job_claim", "claimed", **base_fields)

        heartbeat_stop = threading.Event()
        heartbeat_lost = threading.Event()
        heartbeat_thread = threading.Thread(
            target=_heartbeat_job_lease,
            args=(job, heartbeat_stop, heartbeat_lost),
            name=f"eventforge-heartbeat-{job['id']}",
            daemon=True,
        )
        heartbeat_thread.start()

        try:
            process_job(job)
            _stop_heartbeat(heartbeat_stop, heartbeat_thread)
            if heartbeat_lost.is_set():
                raise LostLeaseError("job lease was lost while handler was executing")
            complete_job(job["id"], job["lease_token"])
            log_record("INFO", "job_execute", "success", **base_fields)
        except LostLeaseError as exc:
            _stop_heartbeat(heartbeat_stop, heartbeat_thread)
            log_record("WARN", "job_execute", "lost_lease", error=str(exc), **base_fields)
        except Exception as exc:  # worker boundary: persist failure instead of crashing the loop
            _stop_heartbeat(heartbeat_stop, heartbeat_thread)
            if heartbeat_lost.is_set():
                log_record(
                    "WARN",
                    "job_execute",
                    "lost_lease",
                    error=str(exc),
                    **base_fields,
                )
                continue
            try:
                state = fail_job(
                    job["id"],
                    job["lease_token"],
                    str(exc),
                    base_delay_seconds=RETRY_BASE_SECONDS,
                    max_delay_seconds=RETRY_MAX_SECONDS,
                )
                record_workflow_job_failure(job, state, str(exc))
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
