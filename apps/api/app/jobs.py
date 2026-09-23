from __future__ import annotations

import random
import uuid
from typing import Any, Iterable

from app.db import db_connect

JOB_STATUSES = {"PENDING", "RUNNING", "SUCCESS", "RETRY_WAIT", "DEAD_LETTERED"}


class LostLeaseError(RuntimeError):
    """Raised when a worker no longer owns a live lease for a job."""


def retry_delay_seconds(
    attempt_number: int,
    *,
    base_delay_seconds: float = 2.0,
    max_delay_seconds: float = 600.0,
    jitter_seconds: float | None = None,
) -> float:
    """Return exponential backoff plus jitter for a failed attempt.

    attempt_number is 1-based. A failure on attempt 1 schedules the first retry
    at approximately 2 seconds, then 4, 8, 16, ... capped at 600 seconds before
    jitter, matching the v0.2 engineering plan.
    """
    if attempt_number < 1:
        raise ValueError("attempt_number must be >= 1")
    if base_delay_seconds <= 0 or max_delay_seconds <= 0:
        raise ValueError("retry delays must be positive")

    exponential = min(
        max_delay_seconds,
        base_delay_seconds * (2 ** (attempt_number - 1)),
    )
    if jitter_seconds is None:
        jitter_seconds = random.uniform(0.0, 1.0)
    if jitter_seconds < 0:
        raise ValueError("jitter_seconds must be >= 0")
    return exponential + jitter_seconds


def promote_due_retries(*, limit: int = 100) -> int:
    """Move due RETRY_WAIT jobs back to PENDING without double-promoting them."""
    limit = max(1, min(limit, 1000))
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH due AS (
                    SELECT id
                    FROM jobs
                    WHERE status = 'RETRY_WAIT'
                      AND available_at <= now()
                    ORDER BY available_at, created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE jobs AS j
                SET status = 'PENDING',
                    updated_at = now()
                FROM due
                WHERE j.id = due.id
                RETURNING j.id
                """,
                (limit,),
            )
            return len(cur.fetchall())


def recover_expired_leases(*, limit: int = 100) -> dict[str, int]:
    """Recover RUNNING jobs whose worker lease expired.

    If attempts remain the job enters RETRY_WAIT and can be promoted back to
    PENDING. If the attempt budget is exhausted it becomes DEAD_LETTERED.
    """
    limit = max(1, min(limit, 1000))
    requeued = 0
    dead_lettered = 0

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, lease_token, attempt_count, max_attempts
                FROM jobs
                WHERE status = 'RUNNING'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= now()
                ORDER BY lease_expires_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (limit,),
            )
            expired = list(cur.fetchall())

            for job in expired:
                error = "worker lease expired before completion"
                cur.execute(
                    """
                    UPDATE job_attempts
                    SET finished_at = now(),
                        outcome = 'LEASE_EXPIRED',
                        error = %s
                    WHERE job_id = %s
                      AND lease_token = %s
                      AND outcome = 'RUNNING'
                    """,
                    (error, job["id"], job["lease_token"]),
                )

                if job["attempt_count"] >= job["max_attempts"]:
                    cur.execute(
                        """
                        UPDATE jobs
                        SET status = 'DEAD_LETTERED',
                            lease_owner = NULL,
                            lease_token = NULL,
                            lease_expires_at = NULL,
                            last_error = %s,
                            completed_at = now(),
                            updated_at = now()
                        WHERE id = %s
                        """,
                        (error, job["id"]),
                    )
                    dead_lettered += 1
                else:
                    cur.execute(
                        """
                        UPDATE jobs
                        SET status = 'RETRY_WAIT',
                            available_at = now(),
                            lease_owner = NULL,
                            lease_token = NULL,
                            lease_expires_at = NULL,
                            last_error = %s,
                            updated_at = now()
                        WHERE id = %s
                        """,
                        (error, job["id"]),
                    )
                    requeued += 1

    return {"requeued": requeued, "dead_lettered": dead_lettered}


def claim_job(
    worker_id: str,
    *,
    lease_seconds: int = 30,
    kinds: Iterable[str] = ("PROCESS_EVENT",),
) -> dict[str, Any] | None:
    """Atomically claim one PENDING job using FOR UPDATE SKIP LOCKED."""
    worker_id = worker_id.strip()
    if not worker_id:
        raise ValueError("worker_id cannot be blank")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be >= 1")

    supported_kinds = [kind for kind in kinds if kind]
    if not supported_kinds:
        return None

    lease_token = uuid.uuid4()
    attempt_id = uuid.uuid4()

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH candidate AS (
                    SELECT id
                    FROM jobs
                    WHERE status = 'PENDING'
                      AND available_at <= now()
                      AND kind = ANY(%s)
                      AND attempt_count < max_attempts
                    ORDER BY available_at, created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE jobs AS j
                SET status = 'RUNNING',
                    attempt_count = j.attempt_count + 1,
                    lease_owner = %s,
                    lease_token = %s,
                    lease_expires_at = now() + (%s * interval '1 second'),
                    updated_at = now()
                FROM candidate
                WHERE j.id = candidate.id
                RETURNING j.id, j.project_id, j.event_id, j.kind, j.status,
                          j.attempt_count, j.max_attempts, j.available_at,
                          j.lease_owner, j.lease_token, j.lease_expires_at,
                          j.created_at, j.updated_at
                """,
                (supported_kinds, worker_id, lease_token, lease_seconds),
            )
            job = cur.fetchone()
            if job is None:
                return None

            cur.execute(
                """
                INSERT INTO job_attempts(
                    id, project_id, job_id, event_id, attempt_number,
                    worker_id, lease_token, outcome
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'RUNNING')
                """,
                (
                    attempt_id,
                    job["project_id"],
                    job["id"],
                    job["event_id"],
                    job["attempt_count"],
                    worker_id,
                    lease_token,
                ),
            )

    return job


