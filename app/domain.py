from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    REQUESTED = "requested"
    CALLING = "calling"
    QUOTE_RECEIVED = "quote_received"
    AWAITING_APPROVAL = "awaiting_approval"
    BOOKING = "booking"
    RETRY_SCHEDULED = "retry_scheduled"
    NEEDS_HUMAN = "needs_human"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}

ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.REQUESTED: {TaskStatus.CALLING, TaskStatus.NEEDS_HUMAN, TaskStatus.CANCELLED},
    TaskStatus.CALLING: {
        TaskStatus.QUOTE_RECEIVED,
        TaskStatus.RETRY_SCHEDULED,
        TaskStatus.NEEDS_HUMAN,
        TaskStatus.CANCELLED,
    },
    TaskStatus.QUOTE_RECEIVED: {
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.BOOKING,
        TaskStatus.NEEDS_HUMAN,
        TaskStatus.CANCELLED,
    },
    TaskStatus.AWAITING_APPROVAL: {
        TaskStatus.BOOKING,
        TaskStatus.NEEDS_HUMAN,
        TaskStatus.CANCELLED,
    },
    TaskStatus.BOOKING: {
        TaskStatus.COMPLETED,
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.RETRY_SCHEDULED,
        TaskStatus.NEEDS_HUMAN,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RETRY_SCHEDULED: {
        TaskStatus.CALLING,
        TaskStatus.BOOKING,
        TaskStatus.NEEDS_HUMAN,
        TaskStatus.CANCELLED,
    },
    TaskStatus.NEEDS_HUMAN: {
        TaskStatus.CALLING,
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.BOOKING,
        TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: set(),
    TaskStatus.CANCELLED: set(),
}


class InvalidTransition(ValueError):
    pass


def assert_transition(current: str | TaskStatus, target: str | TaskStatus) -> None:
    current_status = TaskStatus(current)
    target_status = TaskStatus(target)
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise InvalidTransition(f"Cannot transition from {current_status} to {target_status}")


def needs_approval(*, quote_paise: int, budget_paise: int | None) -> bool:
    return budget_paise is not None and quote_paise > budget_paise
