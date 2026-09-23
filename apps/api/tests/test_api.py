import concurrent.futures
import hashlib
import hmac
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import db_connect
from app.jobs import (
    LostLeaseError,
    claim_job,
    complete_job,
    fail_job,
    promote_due_retries,
    recover_expired_leases,
    renew_lease,
    retry_delay_seconds,
)
from app.main import ADMIN_PASSWORD, ADMIN_USER, app
from app.security import DEV_ADMIN_PASSWORD, DEV_WEBHOOK_MASTER_SECRET, validate_runtime_security
from app.worker import process_event_job, process_replay_job
from app.workflows import (
    WorkflowActionError,
    process_workflow_step_job,
    schedule_workflows_for_event,
    validate_http_target,
)

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
    assert response.json()["version"] == "0.9.0"
    assert response.json()["status"] == "release-candidate"


def test_control_plane_requires_authentication() -> None:
    response = client.post("/projects", json={"name": "unauthorized"})
    assert response.status_code == 401


def test_admin_project_listing_is_available_for_dashboard_but_not_project_keys() -> None:
    suffix = uuid.uuid4().hex
    project = client.post(
        "/projects", auth=ADMIN_AUTH, json={"name": f"dashboard-list-{suffix}"}
    ).json()

    listed = client.get("/projects", auth=ADMIN_AUTH)
    assert listed.status_code == 200
    assert any(item["id"] == project["id"] for item in listed.json())

    key = client.post(
        f"/projects/{project['id']}/api-keys",
        auth=ADMIN_AUTH,
        json={"name": "dashboard"},
    ).json()["api_key"]
    denied = client.get("/projects", headers={"Authorization": f"Bearer {key}"})
    assert denied.status_code == 404


def test_project_summary_reports_dashboard_counts() -> None:
    suffix = uuid.uuid4().hex
    project = client.post(
        "/projects", auth=ADMIN_AUTH, json={"name": f"dashboard-summary-{suffix}"}
    ).json()
    endpoint = client.post(
        f"/projects/{project['id']}/webhook-endpoints",
        auth=ADMIN_AUTH,
        json={"name": f"summary-{suffix}", "source": "generic"},
    ).json()
    accepted = client.post(
        "/ingest",
        headers={
            "X-EventForge-Endpoint-Token": endpoint["endpoint_token"],
            "X-EventForge-Delivery-ID": f"summary-{suffix}",
            "X-EventForge-Event-Type": "dashboard.created",
        },
        json={"dashboard": True},
    )
    assert accepted.status_code == 202

    summary = client.get(f"/projects/{project['id']}/summary", auth=ADMIN_AUTH)
    assert summary.status_code == 200
    body = summary.json()
    assert body["project"]["id"] == project["id"]
    assert body["counts"]["webhook_endpoints"] == 1
    assert body["counts"]["events"] == 1
    assert body["counts"]["jobs"] >= 1
    assert body["counts"]["replays"] == 0
    assert body["counts"]["workflows"] == 0


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


