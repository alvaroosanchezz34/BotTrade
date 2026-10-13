"""Estrategias: catálogo, detalle y resultados de investigación (solo lectura)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import DB, Auth, SettingsDep
from app.api.routes.trading import _stats, signal_out, trade_out
from app.persistence import models as m
from app.strategies.registry import STRATEGIES

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _backtest_brief(r: m.BacktestRun) -> dict[str, Any]:
    return {
        "id": r.id, "created_at": r.created_at, "segment": r.segment, "interval": r.interval,
        "symbols": r.symbols, "start": r.start, "end": r.end, "strategy_version": r.strategy_version,
        "data_hash": r.data_hash, "metrics": r.metrics, "notes": r.notes,
    }  # fmt: skip


@router.get("")
async def list_strategies(_: Auth, db: DB, settings: SettingsDep) -> dict[str, Any]:
    rows = (await db.execute(select(m.Strategy).order_by(m.Strategy.id))).scalars().all()
    out = []
    for s in rows:
        last_bt = (
            await db.execute(
                select(m.BacktestRun)
                .where(m.BacktestRun.strategy_id == s.id, m.BacktestRun.segment != "lab")
                .order_by(m.BacktestRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        last_signal = (
            await db.execute(
                select(m.SignalRow.created_at)
                .where(
                    m.SignalRow.strategy_id == s.id, m.SignalRow.mode == settings.trading_mode.value
                )
                .order_by(m.SignalRow.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        out.append(
            {
                "id": s.id, "name": s.name, "description": s.description, "enabled": s.enabled,
                "symbols": s.symbols, "interval": s.interval, "version": s.version,
                "last_signal_at": last_signal,
                "last_backtest_at": last_bt.created_at if last_bt else None,
                "last_backtest_verdict": (last_bt.notes.split(":")[0] if last_bt and last_bt.notes else None),
            }
        )  # fmt: skip
    return {
        "strategies": out,
        "disclaimer": "Hipótesis de investigación: ninguna se considera rentable por defecto.",
    }


@router.get("/{strategy_id}")
async def strategy_detail(
    strategy_id: str, _: Auth, db: DB, settings: SettingsDep
) -> dict[str, Any]:
    s = await db.get(m.Strategy, strategy_id)
    if s is None:
        raise HTTPException(404, "Estrategia no encontrada")
    mode = settings.trading_mode.value
    cls = STRATEGIES.get(strategy_id)
    sigs = (
        await db.execute(
            select(m.SignalRow, m.RiskDecisionRow)
            .outerjoin(m.RiskDecisionRow, m.RiskDecisionRow.signal_id == m.SignalRow.id)
            .where(m.SignalRow.strategy_id == strategy_id, m.SignalRow.mode == mode)
            .order_by(m.SignalRow.created_at.desc())
            .limit(20)
        )
    ).all()
    closed = list(
        (
            await db.execute(
                select(m.Position)
                .where(
                    m.Position.strategy_id == strategy_id,
                    m.Position.mode == mode,
                    m.Position.status == "CLOSED",
                )
                .order_by(m.Position.closed_at.desc())
            )
        ).scalars()
    )
    open_positions = list(
        (
            await db.execute(
                select(m.Position).where(
                    m.Position.strategy_id == strategy_id,
                    m.Position.mode == mode,
                    m.Position.status == "OPEN",
                )
            )
        ).scalars()
    )
    runs = list(
        (
            await db.execute(
                select(m.BacktestRun)
                .where(m.BacktestRun.strategy_id == strategy_id, m.BacktestRun.segment != "lab")
                .order_by(m.BacktestRun.created_at.desc())
                .limit(20)
            )
        ).scalars()
    )
    versions = (
        await db.execute(
            select(m.StrategyVersion)
            .where(m.StrategyVersion.strategy_id == strategy_id)
            .order_by(m.StrategyVersion.created_at.desc())
        )
    ).scalars()
    return {
        "id": s.id, "name": s.name, "description": s.description, "enabled": s.enabled,
        "symbols": s.symbols, "interval": s.interval, "version": s.version,
        "params": s.params,
        "params_editable": False,
        "indicators": getattr(cls, "indicators", None) if cls else None,
        "code_hash": cls.code_hash() if cls else None,
        "versions": [{"version": v.version, "code_hash": v.code_hash, "created_at": v.created_at} for v in versions],
        "recent_signals": [signal_out(a, b) for a, b in sigs],
        "open_positions": len(open_positions),
        "live_stats": {**_stats(closed), "mode": mode},
        "recent_trades": [trade_out(p) for p in closed[:20]],
        "backtests": [_backtest_brief(r) for r in runs],
        "out_of_sample": [_backtest_brief(r) for r in runs if r.segment in ("test", "wf-oos")],
        "note": "Los resultados de backtest no son predicciones; revisa el veredicto y la muestra.",
    }  # fmt: skip
