"""Máquina de estados de órdenes (sección 12).

Camino normal: CREATED → VALIDATED → SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED.
Un exchange puede saltarse pasos (una orden a mercado suele pasar de SUBMITTED a FILLED),
así que desde SUBMITTED se admite cualquier estado posterior.

``UNKNOWN`` significa «no sé si el proveedor la aceptó» (timeout, 5XX). Nunca se asume
que no se ejecutó: solo se sale de él consultando al proveedor por ``client_order_id``.
"""

from __future__ import annotations

from app.core.domain import TERMINAL_ORDER_STATUSES, OrderStatus

S = OrderStatus

_POST_SUBMIT = frozenset(
    {S.ACKNOWLEDGED, S.PARTIALLY_FILLED, S.FILLED, S.REJECTED, S.CANCELED, S.EXPIRED}
)

TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    S.CREATED: frozenset({S.VALIDATED, S.REJECTED}),
    S.VALIDATED: frozenset({S.SUBMITTED, S.REJECTED}),
    S.SUBMITTED: _POST_SUBMIT | {S.UNKNOWN},
    S.ACKNOWLEDGED: frozenset(
        {S.PARTIALLY_FILLED, S.FILLED, S.CANCELED, S.EXPIRED, S.REJECTED, S.UNKNOWN}
    ),
    S.PARTIALLY_FILLED: frozenset({S.PARTIALLY_FILLED, S.FILLED, S.CANCELED, S.EXPIRED, S.UNKNOWN}),
    S.UNKNOWN: _POST_SUBMIT | {S.UNKNOWN},
    S.FILLED: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELED: frozenset(),
    S.EXPIRED: frozenset(),
}


class InvalidTransitionError(RuntimeError):
    pass


def can_transition(current: OrderStatus, new: OrderStatus) -> bool:
    return new in TRANSITIONS[current]


def assert_transition(current: OrderStatus, new: OrderStatus) -> None:
    if not can_transition(current, new):
        raise InvalidTransitionError(f"Transición de orden no permitida: {current} → {new}")


def is_terminal(status: OrderStatus) -> bool:
    return status in TERMINAL_ORDER_STATUSES
