import uuid

from fastapi.testclient import TestClient

from app.db import db_connect
from app.jobs import (
    claim_job,
    complete_job,
    fail_job,
    promote_due_retries,
    recover_expired_leases,
    retry_delay_seconds,
)
from app.main import ADMIN_PASSWORD, ADMIN_USER, app
from app.worker import process_replay_job

client = TestClient(app)
ADMIN_AUTH = (ADMIN_USER, ADMIN_PASSWORD)


def create_event() -> tuple[str, str]:
    suffix = uuid.uuid4().hex
    project_response = client.post(
        "/projects",
        auth=ADMIN_AUTH,
        json={"name": f"jobs-test-{suffix}"},
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    endpoint_response = client.post(
        f"/projects/{project_id}/webhook-endpoints",
        auth=ADMIN_AUTH,
        json={"name": f"generic-{suffix}", "source": "generic"},
    )
    assert endpoint_response.status_code == 201
    endpoint_token = endpoint_response.json()["endpoint_token"]

    ingest = client.post(
        f"/ingest/{endpoint_token}",
        headers={
            "X-EventForge-Delivery-ID": f"event-{suffix}",
            "X-EventForge-Event-Type": "test.created",
        },
        json={"value": suffix},
    )
    assert ingest.status_code == 202
    return project_id, ingest.json()["event_id"]


def insert_test_job(project_id: str, event_id: str, kind: str, *, max_attempts: int = 5) -> str:
    job_id = uuid.uuid4()
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs(id, project_id, event_id, kind, status, max_attempts)
                VALUES (%s, %s, %s, %s, 'PENDING', %s)
                """,
                (job_id, project_id, event_id, kind, max_attempts),
            )
    return str(job_id)


def get_job_row(job_id: str) -> dict:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
    assert row is not None
    return row


def attempt_outcomes(job_id: str) -> list[str]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT outcome FROM job_attempts WHERE job_id = %s ORDER BY attempt_number",
                (job_id,),
            )
            return [row["outcome"] for row in cur.fetchall()]




def event_snapshot(event_id: str) -> dict:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.project_id, e.endpoint_id, e.source, e.type,
                       e.provider_delivery_id, e.trace_id, e.headers, e.received_at,
                       p.content_type, p.body, p.size_bytes, p.sha256, p.created_at
                FROM events AS e
                JOIN event_payloads AS p ON p.event_id = e.id
                WHERE e.id = %s
                """,
                (event_id,),
            )
            row = cur.fetchone()
    assert row is not None
    return row


def project_event_count(project_id: str) -> int:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS count FROM events WHERE project_id = %s", (project_id,))
            row = cur.fetchone()
    assert row is not None
    return row["count"]

def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_version() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["version"] == "0.3.0"
    assert response.json()["status"] == "replaydb"


def test_control_plane_requires_authentication() -> None:
    response = client.post("/projects", json={"name": "unauthorized"})
    assert response.status_code == 401


def test_hookledger_idempotency_and_history() -> None:
    suffix = uuid.uuid4().hex

    project_response = client.post(
        "/projects",
        auth=ADMIN_AUTH,
        json={"name": f"hookledger-test-{suffix}"},
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    endpoint_response = client.post(
        f"/projects/{project_id}/webhook-endpoints",
        auth=ADMIN_AUTH,
        json={"name": f"github-{suffix}", "source": "github"},
    )
    assert endpoint_response.status_code == 201
    endpoint = endpoint_response.json()
    endpoint_token = endpoint["endpoint_token"]
    assert endpoint_token not in endpoint["token_prefix"]

    delivery_id = f"delivery-{suffix}"
    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "push",
        "Content-Type": "application/json",
    }
    payload = {"repository": {"name": "eventforge"}, "ref": "refs/heads/main"}

    first = client.post(f"/ingest/{endpoint_token}", headers=headers, json=payload)
    assert first.status_code == 202
    assert first.json()["duplicate"] is False
    event_id = first.json()["event_id"]

    second = client.post(f"/ingest/{endpoint_token}", headers=headers, json=payload)
    assert second.status_code == 202
    assert second.json()["duplicate"] is True
    assert second.json()["event_id"] == event_id

    history = client.get(f"/projects/{project_id}/events", auth=ADMIN_AUTH)
    assert history.status_code == 200
    matching = [item for item in history.json() if item["provider_delivery_id"] == delivery_id]
    assert len(matching) == 1
    assert matching[0]["id"] == event_id

    attempts = client.get(f"/events/{event_id}/ingress-attempts", auth=ADMIN_AUTH)
    assert attempts.status_code == 200
    assert [item["outcome"] for item in attempts.json()] == ["ACCEPTED", "DUPLICATE"]


