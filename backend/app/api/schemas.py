"""Contratos de respuesta de la API (los importes viajan como cadenas Decimal)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class SignalOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    symbol: str
    strategy_id: str
    strategy_version: str
    action: str
    status: str
    reference_price: Decimal
    stop_price: Decimal | None
    target_price: Decimal | None
    score: float | None
    score_is_calibrated: bool
    rationale: str
    invalidation: str
    entry_conditions: dict[str, Any]
    params: dict[str, Any]
    risk_approved: bool | None = None
    risk_reasons: list[str] = []
    risk_quantity: Decimal | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    client_order_id: str
    created_at: datetime
    mode: str
    provider: str
    symbol: str
    side: str
    type: str
    purpose: str
    status: str
    quantity: Decimal
    limit_price: Decimal | None
    filled_qty: Decimal
    avg_fill_price: Decimal | None
    fees: Decimal
    strategy_id: str | None
    strategy_version: str | None
    signal_id: uuid.UUID | None
    risk_decision_id: uuid.UUID | None
    position_id: uuid.UUID | None
    last_error: str | None
    submitted_at: datetime | None
    updated_at: datetime


class PositionOut(BaseModel):
    id: uuid.UUID
    symbol: str
    status: str
    strategy_id: str
    quantity: Decimal
    entry_quantity: Decimal
    avg_entry_price: Decimal
    stop_price: Decimal | None
    target_price: Decimal | None
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    realized_pnl: Decimal | None
    entry_fees: Decimal
    exit_fees: Decimal
    entry_reason: str
    opened_at: datetime
    signal_id: uuid.UUID | None


class TradeOut(BaseModel):
    id: uuid.UUID
    symbol: str
    strategy_id: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal | None
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    duration_seconds: float
    opened_at: datetime
    closed_at: datetime
    entry_reason: str
    exit_reason: str | None
    signal_id: uuid.UUID | None


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    severity: str
    code: str
    message: str
    context: dict[str, Any]
    recommended_action: str
    occurrences: int
    created_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    resolved_at: datetime | None


class CommandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    created_by: str
    command: str
    payload: dict[str, Any]
    status: str
    result: str | None
    processed_at: datetime | None


class CommandAccepted(BaseModel):
    """Una acción *solicitada* (no confirmada): la UI debe esperar a ``APPLIED``."""

    command_id: uuid.UUID
    status: str
    engine_alive: bool
    duplicate_of_pending: bool = False
    detail: str
