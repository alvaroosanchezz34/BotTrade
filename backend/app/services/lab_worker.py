"""Proceso ``lab-worker``: ejecuta las simulaciones del laboratorio en segundo plano.

Es un proceso separado de la API y del engine de trading: una simulación de 1 minuto sobre meses de datos
usa mucha CPU y no debe afectar a la respuesta del panel ni a las operaciones. Reclama los trabajos con
``FOR UPDATE SKIP LOCKED`` (es seguro lanzar varios workers) y deja siempre un estado final claro:
``DONE`` con informe, o ``FAILED`` con el motivo.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.backtesting import lab
from app.backtesting.lab import LabError, LabSpec
from app.backtesting.report import save_result
from app.core.domain import interval_delta, utcnow
from app.market_data import store
from app.persistence import db
from app.persistence import models as m
from app.strategies.registry import build

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=30)


async def recover_stale() -> int:
    """Trabajos que quedaron «RUNNING» por una caída del worker: se marcan fallidos (no se reintentan a ciegas)."""
    async with db.session_scope() as s:
        rows = (
            await s.execute(select(m.LabJob).where(m.LabJob.status == "RUNNING").with_for_update())
        ).scalars()
        n = 0
        for job in rows:
            job.status, job.finished_at = "FAILED", utcnow()
            job.error = "Interrumpida por un reinicio del servidor. Vuelve a lanzarla."
            job.progress = "Interrumpida"
            n += 1
    return n


async def claim_next() -> uuid.UUID | None:
    async with db.session_scope() as s:
        job = (
            await s.execute(
                select(m.LabJob)
                .where(m.LabJob.status == "QUEUED")
                .order_by(m.LabJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if job is None:
            return None
        job.status, job.started_at, job.progress = "RUNNING", utcnow(), "Cargando datos"
        return job.id


async def _set_progress(job_id: uuid.UUID, text: str) -> None:
    async with db.session_scope() as s:
        job = await s.get(m.LabJob, job_id)
        if job is not None:
            job.progress = text


async def _fail(job_id: uuid.UUID, message: str) -> None:
    async with db.session_scope() as s:
        job = await s.get(m.LabJob, job_id)
        if job is not None:
            job.status, job.finished_at, job.error, job.progress = (
                "FAILED",
                utcnow(),
                message,
                "Fallida",
            )


async def process_job(job_id: uuid.UUID, *, workers: int | None = None) -> None:
    try:
        async with db.session_scope() as s:
            job = await s.get(m.LabJob, job_id)
            if job is None:
                return
            spec = LabSpec.from_json(job.spec)
            variants = int(job.spec.get("variants_tried", 1))

        strat = build(spec.strategy_id, spec.params)
        warmup_bars = min(25_000, max(strat.warmup, 1440) + 50)
        load_from = spec.start - interval_delta(spec.interval) * warmup_bars
        async with db.session_scope() as s:
            frames = {
                sym: await store.load_frame(s, sym, spec.interval, load_from, spec.end)
                for sym in spec.symbols
            }
            rules = await store.load_instruments(s)
            missing = 0
            for sym in spec.symbols:
                gaps = await store.missing_ranges(s, sym, spec.interval, spec.start, spec.end)
                missing += sum(g.missing_count() for g in gaps)
        for sym in spec.symbols:
            if frames[sym].empty or sym not in rules:
                raise LabError(f"No hay datos o reglas de instrumento para {sym}")

        await _set_progress(job_id, "Simulando (16 pruebas: periodo, tercios, costes, azar…)")
        report, result = await asyncio.to_thread(
            lab.run_lab, spec, frames, rules, variants_tried=variants, workers=workers
        )
        report["data"]["missing_bars"] = missing
        if missing:
            report["caveats"].append(f"Los datos tenían {missing} velas faltantes en el periodo.")

        await _set_progress(job_id, "Guardando el informe")
        async with db.session_scope() as s:
            run = await save_result(
                s, result, report["main"], segment="lab",
                notes=f"{report['verdict']}: laboratorio (variante {variants})",
            )  # fmt: skip
            job = await s.get(m.LabJob, job_id)
            assert job is not None
            job.run_id, job.report = run.id, report
            job.status, job.finished_at, job.progress = "DONE", utcnow(), "Terminada"
    except LabError as exc:
        await _fail(job_id, str(exc))
    except Exception:
        log.exception("fallo en la simulación del laboratorio", extra={"job_id": str(job_id)})
        await _fail(
            job_id,
            "Error interno al simular. Inténtalo de nuevo; si persiste, avisa al administrador.",
        )


async def run_worker(
    stop: asyncio.Event, *, poll_seconds: float = 2.0, workers: int | None = None
) -> None:
    n = await recover_stale()
    if n:
        log.warning("trabajos interrumpidos marcados como fallidos", extra={"count": n})
    log.info("lab-worker en marcha")
    while not stop.is_set():
        job_id = await claim_next()
        if job_id is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            continue
        log.info("simulando", extra={"job_id": str(job_id)})
        await process_job(job_id, workers=workers)
