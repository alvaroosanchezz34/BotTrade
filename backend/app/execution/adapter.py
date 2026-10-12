"""Contrato de los adaptadores de ejecución.

El motor de estrategias no conoce este módulo: solo el ``OrderManager`` habla con un
``ExecutionAdapter``. Hay dos implementaciones: ``SimulatedExecution`` (BACKTEST/PAPER) y
el adaptador de testnet (TESTNET, Fase 5). No existe adaptador de producción (ADR-006).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.core.domain import OrderStatus, OrderType, Side
from app.core.money import ZERO


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: Side
    type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FillReport:
    trade_id: str  # único por orden: garantiza que un fill se procese una sola vez
    price: Decimal
    quantity: Decimal
    fee: Decimal
    fee_asset: str
    filled_at: datetime
    is_maker: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Estado *acumulado* de la orden según el proveedor (incluye todos sus fills)."""

    client_order_id: str
    exchange_order_id: str | None
    status: OrderStatus
    filled_qty: Decimal = ZERO
    fills: tuple[FillReport, ...] = field(default_factory=tuple)
    reject_reason: str | None = None


class OrderRejectedError(Exception):
    """El proveedor rechazó la orden de forma definitiva y explícita (no es ambiguo)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class AmbiguousResponseError(Exception):
    """Timeout, 5XX o corte de conexión: la orden pudo o no llegar al proveedor."""


class ExecutionAdapter(Protocol):
    name: str

    async def submit(self, request: OrderRequest) -> ExecutionReport: ...

    async def get_order(self, symbol: str, client_order_id: str) -> ExecutionReport | None:
        """``None`` solo si el proveedor confirma que no conoce esa orden."""
        ...

    async def cancel(self, symbol: str, client_order_id: str) -> ExecutionReport: ...

    async def open_orders(self) -> list[ExecutionReport]: ...

    async def balances(self) -> dict[str, Decimal]: ...
