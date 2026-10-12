import itertools

import pytest

from app.core.domain import TERMINAL_ORDER_STATUSES, OrderStatus
from app.execution.state_machine import (
    TRANSITIONS,
    InvalidTransitionError,
    assert_transition,
    can_transition,
)

S = OrderStatus


def test_happy_path_is_allowed() -> None:
    path = [S.CREATED, S.VALIDATED, S.SUBMITTED, S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.FILLED]
    for a, b in itertools.pairwise(path):
        assert can_transition(a, b)


def test_terminal_states_are_final() -> None:
    for status in TERMINAL_ORDER_STATUSES:
        assert TRANSITIONS[status] == frozenset()


def test_every_status_is_covered() -> None:
    assert set(TRANSITIONS) == set(OrderStatus)


def test_cannot_skip_validation_or_resurrect() -> None:
    assert not can_transition(S.CREATED, S.SUBMITTED)
    assert not can_transition(S.VALIDATED, S.FILLED)
    with pytest.raises(InvalidTransitionError):
        assert_transition(S.FILLED, S.ACKNOWLEDGED)
    with pytest.raises(InvalidTransitionError):
        assert_transition(S.REJECTED, S.SUBMITTED)


def test_unknown_resolves_only_through_provider_states() -> None:
    assert can_transition(S.SUBMITTED, S.UNKNOWN)
    for target in (S.ACKNOWLEDGED, S.FILLED, S.REJECTED, S.CANCELED, S.EXPIRED):
        assert can_transition(S.UNKNOWN, target)
    assert not can_transition(S.UNKNOWN, S.CREATED)
