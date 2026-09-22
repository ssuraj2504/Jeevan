from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.domain import InvalidTransition, TaskStatus, assert_transition, needs_approval
from app.integrations.browser import PlaywrightBrowserAdapter, PriceChanged, SimulatedBrowserAdapter
from app.integrations.voice import LiveKitVoiceAdapter, SimulatedVoiceAdapter
from app.models import Memory, ProviderBooking, Task, TaskEvent
from app.schemas import MemoryUpsert, TaskCreate
from app.voice_intake import slot_matches_request

STATUS_ACTIONS = {
    TaskStatus.REQUESTED: "Preparing the request",
    TaskStatus.CALLING: "Calling the service provider",
    TaskStatus.QUOTE_RECEIVED: "Checking the quote against your limits",
    TaskStatus.AWAITING_APPROVAL: "Waiting for your approval",
    TaskStatus.BOOKING: "Confirming the appointment in the provider portal",
    TaskStatus.RETRY_SCHEDULED: "Waiting to retry the provider call",
    TaskStatus.NEEDS_HUMAN: "Waiting for an operator to review the task",
    TaskStatus.COMPLETED: "Booking confirmed",
    TaskStatus.CANCELLED: "Task cancelled",
}


def get_task_or_404(db: Session, task_id: str, *, user_id: str | None = None) -> Task:
    query = (
        select(Task)
        .options(selectinload(Task.events))
        .where(Task.id == task_id)
        .execution_options(populate_existing=True)
    )
    if user_id:
        query = query.where(Task.user_id == user_id)
    task = db.scalar(query)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def add_event(
    db: Session,
    task: Task,
    event_type: str,
    message: str,
    *,
    actor: str = "system",
    data: dict | None = None,
) -> None:
    db.add(
        TaskEvent(
            task_id=task.id,
            event_type=event_type,
            actor=actor,
            message=message,
            data_json=json.dumps(data or {}, ensure_ascii=True),
        )
    )


def transition(
    db: Session,
    task: Task,
    target: TaskStatus,
    message: str,
    *,
    actor: str = "agent",
    data: dict | None = None,
) -> None:
    try:
        assert_transition(task.status, target)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if target == TaskStatus.COMPLETED and not task.confirmation_ref:
        raise HTTPException(
            status_code=409, detail="Completion requires provider confirmation evidence"
        )
    previous = task.status
    task.status = target.value
    task.current_action = STATUS_ACTIONS[target]
    task.version += 1
    if target == TaskStatus.COMPLETED:
        task.completed_at = datetime.now(UTC)
    add_event(
        db,
        task,
        "state.transition",
        message,
        actor=actor,
        data={"from": previous, "to": target.value, **(data or {})},
    )


def _memory_value(db: Session, user_id: str, key: str) -> str | None:
    memory = db.scalar(select(Memory).where(Memory.user_id == user_id, Memory.key == key))
    return memory.value if memory else None


def create_task(db: Session, payload: TaskCreate) -> tuple[Task, bool]:
    existing = db.scalar(select(Task).where(Task.idempotency_key == payload.idempotency_key))
    if existing:
        return get_task_or_404(db, existing.id), False

    address = payload.address or _memory_value(db, payload.user_id, "home_address")
    if not address:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide an address or save a home_address memory first",
        )

    task = Task(
        user_id=payload.user_id,
        idempotency_key=payload.idempotency_key,
        description=payload.description,
        service_type=payload.service_type,
        requested_date=payload.requested_date,
        time_window=payload.time_window,
        budget_paise=payload.budget_rupees * 100 if payload.budget_rupees else None,
        address=address,
        phone=payload.phone,
        scenario=payload.scenario,
    )
    db.add(task)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Task).where(Task.idempotency_key == payload.idempotency_key))
        if not existing:
            raise
        return get_task_or_404(db, existing.id), False

    add_event(
        db,
        task,
        "task.created",
        "Request received and constraints recorded",
        actor="user",
        data={
            "budget_paise": task.budget_paise,
            "requested_date": task.requested_date,
            "time_window": task.time_window,
        },
    )
    if not payload.address:
        add_event(
            db,
            task,
            "memory.retrieved",
            "Used the saved home address",
            data={"key": "home_address"},
        )
    db.commit()
    return get_task_or_404(db, task.id), True


def _booking_adapter():
    settings = get_settings()
    if settings.booking_adapter == "playwright":
        return PlaywrightBrowserAdapter(settings.public_base_url)
    return SimulatedBrowserAdapter()


def approval_reason(task: Task) -> str | None:
    """Explain any provider offer that differs from the user's saved constraints."""
    reasons = []
    if task.quote_paise is None or task.quote_paise <= 0 or not task.quoted_slot:
        return "Provider offer is missing a valid price or appointment slot"
    if needs_approval(quote_paise=task.quote_paise, budget_paise=task.budget_paise):
        reasons.append("Provider quote is above the approved budget")
    if not slot_matches_request(
        requested_date=task.requested_date,
        time_window=task.time_window,
        slot=task.quoted_slot,
    ):
        reasons.append("Provider slot differs from the requested date or time")
    return "; ".join(reasons) or None


