"""Aritmética monetaria con Decimal (ADR-004).

Nunca construyas un ``Decimal`` directamente desde un ``float``: arrastra el error
binario (``Decimal(0.1)`` = 0.1000000000000000055…). Usa :func:`D`.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, getcontext

getcontext().prec = 38

ZERO = Decimal(0)
ONE = Decimal(1)


def D(value: Decimal | str | int | float) -> Decimal:
    """Convierte a Decimal pasando los floats por su representación ``repr`` corta.

    Acepta escalares de NumPy (``np.float64``, ``np.int64``...).
    """
    if isinstance(value, Decimal):
        return value
    if not isinstance(value, str | int | float) and hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"Valor numérico no finito: {value}")
        return Decimal(float.__repr__(value))
    return Decimal(value)


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Redondea hacia abajo al múltiplo de ``step`` (cantidades: nunca comprar de más)."""
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def round_to_tick(value: Decimal, tick: Decimal, *, direction: str = "nearest") -> Decimal:
    """Ajusta un precio al ``tickSize``.

    ``direction``: ``"down"`` (stops de compra largos: más conservador abajo),
    ``"up"`` o ``"nearest"``.
    """
    if tick <= 0:
        return value
    rounding = {"down": ROUND_FLOOR, "up": ROUND_CEILING, "nearest": ROUND_HALF_EVEN}[direction]
    return (value / tick).to_integral_value(rounding=rounding) * tick


def bps(value: Decimal) -> Decimal:
    """Puntos básicos a fracción: 2 bps → 0.0002."""
    return value / Decimal(10_000)