def create_workflow_api(
    project_id: str,
    *,
    name: str | None = None,
    steps: list[dict] | None = None,
) -> dict:
    if name is None:
        name = f"flow-{uuid.uuid4().hex}"
    if steps is None:
        steps = [{"type": "json_transform", "set": {"flowtrace": "ok"}}]
    response = client.post(
        f"/projects/{project_id}/workflows",
        auth=ADMIN_AUTH,
        json={
            "name": name,
            "trigger": {"source": "generic", "type": "test.created"},
            "steps": steps,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def get_run_row(run_id: str | uuid.UUID) -> dict:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM workflow_runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
    assert row is not None
    return row


def get_step_rows(run_id: str | uuid.UUID) -> list[dict]:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM step_runs WHERE workflow_run_id = %s ORDER BY step_index",
                (run_id,),
            )
            return list(cur.fetchall())


def drive_workflow_until_terminal(run_id: uuid.UUID, *, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        run = get_run_row(run_id)
        if run["status"] in {"SUCCESS", "FAILED"}:
            return run

        claimed = claim_job("pytest-flowtrace", kinds=("RUN_WORKFLOW_STEP",))
        if claimed is None:
            time.sleep(0.05)
            continue
        try:
            process_workflow_step_job(claimed)
            complete_job(claimed["id"], claimed["lease_token"])
        except LostLeaseError:
            # A real background worker can win the same race on local Compose.
            pass

    raise AssertionError(f"workflow run {run_id} did not finish before timeout")


def test_flowtrace_create_workflow_persists_version_one() -> None:
    project_id, _ = create_event()
    workflow = create_workflow_api(project_id)

    assert workflow["project_id"] == project_id
    assert workflow["active_version"]["version_number"] == 1
    assert workflow["active_version"]["trigger_source"] == "generic"
    assert workflow["active_version"]["trigger_type"] == "test.created"

    versions = client.get(f"/workflows/{workflow['id']}/versions", auth=ADMIN_AUTH)
    assert versions.status_code == 200
    assert len(versions.json()) == 1
    assert versions.json()[0]["active"] is True


def test_flowtrace_condition_transform_emit_event_pipeline() -> None:
    project_id, event_id = create_event()
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT body FROM event_payloads WHERE event_id = %s", (event_id,))
            body = cur.fetchone()
    assert body is not None
    suffix = json.loads(bytes(body["body"]))["value"]

    create_workflow_api(
        project_id,
        steps=[
            {"type": "conditional", "path": "value", "equals": suffix},
            {"type": "json_transform", "set": {"result.status": "transformed"}},
            {"type": "emit_event", "source": "flowtrace", "event_type": "flow.completed"},
        ],
    )

    run_ids = schedule_workflows_for_event(uuid.UUID(project_id), uuid.UUID(event_id))
    assert len(run_ids) == 1
    run = drive_workflow_until_terminal(run_ids[0])
    assert run["status"] == "SUCCESS"
    assert run["context"]["result"]["status"] == "transformed"

    steps = get_step_rows(run_ids[0])
    assert [step["status"] for step in steps] == ["SUCCESS", "SUCCESS", "SUCCESS"]
    emitted_step = steps[-1]
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, source, type, parent_event_id FROM events WHERE emitted_by_step_run_id = %s",
                (emitted_step["id"],),
            )
            emitted = cur.fetchone()
    assert emitted is not None
    assert emitted["source"] == "flowtrace"
    assert emitted["type"] == "flow.completed"
    assert str(emitted["parent_event_id"]) == event_id


def test_flowtrace_false_condition_skips_remaining_steps() -> None:
    project_id, event_id = create_event()
    create_workflow_api(
        project_id,
        steps=[
            {"type": "conditional", "path": "value", "equals": "definitely-not-the-payload"},
            {"type": "json_transform", "set": {"should_not": "run"}},
            {"type": "emit_event", "event_type": "should.not.emit"},
        ],
    )

    run_ids = schedule_workflows_for_event(uuid.UUID(project_id), uuid.UUID(event_id))
    assert len(run_ids) == 1
    run = drive_workflow_until_terminal(run_ids[0])
    assert run["status"] == "SUCCESS"
    steps = get_step_rows(run_ids[0])
    assert [step["status"] for step in steps] == ["SUCCESS", "SKIPPED", "SKIPPED"]


def test_flowtrace_current_events_use_active_workflow_version() -> None:
    project_id, event_id = create_event()
    workflow = create_workflow_api(project_id)
    second = client.post(
        f"/workflows/{workflow['id']}/versions",
        auth=ADMIN_AUTH,
        json={
            "trigger": {"source": "generic", "type": "test.created"},
            "steps": [{"type": "json_transform", "set": {"version": 2}}],
            "activate": True,
        },
    )
    assert second.status_code == 201
    assert second.json()["version_number"] == 2

    run_ids = schedule_workflows_for_event(uuid.UUID(project_id), uuid.UUID(event_id))
    assert len(run_ids) == 1
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.version_number
                FROM workflow_runs AS wr
                JOIN workflow_versions AS v ON v.id = wr.workflow_version_id
                WHERE wr.id = %s
                """,
                (run_ids[0],),
            )
            row = cur.fetchone()
    assert row is not None
    assert row["version_number"] == 2


def test_replay_original_and_current_resolve_real_workflow_versions() -> None:
    project_id, event_id = create_event()
    workflow = create_workflow_api(project_id)

    original_runs = schedule_workflows_for_event(uuid.UUID(project_id), uuid.UUID(event_id))
    assert len(original_runs) == 1

    second = client.post(
        f"/workflows/{workflow['id']}/versions",
        auth=ADMIN_AUTH,
        json={
            "trigger": {"source": "generic", "type": "test.created"},
            "steps": [{"type": "json_transform", "set": {"version": 2}}],
            "activate": True,
        },
    )
    assert second.status_code == 201

    original_replay = client.post(
        f"/events/{event_id}/replays",
        auth=ADMIN_AUTH,
        json={"workflow_version_mode": "original"},
    ).json()
    current_replay = client.post(
        f"/events/{event_id}/replays",
        auth=ADMIN_AUTH,
        json={"workflow_version_mode": "current"},
    ).json()

    schedule_workflows_for_event(
        uuid.UUID(project_id),
        uuid.UUID(event_id),
        replay_execution_id=uuid.UUID(original_replay["id"]),
        mode="original",
    )
    schedule_workflows_for_event(
        uuid.UUID(project_id),
        uuid.UUID(event_id),
        replay_execution_id=uuid.UUID(current_replay["id"]),
        mode="current",
    )

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT wr.replay_execution_id, v.version_number
                FROM workflow_runs AS wr
                JOIN workflow_versions AS v ON v.id = wr.workflow_version_id
                WHERE wr.replay_execution_id = ANY(%s)
                ORDER BY wr.replay_execution_id
                """,
                ([uuid.UUID(original_replay["id"]), uuid.UUID(current_replay["id"])],),
            )
            rows = list(cur.fetchall())
    versions = {str(row["replay_execution_id"]): row["version_number"] for row in rows}
    assert versions[original_replay["id"]] == 1
    assert versions[current_replay["id"]] == 2


