"""Panel de inicio, mercados, riesgo y estado del sistema (solo lectura)."""

from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.common import (
    ENGINE_MAX_BEAT_AGE,
    effective_limits,
    last_marks,
    load_account,
    max_drawdown_from_equity,
    risk_summary,
)
from app.api.deps import DB, Auth, SettingsDep
from app.api.routes.trading import signal_out, trade_out
from app.core.domain import Severity, utcnow
from app.monitoring.health import readiness
from app.persistence import models as m
from app.risk.store import load_risk_state

router = APIRouter(prefix="/api", tags=["dashboard"])

DATA_ORIGIN = {
    "PAPER": "Cuenta simulada local; precios reales de mercado (datos públicos de Binance)",
    "TESTNET": "Cuenta y precios del Binance Spot Testnet (dinero ficticio)",
    "BACKTEST": "Histórico",
}


async def engine_view(db: DB) -> dict[str, Any]:
    hb = await db.get(m.EngineHeartbeat, "engine")
    if hb is None:
        return {"status": "never_started", "last_beat_at": None, "age_seconds": None, "state": None}
    age = utcnow() - hb.last_beat_at
    alive = age <= ENGINE_MAX_BEAT_AGE
    return {
        "status": "running" if alive else "stopped",
        "mode": hb.mode,
        "started_at": hb.started_at,
        "last_beat_at": hb.last_beat_at,
        "age_seconds": int(age.total_seconds()),
        "state": hb.state,
    }


@router.get("/dashboard/overview")
async def overview(_: Auth, db: DB, settings: SettingsDep) -> dict[str, Any]:
    mode = settings.trading_mode.value
    engine = await engine_view(db)
    snap, marks_stale, _rules = await load_account(db, settings)
    state = await load_risk_state(db, mode)
    limits = await effective_limits(db, settings)

    account: dict[str, Any] | None = None
    drawdown: dict[str, Any] | None = None
    if snap is not None:
        curve = [
            e
            for (e,) in (
                await db.execute(
                    select(m.BalanceSnapshot.equity)
                    .where(m.BalanceSnapshot.mode == mode)
                    .order_by(m.BalanceSnapshot.at)
                )
            ).all()
        ]
        account = {
            "currency": "USDT",
            "initial_capital": snap.initial_capital,
            "cash": snap.cash,
            "equity": snap.equity,
            "realized_pnl": snap.realized_pnl,
            "unrealized_pnl": snap.unrealized_pnl,
            "fees_total": snap.fees_total,
            "return_pct": (snap.equity - snap.initial_capital) / snap.initial_capital * 100,
            "marks_stale": marks_stale,
            "valuation": "posiciones a último cierre 1m, sin comisión de salida futura",
        }
        cur_dd = state.drawdown(snap.equity) if state else None
        drawdown = {
            "current": cur_dd,
            "max_observed": max_drawdown_from_equity([*curve, snap.equity]),
            "limit": limits.max_drawdown,
            "reference": "pico de equity → equity actual",
        }

    mode_ = mode
    closed = int(
        (
            await db.execute(
                select(func.count())
                .select_from(m.Position)
                .where(m.Position.mode == mode_, m.Position.status == "CLOSED")
            )
        ).scalar_one()
    )
    recent = (
        await db.execute(
            select(m.Position)
            .where(m.Position.mode == mode_, m.Position.status == "CLOSED")
            .order_by(m.Position.closed_at.desc())
            .limit(5)
        )
    ).scalars()
    strategies = (
        await db.execute(
            select(m.Strategy.id).where(m.Strategy.enabled.is_(True)).order_by(m.Strategy.id)
        )
    ).scalars()
    conns = (
        (await db.execute(select(m.ConnectionStatus).order_by(m.ConnectionStatus.name)))
        .scalars()
        .all()
    )
    alerts = (
        await db.execute(
            select(m.Alert)
            .where(m.Alert.resolved_at.is_(None), m.Alert.severity == Severity.CRITICAL.value)
            .order_by(m.Alert.last_seen_at.desc())
            .limit(10)
        )
    ).scalars()
    last_data = max((c.last_valid_data_at for c in conns if c.last_valid_data_at), default=None)
    return {
        "mode": mode,
        "mode_matches_engine": engine.get("mode") in (None, mode),
        "data_origin": DATA_ORIGIN[mode],
        "as_of": utcnow(),
        "engine": engine,
        "account": account,
        "drawdown": drawdown,
        "open_positions": len(snap.portfolio.positions) if snap else None,
        "closed_trades": closed,
        "recent_trades": [trade_out(p) for p in recent],
        "active_strategies": list(strategies),
        "connections": [
            {"name": c.name, "status": c.status, "last_message_at": c.last_message_at,
             "last_valid_data_at": c.last_valid_data_at, "detail": c.detail}
            for c in conns
        ],
        "last_data_at": last_data,
        "critical_alerts": [
            {"id": a.id, "code": a.code, "message": a.message,
             "recommended_action": a.recommended_action, "last_seen_at": a.last_seen_at,
             "occurrences": a.occurrences}
            for a in alerts
        ],
        "risk": risk_summary(limits, state, snap),
    }  # fmt: skip