def test_requests_without_idempotency_key_create_independent_events() -> None:
    suffix = uuid.uuid4().hex
    project = client.post(
        "/projects", auth=ADMIN_AUTH, json={"name": f"generic-test-{suffix}"}
    ).json()
    endpoint = client.post(
        f"/projects/{project['id']}/webhook-endpoints",
        auth=ADMIN_AUTH,
        json={"name": f"generic-{suffix}", "source": "generic"},
    ).json()

    first = client.post(
        f"/ingest/{endpoint['endpoint_token']}",
        headers={"X-EventForge-Event-Type": "example.created"},
        json={"value": 1},
    )
    second = client.post(
        f"/ingest/{endpoint['endpoint_token']}",
        headers={"X-EventForge-Event-Type": "example.created"},
        json={"value": 1},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["event_id"] != second.json()["event_id"]


def test_retry_equation() -> None:
    assert retry_delay_seconds(1, jitter_seconds=0) == 2
    assert retry_delay_seconds(2, jitter_seconds=0) == 4
    assert retry_delay_seconds(3, jitter_seconds=0) == 8
    assert retry_delay_seconds(20, jitter_seconds=0) == 600
    assert retry_delay_seconds(1, jitter_seconds=0.75) == 2.75


def test_job_success_records_attempt() -> None:
    project_id, event_id = create_event()
    kind = f"TEST_SUCCESS_{uuid.uuid4().hex}"
    job_id = insert_test_job(project_id, event_id, kind)

    claimed = claim_job("worker-success", kinds=(kind,))
    assert claimed is not None
    assert str(claimed["id"]) == job_id
    assert claimed["status"] == "RUNNING"
    assert claimed["attempt_count"] == 1

    complete_job(claimed["id"], claimed["lease_token"])
    job = get_job_row(job_id)
    assert job["status"] == "SUCCESS"
    assert job["completed_at"] is not None
    assert attempt_outcomes(job_id) == ["SUCCESS"]

    api_job = client.get(f"/jobs/{job_id}", auth=ADMIN_AUTH)
    assert api_job.status_code == 200
    assert api_job.json()["status"] == "SUCCESS"


def test_retry_then_dead_letter() -> None:
    project_id, event_id = create_event()
    kind = f"TEST_RETRY_{uuid.uuid4().hex}"
    job_id = insert_test_job(project_id, event_id, kind, max_attempts=2)

    first = claim_job("worker-retry-a", kinds=(kind,))
    assert first is not None
    state = fail_job(first["id"], first["lease_token"], "temporary failure", jitter_seconds=0)
    assert state == "RETRY_WAIT"
    assert get_job_row(job_id)["status"] == "RETRY_WAIT"

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET available_at = now() WHERE id = %s", (job_id,))
    # A real background worker may promote this due retry between the UPDATE
    # above and this call. Promotion is idempotent under concurrency: this call
    # may perform the transition itself, or return 0 because the worker already
    # won the race. The invariant we require is the resulting PENDING state.
    promote_due_retries()
    assert get_job_row(job_id)["status"] == "PENDING"

    second = claim_job("worker-retry-b", kinds=(kind,))
    assert second is not None
    assert second["attempt_count"] == 2
    state = fail_job(second["id"], second["lease_token"], "still failing", jitter_seconds=0)
    assert state == "DEAD_LETTERED"

    job = get_job_row(job_id)
    assert job["status"] == "DEAD_LETTERED"
    assert job["attempt_count"] == 2
    assert job["completed_at"] is not None
    assert attempt_outcomes(job_id) == ["RETRY", "DEAD_LETTERED"]


def test_expired_lease_is_recovered_and_reclaimed() -> None:
    project_id, event_id = create_event()
    kind = f"TEST_LEASE_{uuid.uuid4().hex}"
    job_id = insert_test_job(project_id, event_id, kind, max_attempts=3)

    first = claim_job("worker-crashed", kinds=(kind,), lease_seconds=30)
    assert first is not None

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
                (job_id,),
            )

    recovery = recover_expired_leases()
    assert recovery["requeued"] >= 1

    # A real background worker may promote this due retry between the recovery
    # call above and our assertion below. Both RETRY_WAIT and PENDING are valid
    # observations; what matters is that the job is no longer RUNNING and the
    # expired attempt was durably recorded.
    assert get_job_row(job_id)["status"] in {"RETRY_WAIT", "PENDING"}
    assert attempt_outcomes(job_id) == ["LEASE_EXPIRED"]

    # Idempotent under concurrency: this call may promote the job itself, or it
    # may return 0 because the background worker already won the race.
    promote_due_retries()
    assert get_job_row(job_id)["status"] == "PENDING"

    second = claim_job("worker-recovery", kinds=(kind,))
    assert second is not None
    assert second["attempt_count"] == 2
    complete_job(second["id"], second["lease_token"])
    assert get_job_row(job_id)["status"] == "SUCCESS"
    assert attempt_outcomes(job_id) == ["LEASE_EXPIRED", "SUCCESS"]


