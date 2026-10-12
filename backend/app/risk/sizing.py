"""Modelo de costes y dimensionamiento de posición (Decimal en todo).

El tamaño sale del riesgo, nunca al revés: si la orden mínima del exchange
implica arriesgar más de lo autorizado, la operación se rechaza (sección 11).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from app.core.domain import InstrumentRules
from app.core.money import ZERO, bps, floor_to_step


@dataclass(frozen=True)
class CostModel:
    """Costes supuestos para simular. Fracciones, no porcentajes (0.001 = 0,1 %)."""

    taker_fee: Decimal = Decimal("0.001")
    maker_fee: Decimal = Decimal("0.001")
    slippage: Decimal = bps(Decimal(2))  # noqa: RUF009
    # Spread completo supuesto cuando no hay bid/ask (velas históricas). Se paga la mitad por lado.
    assumed_spread: Decimal = bps(Decimal(1))  # noqa: RUF009

    @property
    def adverse_per_side(self) -> Decimal:
        return self.assumed_spread / 2 + self.slippage

    def buy_fill(self, reference: Decimal) -> Decimal:
        return reference * (1 + self.adverse_per_side)

    def sell_fill(self, reference: Decimal) -> Decimal:
        return reference * (1 - self.adverse_per_side)

    @property
    def round_trip_fraction(self) -> Decimal:
        return 2 * self.taker_fee + 2 * self.adverse_per_side

    def as_dict(self) -> dict[str, str]:
        return {
            "taker_fee": str(self.taker_fee),
            "maker_fee": str(self.maker_fee),
            "slippage": str(self.slippage),
            "assumed_spread": str(self.assumed_spread),
        }


@dataclass(frozen=True)
class SizingResult:
    ok: bool
    reason: str
    quantity: Decimal = ZERO
    notional: Decimal = ZERO
    risk_amount: Decimal = ZERO  # pérdida estimada si salta el stop (con costes)
    estimated_costs: Decimal = ZERO  # comisiones ida y vuelta estimadas
    binding: str = ""  # qué restricción limitó el tamaño


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def position_size(
    *,
    entry_price: Decimal,
    stop_price: Decimal,
    risk_budget: Decimal,
    cash: Decimal,
    exposure_room: Decimal,
    rules: InstrumentRules,
    costs: CostModel,
) -> SizingResult:
    """Cantidad máxima que respeta riesgo, exposición, caja y filtros del instrumento.

    ``entry_price`` es el precio de ejecución esperado (ya con spread/deslizamiento).
    """
    if entry_price <= 0 or stop_price <= 0:
        return SizingResult(False, "precio de entrada o stop no válido")
    if stop_price >= entry_price:
        return SizingResult(False, "el stop debe estar por debajo de la entrada (solo largos)")
    if risk_budget <= 0:
        return SizingResult(False, "sin presupuesto de riesgo")

    stop_fill = costs.sell_fill(stop_price)
    fee = costs.taker_fee
    per_unit_loss = (entry_price - stop_fill) + entry_price * fee + stop_fill * fee
    per_unit_cash = entry_price * (1 + fee)

    candidates = {
        "riesgo": risk_budget / per_unit_loss,
        "exposición": max(ZERO, exposure_room) / entry_price,
        "caja": max(ZERO, cash) / per_unit_cash,
        "cantidad máxima": rules.max_qty,
    }
    binding, raw_qty = min(candidates.items(), key=lambda kv: kv[1])
    qty = floor_to_step(raw_qty, rules.step_size)

    min_qty_notional = _ceil_to_step(rules.min_notional / entry_price, rules.step_size)
    min_qty = max(rules.min_qty, min_qty_notional)
    if qty < min_qty:
        min_risk = min_qty * per_unit_loss
        if binding == "riesgo" or min_risk > risk_budget:
            return SizingResult(
                False,
                f"la orden mínima ({min_qty} → riesgo {min_risk:.4f}) supera el riesgo "
                f"autorizado ({risk_budget:.4f})",
                binding="riesgo",
            )
        return SizingResult(
            False, f"tamaño limitado por {binding} por debajo del mínimo", binding=binding
        )

    notional = qty * entry_price
    return SizingResult(
        True,
        "ok",
        quantity=qty,
        notional=notional,
        risk_amount=qty * per_unit_loss,
        estimated_costs=notional * fee + qty * stop_fill * fee,
        binding=binding,
    )
