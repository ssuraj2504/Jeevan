from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.domain import TaskStatus


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    service_type: Mapped[str] = mapped_column(String(80), default="AC servicing")
    requested_date: Mapped[str] = mapped_column(String(40))
    time_window: Mapped[str] = mapped_column(String(80))
    budget_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    address: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    scenario: Mapped[str] = mapped_column(String(40), default="above_budget")
    status: Mapped[str] = mapped_column(String(40), default=TaskStatus.REQUESTED.value, index=True)
    current_action: Mapped[str] = mapped_column(String(180), default="Preparing the request")
    attention_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    quote_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quoted_slot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confirmation_ref: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    assigned_operator: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list[TaskEvent]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskEvent.occurred_at"
    )


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    message: Mapped[str] = mapped_column(Text)
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="events")


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_memory_user_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    key: Mapped[str] = mapped_column(String(80))
    value: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), default="explicit")
    source: Mapped[str] = mapped_column(String(120), default="user")
    durable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProviderBooking(Base):
    __tablename__ = "provider_bookings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    confirmation_ref: Mapped[str] = mapped_column(String(80), unique=True)
    provider_name: Mapped[str] = mapped_column(String(120))
    slot: Mapped[str] = mapped_column(String(80))
    price_paise: Mapped[int] = mapped_column(Integer)
    address: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_tasks_user_updated", Task.user_id, Task.updated_at)