def test_skip_locked_allows_two_workers_to_select_different_jobs() -> None:
    project_id, event_id = create_event()
    kind_a = f"TEST_LOCK_A_{uuid.uuid4().hex}"
    kind_b = f"TEST_LOCK_B_{uuid.uuid4().hex}"
    first_job_id = insert_test_job(project_id, event_id, kind_a)
    second_job_id = insert_test_job(project_id, event_id, kind_b)
    expected = {first_job_id, second_job_id}

    conn_a = db_connect()
    conn_b = db_connect()
    try:
        cur_a = conn_a.cursor()
        cur_b = conn_b.cursor()
        cur_a.execute("BEGIN")
        cur_b.execute("BEGIN")
        cur_a.execute(
            """
            SELECT id FROM jobs
            WHERE id = ANY(%s)
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            ([uuid.UUID(first_job_id), uuid.UUID(second_job_id)],),
        )
        selected_a = cur_a.fetchone()
        assert selected_a is not None

        cur_b.execute(
            """
            SELECT id FROM jobs
            WHERE id = ANY(%s)
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            ([uuid.UUID(first_job_id), uuid.UUID(second_job_id)],),
        )
        selected_b = cur_b.fetchone()
        assert selected_b is not None

        assert str(selected_a["id"]) != str(selected_b["id"])
        assert {str(selected_a["id"]), str(selected_b["id"])} == expected
    finally:
        conn_a.rollback()
        conn_b.rollback()
        conn_a.close()
        conn_b.close()

def test_replay_creates_new_execution_without_mutating_original_event() -> None:
    project_id, event_id = create_event()
    before = event_snapshot(event_id)
    before_count = project_event_count(project_id)

    response = client.post(
        f"/events/{event_id}/replays",
        auth=ADMIN_AUTH,
        json={"workflow_version_mode": "original"},
    )
    assert response.status_code == 202
    replay = response.json()
    assert replay["replay_of"] == event_id
    assert replay["project_id"] == project_id
    assert replay["workflow_version_mode"] == "original"
    assert replay["job_status"] == "PENDING"

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, event_id, replay_execution_id, kind
                FROM jobs
                WHERE id = %s
                """,
                (replay["job_id"],),
            )
            job = cur.fetchone()
    assert job is not None
    assert job["kind"] == "REPLAY_EVENT"
    assert str(job["event_id"]) == event_id
    assert str(job["replay_execution_id"]) == replay["id"]

    after = event_snapshot(event_id)
    assert after == before
    assert project_event_count(project_id) == before_count


def test_multiple_replays_are_independent_and_record_workflow_mode() -> None:
    project_id, event_id = create_event()

    original = client.post(
        f"/events/{event_id}/replays",
        auth=ADMIN_AUTH,
        json={"workflow_version_mode": "original"},
    )
    current = client.post(
        f"/events/{event_id}/replays",
        auth=ADMIN_AUTH,
        json={"workflow_version_mode": "current"},
    )

    assert original.status_code == 202
    assert current.status_code == 202
    original_json = original.json()
    current_json = current.json()
    assert original_json["id"] != current_json["id"]
    assert original_json["job_id"] != current_json["job_id"]
    assert original_json["replay_of"] == event_id
    assert current_json["replay_of"] == event_id
    assert original_json["workflow_version_mode"] == "original"
    assert current_json["workflow_version_mode"] == "current"

    history = client.get(f"/events/{event_id}/replays", auth=ADMIN_AUTH)
    assert history.status_code == 200
    ids = {item["id"] for item in history.json()}
    assert original_json["id"] in ids
    assert current_json["id"] in ids
    assert project_event_count(project_id) == 1


def test_replay_requires_auth_and_valid_mode_and_event() -> None:
    _, event_id = create_event()

    unauthenticated = client.post(
        f"/events/{event_id}/replays",
        json={"workflow_version_mode": "original"},
    )
    assert unauthenticated.status_code == 401

    invalid_mode = client.post(
        f"/events/{event_id}/replays",
        auth=ADMIN_AUTH,
        json={"workflow_version_mode": "future"},
    )
    assert invalid_mode.status_code == 422

    missing_event = client.post(
        f"/events/{uuid.uuid4()}/replays",
        auth=ADMIN_AUTH,
        json={"workflow_version_mode": "current"},
    )
    assert missing_event.status_code == 404


def test_replay_processor_reads_original_postgres_payload_without_copying_it() -> None:
    project_id, event_id = create_event()
    before = event_snapshot(event_id)

    response = client.post(
        f"/events/{event_id}/replays",
        auth=ADMIN_AUTH,
        json={"workflow_version_mode": "original"},
    )
    assert response.status_code == 202
    replay = response.json()

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (replay["job_id"],))
            job = cur.fetchone()
            cur.execute(
                "SELECT count(*) AS count FROM event_payloads WHERE event_id = %s",
                (event_id,),
            )
            payload_count = cur.fetchone()

    assert job is not None
    process_replay_job(job)
    assert payload_count is not None
    assert payload_count["count"] == 1
    assert event_snapshot(event_id) == before
    assert project_event_count(project_id) == 1