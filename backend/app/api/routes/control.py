"""Controles remotos: pausa, reanudar, kill switch, activar/desactivar estrategias.

El API **no ejecuta** nada: encola un comando en ``bot_commands`` y responde 202 con su
identificador. Es el engine quien lo valida y lo marca ``APPLIED`` o ``REJECTED``; la UI
distingue «solicitado» de «confirmado» consultando ``GET /api/commands/{id}``.

Todas las rutas exigen sesión de administrador, cabecera CSRF y están limitadas en
frecuencia. Los pulsos repetidos (doble toque) devuelven el comando pendiente existente.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.common import ENGINE_MAX_BEAT_AGE
from app.api.deps import DB, AdminAction
from app.api.ratelimit import client_ip, control_limiter, limit
from app.api.schemas import CommandAccepted
from app.audit.log import audit
from app.core.domain import utcnow
from app.persistence import models as m

router = APIRouter(
    prefix="/api",
    tags=["control"],
    dependencies=[Depends(limit(control_limiter, "control"))],
)

DEDUP_WINDOW = timedelta(seconds=60)


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clear_kill_switch: bool = False


class KillIn(BaseModel):
    """``confirm`` debe ser literalmente «KILL»: confirmación validada en el servidor."""

    model_config = ConfigDict(extra="forbid")
    confirm: Literal["KILL"]
    liquidate_positions: bool = False


async def _enqueue(
    db: DB, auth: AdminAction, request: Request, command: str, payload: dict[str, Any]
) -> CommandAccepted:
    now = utcnow()
    hb = await db.get(m.EngineHeartbeat, "engine")
    alive = hb is not None and now - hb.last_beat_at <= ENGINE_MAX_BEAT_AGE
    pending = (
        await db.execute(
            select(m.BotCommand).where(
                m.BotCommand.command == command,
                m.BotCommand.status == "PENDING",
                m.BotCommand.created_at >= now - DEDUP_WINDOW,
            )
        )
    ).scalars()
    for p in pending:
        if p.payload == payload:
            return CommandAccepted(
                command_id=p.id, status=p.status, engine_alive=alive, duplicate_of_pending=True,
                detail="Ya hay una solicitud idéntica pendiente de confirmación.",
            )  # fmt: skip
    cmd = m.BotCommand(created_by=auth.actor, command=command, payload=payload)
    db.add(cmd)
    await db.flush()
    await audit(
        db, actor=auth.actor, action="command.requested", target=command, after=payload,
        ip=client_ip(request),
    )  # fmt: skip
    return CommandAccepted(
        command_id=cmd.id,
        status="PENDING",
        engine_alive=alive,
        detail=(
            "Solicitud registrada; pendiente de que el engine la confirme."
            if alive
            else "Solicitud registrada, pero el engine no responde: no está confirmada."
        ),
    )


@router.post("/bot/pause", status_code=status.HTTP_202_ACCEPTED, response_model=CommandAccepted)
async def pause(_: Empty, request: Request, auth: AdminAction, db: DB) -> CommandAccepted:
    """Bloquea nuevas entradas. NO cierra posiciones: siguen vigiladas."""
    return await _enqueue(db, auth, request, "PAUSE", {})


@router.post("/bot/resume", status_code=status.HTTP_202_ACCEPTED, response_model=CommandAccepted)
async def resume(body: ResumeIn, request: Request, auth: AdminAction, db: DB) -> CommandAccepted:
    """Reanuda solo si el engine verifica conexiones, datos, reconciliación y límites."""
    return await _enqueue(
        db, auth, request, "RESUME", {"clear_kill_switch": body.clear_kill_switch}
    )


@router.post(
    "/bot/kill-switch", status_code=status.HTTP_202_ACCEPTED, response_model=CommandAccepted
)
async def kill(body: KillIn, request: Request, auth: AdminAction, db: DB) -> CommandAccepted:
    """Bloqueo inmediato. Por defecto cancela entradas pendientes y conserva las posiciones."""
    return await _enqueue(
        db, auth, request, "KILL_SWITCH", {"liquidate_positions": body.liquidate_positions}
    )


async def _strategy_command(
    strategy_id: str, command: str, request: Request, auth: AdminAction, db: DB
) -> CommandAccepted:
    if await db.get(m.Strategy, strategy_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Estrategia no encontrada")
    return await _enqueue(db, auth, request, command, {"strategy_id": strategy_id})


@router.post("/strategies/{strategy_id}/enable", status_code=202, response_model=CommandAccepted)
async def enable(
    strategy_id: str, _: Empty, request: Request, auth: AdminAction, db: DB
) -> CommandAccepted:
    return await _strategy_command(strategy_id, "ENABLE_STRATEGY", request, auth, db)


@router.post("/strategies/{strategy_id}/disable", status_code=202, response_model=CommandAccepted)
async def disable(
    strategy_id: str, _: Empty, request: Request, auth: AdminAction, db: DB
) -> CommandAccepted:
    return await _strategy_command(strategy_id, "DISABLE_STRATEGY", request, auth, db)