def test_flowtrace_expired_step_lease_resumes_after_previous_step_commit() -> None:
    project_id, event_id = create_event()
    create_workflow_api(
        project_id,
        steps=[
            {"type": "json_transform", "set": {"phase": "one"}},
            {"type": "json_transform", "set": {"phase": "two"}},
        ],
    )
    test_kind = f"TEST_FLOW_STEP_{uuid.uuid4().hex}"
    run_ids = schedule_workflows_for_event(
        uuid.UUID(project_id),
        uuid.UUID(event_id),
        first_job_kind=test_kind,
    )
    assert len(run_ids) == 1

    first = claim_job("flowtrace-first", kinds=(test_kind,))
    assert first is not None
    process_workflow_step_job(first)
    complete_job(first["id"], first["lease_token"])
    assert get_step_rows(run_ids[0])[0]["status"] == "SUCCESS"

    second = claim_job("flowtrace-crashed", kinds=(test_kind,), lease_seconds=30)
    assert second is not None
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
                (second["id"],),
            )

    recovery = recover_expired_leases()
    assert recovery["requeued"] >= 1
    promote_due_retries()

    resumed = claim_job("flowtrace-resumed", kinds=(test_kind,))
    assert resumed is not None
    process_workflow_step_job(resumed)
    complete_job(resumed["id"], resumed["lease_token"])

    run = get_run_row(run_ids[0])
    assert run["status"] == "SUCCESS"
    assert run["context"]["phase"] == "two"
    assert [step["status"] for step in get_step_rows(run_ids[0])] == ["SUCCESS", "SUCCESS"]
    assert attempt_outcomes(str(second["id"])) == ["LEASE_EXPIRED", "SUCCESS"]


