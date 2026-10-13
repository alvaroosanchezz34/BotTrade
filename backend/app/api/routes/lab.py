"""Laboratorio de estrategias: pedir una simulación y leer su informe.

La API **no simula**: valida la petición, comprueba cuotas y encola un trabajo; lo ejecuta el proceso
``lab-worker``. Solo se aceptan plantillas del catálogo con parámetros numéricos acotados: nunca código del usuario.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.api.common import Page, PageParams, page_params, paginate
from app.api.deps import DB, Auth, AuthContext, SettingsDep, require_csrf
from app.api.ratelimit import client_ip, control_limiter, limit
from app.audit.log import audit
from app.backtesting import lab
from app.backtesting.lab import LabError, LabSpec
from app.core.domain import utcnow
from app.persistence import models as m
from app.strategies.registry import STRATEGIES

router = APIRouter(prefix="/api/lab", tags=["lab"])

MAX_ACTIVE_PER_USER = 1
MAX_PER_HOUR = 10
MAX_QUEUE = 30
VARIANT_WINDOW = timedelta(days=90)


class JobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    symbols: list[str] = Field(min_length=1, max_length=lab.MAX_SYMBOLS)
    interval: Literal["1m", "5m"]
    months: Literal[1, 3, 6, 12]
    params: dict[str, int | float] = Field(default_factory=dict, max_length=20)
    cost_level: Literal["normal", "conservador"] = "normal"


async def _availability(db: DB, symbols: list[str], interval: str) -> dict[str, dict[str, Any]]:
    rows = (
        await db.execute(
            select(
                m.CandleRow.symbol,
                func.min(m.CandleRow.open_time),
                func.max(m.CandleRow.open_time),
                func.count(),
            )
            .where(m.CandleRow.interval == interval, m.CandleRow.symbol.in_(symbols))
            .group_by(m.CandleRow.symbol)
        )
    ).all()
    return {s: {"from": a, "to": b, "bars": int(n)} for s, a, b, n in rows}


@router.get("/options")
async def options(_: Auth, db: DB, settings: SettingsDep) -> dict[str, Any]:
    """Plantillas, rangos de parámetros y datos disponibles (para construir el formulario)."""
    data = {i: await _availability(db, settings.symbols, i) for i in lab.ALLOWED_INTERVALS}
    return {
        "strategies": [
            {
                "id": sid,
                "name": cls.name,
                "description": cls.description,
                "params": lab.param_specs(sid),
            }
            for sid, cls in STRATEGIES.items()
        ],
        "symbols": settings.symbols,
        "intervals": list(lab.ALLOWED_INTERVALS),
        "months": list(lab.MONTH_CHOICES),
        "max_months": lab.MAX_MONTHS,
        "cost_levels": {
            k: {
                "taker_fee": str(c.taker_fee),
                "slippage": str(c.slippage),
                "spread": str(c.assumed_spread),
            }
            for k, c in lab.COST_LEVELS.items()
        },
        "data": data,
        "limits": {
            "active_jobs_per_user": MAX_ACTIVE_PER_USER,
            "jobs_per_hour": MAX_PER_HOUR,
            "min_trades_for_verdict": lab.MIN_TRADES_FOR_VERDICT,
        },
        "disclaimer": "Simulación histórica con dinero ficticio. No predice resultados futuros ni es asesoramiento.",
    }


def _job_out(j: m.LabJob, *, with_report: bool) -> dict[str, Any]:
    return {
        "id": j.id,
        "status": j.status,
        "progress": j.progress,
        "created_at": j.created_at,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
        "spec": {k: v for k, v in j.spec.items() if k != "variants_tried"},
        "variants_tried": j.spec.get("variants_tried"),
        "run_id": j.run_id,
        "error": j.error,
        "verdict": (j.report or {}).get("verdict"),
        "report": j.report if with_report else None,
    }


@router.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(limit(control_limiter, "lab"))],
)
async def create_job(
    body: JobIn,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_csrf)],
    db: DB,
    settings: SettingsDep,
) -> dict[str, Any]:
    now = utcnow()
    symbols = sorted({s.upper() for s in body.symbols})
    bad = [s for s in symbols if s not in settings.symbols]
    if bad:
        raise HTTPException(422, f"Activo no disponible: {', '.join(bad)}")
    try:
        params = lab.validate_params(body.strategy_id, dict(body.params))
        avail = await _availability(db, symbols, body.interval)
        missing = [s for s in symbols if s not in avail]
        if missing:
            raise LabError(f"Sin datos de {', '.join(missing)} en velas de {body.interval}")
        first = max(a["from"] for a in avail.values())
        last = min(a["to"] for a in avail.values())
        start, end = lab.plan_period(first, last, body.interval, body.months)
    except LabError as exc:
        raise HTTPException(422, str(exc)) from exc

    active = int(
        (
            await db.execute(
                select(func.count())
                .select_from(m.LabJob)
                .where(m.LabJob.user_id == auth.user.id, m.LabJob.status.in_(("QUEUED", "RUNNING")))
            )
        ).scalar_one()
    )
    if active >= MAX_ACTIVE_PER_USER:
        raise HTTPException(409, "Ya tienes una simulación en curso. Espera a que termine.")
    last_hour = int(
        (
            await db.execute(
                select(func.count())
                .select_from(m.LabJob)
                .where(
                    m.LabJob.user_id == auth.user.id,
                    m.LabJob.created_at >= now - timedelta(hours=1),
                )
            )
        ).scalar_one()
    )
    if last_hour >= MAX_PER_HOUR:
        raise HTTPException(
            429,
            f"Límite de {MAX_PER_HOUR} simulaciones por hora alcanzado.",
            headers={"Retry-After": "600"},
        )
    queued = int(
        (
            await db.execute(
                select(func.count()).select_from(m.LabJob).where(m.LabJob.status == "QUEUED")
            )
        ).scalar_one()
    )
    if queued >= MAX_QUEUE:
        raise HTTPException(503, "La cola de simulaciones está llena. Inténtalo en unos minutos.")

    # Comparaciones múltiples: cuántas variantes de la MISMA idea ha probado ya este usuario.
    prior = int(
        (
            await db.execute(
                select(func.count())
                .select_from(m.LabJob)
                .where(
                    m.LabJob.user_id == auth.user.id,
                    m.LabJob.created_at >= now - VARIANT_WINDOW,
                    m.LabJob.spec["strategy_id"].astext == body.strategy_id,
                    m.LabJob.spec["interval"].astext == body.interval,
                    m.LabJob.spec["symbols"] == [s for s in symbols],
                )
            )
        ).scalar_one()
    )
    spec = LabSpec(
        strategy_id=body.strategy_id,
        symbols=tuple(symbols),
        interval=body.interval,
        start=start,
        end=end,
        params=params,
        cost_level=body.cost_level,
    )
    job = m.LabJob(user_id=auth.user.id, spec={**spec.to_json(), "variants_tried": prior + 1})
    db.add(job)
    await db.flush()
    await audit(
        db, actor=auth.actor, action="lab.job_created", target=str(job.id),
        after={"strategy": body.strategy_id, "interval": body.interval, "months": body.months},
        ip=client_ip(request),
    )  # fmt: skip
    return {"job_id": job.id, "status": "QUEUED", "variants_tried": prior + 1}


@router.get("/jobs")
async def list_jobs(
    auth: Auth, db: DB, p: Annotated[PageParams, Depends(page_params)]
) -> Page[dict[str, Any]]:
    stmt = (
        select(m.LabJob)
        .where(m.LabJob.user_id == auth.user.id)
        .order_by(m.LabJob.created_at.desc(), m.LabJob.id)
    )
    rows, total = await paginate(db, stmt, p)
    return Page(
        items=[_job_out(j, with_report=False) for (j,) in rows],
        total=total,
        limit=p.limit,
        offset=p.offset,
    )


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, auth: Auth, db: DB) -> dict[str, Any]:
    job = await db.get(m.LabJob, job_id)
    # Un usuario solo ve sus propios trabajos (no se distingue «no existe» de «es de otro»).
    if job is None or job.user_id != auth.user.id:
        raise HTTPException(404, "Simulación no encontrada")
    return _job_out(job, with_report=True)
