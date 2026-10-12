from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from app.api.deps import SettingsDep
from app.monitoring.health import readiness

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    """Liveness: el proceso API responde. No toca la BD."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(response: Response, settings: SettingsDep) -> dict[str, Any]:
    """Readiness por componente. 503 solo si la base de datos está caída."""
    result = await readiness(settings)
    if result["status"] == "down":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
