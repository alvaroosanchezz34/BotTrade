"""Contratos de dominio compartidos por todos los módulos.

Son dataclasses inmutables: cruzan fronteras entre módulos (datos → estrategia →
riesgo → ejecución) y no deben mutarse por el camino.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

# --------------------------------------------------------------------------- enums


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class SignalAction(StrEnum):
    """Solo spot y solo largos en v1: no existe ``ENTER_SHORT``."""

    ENTER_LONG = "ENTER_LONG"
    EXIT_LONG = "EXIT_LONG"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"  # p. ej. timeout o 5XX: puede haberse ejecutado


TERMINAL_ORDER_STATUSES = frozenset(
    {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELED, OrderStatus.EXPIRED}
)


class DataQuality(StrEnum):
    VALID = "VALID"
    OPEN = "OPEN"  # vela todavía no cerrada
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    INVALID = "INVALID"  # OHLC incoherente, volumen negativo, timestamps mal alineados


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# --------------------------------------------------------------------------- utils


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


INTERVAL_SECONDS: dict[str, int] = {"1m": 60, "5m": 300}


def interval_delta(interval: str) -> timedelta:
    try:
        return timedelta(seconds=INTERVAL_SECONDS[interval])
    except KeyError as exc:
        raise ValueError(f"Intervalo no soportado: {interval}") from exc


# --------------------------------------------------------------------------- datos


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    interval: str
    open_time: datetime  # UTC, identifica la vela
    close_time: datetime  # UTC, último ms de la vela (convención Binance)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trades: int
    is_closed: bool
    source: str  # "binance-rest", "binance-ws", "binance-archive", "test"
    received_at: datetime = field(default_factory=utcnow)

    @property
    def key(self) -> tuple[str, str, datetime]:
        return (self.symbol, self.interval, self.open_time)


@dataclass(frozen=True, slots=True)
class Quote:
    """Mejor bid/ask. Puede no estar disponible (p. ej. en backtest)."""

    symbol: str
    bid: Decimal
    ask: Decimal
    ts: datetime
    source: str

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def mid(self) -> Decimal:
        return (self.ask + self.bid) / 2


@dataclass(frozen=True, slots=True)
class InstrumentRules:
    """Filtros de exchangeInfo relevantes para validar órdenes."""

    symbol: str
    base_asset: str
    quote_asset: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal
    status: str = "TRADING"


# --------------------------------------------------------------------------- señales


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    strategy_id: str
    strategy_version: str
    action: SignalAction
    created_at: datetime  # close_time de la última vela usada: nada posterior
    reference_price: Decimal
    rationale: str
    entry_conditions: dict[str, Any]
    invalidation: str
    stop_price: Decimal | None
    target_price: Decimal | None
    params: dict[str, Any]
    valid_until: datetime
    # Puntuación heurística. NO es una probabilidad salvo que esté calibrada.
    score: float | None = None
    score_is_calibrated: bool = False
    id: uuid.UUID = field(default_factory=new_id)


# --------------------------------------------------------------------------- riesgo


@dataclass(frozen=True, slots=True)
class RiskCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RiskDecision:
    signal_id: uuid.UUID
    approved: bool
    checks: tuple[RiskCheck, ...]
    quantity: Decimal = Decimal(0)
    notional: Decimal = Decimal(0)
    risk_amount: Decimal = Decimal(0)
    estimated_costs: Decimal = Decimal(0)
    decided_at: datetime = field(default_factory=utcnow)
    id: uuid.UUID = field(default_factory=new_id)

    @property
    def reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]
