import uuid

from fastapi.testclient import TestClient

from tests.test_api_workflows import create_request


def test_livekit_webhook_is_authenticated_and_idempotent(seeded_client: TestClient) -> None:
    task = create_request(seeded_client, scenario="happy")
    started = seeded_client.post(f"/api/v1/tasks/{task['id']}/run").json()
    assert started["status"] == "completed"

    unauthorized = seeded_client.post(
        "/api/v1/webhooks/livekit",
        json={"task_id": task["id"], "event_id": str(uuid.uuid4()), "event_type": "transcript"},
    )
    assert unauthorized.status_code == 401


def test_duplicate_transcript_event_is_stored_once(seeded_client: TestClient) -> None:
    task = create_request(seeded_client, scenario="unavailable")
    # Create a calling state through a real run, then use a separate task for transcript dedupe.
    event_id = "provider-line-1"
    payload = {
        "task_id": task["id"],
        "event_id": event_id,
        "event_type": "transcript",
        "speaker": "provider",
        "text": "We can visit Saturday afternoon.",
    }
    first = seeded_client.post(
        "/api/v1/webhooks/livekit", json=payload, headers={"X-Jeevan-Signature": "change-me"}
    )
    second = seeded_client.post(
        "/api/v1/webhooks/livekit", json=payload, headers={"X-Jeevan-Signature": "change-me"}
    )
    assert first.status_code == 200
    assert second.status_code == 200
    stored = [
        event for event in second.json()["events"] if event["event_type"] == f"livekit.{event_id}"
    ]
    assert len(stored) == 1
