from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.core.domain import OrderStatus, OrderType, Side
from app.execution.adapter import OrderRequest
from app.execution.controls import (
    KillSwitchPolicy,
    ResumeContext,
    kill_switch,
    pause_entries,
    resume,
)
from app.execution.reconciler import reconcile
from app.execution.simulated import Fault
from app.persistence import db
from app.persistence import models as m
from tests.exec_helpers import MODE, Harness, make_signal

ZERO_BASELINE = {"BTC": Decimal(0), "ETH": Decimal(0)}
GOOD = ResumeContext(connections_ok=True, data_ready=True, data_reason="ok", equity=Decimal(500))


async def enter(h: Harness, **kw: object) -> m.OrderRow:
    sig = make_signal(**kw)  # type: ignore[arg-type]
    return await h.manager.submit_entry(sig, await h.decide(sig))


async def alerts() -> list[m.Alert]:
    async with db.session_scope() as s:
        return list(
            (await s.execute(select(m.Alert).where(m.Alert.resolved_at.is_(None)))).scalars()
        )


def failed(decision) -> set[str]:  # type: ignore[no-untyped-def]
    return {c.name for c in decision.checks if not c.passed}


# ------------------------------------------------------------------ reconciliación


async def test_clean_state_reconciles_ok() -> None:
    h = await Harness.create()
    await enter(h)
    report = await reconcile(h.manager, baseline=ZERO_BASELINE)
    assert report.ok and report.discrepancies == ()
    assert (await h.risk_state()).latched_reasons == []


async def test_ghost_position_blocks_entries_and_alerts() -> None:
    h = await Harness.create()
    await enter(h)
    h.adapter._balances["BTC"] = Decimal(0)  # el exchange ya no tiene lo que la BD cree tener

    report = await reconcile(h.manager, baseline=ZERO_BASELINE)

    assert [d.kind for d in report.discrepancies] == ["ghost_position"]
    state = await h.risk_state()
    assert any("ghost_position" in r for r in state.latched_reasons)
    decision = await h.decide(make_signal("ETHUSDT", "3000", "2985", "3100"))
    assert not decision.approved and "bloqueos" in failed(decision)
    crit = [a for a in await alerts() if a.code == "reconciliation_ghost_position"]
    assert crit and crit[0].severity == "CRITICAL" and crit[0].recommended_action


async def test_untracked_balance_detected() -> None:
    h = await Harness.create()
    h.adapter._balances["ETH"] = Decimal("0.5")
    report = await reconcile(h.manager, baseline=ZERO_BASELINE)
    assert [d.kind for d in report.discrepancies] == ["untracked_balance"]
    # Con línea base que lo justifica (cuenta de testnet con fondos previos) no hay discrepancia.
    ok = await reconcile(h.manager, baseline={"BTC": Decimal(0), "ETH": Decimal("0.5")})
    assert ok.ok


async def test_orphan_provider_order_detected() -> None:
    h = await Harness.create()
    await h.adapter.submit(
        OrderRequest(
            "bt-orphan", "BTCUSDT", Side.BUY, OrderType.LIMIT, Decimal("0.001"), Decimal("70000")
        )
    )
    report = await reconcile(h.manager, baseline=ZERO_BASELINE)
    assert [d.kind for d in report.discrepancies] == ["orphan_order"]


async def test_foreign_client_ids_are_ignored() -> None:
    h = await Harness.create()
    await h.adapter.submit(
        OrderRequest(
            "manual-1", "BTCUSDT", Side.BUY, OrderType.LIMIT, Decimal("0.001"), Decimal("70000")
        )
    )
    assert (await reconcile(h.manager, baseline=ZERO_BASELINE)).ok


async def test_duplicate_open_positions_detected() -> None:
    h = await Harness.create()
    await enter(h)
    async with db.session_scope() as s:
        pos = (await s.execute(select(m.Position))).scalar_one()
        s.add(
            m.Position(
                mode=MODE, symbol=pos.symbol, strategy_id="x", status="OPEN", quantity=pos.quantity,
                entry_quantity=pos.quantity, avg_entry_price=pos.avg_entry_price, opened_at=pos.opened_at,
            )
        )  # fmt: skip
    report = await reconcile(h.manager, baseline=ZERO_BASELINE)
    assert "duplicate_position" in {d.kind for d in report.discrepancies}


