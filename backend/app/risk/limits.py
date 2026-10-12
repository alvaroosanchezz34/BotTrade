"""Límites de riesgo y estado de riesgo (puro, sin E/S).

El mismo código lo usan el backtest (estado en memoria) y el engine en vivo
(estado persistido en ``risk_state``), para que ambos apliquen las mismas reglas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.money import ZERO


class RiskLimits(BaseModel):
    """Valores por defecto de la sección 11. Son límites de diseño, no garantías."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_capital: Decimal = Field(Decimal("500"), gt=0)
    risk_per_trade_pct: Decimal = Field(Decimal("0.25"), gt=0, le=Decimal("2"))
    max_daily_loss_pct: Decimal = Field(Decimal("1"), gt=0, le=Decimal("10"))
    max_drawdown_pct: Decimal = Field(Decimal("5"), gt=0, le=Decimal("30"))
    max_positions: int = Field(2, ge=1, le=10)
    max_exposure_pct: Decimal = Field(Decimal("50"), gt=0, le=Decimal("100"))
    max_asset_exposure_pct: Decimal = Field(Decimal("50"), gt=0, le=Decimal("100"))
    max_trades_per_day: int = Field(20, ge=1, le=500)
    max_consecutive_losses: int = Field(5, ge=1, le=100)
    # Posiciones con correlación > umbral comparten presupuesto de riesgo.
    correlation_threshold: float = Field(0.7, ge=0, le=1)
    max_correlated_risk_multiple: Decimal = Field(Decimal("1.5"), ge=1, le=Decimal("10"))
    max_spread_bps: Decimal = Field(Decimal("10"), gt=0, le=Decimal("500"))
    max_execution_errors: int = Field(3, ge=1, le=100)

    @property
    def risk_per_trade(self) -> Decimal:
        return self.reference_capital * self.risk_per_trade_pct / 100

    @property
    def max_daily_loss(self) -> Decimal:
        return self.reference_capital * self.max_daily_loss_pct / 100

    @property
    def max_drawdown(self) -> Decimal:
        return self.reference_capital * self.max_drawdown_pct / 100

    @property
    def max_exposure(self) -> Decimal:
        return self.reference_capital * self.max_exposure_pct / 100

    @property
    def max_asset_exposure(self) -> Decimal:
        return self.reference_capital * self.max_asset_exposure_pct / 100


@dataclass
class RiskState:
    """Estado mutable de riesgo. ``latched`` solo se borra con una acción manual auditada."""

    trading_day: date
    day_start_equity: Decimal
    peak_equity: Decimal
    consecutive_losses: int = 0
    entries_today: int = 0
    paused: bool = False
    kill_switch_active: bool = False
    latched_reasons: list[str] = field(default_factory=list)
    execution_errors: int = 0
    cooldown_until_day: date | None = None  # bloqueo por pérdidas consecutivas

    @classmethod
    def fresh(cls, now: datetime, equity: Decimal) -> RiskState:
        return cls(trading_day=now.date(), day_start_equity=equity, peak_equity=equity)

    def roll(self, now: datetime, equity: Decimal) -> None:
        """Cambio de día UTC: reinicia contadores diarios, nunca los bloqueos latched."""
        if now.date() != self.trading_day:
            self.trading_day = now.date()
            self.day_start_equity = equity
            self.entries_today = 0
            if self.cooldown_until_day is not None and self.trading_day > self.cooldown_until_day:
                self.cooldown_until_day = None
                self.consecutive_losses = 0
        if equity > self.peak_equity:
            self.peak_equity = equity

    def daily_loss(self, equity: Decimal) -> Decimal:
        return max(ZERO, self.day_start_equity - equity)

    def drawdown(self, equity: Decimal) -> Decimal:
        return max(ZERO, self.peak_equity - equity)

    def on_entry(self) -> None:
        self.entries_today += 1

    def on_trade_closed(self, net_pnl: Decimal, limits: RiskLimits) -> None:
        if net_pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= limits.max_consecutive_losses:
                self.cooldown_until_day = self.trading_day
        else:
            self.consecutive_losses = 0

    def latch(self, reason: str) -> None:
        if reason not in self.latched_reasons:
            self.latched_reasons.append(reason)
