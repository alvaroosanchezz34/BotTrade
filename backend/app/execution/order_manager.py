"""Gestor de órdenes: única puerta entre una decisión de riesgo y un ``ExecutionAdapter``.

Garantías (cada una con tests en ``tests/test_order_manager.py``):

1. Solo acepta entradas con una ``RiskDecision`` **aprobada** y asociada a la misma señal.
2. La intención se persiste *antes* de enviarla (estado ``SUBMITTED`` confirmado en BD).
   Si el proceso muere a mitad, al reiniciar existe rastro de la orden y puede consultarse.
3. ``client_order_id`` identifica la orden de forma idempotente. Una respuesta ambigua
   (timeout/5XX) deja la orden en ``UNKNOWN`` y **nunca** se reenvía a ciegas: primero se
   consulta al proveedor por ese identificador.
4. Cada fill se procesa una sola vez (clave ``(order_id, trade_id)``).
5. Todo cambio de estado queda en ``order_events``.
6. Las salidas reducen riesgo y no pasan por el motor de riesgo, pero sí por la máquina
   de estados y por la misma deduplicación (una sola orden de salida viva por posición).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain import (
    TERMINAL_ORDER_STATUSES,
    InstrumentRules,
    OrderStatus,
    OrderType,
    RiskDecision,
    Severity,
    Side,
    Signal,
    SignalAction,
    utcnow,
)
from app.core.money import ZERO, floor_to_step
from app.execution.adapter import (
    AmbiguousResponseError,
    ExecutionAdapter,
    ExecutionReport,
    OrderRejectedError,
    OrderRequest,
)
from app.execution.fees import fee_in_quote, net_base_quantity
from app.execution.state_machine import can_transition
from app.monitoring.alerts import raise_alert
from app.persistence import db
from app.persistence import models as m
from app.risk.engine import RiskEngine
from app.risk.store import load_risk_state, save_risk_state

log = logging.getLogger(__name__)

NON_TERMINAL = tuple(s.value for s in OrderStatus if s not in TERMINAL_ORDER_STATUSES)
EXIT_PURPOSES = (
    "EXIT_STOP",
    "EXIT_TARGET",
    "EXIT_SIGNAL",
    "EXIT_TIME",
    "EXIT_MANUAL",
    "EXIT_EMERGENCY",
)


class OrderNotAllowedError(PermissionError):
    """Se intentó crear una orden sin autorización del motor de riesgo."""


class DuplicateOrderError(RuntimeError):
    """Ya existe una orden o posición equivalente."""


def new_client_order_id() -> str:
    return f"bt-{uuid.uuid4().hex[:28]}"  # ≤ 36 caracteres [a-z0-9-] (límite de Binance)


def _validate_against_rules(
    qty: Decimal, price: Decimal | None, rules: InstrumentRules
) -> str | None:
    if rules.status != "TRADING":
        return f"instrumento no operable ({rules.status})"
    if qty < rules.min_qty or qty > rules.max_qty:
        return "cantidad fuera de [min_qty, max_qty]"
    if floor_to_step(qty, rules.step_size) != qty:
        return "cantidad no es múltiplo de stepSize"
    if price is not None and qty * price < rules.min_notional:
        return f"nocional {qty * price:.4f} < mínimo {rules.min_notional}"
    return None


class OrderManager:
    def __init__(
        self,
        *,
        mode: str,
        adapter: ExecutionAdapter,
        rules: Mapping[str, InstrumentRules],
        risk_engine: RiskEngine,
    ):
        self.mode = mode
        self.adapter = adapter
        self.rules = rules
        self.risk_engine = risk_engine

    # ================================================================ entradas

    async def submit_entry(
        self,
        signal: Signal,
        decision: RiskDecision,
        *,
        limit_price: Decimal | None = None,
        now: datetime | None = None,
    ) -> m.OrderRow:
        """Crea y envía una entrada (a mercado, o límite si se indica ``limit_price``).

        Solo con decisión de riesgo aprobada. El tamaño lo fija el motor de riesgo; un precio
        límite solo puede abaratar la entrada, nunca aumentar la cantidad."""
        if not decision.approved or decision.quantity <= 0:
            raise OrderNotAllowedError("La decisión de riesgo no está aprobada")
        if decision.signal_id != signal.id:
            raise OrderNotAllowedError("La decisión de riesgo pertenece a otra señal")
        if signal.action is not SignalAction.ENTER_LONG:
            raise OrderNotAllowedError("Solo se admiten entradas largas")
        rules = self.rules.get(signal.symbol)
        if rules is None:
            raise OrderNotAllowedError(f"Instrumento desconocido: {signal.symbol}")

        async with db.session_scope() as s:
            existing = (
                await s.execute(
                    select(m.OrderRow).where(
                        m.OrderRow.signal_id == signal.id, m.OrderRow.mode == self.mode
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing  # misma señal ⇒ misma orden (idempotencia)
            await self._assert_no_overlap(s, signal.symbol)
            await record_signal_and_decision(s, self.mode, signal, decision)

            order = m.OrderRow(
                client_order_id=new_client_order_id(),
                mode=self.mode,
                provider=self.adapter.name,
                signal_id=signal.id,
                risk_decision_id=decision.id,
                strategy_id=signal.strategy_id,
                strategy_version=signal.strategy_version,
                purpose="ENTRY",
                symbol=signal.symbol,
                side=Side.BUY.value,
                type=(OrderType.LIMIT if limit_price else OrderType.MARKET).value,
                quantity=decision.quantity,
                limit_price=limit_price,
                status=OrderStatus.CREATED.value,
            )
            s.add(order)
            await s.flush()
            await self._event(s, order, None, OrderStatus.CREATED, {"signal": str(signal.id)})
            problem = _validate_against_rules(
                decision.quantity, limit_price or signal.reference_price, rules
            )
            if (
                not problem
                and limit_price is not None
                and (
                    limit_price >= signal.reference_price
                    or floor_to_step(limit_price, rules.tick_size) != limit_price
                )
            ):
                problem = "precio límite no válido (debe ser < referencia y múltiplo de tickSize)"
            if problem:
                await self._set_status(s, order, OrderStatus.REJECTED, {"reason": problem})
                order.last_error = problem
                return order
            await self._set_status(s, order, OrderStatus.VALIDATED, {})
            await self._set_status(s, order, OrderStatus.SUBMITTED, {})
            order.submitted_at = now or utcnow()
            order_id = order.id
        # La transacción anterior se confirmó: la intención ya es durable.
        return await self._send(order_id)

    async def _assert_no_overlap(self, s: AsyncSession, symbol: str) -> None:
        open_pos = (
            await s.execute(
                select(m.Position.id).where(
                    m.Position.mode == self.mode,
                    m.Position.symbol == symbol,
                    m.Position.status == "OPEN",
                )
            )
        ).first()
        live_entry = (
            await s.execute(
                select(m.OrderRow.id).where(
                    m.OrderRow.mode == self.mode,
                    m.OrderRow.symbol == symbol,
                    m.OrderRow.purpose == "ENTRY",
                    m.OrderRow.status.in_(NON_TERMINAL),
                )
            )
        ).first()
        if open_pos or live_entry:
            raise DuplicateOrderError(f"Ya hay posición u orden de entrada viva en {symbol}")

    # ================================================================ salidas

    async def submit_exit(
        self, position_id: uuid.UUID, purpose: str, *, now: datetime | None = None
    ) -> m.OrderRow | None:
        """Vende la posición abierta a mercado. Devuelve None si no procede (y por qué se
        registra); no hace nada si ya hay una salida viva (evita duplicar ventas)."""
        if purpose not in EXIT_PURPOSES:
            raise ValueError(f"Motivo de salida no válido: {purpose}")
        async with db.session_scope() as s:
            pos = await s.get(m.Position, position_id)
            if pos is None or pos.mode != self.mode or pos.status != "OPEN":
                return None
            live = (
                await s.execute(
                    select(m.OrderRow).where(
                        m.OrderRow.position_id == position_id,
                        m.OrderRow.side == Side.SELL.value,
                        m.OrderRow.status.in_(NON_TERMINAL),
                    )
                )
            ).scalar_one_or_none()
            if live is not None:
                return live
            rules = self.rules[pos.symbol]
            qty = floor_to_step(pos.quantity, rules.step_size)
            problem = _validate_against_rules(qty, pos.avg_entry_price, rules) if qty > 0 else "0"
            if problem:
                await raise_alert(
                    s,
                    severity=Severity.WARNING,
                    code="exit_not_possible",
                    message=f"No se puede vender {pos.quantity} {pos.symbol}: {problem}",
                    recommended_action="Posición residual (polvo) o filtro del exchange; revisar.",
                    context={"position_id": str(pos.id)},
                    dedup_key=f"exit_not_possible:{pos.id}",
                )
                return None
            order = m.OrderRow(
                client_order_id=new_client_order_id(),
                mode=self.mode,
                provider=self.adapter.name,
                position_id=pos.id,
                strategy_id=pos.strategy_id,
                purpose=purpose,
                symbol=pos.symbol,
                side=Side.SELL.value,
                type=OrderType.MARKET.value,
                quantity=qty,
                status=OrderStatus.CREATED.value,
            )
            s.add(order)
            await s.flush()
            await self._event(s, order, None, OrderStatus.CREATED, {"position": str(pos.id)})
            await self._set_status(s, order, OrderStatus.VALIDATED, {})
            await self._set_status(s, order, OrderStatus.SUBMITTED, {})
            order.submitted_at = now or utcnow()
            order_id = order.id
        return await self._send(order_id)

    # ================================================================ envío y consulta

    async def _send(self, order_id: uuid.UUID) -> m.OrderRow:
        async with db.session_scope() as s:
            order = await s.get(m.OrderRow, order_id)
            assert order is not None
            request = OrderRequest(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=Side(order.side),
                type=OrderType(order.type),
                quantity=order.quantity,
                limit_price=order.limit_price,
            )
        report: ExecutionReport | None = None
        error: tuple[OrderStatus, str] | None = None
        try:
            report = await self.adapter.submit(request)
        except OrderRejectedError as exc:
            error = (OrderStatus.REJECTED, exc.reason)
        except AmbiguousResponseError as exc:
            error = (OrderStatus.UNKNOWN, str(exc))
        async with db.session_scope() as s:
            order = await s.get(m.OrderRow, order_id)
            assert order is not None
            if report is not None:
                await self._apply_report(s, order, report)
                await self._reset_execution_errors(s)
            else:
                assert error is not None
                status, reason = error
                order.last_error = reason
                await self._set_status(s, order, status, {"reason": reason})
                await self._record_execution_error(s, f"{status.value}: {reason}")
                if status is OrderStatus.UNKNOWN:
                    await raise_alert(
                        s,
                        severity=Severity.CRITICAL,
                        code="order_unknown",
                        message=f"Orden {order.client_order_id} en estado desconocido: {reason}",
                        recommended_action=(
                            "Se consultará al proveedor. Hasta resolverla no se abren entradas."
                        ),
                        context={"client_order_id": order.client_order_id, "symbol": order.symbol},
                        dedup_key=f"order_unknown:{order.client_order_id}",
                    )
        if error is not None and error[0] is OrderStatus.UNKNOWN:
            return await self.sync_order(order_id)
        async with db.session_scope() as s:
            final = await s.get(m.OrderRow, order_id)
            assert final is not None
            return final

    async def sync_order(self, order_id: uuid.UUID) -> m.OrderRow:
        """Alinea una orden no terminal con el proveedor, consultando por ``client_order_id``.

        * Proveedor la conoce → se aplica su estado (y fills).
        * Proveedor confirma que NO la conoce → la orden nunca llegó: ``REJECTED``.
        * La consulta también es ambigua → se queda en ``UNKNOWN`` (bloquea entradas).
        """
        async with db.session_scope() as s:
            order = await s.get(m.OrderRow, order_id)
            assert order is not None
            if OrderStatus(order.status) in TERMINAL_ORDER_STATUSES:
                return order
            symbol, cid = order.symbol, order.client_order_id
        try:
            report = await self.adapter.get_order(symbol, cid)
        except AmbiguousResponseError:
            async with db.session_scope() as s:
                order = await s.get(m.OrderRow, order_id)
                assert order is not None
                if order.status != OrderStatus.UNKNOWN.value:
                    await self._set_status(
                        s, order, OrderStatus.UNKNOWN, {"reason": "consulta ambigua"}
                    )
                return order
        async with db.session_scope() as s:
            order = await s.get(m.OrderRow, order_id)
            assert order is not None
            if report is None:
                reason = "el proveedor no conoce la orden (no llegó)"
                order.last_error = reason
                if can_transition(OrderStatus(order.status), OrderStatus.REJECTED):
                    await self._set_status(s, order, OrderStatus.REJECTED, {"reason": reason})
                else:  # p. ej. ya tenía fills registrados: es una discrepancia, no un descarte
                    await raise_alert(
                        s,
                        severity=Severity.CRITICAL,
                        code="order_vanished",
                        message=f"El proveedor no conoce la orden {cid} ({order.status})",
                        recommended_action="Reconciliar manualmente; ¿reset del testnet?",
                        context={"client_order_id": cid},
                        dedup_key=f"order_vanished:{cid}",
                    )
            else:
                await self._apply_report(s, order, report)
                await raise_resolved(s, f"order_unknown:{cid}")
            return order

    async def cancel_order(self, order_id: uuid.UUID) -> m.OrderRow:
        async with db.session_scope() as s:
            order = await s.get(m.OrderRow, order_id)
            assert order is not None
            if OrderStatus(order.status) in TERMINAL_ORDER_STATUSES:
                return order
            symbol, cid = order.symbol, order.client_order_id
        try:
            report = await self.adapter.cancel(symbol, cid)
        except OrderRejectedError:
            return await self.sync_order(
                order_id
            )  # quizá ya estaba ejecutada: que decida el proveedor
        except AmbiguousResponseError:
            return await self.sync_order(order_id)
        async with db.session_scope() as s:
            order = await s.get(m.OrderRow, order_id)
            assert order is not None
            await self._apply_report(s, order, report)
            return order

    # ================================================================ aplicar informes

    async def _apply_report(
        self, s: AsyncSession, order: m.OrderRow, report: ExecutionReport
    ) -> None:
        known = set(
            (
                await s.execute(select(m.Fill.exchange_trade_id).where(m.Fill.order_id == order.id))
            ).scalars()
        )
        new_fills = [f for f in report.fills if f.trade_id not in known]
        for f in new_fills:
            s.add(
                m.Fill(
                    order_id=order.id,
                    exchange_trade_id=f.trade_id,
                    price=f.price,
                    quantity=f.quantity,
                    fee=f.fee,
                    fee_asset=f.fee_asset,
                    is_maker=f.is_maker,
                    filled_at=f.filled_at,
                )
            )
        await s.flush()
        if report.exchange_order_id:
            order.exchange_order_id = report.exchange_order_id

        if new_fills:
            total_qty = order.filled_qty + sum((f.quantity for f in new_fills), ZERO)
            notional = (order.avg_fill_price or ZERO) * order.filled_qty + sum(
                (f.price * f.quantity for f in new_fills), ZERO
            )
            order.filled_qty = total_qty
            order.avg_fill_price = notional / total_qty
            rules = self.rules[order.symbol]
            order.fees += sum(
                (
                    fee_in_quote(
                        fee=f.fee, fee_asset=f.fee_asset, price=f.price, quantity=f.quantity,
                        rules=rules,
                    )
                    for f in new_fills
                ),
                ZERO,
            )  # fmt: skip
            order.fee_asset = new_fills[-1].fee_asset

        current = OrderStatus(order.status)
        target = report.status
        if target != current:
            if can_transition(current, target):
                detail: dict[str, Any] = {"filled_qty": str(order.filled_qty)}
                if report.reject_reason:
                    detail["reason"] = report.reject_reason
                await self._set_status(s, order, target, detail)
            elif current in TERMINAL_ORDER_STATUSES:
                await raise_alert(
                    s,
                    severity=Severity.CRITICAL,
                    code="order_state_conflict",
                    message=(
                        f"Orden {order.client_order_id}: local {current.value}, "
                        f"proveedor {target.value}"
                    ),
                    recommended_action="Revisar manualmente; entradas bloqueadas por reconciliación.",
                    context={"client_order_id": order.client_order_id},
                    dedup_key=f"order_state_conflict:{order.client_order_id}",
                )
        elif new_fills:
            await self._event(s, order, current, current, {"filled_qty": str(order.filled_qty)})

        for f in new_fills:
            rules = self.rules[order.symbol]
            await self._apply_fill_to_position(
                s,
                order,
                price=f.price,
                gross_qty=f.quantity,
                held_qty=net_base_quantity(
                    quantity=f.quantity, fee=f.fee, fee_asset=f.fee_asset, rules=rules
                ),
                fee_quote=fee_in_quote(
                    fee=f.fee, fee_asset=f.fee_asset, price=f.price, quantity=f.quantity,
                    rules=rules,
                ),
                at=f.filled_at,
            )  # fmt: skip

    async def _apply_fill_to_position(
        self,
        s: AsyncSession,
        order: m.OrderRow,
        *,
        price: Decimal,
        gross_qty: Decimal,
        held_qty: Decimal,
        fee_quote: Decimal,
        at: datetime,
    ) -> None:
        """``gross_qty``: lo ejecutado. ``held_qty``: lo que realmente queda en cartera
        (en compras con comisión en la moneda base es algo menos). La posición lleva
        ``held_qty``; la comisión va en ``fee_quote`` (moneda de cotización)."""
        if order.side == Side.BUY.value:
            pos = await s.get(m.Position, order.position_id) if order.position_id else None
            if pos is None:
                sig = await s.get(m.SignalRow, order.signal_id) if order.signal_id else None
                pos = m.Position(
                    mode=self.mode,
                    symbol=order.symbol,
                    strategy_id=order.strategy_id or "",
                    signal_id=order.signal_id,
                    status="OPEN",
                    quantity=ZERO,
                    entry_quantity=ZERO,
                    avg_entry_price=ZERO,
                    stop_price=sig.stop_price if sig else None,
                    target_price=sig.target_price if sig else None,
                    entry_reason=sig.rationale if sig else "",
                    opened_at=at,
                    realized_pnl=ZERO,
                )
                s.add(pos)
                await s.flush()
                order.position_id = pos.id
                state = await load_risk_state(s, self.mode)
                if state is not None:
                    state.on_entry()
                    await save_risk_state(s, self.mode, state)
            total = pos.entry_quantity + held_qty
            pos.avg_entry_price = (
                pos.avg_entry_price * pos.entry_quantity + price * held_qty
            ) / total
            pos.quantity += held_qty
            pos.entry_quantity += held_qty
            pos.entry_fees += fee_quote
            return

        qty = gross_qty
        pos = await s.get(m.Position, order.position_id) if order.position_id else None
        if pos is None:
            raise RuntimeError(f"Venta {order.client_order_id} sin posición asociada")
        if qty > pos.quantity:
            await raise_alert(
                s,
                severity=Severity.CRITICAL,
                code="oversold_position",
                message=f"Venta de {qty} > posición abierta {pos.quantity} en {pos.symbol}",
                recommended_action="Reconciliar con el proveedor; entradas bloqueadas.",
                context={"position_id": str(pos.id)},
                dedup_key=f"oversold:{pos.id}",
            )
            qty = pos.quantity
        sold_before = pos.entry_quantity - pos.quantity
        pos.exit_price = (
            price
            if pos.exit_price is None or sold_before == 0
            else (pos.exit_price * sold_before + price * qty) / (sold_before + qty)
        )
        entry_fee_share = pos.entry_fees * qty / pos.entry_quantity
        pnl = (price - pos.avg_entry_price) * qty - fee_quote - entry_fee_share
        pos.realized_pnl = (pos.realized_pnl or ZERO) + pnl
        pos.exit_fees += fee_quote
        pos.quantity -= qty
        if pos.quantity <= 0:
            pos.status = "CLOSED"
            pos.closed_at = at
            pos.exit_reason = order.purpose.removeprefix("EXIT_").lower()
            state = await load_risk_state(s, self.mode)
            if state is not None:
                state.on_trade_closed(pos.realized_pnl, self.risk_engine.limits)
                await save_risk_state(s, self.mode, state)

    # ================================================================ utilidades

    async def _set_status(
        self, s: AsyncSession, order: m.OrderRow, new: OrderStatus, detail: dict[str, Any]
    ) -> None:
        current = OrderStatus(order.status)
        if current != new and not can_transition(current, new):
            from app.execution.state_machine import InvalidTransitionError

            raise InvalidTransitionError(f"{current} → {new}")
        order.status = new.value
        await self._event(s, order, current, new, detail)

    async def _event(
        self,
        s: AsyncSession,
        order: m.OrderRow,
        old: OrderStatus | None,
        new: OrderStatus,
        detail: dict[str, Any],
    ) -> None:
        s.add(
            m.OrderEvent(
                order_id=order.id,
                from_status=old.value if old else None,
                to_status=new.value,
                detail=detail,
            )
        )

    async def _record_execution_error(self, s: AsyncSession, reason: str) -> None:
        state = await load_risk_state(s, self.mode)
        if state is None:
            raise RuntimeError("Estado de riesgo no inicializado: no se puede operar")
        state.execution_errors += 1
        for latch in self.risk_engine.update_latches(state, state.peak_equity):
            await raise_alert(
                s,
                severity=Severity.CRITICAL,
                code="risk_latch",
                message=f"Entradas bloqueadas: {latch}",
                recommended_action="Investigar la causa y reanudar manualmente.",
                context={"reason": latch},
                dedup_key=f"risk_latch:{latch}",
            )
        await save_risk_state(s, self.mode, state)
        log.warning("error de ejecución: %s", reason)

    async def _reset_execution_errors(self, s: AsyncSession) -> None:
        state = await load_risk_state(s, self.mode)
        if state is not None and state.execution_errors:
            state.execution_errors = 0
            await save_risk_state(s, self.mode, state)


async def raise_resolved(s: AsyncSession, dedup_key: str) -> None:
    from app.monitoring.alerts import resolve_alert

    await resolve_alert(s, dedup_key)


async def record_signal(s: AsyncSession, mode: str, signal: Signal, status: str) -> None:
    """Persiste una señal (idempotente). Todas las señales se guardan, también las rechazadas."""
    if await s.get(m.SignalRow, signal.id) is not None:
        return
    s.add(
        m.SignalRow(
            id=signal.id,
            mode=mode,
            symbol=signal.symbol,
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            action=signal.action.value,
            created_at=signal.created_at,
            valid_until=signal.valid_until,
            reference_price=signal.reference_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            score=signal.score,
            score_is_calibrated=signal.score_is_calibrated,
            rationale=signal.rationale,
            entry_conditions=signal.entry_conditions,
            invalidation=signal.invalidation,
            params=signal.params,
            status=status,
        )
    )
    await s.flush()


async def record_signal_and_decision(
    s: AsyncSession, mode: str, signal: Signal, decision: RiskDecision
) -> None:
    """Persiste señal y decisión de riesgo (idempotente)."""
    await record_signal(s, mode, signal, "APPROVED" if decision.approved else "REJECTED")
    if await s.get(m.RiskDecisionRow, decision.id) is None:
        s.add(
            m.RiskDecisionRow(
                id=decision.id,
                signal_id=signal.id,
                mode=mode,
                approved=decision.approved,
                checks=[
                    {"name": c.name, "passed": c.passed, "detail": c.detail}
                    for c in decision.checks
                ],
                quantity=decision.quantity,
                notional=decision.notional,
                risk_amount=decision.risk_amount,
                estimated_costs=decision.estimated_costs,
                decided_at=decision.decided_at,
            )
        )
        await s.flush()