def confirm_booking(db: Session, task: Task) -> Task:
    try:
        evidence = _booking_adapter().confirm(db, task)
    except PriceChanged as exc:
        task.quote_paise = exc.new_price_paise
        task.attention_reason = "Provider changed the price before confirmation"
        transition(
            db,
            task,
            TaskStatus.AWAITING_APPROVAL,
            "Provider changed the price; requesting fresh approval",
            data={"new_quote_paise": exc.new_price_paise},
        )
        db.commit()
        return get_task_or_404(db, task.id)
    except Exception as exc:
        task.attention_reason = str(exc)
        transition(
            db,
            task,
            TaskStatus.NEEDS_HUMAN,
            "Browser confirmation could not be verified",
            data={"error": type(exc).__name__},
        )
        db.commit()
        return get_task_or_404(db, task.id)

    task.confirmation_ref = evidence.confirmation_ref
    task.attention_reason = None
    add_event(
        db,
        task,
        "browser.confirmed",
        f"Provider returned confirmation {evidence.confirmation_ref}",
        data={"source": evidence.source},
    )
    transition(db, task, TaskStatus.COMPLETED, "Appointment confirmed with provider evidence")
    upsert_memory(
        db,
        task.user_id,
        "last_provider",
        MemoryUpsert(
            value=task.provider_name or "CoolCare Services",
            kind="history",
            source=f"task:{task.id}",
        ),
        commit=False,
    )
    db.commit()
    return get_task_or_404(db, task.id)


def run_task(db: Session, task: Task) -> Task:
    if task.status not in {
        TaskStatus.REQUESTED.value,
        TaskStatus.RETRY_SCHEDULED.value,
        TaskStatus.NEEDS_HUMAN.value,
    }:
        raise HTTPException(status_code=409, detail=f"Task cannot run from {task.status}")

    transition(db, task, TaskStatus.CALLING, "Started outbound provider call")
    task.attempt_count += 1
    settings = get_settings()
    if settings.voice_adapter == "livekit":
        # FastAPI executes this synchronous endpoint in a worker thread, so a
        # short-lived event loop is safe here. The callback continues the workflow.
        try:
            dispatch_id = asyncio.run(
                LiveKitVoiceAdapter(
                    url=settings.livekit_url,
                    api_key=settings.livekit_api_key,
                    api_secret=settings.livekit_api_secret,
                    sip_trunk_id=settings.livekit_sip_trunk_id,
                    agent_name=settings.livekit_agent_name,
                    callback_url=f"{settings.public_base_url}/api/v1/webhooks/livekit",
                    callback_secret=settings.jeevan_webhook_secret,
                ).dispatch(task)
            )
        except Exception as exc:
            task.attention_reason = str(exc)
            target = (
                TaskStatus.RETRY_SCHEDULED
                if task.attempt_count < settings.max_call_attempts
                else TaskStatus.NEEDS_HUMAN
            )
            transition(
                db,
                task,
                target,
                "LiveKit dispatch failed before a call was placed",
                data={"error": type(exc).__name__},
            )
        else:
            add_event(
                db,
                task,
                "voice.dispatched",
                "LiveKit worker dispatched; waiting for signed call events",
                data={"dispatch_id": dispatch_id},
            )
        db.commit()
        return get_task_or_404(db, task.id)

    outcome = SimulatedVoiceAdapter().call(
        scenario=task.scenario,
        attempt=task.attempt_count,
        service_type=task.service_type,
        requested_date=task.requested_date,
        time_window=task.time_window,
    )
    for speaker, text in outcome.transcript:
        add_event(
            db,
            task,
            "voice.transcript",
            text,
            actor=speaker.lower(),
            data={"speaker": speaker, "attempt": task.attempt_count},
        )

    if not outcome.success:
        task.attention_reason = outcome.reason
        if outcome.retryable and task.attempt_count < settings.max_call_attempts:
            transition(
                db,
                task,
                TaskStatus.RETRY_SCHEDULED,
                outcome.reason or "Call will be retried",
                data={"attempt": task.attempt_count},
            )
        else:
            transition(
                db,
                task,
                TaskStatus.NEEDS_HUMAN,
                outcome.reason or "Provider call needs operator review",
                data={"attempt": task.attempt_count},
            )
        db.commit()
        return get_task_or_404(db, task.id)

    task.provider_name = outcome.provider_name
    task.quote_paise = outcome.quote_paise
    task.quoted_slot = outcome.slot
    task.attention_reason = None
    transition(
        db,
        task,
        TaskStatus.QUOTE_RECEIVED,
        "Captured a structured price and appointment slot from the call",
        data={"quote_paise": task.quote_paise, "slot": task.quoted_slot},
    )
    reason = approval_reason(task)
    if reason:
        task.attention_reason = reason
        transition(
            db,
            task,
            TaskStatus.AWAITING_APPROVAL,
            "Provider offer needs user approval; no booking action was taken",
        )
        db.commit()
        return get_task_or_404(db, task.id)

    transition(db, task, TaskStatus.BOOKING, "Quote is within the approved constraints")
    return confirm_booking(db, task)


