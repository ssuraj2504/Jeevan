import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ProviderBooking


def create_request(client: TestClient, *, scenario: str = "happy", key: str | None = None) -> dict:
    response = client.post(
        "/api/v1/tasks",
        json={
            "user_id": "demo-user",
            "idempotency_key": key or str(uuid.uuid4()),
            "description": "Book AC servicing this Saturday after 2 PM, under Rs 1,000.",
            "service_type": "AC servicing",
            "requested_date": "Saturday",
            "time_window": "After 2:00 PM",
            "budget_rupees": 1000,
            "address": None,
            "scenario": scenario,
        },
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()


def test_happy_path_requires_and_records_confirmation(
    seeded_client: TestClient, db_session: Session
) -> None:
    task = create_request(seeded_client, scenario="happy")
    result = seeded_client.post(f"/api/v1/tasks/{task['id']}/run")

    assert result.status_code == 200
    completed = result.json()
    assert completed["status"] == "completed"
    assert completed["confirmation_ref"].startswith("JVN-")
    assert any(event["event_type"] == "browser.confirmed" for event in completed["events"])
    booking_count = db_session.scalar(select(func.count(ProviderBooking.id)))
    assert booking_count == 1


def test_above_budget_pauses_and_stale_approval_is_rejected(seeded_client: TestClient) -> None:
    task = create_request(seeded_client, scenario="above_budget")
    quoted = seeded_client.post(f"/api/v1/tasks/{task['id']}/run").json()

    assert quoted["status"] == "awaiting_approval"
    assert quoted["quote_paise"] == 115_000
    assert quoted["confirmation_ref"] is None

    stale = seeded_client.post(
        f"/api/v1/tasks/{task['id']}/approve",
        json={"approved": True, "task_version": quoted["version"] - 1},
    )
    assert stale.status_code == 409

    approved = seeded_client.post(
        f"/api/v1/tasks/{task['id']}/approve",
        json={"approved": True, "task_version": quoted["version"]},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "completed"
    assert approved.json()["confirmation_ref"]


def test_idempotency_key_returns_same_task_and_one_side_effect(
    seeded_client: TestClient, db_session: Session
) -> None:
    key = "same-request-123"
    first = create_request(seeded_client, key=key)
    second = create_request(seeded_client, key=key)
    assert first["id"] == second["id"]

    seeded_client.post(f"/api/v1/tasks/{first['id']}/run")
    replay = seeded_client.post(f"/api/v1/tasks/{first['id']}/run")
    assert replay.status_code == 409
    assert db_session.scalar(select(func.count(ProviderBooking.id))) == 1


def test_retry_preserves_task_and_recovers_after_no_answer(seeded_client: TestClient) -> None:
    task = create_request(seeded_client, scenario="no_answer")
    first_attempt = seeded_client.post(f"/api/v1/tasks/{task['id']}/run").json()
    assert first_attempt["status"] == "retry_scheduled"
    assert first_attempt["attempt_count"] == 1

    second_attempt = seeded_client.post(f"/api/v1/tasks/{task['id']}/retry").json()
    assert second_attempt["status"] == "completed"
    assert second_attempt["attempt_count"] == 2
    assert second_attempt["confirmation_ref"]


def test_price_change_requires_fresh_approval(seeded_client: TestClient) -> None:
    task = create_request(seeded_client, scenario="price_change")
    changed = seeded_client.post(f"/api/v1/tasks/{task['id']}/run").json()

    assert changed["status"] == "awaiting_approval"
    assert changed["quote_paise"] == 115_000
    assert changed["confirmation_ref"] is None

    completed = seeded_client.post(
        f"/api/v1/tasks/{task['id']}/approve",
        json={"approved": True, "task_version": changed["version"]},
    ).json()
    assert completed["status"] == "completed"


def test_operator_can_recover_unavailable_provider(seeded_client: TestClient) -> None:
    task = create_request(seeded_client, scenario="unavailable")
    blocked = seeded_client.post(f"/api/v1/tasks/{task['id']}/run").json()
    assert blocked["status"] == "needs_human"

    resolved = seeded_client.post(
        f"/api/v1/ops/tasks/{task['id']}/resolve",
        json={
            "operator": "Suraj",
            "note": "Found an alternate provider manually",
            "quote_rupees": 900,
            "quoted_slot": "Sunday, 3:00 PM - 4:00 PM",
        },
    ).json()
    assert resolved["status"] == "awaiting_approval"
    assert "slot differs" in resolved["attention_reason"]
    assert resolved["confirmation_ref"] is None

    approved = seeded_client.post(
        f"/api/v1/tasks/{task['id']}/approve",
        json={"approved": True, "task_version": resolved["version"]},
    ).json()
    assert approved["status"] == "completed"
    assert approved["confirmation_ref"]


def test_memory_queries_are_scoped_to_the_requested_user(seeded_client: TestClient) -> None:
    saved = seeded_client.put(
        "/api/v1/users/another-user/memories/home_address",
        json={"value": "A different address", "kind": "explicit", "source": "user"},
    )
    assert saved.status_code == 200

    demo_memories = seeded_client.get("/api/v1/users/demo-user/memories").json()
    other_memories = seeded_client.get("/api/v1/users/another-user/memories").json()
    assert "A different address" not in {memory["value"] for memory in demo_memories}
    assert {memory["value"] for memory in other_memories} == {"A different address"}


def test_declining_quote_never_creates_booking(
    seeded_client: TestClient, db_session: Session
) -> None:
    task = create_request(seeded_client, scenario="above_budget")
    quoted = seeded_client.post(f"/api/v1/tasks/{task['id']}/run").json()
    declined = seeded_client.post(
        f"/api/v1/tasks/{task['id']}/approve",
        json={"approved": False, "task_version": quoted["version"]},
    ).json()

    assert declined["status"] == "needs_human"
    assert declined["confirmation_ref"] is None
    assert db_session.scalar(select(func.count(ProviderBooking.id))) == 0
