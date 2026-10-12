"""Gestión de salidas de posiciones abiertas (stop, objetivo, tiempo).

Los stops son *sintéticos*: los vigila el engine y vende a mercado al detectarlos (no hay
orden stop resting en el exchange). Consecuencia documentada: entre el cruce del stop y la
ejecución hay latencia + deslizamiento, y en un corte del engine el stop no actúa.
Supuestos conservadores:

* Si en la misma vela se tocan stop y objetivo, **gana el stop**.
* El objetivo solo cuenta si el máximo lo *supera*.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal


def exit_purpose_for_bar(
    *, stop: Decimal | None, target: Decimal | None, low: Decimal, high: Decimal
) -> str | None:
    if stop is not None and low <= stop:
        return "EXIT_STOP"
    if target is not None and high > target:
        return "EXIT_TARGET"
    return None


def exit_purpose_for_price(
    *, stop: Decimal | None, target: Decimal | None, bid: Decimal
) -> str | None:
    """Con cotización en vivo se compara el *bid* (lo que se cobraría al vender)."""
    return exit_purpose_for_bar(stop=stop, target=target, low=bid, high=bid)


def time_exit_due(*, opened_at: datetime, now: datetime, max_hold: timedelta) -> bool:
    return now - opened_at >= max_hold