async def test_provider_unreachable_is_reported_without_latching() -> None:
    h = await Harness.create()

    async def boom() -> list:  # type: ignore[type-arg]
        from app.execution.adapter import AmbiguousResponseError

        raise AmbiguousResponseError("sin red")

    h.adapter.open_orders = boom  # type: ignore[method-assign]
    report = await reconcile(h.manager, baseline=ZERO_BASELINE)
    assert [d.kind for d in report.discrepancies] == ["provider_unreachable"]
    assert (await h.risk_state()).latched_reasons == []  # sin red no es evidencia de desajuste


async def test_resolved_discrepancy_clears_alert_but_latch_needs_manual_resume() -> None:
    h = await Harness.create()
    await enter(h)
    good_qty = h.adapter._balances["BTC"]
    h.adapter._balances["BTC"] = Decimal(0)
    await reconcile(h.manager, baseline=ZERO_BASELINE)
    h.adapter._balances["BTC"] = good_qty  # se corrige la causa

    again = await reconcile(h.manager, baseline=ZERO_BASELINE)
    assert again.ok
    assert [a for a in await alerts() if a.code.startswith("reconciliation_")] == []
    assert (await h.risk_state()).latched_reasons != []  # el bloqueo NO se levanta solo

    result = await resume(h.manager, "tester", GOOD)
    assert result.ok
    assert (await h.risk_state()).latched_reasons == []


# ------------------------------------------------------------------ recuperación tras reinicio


async def test_restart_adopts_order_submitted_but_unacknowledged() -> None:
    """Caída del proceso tras enviar y antes de procesar la respuesta."""
    h = await Harness.create()
    sig = make_signal()
    decision = await h.decide(sig)
    h.adapter.inject(Fault.TIMEOUT_AFTER)
    h.adapter.inject(Fault.QUERY_TIMEOUT)
    order = await h.manager.submit_entry(sig, decision)
    assert order.status == OrderStatus.UNKNOWN.value

    new_manager = h.restart()  # proceso nuevo: solo conserva la BD y el exchange
    report = await reconcile(new_manager, baseline=ZERO_BASELINE)
    assert report.ok
    async with db.session_scope() as s:
        row = await s.get(m.OrderRow, order.id)
        positions = (await s.execute(select(m.Position))).scalars().all()
    assert row is not None and row.status == OrderStatus.FILLED.value
    assert len(positions) == 1 and positions[0].quantity == row.filled_qty


async def test_restart_discards_order_that_never_reached_provider() -> None:
    h = await Harness.create()
    sig = make_signal()
    decision = await h.decide(sig)
    h.adapter.inject(Fault.TIMEOUT_BEFORE)
    h.adapter.inject(Fault.QUERY_TIMEOUT)
    order = await h.manager.submit_entry(sig, decision)
    assert order.status == OrderStatus.UNKNOWN.value
    report = await reconcile(h.restart(), baseline=ZERO_BASELINE)
    assert report.ok
    async with db.session_scope() as s:
        row = await s.get(m.OrderRow, order.id)
        assert row is not None and row.status == OrderStatus.REJECTED.value
        assert (await s.execute(select(m.Position))).first() is None


async def test_risk_state_survives_restart() -> None:
    h = await Harness.create()
    await pause_entries(MODE, "tester")
    await kill_switch(h.manager, "tester")
    fresh_view = await h.risk_state()  # lectura nueva desde BD
    assert fresh_view.paused and fresh_view.kill_switch_active
    decision = await h.decide(make_signal())
    assert not decision.approved and {"kill_switch", "pausa"} <= failed(decision)


# ------------------------------------------------------------------ pausa / kill switch / reanudar


async def test_pause_blocks_entries_but_positions_are_still_managed() -> None:
    h = await Harness.create()
    await enter(h)
    await pause_entries(MODE, "tester")
    decision = await h.decide(make_signal("ETHUSDT", "3000", "2985", "3100"))
    assert not decision.approved and "pausa" in failed(decision)

    async with db.session_scope() as s:
        pos = (await s.execute(select(m.Position))).scalar_one()
    exit_order = await h.manager.submit_exit(pos.id, "EXIT_STOP")  # sigue pudiendo salir
    assert exit_order is not None and exit_order.status == OrderStatus.FILLED.value


