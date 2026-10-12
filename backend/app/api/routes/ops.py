"""Alertas, auditoría, eventos del sistema y backtests."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api.common import Page, PageParams, page_params, paginate
from app.api.deps import DB, AdminAction, Auth, SettingsDep
from app.api.ratelimit import client_ip, control_limiter, limit
from app.api.schemas import AlertOut, CommandOut
from app.audit.log import audit
from app.core.domain import utcnow
from app.persistence import models as m

router = APIRouter(prefix="/api", tags=["operations"])


@router.get("/alerts", response_model=Page[AlertOut])
async def list_alerts(
    _: Auth,
    db: DB,
    p: Annotated[PageParams, Depends(page_params)],
    state: Annotated[str, Query(pattern=r"^(active|resolved|all)$")] = "active",
    severity: Annotated[str | None, Query(pattern=r"^(INFO|WARNING|CRITICAL)$")] = None,
) -> Page[AlertOut]:
    stmt = select(m.Alert).order_by(m.Alert.last_seen_at.desc(), m.Alert.id)
    if state == "active":
        stmt = stmt.where(m.Alert.resolved_at.is_(None))
    elif state == "resolved":
        stmt = stmt.where(m.Alert.resolved_at.is_not(None))
    if severity:
        stmt = stmt.where(m.Alert.severity == severity)
    rows, total = await paginate(db, stmt, p)
    return Page(
        items=[AlertOut.model_validate(r[0]) for r in rows],
        total=total,
        limit=p.limit,
        offset=p.offset,
    )


@router.post("/alerts/{alert_id}/ack", dependencies=[Depends(limit(control_limiter, "ack"))])
async def ack_alert(
    alert_id: uuid.UUID, request: Request, auth: AdminAction, db: DB
) -> dict[str, Any]:
    """Reconocer una alerta la marca como vista; NO la resuelve ni levanta bloqueos."""
    alert = await db.get(m.Alert, alert_id)
    if alert is None:
        raise HTTPException(404, "Alerta no encontrada")
    if alert.acknowledged_at is None:
        alert.acknowledged_at, alert.acknowledged_by = utcnow(), auth.actor
        await audit(
            db, actor=auth.actor, action="alert.ack", target=str(alert_id), ip=client_ip(request)
        )
    return {
        "id": alert.id,
        "acknowledged_at": alert.acknowledged_at,
        "acknowledged_by": alert.acknowledged_by,
    }


@router.get("/audit")
async def audit_log(
    auth: Auth,
    db: DB,
    p: Annotated[PageParams, Depends(page_params)],
    actor: Annotated[str | None, Query(max_length=64)] = None,
    action: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z_.]+$")] = None,
) -> dict[str, Any]:
    if auth.user.role != "admin":
        raise HTTPException(403, "Permiso insuficiente")
    stmt = select(m.AuditLog).order_by(m.AuditLog.id.desc())
    if actor:
        stmt = stmt.where(m.AuditLog.actor == actor)
    if action:
        stmt = stmt.where(m.AuditLog.action == action)
    rows, total = await paginate(db, stmt, p)
    return {
        "items": [
            {"id": a.id, "at": a.at, "actor": a.actor, "action": a.action, "target": a.target,
             "before": a.before, "after": a.after, "ip": a.ip}
            for (a,) in rows
        ],
        "total": total, "limit": p.limit, "offset": p.offset,
    }  # fmt: skip


@router.get("/events")
async def system_events(
    auth: Auth,
    db: DB,
    p: Annotated[PageParams, Depends(page_params)],
    kind: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z_.]+$")] = None,
) -> dict[str, Any]:
    """Diagnóstico: eventos del sistema (arranques, paradas, reanudaciones...)."""
    if auth.user.role != "admin":
        raise HTTPException(403, "Permiso insuficiente")
    stmt = select(m.SystemEvent).order_by(m.SystemEvent.id.desc())
    if kind:
        stmt = stmt.where(m.SystemEvent.kind == kind)
    rows, total = await paginate(db, stmt, p)
    return {
        "items": [{"id": e.id, "at": e.at, "kind": e.kind, "source": e.source, "detail": e.detail} for (e,) in rows],
        "total": total, "limit": p.limit, "offset": p.offset,
    }  # fmt: skip


@router.get("/commands", response_model=Page[CommandOut])
async def list_commands(
    _: Auth, db: DB, p: Annotated[PageParams, Depends(page_params)]
) -> Page[CommandOut]:
    stmt = select(m.BotCommand).order_by(m.BotCommand.created_at.desc(), m.BotCommand.id)
    rows, total = await paginate(db, stmt, p)
    return Page(
        items=[CommandOut.model_validate(r[0]) for r in rows],
        total=total,
        limit=p.limit,
        offset=p.offset,
    )


@router.get("/commands/{command_id}", response_model=CommandOut)
async def get_command(command_id: uuid.UUID, _: Auth, db: DB) -> CommandOut:
    cmd = await db.get(m.BotCommand, command_id)
    if cmd is None:
        raise HTTPException(404, "Comando no encontrado")
    return CommandOut.model_validate(cmd)


@router.get("/backtests")
async def list_backtests(
    _: Auth,
    db: DB,
    p: Annotated[PageParams, Depends(page_params)],
    strategy: Annotated[str | None, Query(max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")] = None,
    segment: Annotated[str | None, Query(max_length=16, pattern=r"^[A-Za-z0-9_\-]+$")] = None,
) -> dict[str, Any]:
    stmt = select(m.BacktestRun).order_by(m.BacktestRun.created_at.desc(), m.BacktestRun.id)
    if strategy:
        stmt = stmt.where(m.BacktestRun.strategy_id == strategy)
    if segment:
        stmt = stmt.where(m.BacktestRun.segment == segment)
    rows, total = await paginate(db, stmt, p)
    return {
        "items": [
            {"id": r.id, "created_at": r.created_at, "strategy_id": r.strategy_id,
             "strategy_version": r.strategy_version, "symbols": r.symbols, "interval": r.interval,
             "start": r.start, "end": r.end, "segment": r.segment, "metrics": r.metrics,
             "data_hash": r.data_hash, "seed": r.seed, "status": r.status, "notes": r.notes}
            for (r,) in rows
        ],
        "total": total, "limit": p.limit, "offset": p.offset,
    }  # fmt: skip


@router.get("/backtests/{run_id}")
async def backtest_detail(
    run_id: uuid.UUID, _: Auth, db: DB, settings: SettingsDep
) -> dict[str, Any]:
    r = await db.get(m.BacktestRun, run_id)
    if r is None:
        raise HTTPException(404, "Backtest no encontrado")
    trades = (
        await db.execute(
            select(m.BacktestTrade)
            .where(m.BacktestTrade.run_id == run_id)
            .order_by(m.BacktestTrade.entry_time)
            .limit(500)
        )
    ).scalars()
    return {
        "id": r.id, "created_at": r.created_at, "strategy_id": r.strategy_id,
        "strategy_version": r.strategy_version, "symbols": r.symbols, "interval": r.interval,
        "start": r.start, "end": r.end, "segment": r.segment, "params": r.params,
        "cost_model": r.cost_model, "data_hash": r.data_hash, "seed": r.seed,
        "metrics": r.metrics, "notes": r.notes,
        "trades": [
            {"symbol": t.symbol, "entry_time": t.entry_time, "exit_time": t.exit_time,
             "entry_price": t.entry_price, "exit_price": t.exit_price, "quantity": t.quantity,
             "fees": t.fees, "net_pnl": t.net_pnl, "exit_reason": t.exit_reason}
            for t in trades
        ],
        "reproducibility": "Mismos datos (data_hash), parámetros, costes y semilla ⇒ mismo resultado.",
    }  # fmt: skip
