from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.voice_intake import INDIA_TZ, interpret_request, simulated_slot, slot_matches_request
from tests.test_api_workflows import create_request


def test_interpret_extracts_relative_date_time_and_budget_without_guessing() -> None:
    now = datetime(2026, 9, 22, 23, 30, tzinfo=INDIA_TZ)
    result = interpret_request(
        "Book AC servicing tomorrow after 2 PM under ₹1,000 at 18th Main, HSR Layout",
        now=now,
    )

    assert result.ready
    assert result.missing_fields == []
    assert result.draft.service_type == "AC servicing"
    assert result.draft.requested_date == "2026-09-23"
    assert result.draft.time_window == "After 2:00 PM"
    assert result.draft.budget_rupees == 1000
    assert result.draft.address == "18th Main, HSR Layout"


def test_interpret_asks_for_missing_booking_constraints() -> None:
    result = interpret_request("Please book AC servicing", saved_address="My saved address")

    assert not result.ready
    assert result.draft.requested_date is None
    assert result.draft.time_window is None
    assert result.draft.budget_rupees is None
    assert result.missing_fields == ["requested_date", "time_window", "budget_rupees"]


def test_followup_with_spoken_calendar_date_completes_draft() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=INDIA_TZ)
    result = interpret_request(
        "Book AC servicing 26 september 2026, after 2 pm, budget is 2500",
        saved_address="18th Main, HSR Layout",
        now=now,
    )

    assert result.ready
    assert result.missing_fields == []
    assert result.draft.requested_date == "2026-09-26"
    assert result.draft.time_window == "After 2:00 PM"
    assert result.draft.budget_rupees == 2500


def test_spoken_date_variants_and_corrections() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=INDIA_TZ)
    for transcript in (
        "26th of September 2026 at 2 p.m.",
        "September 26, 2026 at 2 PM",
        "26/09/2026 at two pm",
        "2026-09-26 at 2:00 p.m.",
    ):
        result = interpret_request(transcript, now=now)
        assert result.draft.requested_date == "2026-09-26", transcript
        assert result.draft.time_window == "At 2:00 PM", transcript

    correction = interpret_request(
        "Book AC servicing this Saturday after 4 PM. 26 September 2026 at 2 PM",
        now=now,
    )
    assert correction.draft.requested_date == "2026-09-26"
    assert correction.draft.time_window == "At 2:00 PM"


def test_spoken_at_time_does_not_override_saved_address() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=INDIA_TZ)
    for transcript in (
        "AC servicing 26 September 2026 at two pm under 1000",
        "AC servicing 26 September 2026 at 2 p.m. under 1000",
    ):
        result = interpret_request(transcript, saved_address="18th Main, HSR Layout", now=now)
        assert result.ready
        assert result.draft.address == "18th Main, HSR Layout"


def test_broad_daypart_and_around_preserve_spoken_constraint() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=INDIA_TZ)
    around = interpret_request("AC servicing this Saturday around 2 p.m.", now=now)
    assert around.draft.time_window == "Around 2:00 PM"
    evening = interpret_request("AC servicing next Friday evening", now=now)
    assert evening.draft.requested_date == "2026-10-02"
    assert evening.draft.time_window == "Evening"


def test_missing_meridiem_requires_clarification() -> None:
    result = interpret_request(
        "Book AC servicing tomorrow at 3 under 1000",
        saved_address="18th Main, HSR Layout",
        now=datetime(2026, 9, 22, 12, 0, tzinfo=INDIA_TZ),
    )
    assert not result.ready
    assert result.draft.requested_date == "2026-09-23"
    assert result.draft.time_window is None
    assert result.missing_fields == ["time_window"]


def test_past_or_invalid_explicit_date_requires_clarification() -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=INDIA_TZ)
    for spoken_date in ("31 September 2026", "21 September 2026"):
        result = interpret_request(f"AC servicing {spoken_date} after 2 PM", now=now)
        assert result.draft.requested_date is None