@router.get("/markets")
async def markets(
    _: Auth,
    db: DB,
    settings: SettingsDep,
    symbol: Annotated[str | None, Query(max_length=32, pattern=r"^[A-Za-z0-9]+$")] = None,
    strategy: Annotated[str | None, Query(max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")] = None,
) -> dict[str, Any]:
    symbols = [symbol.upper()] if symbol else settings.symbols
    marks = await last_marks(db, symbols)
    now = utcnow()
    stale_after = timedelta(seconds=settings.stale_data_seconds) + timedelta(minutes=1)
    strat_rows = (await db.execute(select(m.Strategy).order_by(m.Strategy.id))).scalars().all()
    out = []
    for sym in symbols:
        mark = marks.get(sym)
        candles = (
            await db.execute(
                select(m.CandleRow.close, m.CandleRow.volume, m.CandleRow.open_time)
                .where(m.CandleRow.symbol == sym, m.CandleRow.interval == "1m")
                .order_by(m.CandleRow.open_time.desc())
                .limit(1440)
            )
        ).all()
        closes = [c.close for c in reversed(candles)]
        change = float((closes[-1] / closes[0] - 1) * 100) if len(closes) >= 1440 // 2 else None
        vol24 = sum((c.volume for c in candles), Decimal(0)) if len(candles) >= 1440 // 2 else None
        rets = [
            math.log(float(closes[i] / closes[i - 1]))
            for i in range(max(1, len(closes) - 60), len(closes))
            if closes[i - 1] > 0
        ]
        vol1h = None
        if len(rets) >= 30:
            mu = sum(rets) / len(rets)
            vol1h = (
                math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(60) * 100
            )
        analysing = [
            s.id for s in strat_rows if sym in s.symbols and (s.enabled or strategy == s.id)
        ]
        if strategy and strategy not in {s.id for s in strat_rows if sym in s.symbols}:
            continue
        sigs = (
            await db.execute(
                select(m.SignalRow, m.RiskDecisionRow)
                .outerjoin(m.RiskDecisionRow, m.RiskDecisionRow.signal_id == m.SignalRow.id)
                .where(m.SignalRow.symbol == sym, m.SignalRow.mode == settings.trading_mode.value)
                .order_by(m.SignalRow.created_at.desc())
                .limit(3)
            )
        ).all()
        age = None if mark is None else (now - mark[1]).total_seconds()
        out.append(
            {
                "symbol": sym,
                "price": None if mark is None else mark[0],
                "price_as_of": None if mark is None else mark[1],
                "price_source": "última vela cerrada de 1m",
                "data_status": "sin_datos" if mark is None else ("obsoleto" if now - mark[1] > stale_after else "al_dia"),
                "data_age_seconds": age,
                "change_24h_pct": change,
                "volume_24h": vol24,
                "volatility_1h_pct": vol1h,
                "strategies": analysing,
                "latest_signals": [signal_out(s, d) for s, d in sigs],
            }
        )  # fmt: skip
    return {"as_of": now, "markets": out}


@router.get("/markets/{symbol}/candles")
async def candles(
    symbol: str,
    _: Auth,
    db: DB,
    settings: SettingsDep,
    interval: Annotated[str, Query(pattern=r"^(1m|5m)$")] = "1m",
    limit: Annotated[int, Query(ge=10, le=1000)] = 300,
) -> dict[str, Any]:
    sym = symbol.upper()
    if sym not in settings.symbols:
        raise HTTPException(404, "Símbolo no observado")
    rows = (
        (
            await db.execute(
                select(m.CandleRow)
                .where(m.CandleRow.symbol == sym, m.CandleRow.interval == interval)
                .order_by(m.CandleRow.open_time.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    marks = []
    for p in (
        await db.execute(
            select(m.Position)
            .where(m.Position.symbol == sym, m.Position.mode == settings.trading_mode.value)
            .order_by(m.Position.opened_at.desc())
            .limit(50)
        )
    ).scalars():
        marks.append({"time": p.opened_at, "type": "entry", "price": p.avg_entry_price})
        if p.closed_at and p.exit_price:
            marks.append({"time": p.closed_at, "type": "exit", "price": p.exit_price})
    return {
        "symbol": sym,
        "interval": interval,
        "source": "velas cerradas persistidas",
        "candles": [
            {"t": int(r.open_time.timestamp()), "o": r.open, "h": r.high, "l": r.low,
             "c": r.close, "v": r.volume}
            for r in reversed(rows)
        ],
        "trade_markers": marks,
    }  # fmt: skip


@router.get("/risk")
async def risk(_: Auth, db: DB, settings: SettingsDep) -> dict[str, Any]:
    snap, marks_stale, _r = await load_account(db, settings)
    state = await load_risk_state(db, settings.trading_mode.value)
    limits = await effective_limits(db, settings)
    recent = (
        await db.execute(
            select(m.RiskDecisionRow)
            .where(m.RiskDecisionRow.mode == settings.trading_mode.value)
            .order_by(m.RiskDecisionRow.decided_at.desc())
            .limit(20)
        )
    ).scalars()
    rejections: dict[str, int] = {}
    decisions = []
    for d in recent:
        for c in d.checks:
            if not c["passed"]:
                rejections[c["name"]] = rejections.get(c["name"], 0) + 1
        decisions.append(
            {"id": d.id, "signal_id": d.signal_id, "approved": d.approved, "at": d.decided_at,
             "reasons": [f"{c['name']}: {c['detail']}" for c in d.checks if not c["passed"]]}
        )  # fmt: skip
    return {
        **risk_summary(limits, state, snap),
        "marks_stale": marks_stale,
        "recent_decisions": decisions,
        "recent_rejection_counts": rejections,
        "policy": "Límites de diseño: no garantizan que la pérdida real no los supere (gaps, deslizamiento).",
        "editable": False,
    }


@router.get("/system/status")
async def system_status(_: Auth, db: DB, settings: SettingsDep) -> dict[str, Any]:
    health = await readiness(settings)
    return {
        **health,
        "engine": await engine_view(db),
        "environment": settings.environment.value,
        "symbols": settings.symbols,
        "intervals": settings.intervals,
        "real_money_enabled": False,
    }
