from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskCreate(BaseModel):
    user_id: str = Field(default="demo-user", min_length=2, max_length=80)
    idempotency_key: str = Field(min_length=4, max_length=120)
    description: str = Field(min_length=10, max_length=1200)
    service_type: str = Field(default="AC servicing", min_length=2, max_length=80)
    requested_date: str = Field(default="Saturday", min_length=2, max_length=40)
    time_window: str = Field(default="After 2:00 PM", min_length=2, max_length=80)
    budget_rupees: int | None = Field(default=1000, ge=1, le=1_000_000)
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=30)
    scenario: str = Field(
        default="above_budget",
        pattern="^(happy|above_budget|no_answer|dropped_call|unavailable|price_change)$",
    )

    @field_validator("user_id", "description", "service_type", "requested_date", "time_window")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()


class VoiceInterpretRequest(BaseModel):
    user_id: str = Field(default="demo-user", min_length=2, max_length=80)
    transcript: str = Field(min_length=3, max_length=1200)

    @field_validator("transcript")
    @classmethod
    def strip_transcript(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Transcript is too short")
        return value


class VoiceDraft(BaseModel):
    description: str
    service_type: str | None
    requested_date: str | None
    time_window: str | None
    budget_rupees: int | None
    address: str | None


class VoiceInterpretRead(BaseModel):
    transcript: str
    draft: VoiceDraft
    missing_fields: list[str]
    ready: bool
    reply: str


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    actor: str
    message: str
    data_json: str
    occurred_at: datetime


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    description: str
    service_type: str
    requested_date: str
    time_window: str
    budget_paise: int | None
    address: str
    scenario: str
    status: str
    current_action: str
    attention_reason: str | None
    provider_name: str | None
    quote_paise: int | None
    quoted_slot: str | None
    confirmation_ref: str | None
    attempt_count: int
    version: int
    assigned_operator: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    events: list[EventRead] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    approved: bool
    task_version: int = Field(ge=1)


class OperatorAction(BaseModel):
    operator: str = Field(min_length=2, max_length=80)
    note: str | None = Field(default=None, max_length=500)


class ResolveRequest(OperatorAction):
    quote_rupees: int | None = Field(default=None, ge=1, le=1_000_000)
    quoted_slot: str | None = Field(default=None, max_length=80)


class MemoryUpsert(BaseModel):
    value: str = Field(min_length=1, max_length=500)
    kind: str = Field(default="explicit", pattern="^(explicit|history)$")
    source: str = Field(default="user", min_length=1, max_length=120)
    durable: bool = True


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    key: str
    value: str
    kind: str
    source: str
    durable: bool
    created_at: datetime
    updated_at: datetime


class MetricsRead(BaseModel):
    total_tasks: int
    active_tasks: int
    needs_attention: int
    completed_tasks: int
    completion_rate: float
    human_handoff_rate: float
    duplicate_bookings: int
