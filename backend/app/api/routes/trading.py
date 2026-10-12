"""Lectura de señales, órdenes, posiciones, operaciones cerradas y rendimiento."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.api.common import (
    Page,
    PageParams,
    last_marks,
    max_drawdown_from_equity,
    page_params,
    paginate,
)
from app.api.deps import DB, Auth, SettingsDep
from app.api.schemas import OrderOut, PositionOut, SignalOut, TradeOut
from app.core.money import ZERO
from app.persistence import models as m

router = APIRouter(prefix="/api", tags=["trading"])

Symbol = Annotated[str | None, Query(max_length=32, pattern=r"^[A-Za-z0-9]+$")]
StrategyQ = Annotated[str | None, Query(max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")]
FromQ = Annotated[datetime | None, Query(alias="from")]
ToQ = Annotated[datetime | None, Query(alias="to")]


@router.get("/signals", response_model=Page[SignalOut])
async def list_signals(
    _: Auth,
    db: DB,
    settings: SettingsDep,
    p: Annotated[PageParams, Depends(page_params)],
    symbol: Symbol = None,
    strategy: StrategyQ = None,
    status: Annotated[str | None, Query(pattern=r"^[A-Z_]{1,16}$")] = None,
    date_from: FromQ = None,
    date_to: ToQ = None,
) -> Page[SignalOut]:
    stmt = (
        select(m.SignalRow, m.RiskDecisionRow)
        .outerjoin(m.RiskDecisionRow, m.RiskDecisionRow.signal_id == m.SignalRow.id)
        .where(m.SignalRow.mode == settings.trading_mode.value)
        .order_by(m.SignalRow.created_at.desc(), m.SignalRow.id)
    )
    if symbol:
        stmt = stmt.where(m.SignalRow.symbol == symbol.upper())
    if strategy:
        stmt = stmt.where(m.SignalRow.strategy_id == strategy)
    if status:
        stmt = stmt.where(m.SignalRow.status == status)
    if date_from:
        stmt = stmt.where(m.SignalRow.created_at >= date_from)
    if date_to:
        stmt = stmt.where(m.SignalRow.created_at <= date_to)
    rows, total = await paginate(db, stmt, p)
    return Page(
        items=[signal_out(s, d) for s, d in rows], total=total, limit=p.limit, offset=p.offset
    )


def signal_out(s: m.SignalRow, d: m.RiskDecisionRow | None) -> SignalOut:
    return SignalOut(
        id=s.id, created_at=s.created_at, symbol=s.symbol, strategy_id=s.strategy_id,
        strategy_version=s.strategy_version, action=s.action, status=s.status,
        reference_price=s.reference_price, stop_price=s.stop_price, target_price=s.target_price,
        score=s.score, score_is_calibrated=s.score_is_calibrated, rationale=s.rationale,
        invalidation=s.invalidation, entry_conditions=s.entry_conditions, params=s.params,
        risk_approved=None if d is None else d.approved,
        risk_reasons=[] if d is None else [f"{c['name']}: {c['detail']}" for c in d.checks if not c["passed"]],
        risk_quantity=None if d is None else d.quantity,
    )  # fmt: skip


@router.get("/orders", response_model=Page[OrderOut])
async def list_orders(
    _: Auth,
    db: DB,
    settings: SettingsDep,
    p: Annotated[PageParams, Depends(page_params)],
    symbol: Symbol = None,
    strategy: StrategyQ = None,
    status: Annotated[str | None, Query(pattern=r"^[A-Z_]{1,20}$")] = None,
    pending: Annotated[bool, Query(description="solo órdenes no terminales")] = False,
    date_from: FromQ = None,
    date_to: ToQ = None,
) -> Page[OrderOut]:
    stmt = (
        select(m.OrderRow)
        .where(m.OrderRow.mode == settings.trading_mode.value)
        .order_by(m.OrderRow.created_at.desc(), m.OrderRow.id)
    )
    if symbol:
        stmt = stmt.where(m.OrderRow.symbol == symbol.upper())
    if strategy:
        stmt = stmt.where(m.OrderRow.strategy_id == strategy)
    if status:
        stmt = stmt.where(m.OrderRow.status == status)
    if pending:
        stmt = stmt.where(
            m.OrderRow.status.in_(("SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "UNKNOWN"))
        )
    if date_from:
        stmt = stmt.where(m.OrderRow.created_at >= date_from)
    if date_to:
        stmt = stmt.where(m.OrderRow.created_at <= date_to)
    rows, total = await paginate(db, stmt, p)
    return Page(
        items=[OrderOut.model_validate(r[0]) for r in rows],
        total=total,
        limit=p.limit,
        offset=p.offset,
    )


@router.get("/orders/{order_id}")
async def order_detail(order_id: str, _: Auth, db: DB, settings: SettingsDep) -> dict[str, Any]:
    """Orden con su cadena completa: señal → decisión de riesgo → eventos → fills."""
    try:
        import uuid

        oid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(422, "Identificador no válido") from None
    order = await db.get(m.OrderRow, oid)
    if order is None or order.mode != settings.trading_mode.value:
        raise HTTPException(404, "Orden no encontrada")
    events = (
        await db.execute(
            select(m.OrderEvent).where(m.OrderEvent.order_id == oid).order_by(m.OrderEvent.id)
        )
    ).scalars()
    fills = (
        await db.execute(select(m.Fill).where(m.Fill.order_id == oid).order_by(m.Fill.filled_at))
    ).scalars()
    signal = await db.get(m.SignalRow, order.signal_id) if order.signal_id else None
    decision = (
        await db.get(m.RiskDecisionRow, order.risk_decision_id) if order.risk_decision_id else None
    )
    return {
        "order": OrderOut.model_validate(order),
        "signal": signal_out(signal, decision) if signal else None,
        "events": [
            {"at": e.at, "from": e.from_status, "to": e.to_status, "detail": e.detail}
            for e in events
        ],
        "fills": [
            {"trade_id": f.exchange_trade_id, "price": f.price, "quantity": f.quantity,
             "fee": f.fee, "fee_asset": f.fee_asset, "is_maker": f.is_maker, "at": f.filled_at}
            for f in fills
        ],
    }  # fmt: skip


@router.get("/positions", response_model=Page[PositionOut])
async def list_positions(
    _: Auth,
    db: DB,
    settings: SettingsDep,
    p: Annotated[PageParams, Depends(page_params)],
    status: Annotated[str, Query(pattern=r"^(OPEN|CLOSED|ALL)$")] = "OPEN",
    symbol: Symbol = None,
    strategy: StrategyQ = None,
) -> Page[PositionOut]:
    stmt = (
        select(m.Position)
        .where(m.Position.mode == settings.trading_mode.value)
        .order_by(m.Position.opened_at.desc(), m.Position.id)
    )
    if status != "ALL":
        stmt = stmt.where(m.Position.status == status)
    if symbol:
        stmt = stmt.where(m.Position.symbol == symbol.upper())
    if strategy:
        stmt = stmt.where(m.Position.strategy_id == strategy)
    rows, total = await paginate(db, stmt, p)
    positions = [r[0] for r in rows]
    marks = await last_marks(db, sorted({x.symbol for x in positions})) if positions else {}
    items = []
    for x in positions:
        mark = marks.get(x.symbol, (None, None))[0] if x.status == "OPEN" else None
        items.append(
            PositionOut(
                id=x.id, symbol=x.symbol, status=x.status, strategy_id=x.strategy_id,
                quantity=x.quantity, entry_quantity=x.entry_quantity,
                avg_entry_price=x.avg_entry_price, stop_price=x.stop_price,
                target_price=x.target_price, mark_price=mark,
                unrealized_pnl=None if mark is None else (mark - x.avg_entry_price) * x.quantity,
                realized_pnl=x.realized_pnl, entry_fees=x.entry_fees, exit_fees=x.exit_fees,
                entry_reason=x.entry_reason, opened_at=x.opened_at, signal_id=x.signal_id,
            )
        )  # fmt: skip
    return Page(items=items, total=total, limit=p.limit, offset=p.offset)


def trade_out(x: m.Position) -> TradeOut:
    assert x.closed_at is not None and x.realized_pnl is not None
    fees = x.entry_fees + x.exit_fees
    return TradeOut(
        id=x.id, symbol=x.symbol, strategy_id=x.strategy_id, quantity=x.entry_quantity,
        entry_price=x.avg_entry_price, exit_price=x.exit_price,
        gross_pnl=x.realized_pnl + fees, fees=fees, net_pnl=x.realized_pnl,
        duration_seconds=(x.closed_at - x.opened_at).total_seconds(), opened_at=x.opened_at,
        closed_at=x.closed_at, entry_reason=x.entry_reason, exit_reason=x.exit_reason,
        signal_id=x.signal_id,
    )  # fmt: skip


@router.get("/trades", response_model=Page[TradeOut])
async def list_trades(
    _: Auth,
    db: DB,
    settings: SettingsDep,
    p: Annotated[PageParams, Depends(page_params)],
    symbol: Symbol = None,
    strategy: StrategyQ = None,
    outcome: Annotated[str | None, Query(pattern=r"^(win|loss)$")] = None,
    date_from: FromQ = None,
    date_to: ToQ = None,
) -> Page[TradeOut]:
    """Operaciones **cerradas**. Las abiertas están en /positions y no entran aquí."""
    stmt = (
        select(m.Position)
        .where(m.Position.mode == settings.trading_mode.value, m.Position.status == "CLOSED")
        .order_by(m.Position.closed_at.desc(), m.Position.id)
    )
    if symbol:
        stmt = stmt.where(m.Position.symbol == symbol.upper())
    if strategy:
        stmt = stmt.where(m.Position.strategy_id == strategy)
    if outcome == "win":
        stmt = stmt.where(m.Position.realized_pnl > 0)
    if outcome == "loss":
        stmt = stmt.where(m.Position.realized_pnl <= 0)
    if date_from:
        stmt = stmt.where(m.Position.closed_at >= date_from)
    if date_to:
        stmt = stmt.where(m.Position.closed_at <= date_to)
    rows, total = await paginate(db, stmt, p)
    return Page(items=[trade_out(r[0]) for r in rows], total=total, limit=p.limit, offset=p.offset)


def _stats(trades: list[m.Position]) -> dict[str, Any]:
    n = len(trades)
    pnls = [t.realized_pnl or ZERO for t in trades]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    gross_win, gross_loss = sum(wins, ZERO), -sum(losses, ZERO)
    return {
        "trades": n,
        "net_pnl": sum(pnls, ZERO),
        "fees": sum((t.entry_fees + t.exit_fees for t in trades), ZERO),
        "win_rate": None if n == 0 else len(wins) / n,
        "avg_win": None if not wins else gross_win / len(wins),
        "avg_loss": None if not losses else -gross_loss / len(losses),
        "expectancy_net": None if n == 0 else sum(pnls, ZERO) / n,
        "profit_factor": None if gross_loss == 0 else gross_win / gross_loss,
        "avg_duration_seconds": None
        if n == 0
        else sum(((t.closed_at - t.opened_at).total_seconds() for t in trades if t.closed_at), 0.0) / n,
        "largest_win_share": None
        if not wins or gross_win == 0
        else float(max(wins) / gross_win),
    }  # fmt: skip


@router.get("/performance")
async def performance(_: Auth, db: DB, settings: SettingsDep) -> dict[str, Any]:
    """Rendimiento de las operaciones **cerradas** del modo activo, neto de costes.

    Las posiciones abiertas se excluyen de las estadísticas (se informan aparte). Con pocas
    operaciones las cifras no son concluyentes: ``sample_warning`` lo indica.
    """
    mode = settings.trading_mode.value
    trades = list(
        (
            await db.execute(
                select(m.Position)
                .where(m.Position.mode == mode, m.Position.status == "CLOSED")
                .order_by(m.Position.closed_at)
            )
        ).scalars()
    )
    open_count = len(
        list(
            (
                await db.execute(
                    select(m.Position.id).where(
                        m.Position.mode == mode, m.Position.status == "OPEN"
                    )
                )
            ).scalars()
        )
    )
    equity_curve = (
        await db.execute(
            select(m.BalanceSnapshot.at, m.BalanceSnapshot.equity)
            .where(m.BalanceSnapshot.mode == mode)
            .order_by(m.BalanceSnapshot.at)
        )
    ).all()
    by: dict[str, dict[str, list[m.Position]]] = {"strategy": {}, "symbol": {}}
    for t in trades:
        by["strategy"].setdefault(t.strategy_id, []).append(t)
        by["symbol"].setdefault(t.symbol, []).append(t)
    n = len(trades)
    return {
        "mode": mode,
        "overall": _stats(trades),
        "open_positions_excluded": open_count,
        "by_strategy": {k: _stats(v) for k, v in by["strategy"].items()},
        "by_symbol": {k: _stats(v) for k, v in by["symbol"].items()},
        "max_drawdown_observed": max_drawdown_from_equity([e for _, e in equity_curve])
        if equity_curve
        else None,
        "equity_curve": [{"at": a, "equity": e} for a, e in equity_curve[-1000:]],
        "sample_warning": (
            None
            if n >= 300
            else f"{n} operaciones cerradas: muestra insuficiente para conclusiones (objetivo ≥ 300)."
        ),
        "notes": "Neto de comisiones; excluye posiciones abiertas; sin extrapolaciones.",
    }


__all__ = ["Decimal", "_stats", "router", "signal_out", "trade_out"]