def test_integrated_process_event_schedules_matching_flowtrace_run() -> None:
    project_id, event_id = create_event()
    workflow = create_workflow_api(project_id)

    process_event_job(
        {
            "project_id": uuid.UUID(project_id),
            "event_id": uuid.UUID(event_id),
            "kind": "PROCESS_EVENT",
        }
    )

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT wr.id, wr.workflow_id, v.version_number, wr.replay_execution_id
                FROM workflow_runs AS wr
                JOIN workflow_versions AS v ON v.id = wr.workflow_version_id
                WHERE wr.event_id = %s AND wr.workflow_id = %s
                """,
                (event_id, workflow["id"]),
            )
            run = cur.fetchone()
    assert run is not None
    assert run["version_number"] == 1
    assert run["replay_execution_id"] is None


def test_replay_current_selection_is_frozen_at_request_time() -> None:
    project_id, event_id = create_event()
    workflow = create_workflow_api(project_id)

    original_runs = schedule_workflows_for_event(uuid.UUID(project_id), uuid.UUID(event_id))
    assert len(original_runs) == 1

    version_two = client.post(
        f"/workflows/{workflow['id']}/versions",
        auth=ADMIN_AUTH,
        json={
            "trigger": {"source": "generic", "type": "test.created"},
            "steps": [{"type": "json_transform", "set": {"version": 2}}],
            "activate": True,
        },
    )
    assert version_two.status_code == 201

    replay = client.post(
        f"/events/{event_id}/replays",
        auth=ADMIN_AUTH,
        json={"workflow_version_mode": "current"},
    )
    assert replay.status_code == 202
    replay_body = replay.json()
    assert replay_body["workflow_selection_count"] == 1

    version_three = client.post(
        f"/workflows/{workflow['id']}/versions",
        auth=ADMIN_AUTH,
        json={
            "trigger": {"source": "generic", "type": "test.created"},
            "steps": [{"type": "json_transform", "set": {"version": 3}}],
            "activate": True,
        },
    )
    assert version_three.status_code == 201

    selections = client.get(
        f"/replays/{replay_body['id']}/workflow-selections",
        auth=ADMIN_AUTH,
    )
    assert selections.status_code == 200
    assert len(selections.json()) == 1
    assert selections.json()[0]["version_number"] == 2

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, event_id, replay_execution_id, kind
                FROM jobs
                WHERE replay_execution_id = %s
                """,
                (replay_body["id"],),
            )
            replay_job = cur.fetchone()
    assert replay_job is not None
    process_replay_job(replay_job)

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.version_number
                FROM workflow_runs AS wr
                JOIN workflow_versions AS v ON v.id = wr.workflow_version_id
                WHERE wr.replay_execution_id = %s
                """,
                (replay_body["id"],),
            )
            replay_run = cur.fetchone()
    assert replay_run is not None
    assert replay_run["version_number"] == 2


def test_event_integration_view_connects_all_three_products() -> None:
    project_id, event_id = create_event()
    create_workflow_api(
        project_id,
        steps=[{"type": "emit_event", "source": "flowtrace", "event_type": "integrated.done"}],
    )

    run_ids = schedule_workflows_for_event(uuid.UUID(project_id), uuid.UUID(event_id))
    assert len(run_ids) == 1
    run = drive_workflow_until_terminal(run_ids[0])
    assert run["status"] == "SUCCESS"

    replay = client.post(
        f"/events/{event_id}/replays",
        auth=ADMIN_AUTH,
        json={"workflow_version_mode": "original"},
    )
    assert replay.status_code == 202
    assert replay.json()["workflow_selection_count"] == 1

    integrated = client.get(f"/events/{event_id}/integration", auth=ADMIN_AUTH)
    assert integrated.status_code == 200
    body = integrated.json()
    assert body["event"]["id"] == event_id
    assert any(job["kind"] == "PROCESS_EVENT" for job in body["jobs"])
    assert any(item["status"] == "SUCCESS" for item in body["workflow_runs"])
    assert len(body["replays"]) == 1
    assert body["replays"][0]["workflow_selection_count"] == 1
    assert any(item["type"] == "integrated.done" for item in body["emitted_events"])


def test_replay_zero_selection_remains_frozen_after_workflow_is_created() -> None:
    project_id, event_id = create_event()

    replay = client.post(
        f"/events/{event_id}/replays",
        auth=ADMIN_AUTH,
        json={"workflow_version_mode": "current"},
    )
    assert replay.status_code == 202
    replay_body = replay.json()
    assert replay_body["workflow_selection_count"] == 0

    create_workflow_api(project_id)

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, event_id, replay_execution_id, kind
                FROM jobs
                WHERE replay_execution_id = %s
                """,
                (replay_body["id"],),
            )
            replay_job = cur.fetchone()
    assert replay_job is not None
    process_replay_job(replay_job)

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS count FROM workflow_runs WHERE replay_execution_id = %s",
                (replay_body["id"],),
            )
            row = cur.fetchone()
    assert row is not None
    assert row["count"] == 0


