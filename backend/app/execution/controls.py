"""Controles de operación: pausa, reanudación y kill switch (sección 17).

Tres cosas distintas:

* **Pausa**: bloquea nuevas entradas. Las posiciones siguen gestionándose (stops/objetivos).
  No cierra nada.
* **Kill switch**: bloquea nuevas entradas de inmediato y aplica una ``KillSwitchPolicy``
  explícita. Por defecto: cancela las órdenes de *entrada* pendientes, conserva las órdenes
  de salida y **no liquida** posiciones (los stops siguen activos). Liquidar es una opción
  explícita (``liquidate_positions=True``), nunca el comportamiento por defecto.
* **Reanudar**: solo si pasan todas las comprobaciones previas; si alguna falla se rechaza
  y se explica el motivo. Tras un kill switch exige además ``clear_kill_switch=True``.

Solo afectan a la simulación / testnet: el sistema no tiene otro destino posible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select

from app.audit.log import audit, system_event
from app.core.domain import OrderStatus, Severity
from app.execution.order_manager import EXIT_PURPOSES, NON_TERMINAL, OrderManager
from app.execution.reconciler import reconcile
from app.monitoring.alerts import raise_alert
from app.persistence import db
from app.persistence import models as m
from app.risk.store import load_risk_state, save_risk_state


@dataclass(frozen=True)
class KillSwitchPolicy:
    cancel_pending_entries: bool = True
    cancel_pending_exits: bool = (
        False  # cancelar un stop de salida dejaría la posición sin protección
    )
    liquidate_positions: bool = False


@dataclass(frozen=True)
class ResumeContext:
    """Estado externo que aporta el engine; el resto se comprueba aquí contra la BD."""

    connections_ok: bool
    data_ready: bool
    data_reason: str
    equity: Decimal
    config_ok: bool = True
    config_reason: str = "ok"


@dataclass(frozen=True)
class ControlResult:
    ok: bool
    message: str
    reasons: tuple[str, ...] = field(default_factory=tuple)


async def pause_entries(mode: str, actor: str) -> ControlResult:
    async with db.session_scope() as s:
        state = await load_risk_state(s, mode)
        if state is None:
            return ControlResult(False, "estado de riesgo no inicializado")
        state.paused = True
        await save_risk_state(s, mode, state)
        await audit(s, actor=actor, action="bot.pause", target=mode)
    return ControlResult(True, "Nuevas entradas pausadas; las posiciones siguen gestionándose")


async def kill_switch(
    manager: OrderManager,
    actor: str,
    policy: KillSwitchPolicy = KillSwitchPolicy(),  # noqa: B008 - inmutable
) -> ControlResult:
    mode = manager.mode
    # 1) Bloqueo inmediato y durable, antes de tocar ninguna orden.
    async with db.session_scope() as s:
        state = await load_risk_state(s, mode)
        if state is None:
            return ControlResult(False, "estado de riesgo no inicializado")
        state.kill_switch_active = True
        await save_risk_state(s, mode, state)
        await audit(
            s,
            actor=actor,
            action="bot.kill_switch",
            target=mode,
            after={
                "cancel_pending_entries": policy.cancel_pending_entries,
                "cancel_pending_exits": policy.cancel_pending_exits,
                "liquidate_positions": policy.liquidate_positions,
            },
        )
        await raise_alert(
            s,
            severity=Severity.CRITICAL,
            code="kill_switch",
            message=f"Kill switch activado por {actor}",
            recommended_action="Revisar la causa. Reanudar exige comprobaciones y confirmación.",
            dedup_key="kill_switch",
        )
        pending = (
            (
                await s.execute(
                    select(m.OrderRow).where(
                        m.OrderRow.mode == mode, m.OrderRow.status.in_(NON_TERMINAL)
                    )
                )
            )
            .scalars()
            .all()
        )
        to_cancel = [
            o.id
            for o in pending
            if (o.purpose == "ENTRY" and policy.cancel_pending_entries)
            or (o.purpose in EXIT_PURPOSES and policy.cancel_pending_exits)
        ]
        open_positions = (
            (
                await s.execute(
                    select(m.Position.id).where(
                        m.Position.mode == mode, m.Position.status == "OPEN"
                    )
                )
            )
            .scalars()
            .all()
        )
    # 2) Órdenes pendientes según la política.
    for oid in to_cancel:
        await manager.cancel_order(oid)
    # 3) Posiciones: solo se liquidan si se pidió expresamente.
    if policy.liquidate_positions:
        for pid in open_positions:
            await manager.submit_exit(pid, "EXIT_EMERGENCY")
    return ControlResult(
        True,
        f"Kill switch activo: {len(to_cancel)} órdenes canceladas, "
        + (
            f"{len(open_positions)} posiciones en liquidación"
            if policy.liquidate_positions
            else f"{len(open_positions)} posiciones conservadas"
        ),
    )


async def resume(
    manager: OrderManager, actor: str, ctx: ResumeContext, *, clear_kill_switch: bool = False
) -> ControlResult:
    mode = manager.mode
    limits = manager.risk_engine.limits
    reasons: list[str] = []

    async with db.session_scope() as s:
        state = await load_risk_state(s, mode)
    if state is None:
        return ControlResult(False, "estado de riesgo no inicializado", ("sin estado de riesgo",))
    if state.kill_switch_active and not clear_kill_switch:
        reasons.append("el kill switch sigue activo: confirma con clear_kill_switch")
    if not ctx.connections_ok:
        reasons.append("conexiones no operativas")
    if not ctx.data_ready:
        reasons.append(f"datos de mercado no válidos: {ctx.data_reason}")
    if not ctx.config_ok:
        reasons.append(f"configuración inválida: {ctx.config_reason}")
    if state.daily_loss(ctx.equity) >= limits.max_daily_loss:
        reasons.append("pérdida diaria en el límite")
    if state.drawdown(ctx.equity) >= limits.max_drawdown:
        reasons.append("drawdown en el límite")
    if state.cooldown_until_day is not None:
        reasons.append("enfriamiento por pérdidas consecutivas")

    # Estado de las órdenes y reconciliación en vivo (no se confía en un resultado antiguo).
    report = await reconcile(manager, baseline=await _baseline(mode))
    if not report.ok:
        reasons += [f"reconciliación: {d.kind} {d.key}" for d in report.discrepancies]
    async with db.session_scope() as s:
        unknown = (
            await s.execute(
                select(m.OrderRow.id).where(
                    m.OrderRow.mode == mode, m.OrderRow.status == OrderStatus.UNKNOWN.value
                )
            )
        ).first()
    if unknown:
        reasons.append("hay órdenes en estado desconocido")

    async with db.session_scope() as s:
        if reasons:
            await audit(
                s,
                actor=actor,
                action="bot.resume_rejected",
                target=mode,
                after={"reasons": reasons},
            )
            return ControlResult(False, "Reanudación rechazada", tuple(reasons))
        state = await load_risk_state(s, mode)
        assert state is not None
        state.paused = False
        state.kill_switch_active = False
        state.latched_reasons.clear()
        state.execution_errors = 0
        await save_risk_state(s, mode, state)
        await audit(s, actor=actor, action="bot.resume", target=mode)
        await system_event(s, kind="bot_resumed", source="control", detail={"actor": actor})
    return ControlResult(True, "Entradas reanudadas")


async def _baseline(mode: str) -> dict[str, Decimal]:
    async with db.session_scope() as s:
        row = await s.get(m.SystemSetting, f"account:{mode}")
    raw = (row.value.get("baseline") if row else None) or {}
    return {k: Decimal(str(v)) for k, v in raw.items()}