def renew_lease(job_id: uuid.UUID, lease_token: uuid.UUID, *, lease_seconds: int = 30) -> bool:
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be >= 1")
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET lease_expires_at = now() + (%s * interval '1 second'),
                    updated_at = now()
                WHERE id = %s
                  AND status = 'RUNNING'
                  AND lease_token = %s
                  AND lease_expires_at > now()
                RETURNING id
                """,
                (lease_seconds, job_id, lease_token),
            )
            return cur.fetchone() is not None


def complete_job(job_id: uuid.UUID, lease_token: uuid.UUID) -> None:
    """Mark a leased job and its active attempt successful."""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status = 'SUCCESS',
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    last_error = NULL,
                    completed_at = now(),
                    updated_at = now()
                WHERE id = %s
                  AND status = 'RUNNING'
                  AND lease_token = %s
                  AND lease_expires_at > now()
                RETURNING id
                """,
                (job_id, lease_token),
            )
            if cur.fetchone() is None:
                raise LostLeaseError("cannot complete job: lease is missing or expired")

            cur.execute(
                """
                UPDATE job_attempts
                SET finished_at = now(), outcome = 'SUCCESS'
                WHERE job_id = %s
                  AND lease_token = %s
                  AND outcome = 'RUNNING'
                """,
                (job_id, lease_token),
            )


def fail_job(
    job_id: uuid.UUID,
    lease_token: uuid.UUID,
    error: str,
    *,
    base_delay_seconds: float = 2.0,
    max_delay_seconds: float = 600.0,
    jitter_seconds: float | None = None,
) -> str:
    """Record a failed attempt and schedule retry or dead-letter the job."""
    error = (error or "job failed")[:4000]

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, attempt_count, max_attempts
                FROM jobs
                WHERE id = %s
                  AND status = 'RUNNING'
                  AND lease_token = %s
                  AND lease_expires_at > now()
                FOR UPDATE
                """,
                (job_id, lease_token),
            )
            job = cur.fetchone()
            if job is None:
                raise LostLeaseError("cannot fail job: lease is missing or expired")

            if job["attempt_count"] >= job["max_attempts"]:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = 'DEAD_LETTERED',
                        lease_owner = NULL,
                        lease_token = NULL,
                        lease_expires_at = NULL,
                        last_error = %s,
                        completed_at = now(),
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (error, job_id),
                )
                cur.execute(
                    """
                    UPDATE job_attempts
                    SET finished_at = now(),
                        outcome = 'DEAD_LETTERED',
                        error = %s
                    WHERE job_id = %s
                      AND lease_token = %s
                      AND outcome = 'RUNNING'
                    """,
                    (error, job_id, lease_token),
                )
                return "DEAD_LETTERED"

            delay = retry_delay_seconds(
                job["attempt_count"],
                base_delay_seconds=base_delay_seconds,
                max_delay_seconds=max_delay_seconds,
                jitter_seconds=jitter_seconds,
            )
            cur.execute(
                """
                UPDATE jobs
                SET status = 'RETRY_WAIT',
                    available_at = now() + (%s * interval '1 second'),
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    last_error = %s,
                    updated_at = now()
                WHERE id = %s
                RETURNING available_at
                """,
                (delay, error, job_id),
            )
            retry_row = cur.fetchone()
            assert retry_row is not None
            cur.execute(
                """
                UPDATE job_attempts
                SET finished_at = now(),
                    outcome = 'RETRY',
                    error = %s,
                    retry_at = %s
                WHERE job_id = %s
                  AND lease_token = %s
                  AND outcome = 'RUNNING'
                """,
                (error, retry_row["available_at"], job_id, lease_token),
            )
            return "RETRY_WAIT"
