from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.domain import OrderStatus, RiskDecision
from app.core.money import ZERO
from app.execution.order_manager import (
    DuplicateOrderError,
    OrderNotAllowedError,
)
from app.execution.simulated import Fault
from app.persistence import db
from app.persistence import models as m
from tests.exec_helpers import Harness, make_signal


async def count(model: type, *where: object) -> int:
    async with db.session_scope() as s:
        return int(
            (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()
        )


async def statuses(order_id: object) -> list[str]:
    async with db.session_scope() as s:
        rows = (
            await s.execute(
                select(m.OrderEvent.to_status)
                .where(m.OrderEvent.order_id == order_id)
                .order_by(m.OrderEvent.id)
            )
        ).scalars()
        return list(rows)


async def open_position() -> m.Position:
    async with db.session_scope() as s:
        return (await s.execute(select(m.Position).where(m.Position.status == "OPEN"))).scalar_one()


# ------------------------------------------------------------------ entrada normal


async def test_approved_entry_fills_and_creates_traceable_position() -> None:
    h = await Harness.create()
    sig = make_signal()
    decision = await h.decide(sig)
    assert decision.approved, decision.reasons

    order = await h.manager.submit_entry(sig, decision)

    assert order.status == OrderStatus.FILLED.value
    assert order.signal_id == sig.id and order.risk_decision_id == decision.id
    assert order.filled_qty == decision.quantity
    assert await statuses(order.id) == ["CREATED", "VALIDATED", "SUBMITTED", "FILLED"]
    pos = await open_position()
    assert pos.quantity == decision.quantity == pos.entry_quantity
    assert pos.stop_price == sig.stop_price and pos.target_price == sig.target_price
    assert pos.signal_id == sig.id and pos.entry_reason == "prueba"
    # Entrada con spread + deslizamiento: peor que la referencia (nunca al precio exacto).
    assert pos.avg_entry_price > sig.reference_price
    assert (await h.risk_state()).entries_today == 1
    # Persistencia de auditoría completa.
    assert await count(m.SignalRow) == 1 and await count(m.RiskDecisionRow) == 1


async def test_cash_and_equity_derive_from_fills() -> None:
    h = await Harness.create()
    sig = make_signal()
    decision = await h.decide(sig)
    order = await h.manager.submit_entry(sig, decision)
    snap = await h.snapshot()
    expected_cash = Decimal(500) - order.filled_qty * order.avg_fill_price - order.fees
    assert snap.cash == expected_cash
    assert snap.fees_total == order.fees > 0
    # Valorada a 80000 (sin spread) la posición pierde costes: equity < capital inicial.
    assert snap.equity < Decimal(500)
    assert snap.equity == snap.cash + order.filled_qty * Decimal(80000)
    # El saldo del exchange simulado coincide con el de la BD (misma contabilidad).
    bal = await h.adapter.balances()
    assert bal["USDT"] == snap.cash and bal["BTC"] == order.filled_qty


# ------------------------------------------------------------------ el riesgo manda


async def test_rejected_or_foreign_decision_never_reaches_the_exchange() -> None:
    h = await Harness.create()
    sig = make_signal()
    rejected = RiskDecision(signal_id=sig.id, approved=False, checks=())
    with pytest.raises(OrderNotAllowedError):
        await h.manager.submit_entry(sig, rejected)

    other = make_signal()
    good = await h.decide(other)
    with pytest.raises(OrderNotAllowedError):  # decisión de OTRA señal
        await h.manager.submit_entry(sig, good)

    assert h.adapter.submit_calls == 0
    assert await count(m.OrderRow) == 0


async def test_same_signal_is_idempotent_and_overlap_is_blocked() -> None:
    h = await Harness.create()
    sig, sig2 = make_signal(), make_signal()
    decision, decision2 = (
        await h.decide(sig),
        await h.decide(sig2),
    )  # ambas aprobadas (cartera vacía)
    assert decision.approved and decision2.approved
    first = await h.manager.submit_entry(sig, decision)
    again = await h.manager.submit_entry(sig, decision)
    assert again.id == first.id
    assert h.adapter.submit_calls == 1

    # Defensa en profundidad: aunque el riesgo aprobara una segunda entrada en el mismo
    # activo (decisión obsoleta), el gestor no la envía.
    with pytest.raises(DuplicateOrderError):
        await h.manager.submit_entry(sig2, decision2)
    assert h.adapter.submit_calls == 1

    # Y el motor de riesgo, con la cartera real, la rechaza por duplicada.
    refreshed = await h.decide(make_signal())
    assert "duplicada" in {c.name for c in refreshed.checks if not c.passed}


# ------------------------------------------------------------------ fallos del proveedor


async def test_exchange_rejection_is_recorded_and_counts_as_execution_error() -> None:
    h = await Harness.create()
    h.adapter.inject(Fault.REJECT)
    sig = make_signal()
    order = await h.manager.submit_entry(sig, await h.decide(sig))
    assert order.status == OrderStatus.REJECTED.value
    assert "rechazo" in (order.last_error or "")
    assert (await h.risk_state()).execution_errors == 1
    assert await count(m.Position) == 0


async def test_repeated_execution_errors_latch_entries() -> None:
    h = await Harness.create()
    for _ in range(3):
        h.adapter.inject(Fault.REJECT)
        sig = make_signal()
        await h.manager.submit_entry(sig, await h.decide(sig))
    state = await h.risk_state()
    assert state.execution_errors == 3
    nxt = await h.decide(make_signal())
    assert not nxt.approved
    assert "bloqueos" in {c.name for c in nxt.checks if not c.passed}
    async with db.session_scope() as s:
        alerts = (await s.execute(select(m.Alert.code))).scalars().all()
    assert "risk_latch" in alerts


async def test_lost_response_after_execution_resolves_without_resending() -> None:
    """Timeout DESPUÉS de que el exchange ejecutó: no se reenvía; se consulta y se adopta."""
    h = await Harness.create()
    h.adapter.inject(Fault.TIMEOUT_AFTER)
    sig = make_signal()
    order = await h.manager.submit_entry(sig, await h.decide(sig))

    assert order.status == OrderStatus.FILLED.value
    assert h.adapter.submit_calls == 1  # jamás se reenvió
    assert await count(m.Position, m.Position.status == "OPEN") == 1
    assert await count(m.Fill) == 1
    hist = await statuses(order.id)
    assert "UNKNOWN" in hist and hist[-1] == "FILLED"
    bal = await h.adapter.balances()
    assert bal["BTC"] == order.filled_qty  # una sola compra en el exchange


async def test_timeout_before_reaching_exchange_is_confirmed_not_placed() -> None:
    h = await Harness.create()
    h.adapter.inject(Fault.TIMEOUT_BEFORE)
    sig = make_signal()
    order = await h.manager.submit_entry(sig, await h.decide(sig))
    assert order.status == OrderStatus.REJECTED.value
    assert "no conoce" in (order.last_error or "")
    assert await count(m.Position) == 0
    assert (await h.adapter.balances())["BTC"] == 0


async def test_unknown_stays_unknown_when_query_also_fails_and_blocks_reconciliation() -> None:
    h = await Harness.create()
    h.adapter.inject(Fault.TIMEOUT_AFTER)
    h.adapter.inject(Fault.QUERY_TIMEOUT)
    sig = make_signal()
    order = await h.manager.submit_entry(sig, await h.decide(sig))
    assert order.status == OrderStatus.UNKNOWN.value
    async with db.session_scope() as s:
        codes = (await s.execute(select(m.Alert.code))).scalars().all()
    assert "order_unknown" in codes
    # No hay posición hasta saber qué pasó, pero tampoco se reenvía.
    assert await count(m.Position) == 0 and h.adapter.submit_calls == 1

    resolved = await h.manager.sync_order(order.id)  # el proveedor ya responde
    assert resolved.status == OrderStatus.FILLED.value
    assert await count(m.Position, m.Position.status == "OPEN") == 1
    async with db.session_scope() as s:
        active = (
            (await s.execute(select(m.Alert).where(m.Alert.resolved_at.is_(None)))).scalars().all()
        )
    assert [a for a in active if a.code == "order_unknown"] == []


# ------------------------------------------------------------------ ejecuciones parciales


async def test_market_entry_with_thin_liquidity_is_partial_then_expires() -> None:
    h = await Harness.create()
    sig = make_signal()
    decision = await h.decide(sig)
    h.adapter.set_price("BTCUSDT", Decimal(80000), available_qty=decision.quantity / 2)
    order = await h.manager.submit_entry(sig, decision)
    assert order.status == OrderStatus.EXPIRED.value
    assert 0 < order.filled_qty < decision.quantity
    pos = await open_position()
    assert pos.quantity == order.filled_qty  # la posición refleja solo lo ejecutado


async def test_limit_entry_partial_fill_then_cancel_keeps_partial_position() -> None:
    h = await Harness.create()
    sig = make_signal()
    decision = await h.decide(sig)
    limit = Decimal("79900.00")
    order = await h.manager.submit_entry(sig, decision, limit_price=limit)
    assert order.status == OrderStatus.ACKNOWLEDGED.value  # reposa en el libro
    assert await count(m.Position) == 0

    # Tocar el límite no basta: debe atravesarlo.
    h.adapter.advance("BTCUSDT", low=limit, high=Decimal(80100))
    order = await h.manager.sync_order(order.id)
    assert order.status == OrderStatus.ACKNOWLEDGED.value

    h.adapter.advance(
        "BTCUSDT", low=limit - 1, high=Decimal(80100), available_qty=decision.quantity / 4
    )
    order = await h.manager.sync_order(order.id)
    assert order.status == OrderStatus.PARTIALLY_FILLED.value
    assert order.avg_fill_price == limit  # límite: sin deslizamiento
    pos = await open_position()
    assert pos.quantity == decision.quantity / 4

    order = await h.manager.cancel_order(order.id)
    assert order.status == OrderStatus.CANCELED.value
    assert (await open_position()).quantity == decision.quantity / 4  # lo ejecutado se conserva
    assert await statuses(order.id) == [
        "CREATED", "VALIDATED", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "CANCELED",
    ]  # fmt: skip


async def test_invalid_limit_price_is_rejected_before_sending() -> None:
    h = await Harness.create()
    sig = make_signal()
    order = await h.manager.submit_entry(sig, await h.decide(sig), limit_price=Decimal("80010"))
    assert order.status == OrderStatus.REJECTED.value  # por encima de la referencia
    assert h.adapter.submit_calls == 0


# ------------------------------------------------------------------ salidas y P&L


async def test_exit_closes_position_and_pnl_matches_cash() -> None:
    h = await Harness.create()
    sig = make_signal()
    entry = await h.manager.submit_entry(sig, await h.decide(sig))
    pos = await open_position()

    h.adapter.set_price("BTCUSDT", Decimal(81000))
    exit_order = await h.manager.submit_exit(pos.id, "EXIT_TARGET")
    assert exit_order is not None and exit_order.status == OrderStatus.FILLED.value

    async with db.session_scope() as s:
        closed = await s.get(m.Position, pos.id)
    assert closed is not None
    assert closed.status == "CLOSED" and closed.exit_reason == "target"
    assert closed.quantity == 0 and closed.exit_price == exit_order.avg_fill_price
    gross = (exit_order.avg_fill_price - entry.avg_fill_price) * entry.filled_qty
    assert closed.realized_pnl == gross - entry.fees - exit_order.fees
    snap = await h.snapshot()
    # Sin posiciones abiertas: equity - capital == P&L realizado neto (todo cuadra).
    assert snap.equity - Decimal(500) == closed.realized_pnl == snap.realized_pnl
    assert snap.unrealized_pnl == ZERO
    assert (await h.risk_state()).consecutive_losses == 0


async def test_losing_trade_updates_risk_state_persistently() -> None:
    h = await Harness.create()
    sig = make_signal()
    await h.manager.submit_entry(sig, await h.decide(sig))
    pos = await open_position()
    h.adapter.set_price("BTCUSDT", Decimal(79600))
    await h.manager.submit_exit(pos.id, "EXIT_STOP")
    async with db.session_scope() as s:
        closed = await s.get(m.Position, pos.id)
    assert closed is not None and closed.realized_pnl is not None and closed.realized_pnl < 0
    assert (await h.risk_state()).consecutive_losses == 1
    # Pérdida realizada ≈ riesgo presupuestado (1,25) por el diseño del sizing.
    assert abs(closed.realized_pnl) <= Decimal("1.25") * Decimal("1.05")


async def test_partial_exit_leaves_remainder_open_and_second_exit_completes() -> None:
    h = await Harness.create()
    sig = make_signal()
    entry = await h.manager.submit_entry(sig, await h.decide(sig))
    pos = await open_position()

    h.adapter.set_price("BTCUSDT", Decimal(81000), available_qty=entry.filled_qty / 2)
    first = await h.manager.submit_exit(pos.id, "EXIT_SIGNAL")
    assert first is not None and first.status == OrderStatus.EXPIRED.value
    remaining = await open_position()
    assert remaining.quantity == entry.filled_qty - first.filled_qty > 0
    assert remaining.realized_pnl is not None  # P&L parcial ya realizado

    h.adapter.set_price("BTCUSDT", Decimal(81000))
    second = await h.manager.submit_exit(pos.id, "EXIT_SIGNAL")
    assert second is not None and second.status == OrderStatus.FILLED.value
    async with db.session_scope() as s:
        closed = await s.get(m.Position, pos.id)
    assert closed is not None and closed.status == "CLOSED" and closed.quantity == 0
    total_fees = entry.fees + first.fees + second.fees
    gross = (
        (first.avg_fill_price - entry.avg_fill_price) * first.filled_qty
        + (second.avg_fill_price - entry.avg_fill_price) * second.filled_qty
    )  # fmt: skip
    assert abs(closed.realized_pnl - (gross - total_fees)) < Decimal("1e-12")


async def test_no_duplicate_exit_while_one_is_unresolved() -> None:
    h = await Harness.create()
    sig = make_signal()
    await h.manager.submit_entry(sig, await h.decide(sig))
    pos = await open_position()
    h.adapter.inject(Fault.TIMEOUT_AFTER)
    h.adapter.inject(Fault.QUERY_TIMEOUT)
    first = await h.manager.submit_exit(pos.id, "EXIT_STOP")
    assert first is not None and first.status == OrderStatus.UNKNOWN.value
    second = await h.manager.submit_exit(pos.id, "EXIT_STOP")
    assert second is not None and second.id == first.id
    assert await count(m.OrderRow, m.OrderRow.side == "SELL") == 1
    final = await h.manager.sync_order(first.id)
    assert final.status == OrderStatus.FILLED.value  # se vendió una sola vez
    assert (await h.adapter.balances())["BTC"] == 0


async def test_fill_replay_is_not_double_counted() -> None:
    """El proveedor reenvía el mismo informe (p. ej. en cada consulta): no duplica fills."""
    h = await Harness.create()
    sig = make_signal()
    order = await h.manager.submit_entry(sig, await h.decide(sig))
    for _ in range(3):
        await h.manager.sync_order(order.id)  # terminal: no-op
    report = await h.adapter.get_order("BTCUSDT", order.client_order_id)
    assert report is not None
    async with db.session_scope() as s:
        row = await s.get(m.OrderRow, order.id)
        assert row is not None
        await h.manager._apply_report(s, row, report)
        await h.manager._apply_report(s, row, report)
    assert await count(m.Fill) == 1
    assert (await open_position()).quantity == order.filled_qty