async def test_kill_switch_default_cancels_pending_entries_but_keeps_positions() -> None:
    h = await Harness.create()
    await enter(h)  # posición abierta en BTC
    eth = make_signal("ETHUSDT", "3000", "2985", "3100")
    pending = await h.manager.submit_entry(eth, await h.decide(eth), limit_price=Decimal("2990.00"))
    assert pending.status == OrderStatus.ACKNOWLEDGED.value

    result = await kill_switch(h.manager, "tester")

    assert result.ok
    async with db.session_scope() as s:
        pending_row = await s.get(m.OrderRow, pending.id)
        pos = (await s.execute(select(m.Position))).scalar_one()
        assert pending_row is not None and pending_row.status == OrderStatus.CANCELED.value
        assert pos.status == "OPEN"  # NO se liquida por defecto
    assert (await h.risk_state()).kill_switch_active
    assert any(a.code == "kill_switch" for a in await alerts())
    decision = await h.decide(make_signal("ETHUSDT", "3000", "2985", "3100"))
    assert "kill_switch" in failed(decision)


async def test_kill_switch_with_explicit_liquidation_policy() -> None:
    h = await Harness.create()
    await enter(h)
    await kill_switch(h.manager, "tester", KillSwitchPolicy(liquidate_positions=True))
    async with db.session_scope() as s:
        pos = (await s.execute(select(m.Position))).scalar_one()
        sells = (
            (await s.execute(select(m.OrderRow).where(m.OrderRow.side == "SELL"))).scalars().all()
        )
    assert pos.status == "CLOSED" and pos.exit_reason == "emergency"
    assert len(sells) == 1


async def test_kill_switch_is_audited_and_blocks_even_if_cancel_fails() -> None:
    h = await Harness.create()
    eth = make_signal("ETHUSDT", "3000", "2985", "3100")
    pending = await h.manager.submit_entry(eth, await h.decide(eth), limit_price=Decimal("2990.00"))
    h.adapter.inject(Fault.QUERY_TIMEOUT, count=2)  # la cancelación y su consulta fallan
    await kill_switch(h.manager, "tester")
    assert (await h.risk_state()).kill_switch_active  # el bloqueo no depende de la cancelación
    async with db.session_scope() as s:
        audit = (
            (await s.execute(select(m.AuditLog).where(m.AuditLog.action == "bot.kill_switch")))
            .scalars()
            .all()
        )
        row = await s.get(m.OrderRow, pending.id)
    assert len(audit) == 1 and audit[0].actor == "tester"
    assert row is not None and row.status == OrderStatus.UNKNOWN.value  # pendiente de resolver


async def test_resume_is_rejected_with_reasons() -> None:
    h = await Harness.create()
    await kill_switch(h.manager, "tester")
    bad = ResumeContext(
        connections_ok=False, data_ready=False, data_reason="datos obsoletos", equity=Decimal(500)
    )
    result = await resume(h.manager, "tester", bad, clear_kill_switch=True)
    assert not result.ok
    joined = " | ".join(result.reasons)
    assert "conexiones" in joined and "datos obsoletos" in joined
    assert (await h.risk_state()).kill_switch_active  # sigue activo

    no_confirm = await resume(h.manager, "tester", GOOD)
    assert not no_confirm.ok and "kill switch" in no_confirm.reasons[0]

    ok = await resume(h.manager, "tester", GOOD, clear_kill_switch=True)
    assert ok.ok
    state = await h.risk_state()
    assert not state.kill_switch_active and not state.paused
    async with db.session_scope() as s:
        actions = (
            (await s.execute(select(m.AuditLog.action).order_by(m.AuditLog.id))).scalars().all()
        )
    assert "bot.resume_rejected" in actions and actions[-1] == "bot.resume"


async def test_resume_refused_when_drawdown_limit_hit_or_unknown_orders() -> None:
    h = await Harness.create()
    h.adapter.inject(Fault.TIMEOUT_AFTER)
    h.adapter.inject(Fault.QUERY_TIMEOUT, count=3)
    sig = make_signal()
    unknown = await h.manager.submit_entry(sig, await h.decide(sig))
    assert unknown.status == OrderStatus.UNKNOWN.value
    await pause_entries(MODE, "tester")

    low_equity = ResumeContext(True, True, "ok", equity=Decimal("474"))  # drawdown 26 ≥ 25
    result = await resume(h.manager, "tester", low_equity)
    assert not result.ok and any("drawdown" in r for r in result.reasons)
    assert any("desconocido" in r or "order_unknown" in r for r in result.reasons)
    assert (await h.risk_state()).paused  # sigue pausado
