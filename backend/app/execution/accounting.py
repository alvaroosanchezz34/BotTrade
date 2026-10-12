"""Contabilidad de la cuenta simulada a partir de las ejecuciones persistidas (Decimal).

``cash`` y ``equity`` se *derivan* de los fills guardados, no se mantienen como contador
aparte: así un reinicio nunca puede dejarlos desincronizados del registro de operaciones.

Valoración (documentada en docs/risk-policy.md): las posiciones abiertas se valoran a
precio de mercado (último cierre) **sin** descontar la comisión de salida futura; el P&L
no realizado es bruto de esa comisión. La pérdida diaria usa ``equity`` (realizado +
no realizado − comisiones ya pagadas).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain import InstrumentRules
from app.core.money import ZERO
from app.execution.fees import fee_in_quote
from app.persistence import models as m
from app.risk.engine import OpenPositionView, PortfolioView


@dataclass(frozen=True)
class AccountSnapshot:
    initial_capital: Decimal
    cash: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    fees_total: Decimal
    portfolio: PortfolioView


async def get_initial_capital(session: AsyncSession, mode: str, default: Decimal) -> Decimal:
    """Capital inicial de la cuenta del modo, fijado la primera vez y nunca recalculado."""
    key = f"account:{mode}"
    row = await session.get(m.SystemSetting, key)
    if row is None:
        session.add(
            m.SystemSetting(key=key, value={"initial_capital": str(default)}, updated_by="engine")
        )
        await session.flush()
        return default
    return Decimal(str(row.value["initial_capital"]))


async def account_snapshot(
    session: AsyncSession,
    mode: str,
    *,
    initial_capital: Decimal,
    marks: Mapping[str, Decimal],
    rules: Mapping[str, InstrumentRules] | None = None,
) -> AccountSnapshot:
    """``marks``: último precio por símbolo. Una posición sin marca se valora a su entrada
    (no se inventa un beneficio) y el llamador debe tratarlo como dato no disponible."""
    fills = (
        await session.execute(
            select(m.Fill, m.OrderRow.side, m.OrderRow.symbol)
            .join(m.OrderRow, m.OrderRow.id == m.Fill.order_id)
            .where(m.OrderRow.mode == mode)
        )
    ).all()
    cash = initial_capital
    fees = ZERO
    for fill, side, symbol in fills:
        gross = fill.price * fill.quantity
        cash += -gross if side == "BUY" else gross
        fee = (
            fee_in_quote(
                fee=fill.fee, fee_asset=fill.fee_asset, price=fill.price,
                quantity=fill.quantity, rules=rules[symbol],
            )
            if rules is not None and symbol in rules
            else fill.fee
        )  # fmt: skip
        cash -= fee
        fees += fee

    positions = (
        (await session.execute(select(m.Position).where(m.Position.mode == mode))).scalars().all()
    )
    realized = sum((p.realized_pnl or ZERO for p in positions), ZERO)
    open_views: list[OpenPositionView] = []
    unrealized = ZERO
    market_value = ZERO
    for p in positions:
        if p.status != "OPEN":
            continue
        mark = marks.get(p.symbol, p.avg_entry_price)
        value = p.quantity * mark
        market_value += value
        unrealized += (mark - p.avg_entry_price) * p.quantity
        at_risk = (
            max(ZERO, (p.avg_entry_price - p.stop_price) * p.quantity)
            if p.stop_price is not None
            else value
        )
        open_views.append(OpenPositionView(p.symbol, value, at_risk))
    equity = cash + market_value
    return AccountSnapshot(
        initial_capital=initial_capital,
        cash=cash,
        equity=equity,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        fees_total=fees,
        portfolio=PortfolioView(equity=equity, cash=cash, positions=tuple(open_views)),
    )
