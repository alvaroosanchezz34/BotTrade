"""Modelo de datos persistente.

Convenciones:
* Importes y precios: ``NUMERIC(38,18)`` → ``Decimal`` (ADR-004).
* Timestamps: ``timestamptz`` siempre en UTC.
* Cada orden enlaza señal → decisión de riesgo → eventos → fills.
* ``mode`` separa BACKTEST/PAPER/TESTNET en las tablas operativas.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

MONEY = Numeric(38, 18)
TS = DateTime(timezone=True)
MODES = "('BACKTEST','PAPER','TESTNET')"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {  # noqa: RUF012
        Decimal: MONEY,
        datetime: TS,
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        uuid.UUID: UUID(as_uuid=True),
    }


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(server_default=func.now(), nullable=False)


# ============================================================== usuarios y sesiones


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="admin")
    is_active: Mapped[bool] = mapped_column(default=True)
    failed_logins: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _created()
    __table_args__ = (CheckConstraint("role IN ('admin','viewer')", name="role"),)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(64))


# ============================================================== mercado


class Instrument(Base):
    __tablename__ = "instruments"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    base_asset: Mapped[str] = mapped_column(String(16))
    quote_asset: Mapped[str] = mapped_column(String(16))
    tick_size: Mapped[Decimal]
    step_size: Mapped[Decimal]
    min_qty: Mapped[Decimal]
    max_qty: Mapped[Decimal]
    min_notional: Mapped[Decimal]
    status: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class CandleRow(Base):
    __tablename__ = "candles"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    interval: Mapped[str] = mapped_column(String(4), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(primary_key=True)
    close_time: Mapped[datetime]
    open: Mapped[Decimal]
    high: Mapped[Decimal]
    low: Mapped[Decimal]
    close: Mapped[Decimal]
    volume: Mapped[Decimal]
    quote_volume: Mapped[Decimal]
    trades: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32))
    quality: Mapped[str] = mapped_column(String(16), default="VALID")
    ingested_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint("high >= low AND high >= open AND high >= close", name="ohlc_high"),
        CheckConstraint("low <= open AND low <= close", name="ohlc_low"),
        CheckConstraint("volume >= 0", name="volume"),
    )


class DataGap(Base):
    __tablename__ = "data_gaps"
    id: Mapped[uuid.UUID] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(String(32))
    interval: Mapped[str] = mapped_column(String(4))
    gap_start: Mapped[datetime]  # open_time de la primera vela que falta
    gap_end: Mapped[datetime]  # open_time de la última vela que falta
    detected_at: Mapped[datetime] = _created()
    resolved_at: Mapped[datetime | None]
    resolution: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("symbol", "interval", "gap_start", name="uq_data_gaps_key"),)


class ConnectionStatus(Base):
    __tablename__ = "connection_status"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))  # UP / DEGRADED / DOWN
    last_message_at: Mapped[datetime | None]
    last_valid_data_at: Mapped[datetime | None]
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


# ============================================================== estrategias y señales


class Strategy(Base):
    __tablename__ = "strategies"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(default=False)
    symbols: Mapped[list[Any]] = mapped_column(default=list)
    interval: Mapped[str] = mapped_column(String(4), default="1m")
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    version: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"))
    version: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]]
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_key"),)


class SignalRow(Base):
    __tablename__ = "signals"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    strategy_id: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime]
    valid_until: Mapped[datetime]
    reference_price: Mapped[Decimal]
    stop_price: Mapped[Decimal | None]
    target_price: Mapped[Decimal | None]
    score: Mapped[float | None]
    score_is_calibrated: Mapped[bool] = mapped_column(default=False)
    rationale: Mapped[str] = mapped_column(Text)
    entry_conditions: Mapped[dict[str, Any]]
    invalidation: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(String(16), default="NEW")
    __table_args__ = (
        CheckConstraint(f"mode IN {MODES}", name="mode"),
        Index("ix_signals_recent", "created_at"),
        Index("ix_signals_strategy", "strategy_id", "created_at"),
    )


class RiskDecisionRow(Base):
    __tablename__ = "risk_decisions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    signal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("signals.id"))
    mode: Mapped[str] = mapped_column(String(16))
    approved: Mapped[bool]
    checks: Mapped[list[Any]]
    quantity: Mapped[Decimal]
    notional: Mapped[Decimal]
    risk_amount: Mapped[Decimal]
    estimated_costs: Mapped[Decimal]
    decided_at: Mapped[datetime]


# ============================================================== órdenes y posiciones


class OrderRow(Base):
    __tablename__ = "orders"
    id: Mapped[uuid.UUID] = _uuid_pk()
    client_order_id: Mapped[str] = mapped_column(String(36), unique=True)
    mode: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    signal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("signals.id"))
    risk_decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("risk_decisions.id"))
    position_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("positions.id"))
    strategy_id: Mapped[str | None] = mapped_column(String(64))
    strategy_version: Mapped[str | None] = mapped_column(String(32))
    purpose: Mapped[str] = mapped_column(String(16))  # ENTRY / EXIT_STOP / EXIT_TARGET / ...
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    type: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[Decimal]
    limit_price: Mapped[Decimal | None]
    status: Mapped[str] = mapped_column(String(20))
    exchange_order_id: Mapped[str | None] = mapped_column(String(64))
    filled_qty: Mapped[Decimal] = mapped_column(default=Decimal(0))
    avg_fill_price: Mapped[Decimal | None]
    fees: Mapped[Decimal] = mapped_column(default=Decimal(0))
    fee_asset: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    submitted_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint(f"mode IN {MODES}", name="mode"),
        CheckConstraint("quantity > 0", name="qty_positive"),
        CheckConstraint("side IN ('BUY','SELL')", name="side"),
        Index("ix_orders_open", "mode", "status"),
    )


class OrderEvent(Base):
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    at: Mapped[datetime] = _created()
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Fill(Base):
    __tablename__ = "fills"
    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    exchange_trade_id: Mapped[str] = mapped_column(String(64))
    price: Mapped[Decimal]
    quantity: Mapped[Decimal]
    fee: Mapped[Decimal]
    fee_asset: Mapped[str] = mapped_column(String(16))
    is_maker: Mapped[bool] = mapped_column(default=False)
    filled_at: Mapped[datetime]
    __table_args__ = (UniqueConstraint("order_id", "exchange_trade_id", name="uq_fills_trade"),)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    mode: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    strategy_id: Mapped[str] = mapped_column(String(64))
    signal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("signals.id"))
    status: Mapped[str] = mapped_column(String(8))  # OPEN / CLOSED
    quantity: Mapped[Decimal]
    avg_entry_price: Mapped[Decimal]
    stop_price: Mapped[Decimal | None]
    target_price: Mapped[Decimal | None]
    entry_fees: Mapped[Decimal] = mapped_column(default=Decimal(0))
    exit_fees: Mapped[Decimal] = mapped_column(default=Decimal(0))
    exit_price: Mapped[Decimal | None]
    realized_pnl: Mapped[Decimal | None]
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    entry_reason: Mapped[str] = mapped_column(Text, default="")
    opened_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint(f"mode IN {MODES}", name="mode"),
        CheckConstraint("status IN ('OPEN','CLOSED')", name="status"),
        Index("ix_positions_open", "mode", "status"),
    )


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(16))
    at: Mapped[datetime]
    cash: Mapped[Decimal]
    equity: Mapped[Decimal]
    realized_pnl: Mapped[Decimal]
    unrealized_pnl: Mapped[Decimal]
    fees_total: Mapped[Decimal]
    __table_args__ = (Index("ix_balance_snapshots_mode_at", "mode", "at"),)


# ============================================================== riesgo


class RiskState(Base):
    """Estado de riesgo persistido. Sobrevive a reinicios (sección 11)."""

    __tablename__ = "risk_state"
    mode: Mapped[str] = mapped_column(String(16), primary_key=True)
    trading_day: Mapped[date] = mapped_column(Date)
    day_start_equity: Mapped[Decimal]
    peak_equity: Mapped[Decimal]
    consecutive_losses: Mapped[int] = mapped_column(default=0)
    entries_today: Mapped[int] = mapped_column(default=0)
    paused: Mapped[bool] = mapped_column(default=False)
    kill_switch_active: Mapped[bool] = mapped_column(default=False)
    entries_blocked: Mapped[bool] = mapped_column(default=False)
    block_reasons: Mapped[list[Any]] = mapped_column(default=list)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    version: Mapped[int] = mapped_column(default=0)
    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class RiskLimitsRow(Base):
    __tablename__ = "risk_limits"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # "default"
    values: Mapped[dict[str, Any]]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(64))


# ============================================================== backtests


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    created_at: Mapped[datetime] = _created()
    strategy_id: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(32))
    symbols: Mapped[list[Any]]
    interval: Mapped[str] = mapped_column(String(4))
    start: Mapped[datetime]
    end: Mapped[datetime]
    segment: Mapped[str] = mapped_column(String(16))  # train / validation / test / wf-N / full
    params: Mapped[dict[str, Any]]
    cost_model: Mapped[dict[str, Any]]
    data_hash: Mapped[str] = mapped_column(String(64))
    seed: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16))
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("backtest_runs.id"))
    notes: Mapped[str] = mapped_column(Text, default="")


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(32))
    entry_time: Mapped[datetime]
    exit_time: Mapped[datetime | None]
    entry_price: Mapped[Decimal]
    exit_price: Mapped[Decimal | None]
    quantity: Mapped[Decimal]
    fees: Mapped[Decimal]
    gross_pnl: Mapped[Decimal | None]
    net_pnl: Mapped[Decimal | None]
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (Index("ix_backtest_trades_run", "run_id"),)


# ============================================================== operación y auditoría


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    severity: Mapped[str] = mapped_column(String(10))
    code: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(default=dict)
    recommended_action: Mapped[str] = mapped_column(Text, default="")
    dedup_key: Mapped[str] = mapped_column(String(128))
    occurrences: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = _created()
    acknowledged_at: Mapped[datetime | None]
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    resolved_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("severity IN ('INFO','WARNING','CRITICAL')", name="severity"),
        # Solo una alerta activa (no resuelta) por clave de deduplicación.
        Index(
            "uq_alerts_active_dedup",
            "dedup_key",
            unique=True,
            postgresql_where="resolved_at IS NULL",
        ),
    )


class SystemEvent(Base):
    __tablename__ = "system_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = _created()
    kind: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    __table_args__ = (Index("ix_system_events_at", "at"),)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = _created()
    actor: Mapped[str] = mapped_column(String(64))  # username, "engine" o "system"
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), default="")
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (Index("ix_audit_log_at", "at"),)


class BotCommand(Base):
    """Comando del panel al engine. La UI muestra PENDING hasta APPLIED/REJECTED."""

    __tablename__ = "bot_commands"
    id: Mapped[uuid.UUID] = _uuid_pk()
    created_at: Mapped[datetime] = _created()
    created_by: Mapped[str] = mapped_column(String(64))
    command: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    result: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint(
            "command IN ('PAUSE','RESUME','KILL_SWITCH','ENABLE_STRATEGY','DISABLE_STRATEGY')",
            name="command",
        ),
        CheckConstraint("status IN ('PENDING','APPLIED','REJECTED')", name="status"),
        Index("ix_bot_commands_pending", "status", "created_at"),
    )


class EngineHeartbeat(Base):
    __tablename__ = "engine_heartbeat"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # "engine"
    mode: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime]
    last_beat_at: Mapped[datetime]
    pid: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict[str, Any]] = mapped_column(default=dict)
    __table_args__ = (CheckConstraint(f"mode IN {MODES}", name="mode"),)
