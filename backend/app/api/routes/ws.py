"""Canal WebSocket de tiempo real (``/ws``).

Es un canal de *notificación*, no la fuente de verdad: envía «pulsos» compactos con el
estado resumido y el cliente refresca por REST cuando cambian. Si la conexión se pierde,
el cliente reconecta y recibe el pulso completo inmediatamente (recuperación de estado) o
cae a sondeo periódico.

Seguridad: cookie de sesión obligatoria, comprobación de ``Origin`` (contra CSWSH) y
revalidación de la sesión en cada ciclo (una sesión revocada o caducada corta el canal).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from app.api.common import ENGINE_MAX_BEAT_AGE, load_account
from app.api.deps import SESSION_COOKIE
from app.auth.service import resolve_session
from app.core.config import Settings
from app.core.domain import Severity, utcnow
from app.persistence import models as m
from app.persistence.db import get_sessionmaker
from app.risk.store import load_risk_state

log = logging.getLogger(__name__)
router = APIRouter()

POLL_SECONDS = 2.0
KEEPALIVE_SECONDS = 15.0


class WsLike(Protocol):
    @property
    def headers(self) -> Any: ...
    @property
    def cookies(self) -> dict[str, str]: ...

    async def accept(self) -> None: ...
    async def close(self, code: int = 1000) -> None: ...
    async def send_text(self, data: str) -> None: ...


def origin_allowed(origin: str | None, host: str | None, settings: Settings) -> bool:
    if not origin:
        return False  # los navegadores siempre envían Origin en WebSocket
    if origin in settings.cors_origins:
        return True
    return host is not None and urlparse(origin).netloc == host


async def build_pulse(settings: Settings) -> dict[str, Any]:
    mode = settings.trading_mode.value
    now = utcnow()
    async with get_sessionmaker()() as db:
        hb = await db.get(m.EngineHeartbeat, "engine")
        state = await load_risk_state(db, mode)
        snap, stale, _ = await load_account(db, settings)
        crit = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(m.Alert)
                    .where(
                        m.Alert.resolved_at.is_(None), m.Alert.severity == Severity.CRITICAL.value
                    )
                )
            ).scalar_one()
        )
        last_order = (
            await db.execute(select(func.max(m.OrderRow.updated_at)).where(m.OrderRow.mode == mode))
        ).scalar_one()
        last_signal = (
            await db.execute(
                select(func.max(m.SignalRow.created_at)).where(m.SignalRow.mode == mode)
            )
        ).scalar_one()
        commands = (
            (
                await db.execute(
                    select(m.BotCommand)
                    .where(
                        (m.BotCommand.status == "PENDING")
                        | (m.BotCommand.processed_at >= now - timedelta(minutes=2))
                    )
                    .order_by(m.BotCommand.created_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
    alive = hb is not None and now - hb.last_beat_at <= ENGINE_MAX_BEAT_AGE
    return {
        "mode": mode,
        "engine": "running" if alive else ("never_started" if hb is None else "stopped"),
        "paused": bool(state and state.paused),
        "kill_switch": bool(state and state.kill_switch_active),
        "entries_allowed": bool(
            state and not (state.paused or state.kill_switch_active or state.latched_reasons)
        ),
        "equity": None if snap is None else str(snap.equity),
        "marks_stale": stale,
        "open_positions": None if snap is None else len(snap.portfolio.positions),
        "critical_alerts": crit,
        "last_order_at": last_order.isoformat() if last_order else None,
        "last_signal_at": last_signal.isoformat() if last_signal else None,
        "commands": [
            {"id": str(c.id), "command": c.command, "status": c.status, "result": c.result}
            for c in commands
        ],
    }


async def session_valid(token: str | None) -> bool:
    async with get_sessionmaker()() as db:
        ok = await resolve_session(db, token) is not None
        await db.commit()
    return ok


async def serve(ws: WsLike, settings: Settings, *, max_cycles: int | None = None) -> None:
    host = ws.headers.get("host")
    if not origin_allowed(ws.headers.get("origin"), host, settings):
        await ws.close(code=1008)
        return
    token = ws.cookies.get(SESSION_COOKIE)
    if not await session_valid(token):
        await ws.close(code=1008)
        return
    await ws.accept()
    seq = 0
    last_sent: str | None = None
    last_at = 0.0
    cycles = 0
    loop = asyncio.get_running_loop()
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        if not await session_valid(token):
            await ws.send_text(json.dumps({"type": "auth_expired"}))
            await ws.close(code=1008)
            return
        try:
            pulse = await build_pulse(settings)
        except Exception:
            log.exception("no se pudo construir el pulso")
            pulse = None
        if pulse is not None:
            body = json.dumps(pulse, sort_keys=True)
            if body != last_sent or loop.time() - last_at >= KEEPALIVE_SECONDS:
                seq += 1
                await ws.send_text(
                    json.dumps(
                        {"type": "pulse", "seq": seq, "at": utcnow().isoformat(), "data": pulse}
                    )
                )
                last_sent, last_at = body, loop.time()
        await asyncio.sleep(POLL_SECONDS if max_cycles is None else 0)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        await serve(websocket, settings)