def test_exact_and_around_slots_respect_start_time() -> None:
    slot = simulated_slot("2026-09-26", "At 2:00 PM")
    assert slot == "2026-09-26, 2:00 PM - 3:00 PM"
    assert slot_matches_request(
        requested_date="2026-09-26", time_window="At 2:00 PM", slot=slot
    )
    assert not slot_matches_request(
        requested_date="2026-09-26", time_window="At 2:00 PM",
        slot="2026-09-26, 4:00 PM - 5:00 PM",
    )
    assert slot_matches_request(
        requested_date="2026-09-26", time_window="Around 2:00 PM",
        slot="2026-09-26, 2:30 PM - 3:30 PM",
    )
    assert not slot_matches_request(
        requested_date="2026-09-26", time_window="Around 2:00 PM",
        slot="2026-09-26, 4:00 PM - 5:00 PM",
    )
    evening_slot = simulated_slot("2026-09-26", "Evening")
    assert evening_slot == "2026-09-26, 6:00 PM - 7:00 PM"
    assert slot_matches_request(
        requested_date="2026-09-26", time_window="Evening", slot=evening_slot
    )


def test_interpret_endpoint_uses_address_memory_and_never_creates_task(
    seeded_client: TestClient,
) -> None:
    response = seeded_client.post(
        "/api/v1/voice/interpret",
        json={
            "user_id": "demo-user",
            "transcript": "Book AC servicing this Saturday after 2 PM under Rs 1,000",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ready"]
    assert body["draft"]["address"] == "18th Main, HSR Layout, Bengaluru"
    assert body["draft"]["budget_rupees"] == 1000
    assert "saved address" in body["reply"]
    assert seeded_client.get("/api/v1/tasks").json() == []


def test_interpret_tomorrow_uses_india_calendar_day(seeded_client: TestClient) -> None:
    expected = (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()
    body = seeded_client.post(
        "/api/v1/voice/interpret",
        json={"transcript": "AC servicing tomorrow after 6 PM under 900 rupees"},
    ).json()
    assert body["draft"]["requested_date"] == expected
    assert body["draft"]["time_window"] == "After 6:00 PM"


def test_simulated_call_honors_spoken_day_and_time(seeded_client: TestClient) -> None:
    task = create_request(seeded_client, scenario="happy")
    # A second task with a Sunday constraint demonstrates that the provider offer follows it.
    response = seeded_client.post(
        "/api/v1/tasks",
        json={
            "user_id": "demo-user",
            "idempotency_key": "sunday-evening-request",
            "description": "Book AC servicing Sunday after 6 PM, under Rs 1,000.",
            "service_type": "AC servicing",
            "requested_date": "Sunday",
            "time_window": "After 6:00 PM",
            "budget_rupees": 1000,
            "scenario": "happy",
        },
    )
    assert response.status_code == 201
    sunday_task = response.json()
    completed = seeded_client.post(f"/api/v1/tasks/{sunday_task['id']}/run").json()
    assert completed["status"] == "completed"
    assert completed["quoted_slot"] == "Sunday, 7:00 PM - 8:00 PM"
    assert task["status"] == "requested"


def test_slot_guard_rejects_wrong_day_or_time() -> None:
    assert slot_matches_request(
        requested_date="Saturday",
        time_window="After 2:00 PM",
        slot="Saturday, 4:00 PM - 5:00 PM",
    )
    assert not slot_matches_request(
        requested_date="Saturday",
        time_window="After 2:00 PM",
        slot="Sunday, 4:00 PM - 5:00 PM",
    )
    assert not slot_matches_request(
        requested_date="Saturday",
        time_window="After 6:00 PM",
        slot="Saturday, 4:00 PM - 5:00 PM",
    )


def test_operator_quote_above_budget_requires_user_approval(seeded_client: TestClient) -> None:
    task = create_request(seeded_client, scenario="unavailable")
    seeded_client.post(f"/api/v1/tasks/{task['id']}/run")
    result = seeded_client.post(
        f"/api/v1/ops/tasks/{task['id']}/resolve",
        json={
            "operator": "Suraj",
            "quote_rupees": 1500,
            "quoted_slot": "Saturday, 4:00 PM - 5:00 PM",
        },
    ).json()
    assert result["status"] == "awaiting_approval"
    assert result["confirmation_ref"] is None
    assert "above the approved budget" in result["attention_reason"]
