"""Health checks diferenciados: API, base de datos, datos de mercado y engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select

from app.core.config import Settings
from app.core.domain import utcnow
from app.persistence.db import get_sessionmaker, ping
from app.persistence.models import ConnectionStatus, EngineHeartbeat

Status = Literal["ok", "degraded", "down", "unknown"]

ENGINE_HEARTBEAT_MAX_AGE = timedelta(seconds=30)


@dataclass(frozen=True)
class ComponentHealth:
    status: Status
    detail: str
    last_update: datetime | None = None


async def check_database() -> ComponentHealth:
    try:
        await ping()
    except Exception as exc:
        return ComponentHealth("down", f"Sin conexión ({type(exc).__name__})")
    return ComponentHealth("ok", "Conectada")


async def check_engine() -> ComponentHealth:
    async with get_sessionmaker()() as s:
        hb = await s.get(EngineHeartbeat, "engine")
    if hb is None:
        return ComponentHealth("unknown", "El engine no ha arrancado nunca")
    age = utcnow() - hb.last_beat_at
    if age > ENGINE_HEARTBEAT_MAX_AGE:
        return ComponentHealth(
            "down", f"Sin latido desde hace {int(age.total_seconds())} s", hb.last_beat_at
        )
    return ComponentHealth("ok", f"Activo en modo {hb.mode}", hb.last_beat_at)


async def check_market_data(settings: Settings) -> ComponentHealth:
    async with get_sessionmaker()() as s:
        rows = (await s.scalars(select(ConnectionStatus))).all()
    if not rows:
        return ComponentHealth("unknown", "Sin conexiones de datos registradas")
    stale_after = timedelta(seconds=settings.stale_data_seconds)
    now = utcnow()
    last = max((r.last_valid_data_at for r in rows if r.last_valid_data_at), default=None)
    if any(r.status == "DOWN" for r in rows):
        return ComponentHealth("down", "Alguna conexión de datos está caída", last)
    if last is None or now - last > stale_after:
        return ComponentHealth("degraded", "Datos obsoletos", last)
    if any(r.status == "DEGRADED" for r in rows):
        return ComponentHealth("degraded", "Conexión degradada", last)
    return ComponentHealth("ok", "Datos al día", last)


async def readiness(settings: Settings) -> dict[str, Any]:
    db = await check_database()
    components: dict[str, ComponentHealth] = {
        "api": ComponentHealth("ok", "Respondiendo"),
        "database": db,
    }
    if db.status == "ok":
        components["engine"] = await check_engine()
        components["market_data"] = await check_market_data(settings)
    else:
        components["engine"] = ComponentHealth("unknown", "BD no disponible")
        components["market_data"] = ComponentHealth("unknown", "BD no disponible")

    statuses = {c.status for c in components.values()}
    overall: Status = "ok"
    if db.status == "down":
        overall = "down"
    elif statuses & {"down", "degraded", "unknown"}:
        overall = "degraded"
    return {
        "status": overall,
        "mode": settings.trading_mode.value,
        "checked_at": utcnow(),
        "components": {k: asdict(v) for k, v in components.items()},
    }
