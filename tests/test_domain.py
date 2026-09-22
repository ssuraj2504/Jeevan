import pytest

from app.domain import (
    ALLOWED_TRANSITIONS,
    InvalidTransition,
    TaskStatus,
    assert_transition,
    needs_approval,
)


def test_all_declared_transitions_are_accepted() -> None:
    for current, targets in ALLOWED_TRANSITIONS.items():
        for target in targets:
            assert_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.REQUESTED, TaskStatus.COMPLETED),
        (TaskStatus.COMPLETED, TaskStatus.BOOKING),
        (TaskStatus.CANCELLED, TaskStatus.CALLING),
        (TaskStatus.AWAITING_APPROVAL, TaskStatus.COMPLETED),
    ],
)
def test_unsafe_transitions_are_rejected(current: TaskStatus, target: TaskStatus) -> None:
    with pytest.raises(InvalidTransition):
        assert_transition(current, target)


def test_budget_policy_only_escalates_quotes_above_limit() -> None:
    assert needs_approval(quote_paise=100_001, budget_paise=100_000)
    assert not needs_approval(quote_paise=100_000, budget_paise=100_000)
    assert not needs_approval(quote_paise=500_000, budget_paise=None)