def test_project_api_key_is_scoped_hashed_and_revocable() -> None:
    suffix = uuid.uuid4().hex
    project_a = client.post(
        "/projects", auth=ADMIN_AUTH, json={"name": f"security-a-{suffix}"}
    ).json()
    project_b = client.post(
        "/projects", auth=ADMIN_AUTH, json={"name": f"security-b-{suffix}"}
    ).json()

    created = client.post(
        f"/projects/{project_a['id']}/api-keys",
        auth=ADMIN_AUTH,
        json={"name": "automation"},
    )
    assert created.status_code == 201
    body = created.json()
    api_key = body["api_key"]
    assert api_key.startswith("efk_")
    assert api_key not in body["key_prefix"]

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT key_hash, revoked_at FROM project_api_keys WHERE id = %s",
                (body["id"],),
            )
            stored = cur.fetchone()
    assert stored is not None
    assert stored["key_hash"] == hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    assert api_key != stored["key_hash"]
    assert stored["revoked_at"] is None

    bearer = {"Authorization": f"Bearer {api_key}"}
    own = client.get(f"/projects/{project_a['id']}/events", headers=bearer)
    assert own.status_code == 200

    cross_project = client.get(f"/projects/{project_b['id']}/events", headers=bearer)
    assert cross_project.status_code == 404

    revoke = client.post(f"/project-api-keys/{body['id']}/revoke", auth=ADMIN_AUTH)
    assert revoke.status_code == 200
    assert revoke.json()["revoked_at"] is not None

    revoked = client.get(f"/projects/{project_a['id']}/events", headers=bearer)
    assert revoked.status_code == 401


