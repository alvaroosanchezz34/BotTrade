"""Utilidades compartidas por las rutas de lectura.

Reglas de honestidad de datos (sección 18): ninguna cifra se inventa. Si un dato no
existe (engine nunca arrancado, sin velas, sin cuenta), la respuesta lleva ``null`` y el
frontend lo muestra como «no disponible». Las valoraciones llevan su origen y si están
obsoletas.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.domain import InstrumentRules, utcnow
from app.core.money import ZERO
from app.execution.accounting import AccountSnapshot, account_snapshot
from app.market_data.store import load_instruments
from app.persistence import models as m
from app.risk.limits import RiskLimits, RiskState

T = TypeVar("T")

ENGINE_MAX_BEAT_AGE = timedelta(seconds=30)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class PageParams(BaseModel):
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0, le=1_000_000)


def page_params(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


async def paginate(db: AsyncSession, stmt: Any, params: PageParams) -> tuple[list[Any], int]:
    total = int(
        (
            await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
        ).scalar_one()
    )
    rows = (await db.execute(stmt.limit(params.limit).offset(params.offset))).all()
    return list(rows), total


async def last_marks(
    db: AsyncSession, symbols: list[str], interval: str = "1m"
) -> dict[str, tuple[Decimal, Any]]:
    """Último cierre persistido por símbolo: (precio, close_time)."""
    rows = (
        await db.execute(
            select(m.CandleRow.symbol, m.CandleRow.close, m.CandleRow.close_time)
            .where(m.CandleRow.interval == interval, m.CandleRow.symbol.in_(symbols))
            .ext(distinct_on(m.CandleRow.symbol))
            .order_by(m.CandleRow.symbol, m.CandleRow.open_time.desc())
        )
    ).all()
    return {r.symbol: (r.close, r.close_time) for r in rows}


async def load_account(
    db: AsyncSession, settings: Settings
) -> tuple[AccountSnapshot | None, bool, dict[str, InstrumentRules]]:
    """Foto de la cuenta del modo activo. ``None`` si el engine nunca la inicializó.

    Devuelve también si las marcas de precio están obsoletas.
    """
    mode = settings.trading_mode.value
    row = await db.get(m.SystemSetting, f"account:{mode}")
    if row is None:
        return None, False, {}
    rules = await load_instruments(db)
    marks_full = await last_marks(db, settings.symbols)
    marks = {s: p for s, (p, _) in marks_full.items()}
    stale_after = timedelta(seconds=settings.stale_data_seconds) + timedelta(minutes=1)
    now = utcnow()
    stale = any(now - ct > stale_after for _, ct in marks_full.values()) or not marks_full
    snap = await account_snapshot(
        db,
        mode,
        initial_capital=Decimal(str(row.value["initial_capital"])),
        marks=marks,
        rules=rules or None,
    )
    return snap, stale, rules


async def effective_limits(db: AsyncSession, settings: Settings) -> RiskLimits:
    row = await db.get(m.RiskLimitsRow, "default")
    if row is not None:
        return RiskLimits.model_validate(row.values)
    acct = await db.get(m.SystemSetting, f"account:{settings.trading_mode.value}")
    capital = Decimal(str(acct.value["initial_capital"])) if acct else settings.reference_capital
    return RiskLimits(reference_capital=capital)


def risk_summary(
    limits: RiskLimits, state: RiskState | None, snap: AccountSnapshot | None
) -> dict[str, Any]:
    """Uso de cada límite. Sin estado o sin cuenta no se calcula nada."""
    out: dict[str, Any] = {
        "limits": {
            "risk_per_trade": limits.risk_per_trade,
            "max_daily_loss": limits.max_daily_loss,
            "max_drawdown": limits.max_drawdown,
            "max_positions": limits.max_positions,
            "max_exposure": limits.max_exposure,
            "max_trades_per_day": limits.max_trades_per_day,
            "max_consecutive_losses": limits.max_consecutive_losses,
            "max_spread_bps": limits.max_spread_bps,
            "max_execution_errors": limits.max_execution_errors,
            "long_only_spot": True,
            "leverage": 1,
        },
        "state": None,
        "usage": None,
    }
    if state is None:
        return out
    out["state"] = {
        "trading_day": state.trading_day,
        "paused": state.paused,
        "kill_switch_active": state.kill_switch_active,
        "latched_reasons": state.latched_reasons,
        "consecutive_losses": state.consecutive_losses,
        "entries_today": state.entries_today,
        "execution_errors": state.execution_errors,
        "cooldown_until_day": state.cooldown_until_day,
        "entries_allowed": not (
            state.paused
            or state.kill_switch_active
            or state.latched_reasons
            or state.cooldown_until_day
        ),
    }
    if snap is not None:
        equity = snap.equity
        out["usage"] = {
            "daily_loss": state.daily_loss(equity),
            "drawdown": state.drawdown(equity),
            "peak_equity": state.peak_equity,
            "day_start_equity": state.day_start_equity,
            "open_positions": len(snap.portfolio.positions),
            "exposure": snap.portfolio.exposure,
        }
    return out


def max_drawdown_from_equity(values: list[Decimal]) -> Decimal:
    peak, worst = None, ZERO
    for v in values:
        peak = v if peak is None or v > peak else peak
        worst = max(worst, peak - v)
    return worst
