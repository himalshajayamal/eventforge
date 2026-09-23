import uuid

from fastapi.testclient import TestClient

from app.main import ADMIN_PASSWORD, ADMIN_USER, app

client = TestClient(app)
ADMIN_AUTH = (ADMIN_USER, ADMIN_PASSWORD)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_version() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["version"] == "0.1.0"


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
    assert matching[0]["type"] == "push"
    assert matching[0]["source"] == "github"

    attempts = client.get(f"/events/{event_id}/ingress-attempts", auth=ADMIN_AUTH)
    assert attempts.status_code == 200
    assert [item["outcome"] for item in attempts.json()] == ["ACCEPTED", "DUPLICATE"]


def test_requests_without_idempotency_key_create_independent_events() -> None:
    suffix = uuid.uuid4().hex

    project = client.post(
        "/projects",
        auth=ADMIN_AUTH,
        json={"name": f"generic-test-{suffix}"},
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
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is False
    assert first.json()["event_id"] != second.json()["event_id"]