def test_signed_webhook_requires_valid_fresh_hmac_and_header_token() -> None:
    suffix = uuid.uuid4().hex
    project = client.post(
        "/projects", auth=ADMIN_AUTH, json={"name": f"signed-webhook-{suffix}"}
    ).json()
    endpoint_response = client.post(
        f"/projects/{project['id']}/webhook-endpoints",
        auth=ADMIN_AUTH,
        json={
            "name": f"signed-{suffix}",
            "source": "generic",
            "verify_signature": True,
            "signature_tolerance_seconds": 300,
        },
    )
    assert endpoint_response.status_code == 201
    endpoint = endpoint_response.json()
    secret = endpoint["signing_secret"].encode("utf-8")
    payload = b'{"secure":true}'
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret,
        timestamp.encode("ascii") + b"." + payload,
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "X-EventForge-Endpoint-Token": endpoint["endpoint_token"],
        "X-EventForge-Timestamp": timestamp,
        "X-EventForge-Signature": f"sha256={signature}",
        "X-EventForge-Delivery-ID": f"signed-{suffix}",
        "X-EventForge-Event-Type": "security.signed",
        "Content-Type": "application/json",
    }
    accepted = client.post("/ingest", headers=headers, content=payload)
    assert accepted.status_code == 202
    event_id = accepted.json()["event_id"]

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT headers FROM events WHERE id = %s", (event_id,))
            stored_event = cur.fetchone()
    assert stored_event is not None
    assert "x-eventforge-signature" not in stored_event["headers"]
    assert "x-eventforge-timestamp" not in stored_event["headers"]

    bad_headers = dict(headers)
    bad_headers["X-EventForge-Delivery-ID"] = f"bad-{suffix}"
    bad_headers["X-EventForge-Signature"] = "sha256=" + ("0" * 64)
    bad = client.post("/ingest", headers=bad_headers, content=payload)
    assert bad.status_code == 401

    stale_timestamp = str(int(time.time()) - 1000)
    stale_signature = hmac.new(
        secret,
        stale_timestamp.encode("ascii") + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    stale_headers = dict(headers)
    stale_headers["X-EventForge-Delivery-ID"] = f"stale-{suffix}"
    stale_headers["X-EventForge-Timestamp"] = stale_timestamp
    stale_headers["X-EventForge-Signature"] = f"sha256={stale_signature}"
    stale = client.post("/ingest", headers=stale_headers, content=payload)
    assert stale.status_code == 401

    attempts = client.get(
        f"/webhook-endpoints/{endpoint['id']}/ingress-attempts",
        auth=ADMIN_AUTH,
    )
    assert attempts.status_code == 200
    outcomes = [item["outcome"] for item in attempts.json()]
    assert outcomes.count("REJECTED_SIGNATURE") == 2
    assert "ACCEPTED" in outcomes


def test_webhook_signing_secret_rotation_invalidates_previous_secret() -> None:
    suffix = uuid.uuid4().hex
    project = client.post(
        "/projects", auth=ADMIN_AUTH, json={"name": f"rotate-webhook-{suffix}"}
    ).json()
    endpoint = client.post(
        f"/projects/{project['id']}/webhook-endpoints",
        auth=ADMIN_AUTH,
        json={"name": f"rotate-{suffix}", "source": "generic", "verify_signature": True},
    ).json()
    old_secret = endpoint["signing_secret"]

    rotated_response = client.post(
        f"/webhook-endpoints/{endpoint['id']}/rotate-signing-secret",
        auth=ADMIN_AUTH,
    )
    assert rotated_response.status_code == 200
    rotated = rotated_response.json()
    assert rotated["signing_secret_version"] == endpoint["signing_secret_version"] + 1
    assert rotated["signing_secret"] != old_secret

    payload = b'{"rotate":true}'
    timestamp = str(int(time.time()))
    base_headers = {
        "X-EventForge-Endpoint-Token": endpoint["endpoint_token"],
        "X-EventForge-Timestamp": timestamp,
        "X-EventForge-Event-Type": "security.rotated",
        "Content-Type": "application/json",
    }
    old_sig = hmac.new(
        old_secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    old_headers = dict(base_headers)
    old_headers["X-EventForge-Delivery-ID"] = f"old-{suffix}"
    old_headers["X-EventForge-Signature"] = f"sha256={old_sig}"
    assert client.post("/ingest", headers=old_headers, content=payload).status_code == 401

    new_secret = rotated["signing_secret"].encode()
    new_sig = hmac.new(
        new_secret, timestamp.encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    new_headers = dict(base_headers)
    new_headers["X-EventForge-Delivery-ID"] = f"new-{suffix}"
    new_headers["X-EventForge-Signature"] = f"sha256={new_sig}"
    assert client.post("/ingest", headers=new_headers, content=payload).status_code == 202


def test_ssrf_validation_blocks_local_metadata_insecure_and_nonstandard_ports() -> None:
    blocked = [
        "http://example.com/",
        "https://127.0.0.1/",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/",
        "https://localhost/",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://8.8.8.8:8443/",
    ]
    for url in blocked:
        with pytest.raises(WorkflowActionError):
            validate_http_target(url)


def test_production_security_rejects_development_secrets() -> None:
    with pytest.raises(RuntimeError):
        validate_runtime_security(
            "production",
            DEV_ADMIN_PASSWORD,
            "a" * 40,
        )
    with pytest.raises(RuntimeError):
        validate_runtime_security(
            "production",
            "a-strong-production-admin-password",
            DEV_WEBHOOK_MASTER_SECRET,
        )
    validate_runtime_security(
        "production",
        "a-strong-production-admin-password",
        "a-production-webhook-master-secret-that-is-long-enough",
    )


def test_security_headers_are_present() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"



def test_claim_refreshes_lease_after_attempt_persistence_stall() -> None:
    project_id, event_id = create_event()
    kind = f"TEST_CLAIM_STALL_{uuid.uuid4().hex}"
    job_id = insert_test_job(project_id, event_id, kind)

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE OR REPLACE FUNCTION eventforge_test_delay_job_attempt()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.worker_id = 'worker-claim-stall' THEN
                        PERFORM pg_sleep(2.2);
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )
            cur.execute(
                "DROP TRIGGER IF EXISTS eventforge_test_delay_job_attempt_trigger ON job_attempts"
            )
            cur.execute(
                """
                CREATE TRIGGER eventforge_test_delay_job_attempt_trigger
                BEFORE INSERT ON job_attempts
                FOR EACH ROW
                EXECUTE FUNCTION eventforge_test_delay_job_attempt()
                """
            )

    try:
        claimed = claim_job(
            "worker-claim-stall",
            kinds=(kind,),
            lease_seconds=2,
        )
        assert claimed is not None

        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status,
                           EXTRACT(EPOCH FROM (lease_expires_at - clock_timestamp())) AS lease_remaining
                    FROM jobs
                    WHERE id = %s
                    """,
                    (job_id,),
                )
                row = cur.fetchone()

        assert row is not None
        assert row["status"] == "RUNNING"
        assert float(row["lease_remaining"]) > 1.0

        complete_job(claimed["id"], claimed["lease_token"])
    finally:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DROP TRIGGER IF EXISTS eventforge_test_delay_job_attempt_trigger ON job_attempts"
                )
                cur.execute("DROP FUNCTION IF EXISTS eventforge_test_delay_job_attempt()")


def test_lease_heartbeat_extends_expiry_and_is_exposed_by_job_api() -> None:
    project_id, event_id = create_event()
    kind = f"TEST_HEARTBEAT_{uuid.uuid4().hex}"
    job_id = insert_test_job(project_id, event_id, kind)

    claimed = claim_job("worker-heartbeat", kinds=(kind,), lease_seconds=5)
    assert claimed is not None
    before = get_job_row(job_id)
    assert before["last_heartbeat_at"] is not None

    assert renew_lease(claimed["id"], claimed["lease_token"], lease_seconds=30) is True
    after = get_job_row(job_id)
    assert after["lease_expires_at"] > before["lease_expires_at"]
    assert after["last_heartbeat_at"] >= before["last_heartbeat_at"]

    api_job = client.get(f"/jobs/{job_id}", auth=ADMIN_AUTH)
    assert api_job.status_code == 200
    assert api_job.json()["last_heartbeat_at"] is not None
    assert api_job.json()["recovery_count"] == 0

    complete_job(claimed["id"], claimed["lease_token"])


def test_expired_lease_recovery_records_recovery_metadata() -> None:
    project_id, event_id = create_event()
    kind = f"TEST_RECOVERY_META_{uuid.uuid4().hex}"
    job_id = insert_test_job(project_id, event_id, kind, max_attempts=3)
    claimed = claim_job("worker-recovery-meta", kinds=(kind,), lease_seconds=30)
    assert claimed is not None

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
                (job_id,),
            )

    result = recover_expired_leases()
    assert result["requeued"] >= 1
    recovered = get_job_row(job_id)
    assert recovered["recovery_count"] == 1
    assert recovered["last_recovered_at"] is not None
    assert recovered["status"] in {"RETRY_WAIT", "PENDING"}


def test_queue_health_reports_live_and_recovered_queue_state() -> None:
    project_id, event_id = create_event()
    kind = f"TEST_QUEUE_HEALTH_{uuid.uuid4().hex}"
    job_id = insert_test_job(project_id, event_id, kind)
    claimed = claim_job("worker-queue-health", kinds=(kind,), lease_seconds=30)
    assert claimed is not None

    running = client.get(f"/projects/{project_id}/queue-health", auth=ADMIN_AUTH)
    assert running.status_code == 200
    body = running.json()
    assert body["project_id"] == project_id
    assert body["running"] >= 1
    assert body["expired_leases"] == 0
    assert body["latest_running_heartbeat_at"] is not None

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
                (job_id,),
            )
    recover_expired_leases()

    recovered = client.get(f"/projects/{project_id}/queue-health", auth=ADMIN_AUTH).json()
    assert recovered["recovered_jobs"] >= 1
    assert recovered["total_recoveries"] >= 1
    assert recovered["last_recovered_at"] is not None


def test_queue_health_remains_project_scoped_for_api_keys() -> None:
    suffix = uuid.uuid4().hex
    project_a = client.post("/projects", auth=ADMIN_AUTH, json={"name": f"queue-a-{suffix}"}).json()
    project_b = client.post("/projects", auth=ADMIN_AUTH, json={"name": f"queue-b-{suffix}"}).json()
    key = client.post(
        f"/projects/{project_a['id']}/api-keys",
        auth=ADMIN_AUTH,
        json={"name": "queue-health"},
    ).json()["api_key"]
    bearer = {"Authorization": f"Bearer {key}"}

    assert client.get(f"/projects/{project_a['id']}/queue-health", headers=bearer).status_code == 200
    assert client.get(f"/projects/{project_b['id']}/queue-health", headers=bearer).status_code == 404
    assert client.post("/operations/recover-jobs", headers=bearer).status_code == 404


def test_admin_recovery_endpoint_recovers_expired_job() -> None:
    project_id, event_id = create_event()
    kind = f"TEST_ADMIN_RECOVERY_{uuid.uuid4().hex}"
    job_id = insert_test_job(project_id, event_id, kind)
    claimed = claim_job("worker-admin-recovery", kinds=(kind,), lease_seconds=30)
    assert claimed is not None

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
                (job_id,),
            )

    assert client.post("/operations/recover-jobs").status_code == 401
    response = client.post("/operations/recover-jobs", auth=ADMIN_AUTH)
    assert response.status_code == 200
    assert response.json()["expired_requeued"] >= 1
    job = get_job_row(job_id)
    assert job["status"] == "PENDING"
    assert job["recovery_count"] == 1


def test_concurrent_duplicate_ingest_creates_one_event_and_one_process_job() -> None:
    suffix = uuid.uuid4().hex
    project = client.post(
        "/projects", auth=ADMIN_AUTH, json={"name": f"concurrent-ingest-{suffix}"}
    ).json()
    endpoint = client.post(
        f"/projects/{project['id']}/webhook-endpoints",
        auth=ADMIN_AUTH,
        json={"name": f"concurrent-{suffix}", "source": "generic"},
    ).json()
    delivery_id = f"concurrent-{suffix}"

    def send_one(index: int) -> tuple[int, dict]:
        with TestClient(app) as parallel_client:
            response = parallel_client.post(
                "/ingest",
                headers={
                    "X-EventForge-Endpoint-Token": endpoint["endpoint_token"],
                    "X-EventForge-Delivery-ID": delivery_id,
                    "X-EventForge-Event-Type": "reliability.concurrent",
                },
                json={"index": index},
            )
            return response.status_code, response.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(send_one, range(8)))

    assert all(code == 202 for code, _ in results)
    bodies = [body for _, body in results]
    assert len({body["event_id"] for body in bodies}) == 1
    assert sum(body["duplicate"] is False for body in bodies) == 1
    assert sum(body["duplicate"] is True for body in bodies) == 7

    event_id = bodies[0]["event_id"]
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS count FROM events WHERE endpoint_id = %s AND provider_delivery_id = %s",
                (endpoint["id"], delivery_id),
            )
            assert cur.fetchone()["count"] == 1
            cur.execute(
                "SELECT count(*) AS count FROM jobs WHERE event_id = %s AND kind = 'PROCESS_EVENT'",
                (event_id,),
            )
            assert cur.fetchone()["count"] == 1
            cur.execute(
                "SELECT count(*) AS count FROM ingress_attempts WHERE endpoint_id = %s AND provider_delivery_id = %s",
                (endpoint["id"], delivery_id),
            )
            assert cur.fetchone()["count"] == 8