def approve_task(db: Session, task: Task, *, approved: bool, task_version: int) -> Task:
    if task.status != TaskStatus.AWAITING_APPROVAL.value:
        raise HTTPException(status_code=409, detail="Task is not awaiting approval")
    if task.version != task_version:
        raise HTTPException(
            status_code=409,
            detail="This quote changed after it was shown. Refresh before approving.",
        )
    if not approved:
        task.attention_reason = "User declined the proposed quote"
        transition(db, task, TaskStatus.NEEDS_HUMAN, "User declined the quote", actor="user")
        db.commit()
        return get_task_or_404(db, task.id)

    task.attention_reason = None
    transition(
        db,
        task,
        TaskStatus.BOOKING,
        "User approved the current price and slot",
        actor="user",
        data={"approved_version": task_version},
    )
    return confirm_booking(db, task)


def cancel_task(db: Session, task: Task) -> Task:
    if task.status in {TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value}:
        raise HTTPException(status_code=409, detail=f"Task cannot be cancelled from {task.status}")
    transition(db, task, TaskStatus.CANCELLED, "User cancelled the task", actor="user")
    db.commit()
    return get_task_or_404(db, task.id)


def assign_operator(db: Session, task: Task, operator: str, note: str | None) -> Task:
    task.assigned_operator = operator
    if task.status not in {
        TaskStatus.NEEDS_HUMAN.value,
        *[s.value for s in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}],
    }:
        transition(
            db, task, TaskStatus.NEEDS_HUMAN, "Operator took control of the task", actor=operator
        )
    add_event(
        db,
        task,
        "operator.assigned",
        note or f"Task assigned to {operator}",
        actor=operator,
    )
    db.commit()
    return get_task_or_404(db, task.id)


def resolve_task(
    db: Session,
    task: Task,
    *,
    operator: str,
    note: str | None,
    quote_rupees: int | None,
    quoted_slot: str | None,
) -> Task:
    if task.status != TaskStatus.NEEDS_HUMAN.value:
        raise HTTPException(status_code=409, detail="Task is not waiting for an operator")
    if quote_rupees is not None:
        task.quote_paise = quote_rupees * 100
    if quoted_slot is not None:
        task.quoted_slot = quoted_slot
    add_event(
        db, task, "operator.corrected", note or "Operator reviewed task details", actor=operator
    )
    if task.quote_paise and task.quoted_slot:
        reason = approval_reason(task)
        if reason:
            task.attention_reason = reason
            transition(
                db,
                task,
                TaskStatus.AWAITING_APPROVAL,
                "Operator supplied an offer outside the requested constraints",
                actor=operator,
            )
            db.commit()
            return get_task_or_404(db, task.id)
        transition(
            db, task, TaskStatus.BOOKING, "Operator resumed browser confirmation", actor=operator
        )
        return confirm_booking(db, task)
    return run_task(db, task)


def upsert_memory(
    db: Session,
    user_id: str,
    key: str,
    payload: MemoryUpsert,
    *,
    commit: bool = True,
) -> Memory:
    memory = db.scalar(select(Memory).where(Memory.user_id == user_id, Memory.key == key))
    if memory:
        memory.value = payload.value
        memory.kind = payload.kind
        memory.source = payload.source
        memory.durable = payload.durable
    else:
        memory = Memory(user_id=user_id, key=key, **payload.model_dump())
        db.add(memory)
    db.flush()
    if commit:
        db.commit()
        db.refresh(memory)
    return memory


def metrics(db: Session) -> dict[str, int | float]:
    counts = dict(db.execute(select(Task.status, func.count(Task.id)).group_by(Task.status)).all())
    total = sum(counts.values())
    completed = counts.get(TaskStatus.COMPLETED.value, 0)
    attention = counts.get(TaskStatus.NEEDS_HUMAN.value, 0) + counts.get(
        TaskStatus.AWAITING_APPROVAL.value, 0
    )
    handoffs = (
        db.scalar(
            select(func.count(func.distinct(TaskEvent.task_id))).where(
                TaskEvent.event_type == "operator.assigned"
            )
        )
        or 0
    )
    duplicate_bookings = (
        db.scalar(
            select(func.count()).select_from(
                select(ProviderBooking.task_id)
                .group_by(ProviderBooking.task_id)
                .having(func.count(ProviderBooking.id) > 1)
                .subquery()
            )
        )
        or 0
    )
    terminal = completed + counts.get(TaskStatus.CANCELLED.value, 0)
    active = total - terminal
    return {
        "total_tasks": total,
        "active_tasks": active,
        "needs_attention": attention,
        "completed_tasks": completed,
        "completion_rate": round(completed / total * 100, 1) if total else 0.0,
        "human_handoff_rate": round(handoffs / total * 100, 1) if total else 0.0,
        "duplicate_bookings": duplicate_bookings,
    }
