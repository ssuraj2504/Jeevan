"""The anonymous portfolio demo cannot expose another visitor's data."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.demo_security import COOKIE_NAME
from app.main import app
from app.models import Memory, Task


@pytest.fixture()
def public_demo(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_SESSION_SECRET", "portfolio-test-secret-that-is-long-enough")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    get_settings.cache_clear()
    try:
        yield client
    finally:
        get_settings.cache_clear()


def payload(*, key: str | None = None, scenario: str = "above_budget") -> dict:
    return {
        "user_id": "victim-user",
        "idempotency_key": key or str(uuid.uuid4()),
        "description": "My real name is Secret Person and my phone is 9999999999",
        "service_type": "Anything at all",
        "requested_date": "Saturday",
        "time_window": "After 2:00 PM",
        "budget_rupees": 1000,
        "address": "My real house, Bengaluru",
        "phone": "+919999999999",
        "scenario": scenario,
    }


def test_public_demo_uses_private_session_and_never_persists_submitted_pii(
    public_demo: TestClient, db_session: Session
) -> None:
    created = public_demo.post("/api/v1/tasks", json=payload())
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["user_id"].startswith("demo:")
    assert task["address"] == "Demo apartment, HSR Layout, Bengaluru"
    assert "Secret Person" not in task["description"]
    assert "My real house" not in task["address"]
    stored = db_session.scalar(select(Task).where(Task.id == task["id"]))
    assert stored is not None
    assert stored.phone is None
    assert stored.idempotency_key.startswith(task["user_id"])
    assert public_demo.get("/api/v1/tasks?user_id=victim-user").json()[0]["id"] == task["id"]
    memory = public_demo.get("/api/v1/users/demo-user/memories").json()
    assert len(memory) == 1
    assert memory[0]["value"] == "Demo apartment, HSR Layout, Bengaluru"
    assert db_session.scalar(select(func.count(Memory.id))) == 0
    assert "Secret Person" not in " ".join(event.message for event in stored.events)


def test_guessed_task_ids_and_memory_paths_do_not_cross_sessions(
    public_demo: TestClient,
) -> None:
    victim = public_demo.post("/api/v1/tasks", json=payload()).json()
    owner = victim["user_id"]
    public_demo.post(f"/api/v1/tasks/{victim['id']}/run")
    outsider = TestClient(app)
    assert outsider.get("/api/v1/tasks?user_id=" + owner).json() == []
    assert outsider.get(f"/api/v1/tasks/{victim['id']}?user_id={owner}").status_code == 404
    for suffix, body in (
        ("run", None),
        ("retry", None),
        ("cancel", None),
        ("approve", {"approved": True, "task_version": 4}),
    ):
        response = outsider.post(f"/api/v1/tasks/{victim['id']}/{suffix}", json=body)
        assert response.status_code == 404, (suffix, response.text)
    assert outsider.get(f"/api/v1/users/{owner}/memories").status_code == 404
    assert outsider.put(
        "/api/v1/users/demo-user/memories/home_address",
        json={"value": "Someone else's place"},
    ).status_code == 403
    assert outsider.delete("/api/v1/users/demo-user/memories/home_address").status_code == 403


def test_public_mode_disables_privileged_surface_and_bounds_task_count(
    public_demo: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_MAX_TASKS_PER_SESSION", "1")
    get_settings.cache_clear()
    dashboard = public_demo.get("/")
    assert dashboard.status_code == 200
    assert 'data-public-demo="true"' in dashboard.text
    assert 'data-view-target="operations"' not in dashboard.text
    assert "Public sandbox" in dashboard.text
    assert public_demo.get("/docs").status_code == 404
    assert public_demo.get("/openapi.json").status_code == 404
    assert public_demo.get("/demo-provider").status_code == 404
    assert public_demo.post("/api/v1/demo/seed").status_code == 404
    assert public_demo.get("/api/v1/ops/metrics").status_code == 404
    assert public_demo.post(
        "/api/v1/ops/tasks/fake/resolve", json={"operator": "Me"}
    ).status_code == 404
    assert public_demo.post("/api/v1/webhooks/livekit", json={}).status_code == 404
    first_payload = payload(key="first-key")
    assert public_demo.post("/api/v1/tasks", json=first_payload).status_code == 201
    assert public_demo.post("/api/v1/tasks", json=first_payload).status_code == 200
    assert public_demo.post("/api/v1/tasks", json=payload(key="second-key")).status_code == 429


def test_public_demo_voice_uses_fake_address_and_blocks_cross_origin(
    public_demo: TestClient,
) -> None:
    response = public_demo.post(
        "/api/v1/voice/interpret",
        json={
            "user_id": "victim-user",
            "transcript": (
                "Book AC servicing tomorrow after 2 PM budget is 2500 "
                "at my real address, Pune"
            ),
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["ready"]
    assert result["draft"]["address"] == "Demo apartment, HSR Layout, Bengaluru"
    assert "my real address" not in result["reply"]
    rejected = public_demo.post(
        "/api/v1/tasks", json=payload(), headers={"Origin": "https://attacker.example"}
    )
    assert rejected.status_code == 403


def test_forged_cookie_gets_new_identity_and_global_cap_handles_cookie_resets(
    public_demo: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_MAX_TASKS_TOTAL", "1")
    get_settings.cache_clear()
    created = public_demo.post("/api/v1/tasks", json=payload()).json()
    intruder = TestClient(app)
    intruder.cookies.set(COOKIE_NAME, "forged.session.signature")
    assert intruder.get(f"/api/v1/tasks/{created['id']}").status_code == 404
    assert intruder.get("/api/v1/tasks").json() == []
    assert intruder.post("/api/v1/tasks", json=payload()).status_code == 503


def test_identical_client_idempotency_keys_are_isolated_by_session(
    public_demo: TestClient,
) -> None:
    request = payload(key="same-browser-generated-key")
    first = public_demo.post("/api/v1/tasks", json=request)
    second = TestClient(app).post("/api/v1/tasks", json=request)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_demo_requires_secret_and_simulated_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "true")
    monkeypatch.delenv("DEMO_SESSION_SECRET", raising=False)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="DEMO_SESSION_SECRET"):
        with TestClient(app):
            pass
    get_settings.cache_clear()
