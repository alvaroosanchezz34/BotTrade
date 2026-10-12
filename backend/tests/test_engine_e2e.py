"""Flujo completo del engine: datos → señal → riesgo → orden simulada → ejecución → BD."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import select

from app.core.domain import Candle, Quote
from app.execution.order_manager import OrderManager
from app.execution.simulated import SimulatedExecution
from app.market_data import store
from app.persistence import db
from app.persistence import models as m
from app.risk.engine import RiskEngine
from app.risk.limits import RiskLimits
from app.risk.sizing import CostModel
from app.services import engine as engine_mod
from app.services.engine import Engine, EngineConfig
from app.strategies.base import FilterParams, Strategy, StrategyParams
from tests.exec_helpers import RULES
from tests.factories import candle

MODE = "PAPER"
T0 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
BASE = Decimal("80000")


class ScriptedParams(StrategyParams):
    filter: FilterParams = FilterParams(enabled=False)
    max_hold_bars: int = 600
    trigger_pct: float = 0.001


class ScriptedStrategy(Strategy[ScriptedParams]):
    """Entra cuando el cierre sube más de ``trigger_pct`` respecto a la vela anterior."""

    id = "scripted"
    version = "1.0"
    name = "Scripted"
    description = "Estrategia determinista de prueba"
    Params = ScriptedParams

    @property
    def warmup(self) -> int:
        return 5

    def _evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        ret = df["close"].pct_change()
        out = pd.DataFrame(index=df.index)
        out["enter"] = ret > self.params.trigger_pct
        out["exit"] = False
        out["stop"] = df["close"] * 0.995
        out["target"] = df["close"] * 1.01
        out["score"] = 1.0
        out["reason"] = "subida brusca de prueba"
        return out


class ScriptedTwo(ScriptedStrategy):
    id = "scripted2"
    name = "Scripted 2"

    def _evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = super()._evaluate(df)
        out["score"] = 2.0  # gana la coordinación
        return out


@dataclass
class FakeFeed:
    connected: bool = True
    quotes: dict[str, Quote] = field(default_factory=dict)
    ready: bool = True
    reason: str = "ok"

    def readiness(self, symbol: str, interval: str) -> tuple[bool, str]:
        return (self.ready, "ok" if self.ready else self.reason)


@dataclass
class World:
    engine: Engine
    adapter: SimulatedExecution
    feed: FakeFeed
    now: list[datetime]
    i: int = 0

    async def push(self, close: str, *, low: str | None = None, high: str | None = None,
                   persist: bool = True) -> Candle:  # fmt: skip
        """Genera la siguiente vela 1m, la persiste (como hace el feed) y la procesa."""
        c = candle(
            T0 + timedelta(minutes=self.i), o=close, c=close, h=high or str(Decimal(close) + 1),
            l=low or str(Decimal(close) - 1), v="500",
        )  # fmt: skip
        self.i += 1
        self.now[0] = c.close_time + timedelta(milliseconds=1)
        if persist:
            async with db.session_scope() as s:
                await store.save_candles(s, [c])
        await self.engine.on_candle(c)
        return c


async def make_world(
    monkeypatch: pytest.MonkeyPatch, *, strategies: tuple[str, ...] = ("scripted",),
    adapter: SimulatedExecution | None = None, start: bool = True, feed: FakeFeed | None = None,
) -> World:  # fmt: skip
    monkeypatch.setitem(engine_mod.STRATEGIES, "scripted", ScriptedStrategy)
    monkeypatch.setitem(engine_mod.STRATEGIES, "scripted2", ScriptedTwo)
    costs = CostModel()
    now = [T0]
    adapter = adapter or SimulatedExecution(
        costs=costs,
        rules=RULES,
        balances={"USDT": Decimal(500), "BTC": Decimal(0), "ETH": Decimal(0)},
        clock=lambda: now[0],
    )
    feed = feed or FakeFeed()
    risk = RiskEngine(RiskLimits(), costs)
    manager = OrderManager(mode=MODE, adapter=adapter, rules=RULES, risk_engine=risk)
    eng = Engine(
        config=EngineConfig(mode=MODE, symbols=["BTCUSDT"], reference_capital=Decimal(500)),
        manager=manager,
        feed=feed,
        strategy_builder=lambda sid, params: engine_mod.STRATEGIES[sid](params),
        clock=lambda: now[0],
    )
    w = World(eng, adapter, feed, now)
    if start:
        await eng.start()
        for sid in strategies:
            await command("ENABLE_STRATEGY", {"strategy_id": sid})
        await eng.process_commands()
    return w


async def command(name: str, payload: dict[str, Any] | None = None) -> None:
    async with db.session_scope() as s:
        s.add(m.BotCommand(created_by="admin", command=name, payload=payload or {}))


async def warm(w: World, n: int = 10) -> None:
    for _ in range(n):
        await w.push("80000")


async def rows(model: type, *order: Any) -> list[Any]:
    async with db.session_scope() as s:
        q = select(model)
        if order:
            q = q.order_by(*order)
        return list((await s.execute(q)).scalars())


# ------------------------------------------------------------------ camino feliz


async def test_full_pipeline_signal_to_closed_trade_with_full_traceability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = await make_world(monkeypatch)
    await warm(w)
    assert await rows(m.SignalRow) == []  # nada se mueve: no hay señales

    await w.push("80100")  # +0,125 % → señal

    signals = await rows(m.SignalRow)
    decisions = await rows(m.RiskDecisionRow)
    orders = await rows(m.OrderRow)
    assert len(signals) == len(decisions) == len(orders) == 1
    sig, dec, order = signals[0], decisions[0], orders[0]
    assert sig.status == "APPROVED" and dec.approved and dec.signal_id == sig.id
    assert order.status == "FILLED" and order.purpose == "ENTRY"
    assert order.signal_id == sig.id and order.risk_decision_id == dec.id
    assert order.strategy_id == "scripted" and order.strategy_version == "1.0"
    [pos] = await rows(m.Position)
    assert pos.status == "OPEN" and pos.stop_price == sig.stop_price
    # Riesgo: la pérdida si salta el stop no supera el presupuesto de 1,25 USDT.
    assert dec.risk_amount <= Decimal("1.25")

    # Precio cae por debajo del stop: salida a mercado y posición cerrada.
    stop = pos.stop_price
    assert stop is not None
    w.adapter.set_price("BTCUSDT", stop - 10)
    await w.push(str(stop - 10), low=str(stop - 20))
    [pos] = await rows(m.Position)
    assert pos.status == "CLOSED" and pos.exit_reason == "stop"
    assert pos.realized_pnl is not None and pos.realized_pnl < 0
    # La pérdida neta realizada ronda el riesgo presupuestado (el stop se ejecuta con deslizamiento).
    assert abs(pos.realized_pnl) < Decimal("2.0")
    state = (await rows(m.RiskState))[0]
    assert state.consecutive_losses == 1

    snap = await w.engine.snapshot()
    assert snap.equity - Decimal(500) == pos.realized_pnl  # contabilidad cuadra
    await w.engine.save_balance_snapshot()
    await w.engine.heartbeat()
    assert len(await rows(m.BalanceSnapshot)) == 1
    [hb] = await rows(m.EngineHeartbeat)
    assert hb.mode == MODE and hb.state["connected"] is True


async def test_target_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    w = await make_world(monkeypatch)
    await warm(w)
    await w.push("80100")
    [pos] = await rows(m.Position)
    target = pos.target_price
    assert target is not None
    w.adapter.set_price("BTCUSDT", target + 50)
    await w.push(str(target + 50), high=str(target + 60))
    closed = [p for p in await rows(m.Position) if p.status == "CLOSED"]
    assert len(closed) == 1 and closed[0].exit_reason == "target"
    assert closed[0].realized_pnl is not None and closed[0].realized_pnl > 0


async def test_time_exit_after_max_hold_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    w = await make_world(monkeypatch)
    async with db.session_scope() as s:
        row = await s.get(m.Strategy, "scripted")
        assert row is not None
        row.params = {**row.params, "max_hold_bars": 2}
    await warm(w)
    await w.push("80100")
    assert [p.status for p in await rows(m.Position)] == ["OPEN"]
    await w.push("80100")  # 1 vela
    assert [p.status for p in await rows(m.Position)] == ["OPEN"]
    await w.push("80100")  # 2 velas
    [pos] = await rows(m.Position)
    assert pos.status == "CLOSED" and pos.exit_reason == "time"


async def test_stop_and_target_in_same_bar_assumes_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    w = await make_world(monkeypatch)
    await warm(w)
    await w.push("80100")
    [pos] = await rows(m.Position)
    assert pos.stop_price and pos.target_price
    await w.push("80100", low=str(pos.stop_price - 5), high=str(pos.target_price + 5))
    [pos] = await rows(m.Position)
    assert pos.exit_reason == "stop"


async def test_live_quote_triggers_stop_without_waiting_for_candle_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = await make_world(monkeypatch)
    await warm(w)
    await w.push("80100")
    [pos] = await rows(m.Position)
    assert pos.stop_price is not None
    await w.engine.on_quote(Quote("BTCUSDT", pos.stop_price - 1, pos.stop_price + 1, w.now[0], "t"))
    [pos] = await rows(m.Position)
    assert pos.status == "CLOSED" and pos.exit_reason == "stop"


# ------------------------------------------------------------------ bloqueos


async def test_no_signals_when_data_is_not_ready_but_exits_still_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = await make_world(monkeypatch)
    await warm(w)
    w.feed.ready, w.feed.reason = False, "datos obsoletos"
    await w.push("80100")
    assert await rows(m.SignalRow) == [] and await rows(m.OrderRow) == []

    w.feed.ready = True
    await w.push("80300")  # +0,25 % → entrada
    [pos] = await rows(m.Position)
    w.feed.ready, w.feed.reason = False, "websocket desconectado"
    assert pos.stop_price is not None
    w.adapter.set_price("BTCUSDT", pos.stop_price - 10)
    await w.push(str(pos.stop_price - 10), low=str(pos.stop_price - 20))
    [pos] = await rows(m.Position)
    assert pos.status == "CLOSED"  # la gestión de la posición continúa con datos malos


async def test_candle_not_persisted_is_never_evaluated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin look-ahead ni datos a medias: si la vela no está en BD, no hay señal."""
    w = await make_world(monkeypatch)
    await warm(w)
    await w.push("80100", persist=False)
    assert await rows(m.SignalRow) == []


async def test_open_candle_and_unknown_symbol_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    w = await make_world(monkeypatch)
    await warm(w)
    open_c = candle(T0 + timedelta(minutes=99), o="80500", c="80500", closed=False)
    other = candle(T0 + timedelta(minutes=99), symbol="DOGEUSDT", o="1", c="1")
    await w.engine.on_candle(open_c)
    await w.engine.on_candle(other)
    assert await rows(m.SignalRow) == []


async def test_signal_before_first_reconciliation_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = await make_world(monkeypatch, start=False)
    await w.engine.start()  # reconcilia
    w.engine.last_reconcile = None  # simulamos arranque sin reconciliar
    await command("ENABLE_STRATEGY", {"strategy_id": "scripted"})
    await w.engine.process_commands()
    await warm(w)
    await w.push("80100")
    [sig] = await rows(m.SignalRow)
    [dec] = await rows(m.RiskDecisionRow)
    assert sig.status == "REJECTED" and not dec.approved
    assert any(c["name"] == "reconciliación" and not c["passed"] for c in dec.checks)
    assert await rows(m.OrderRow) == []


async def test_risk_rejection_is_persisted_with_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    w = await make_world(monkeypatch)
    await command("PAUSE")
    await w.engine.process_commands()
    await warm(w)
    await w.push("80100")
    [sig] = await rows(m.SignalRow)
    [dec] = await rows(m.RiskDecisionRow)
    assert sig.status == "REJECTED"
    assert [c["detail"] for c in dec.checks if c["name"] == "pausa"] == ["entradas pausadas"]
    assert await rows(m.OrderRow) == []


async def test_coordination_one_entry_per_symbol_and_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    w = await make_world(monkeypatch, strategies=("scripted", "scripted2"))
    await warm(w)
    await w.push("80100")
    signals = {s.strategy_id: s for s in await rows(m.SignalRow)}
    assert signals["scripted2"].status == "APPROVED"
    assert signals["scripted"].status == "SUPERSEDED"
    [order] = await rows(m.OrderRow)
    assert order.strategy_id == "scripted2"
    assert len(await rows(m.Position)) == 1


async def test_disabled_strategy_generates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    w = await make_world(monkeypatch)
    await command("DISABLE_STRATEGY", {"strategy_id": "scripted"})
    await w.engine.process_commands()
    await warm(w)
    await w.push("80100")
    assert await rows(m.SignalRow) == []


# ------------------------------------------------------------------ comandos del panel


async def test_commands_are_applied_rejected_and_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    w = await make_world(monkeypatch)
    await command("ENABLE_STRATEGY", {"strategy_id": "no-existe"})
    await command("KILL_SWITCH")
    await w.engine.process_commands()
    cmds = await rows(m.BotCommand, m.BotCommand.created_at)
    by_cmd = {(c.command, c.payload.get("strategy_id")): c for c in cmds}
    assert by_cmd[("ENABLE_STRATEGY", "no-existe")].status == "REJECTED"
    assert by_cmd[("KILL_SWITCH", None)].status == "APPLIED"
    assert all(c.processed_at for c in cmds)
    assert (await rows(m.RiskState))[0].kill_switch_active

    # Reanudar con los datos caídos se rechaza y explica el motivo.
    w.feed.connected, w.feed.ready, w.feed.reason = False, False, "websocket desconectado"
    await command("RESUME", {"clear_kill_switch": True})
    await w.engine.process_commands()
    last = (await rows(m.BotCommand, m.BotCommand.created_at))[-1]
    assert last.status == "REJECTED" and "conexiones" in (last.result or "")

    w.feed.connected, w.feed.ready = True, True
    await command("RESUME", {"clear_kill_switch": True})
    await w.engine.process_commands()
    last = (await rows(m.BotCommand, m.BotCommand.created_at))[-1]
    assert last.status == "APPLIED", last.result
    assert not (await rows(m.RiskState))[0].kill_switch_active
    actions = [a.action for a in await rows(m.AuditLog, m.AuditLog.id)]
    assert {"strategy.enable", "bot.kill_switch", "bot.resume_rejected", "bot.resume"} <= set(
        actions
    )


# ------------------------------------------------------------------ reinicio


async def test_restart_restores_paper_ledger_and_keeps_managing_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = await make_world(monkeypatch)
    await warm(w)
    await w.push("80100")
    [pos] = await rows(m.Position)

    # «Reinicio»: proceso nuevo con exchange simulado en blanco; todo sale de la BD.
    blank = SimulatedExecution(
        costs=CostModel(), rules=RULES, balances={"USDT": Decimal(500), "BTC": Decimal(0)}
    )
    w2 = await make_world(monkeypatch, adapter=blank, start=False)
    w2.i = w.i
    await w2.engine.start()
    assert w2.engine.last_reconcile is not None and w2.engine.last_reconcile.ok
    bal = await blank.balances()
    assert bal["BTC"] == pos.quantity  # saldo reconstruido desde las posiciones
    alerts = [a.code for a in await rows(m.Alert)]
    assert "engine_unexpected_restart" in alerts  # no hubo parada ordenada

    assert pos.stop_price is not None
    blank.set_price("BTCUSDT", pos.stop_price - 10)
    await w2.push(str(pos.stop_price - 10), low=str(pos.stop_price - 20))
    [pos] = await rows(m.Position)
    assert pos.status == "CLOSED"  # el stop sigue vigilándose tras reiniciar


async def test_kill_switch_state_survives_restart_and_blocks_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = await make_world(monkeypatch)
    await command("KILL_SWITCH")
    await w.engine.process_commands()
    w2 = await make_world(monkeypatch, start=False)
    w2.i = w.i
    await w2.engine.start()
    await command("ENABLE_STRATEGY", {"strategy_id": "scripted"})
    await w2.engine.process_commands()
    await warm(w2)
    await w2.push("80100")
    [dec] = await rows(m.RiskDecisionRow)
    assert not dec.approved
    assert any(c["name"] == "kill_switch" and not c["passed"] for c in dec.checks)
    assert await rows(m.OrderRow) == []


async def test_daily_loss_limit_blocks_further_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dos pérdidas seguidas llevan la pérdida diaria al límite (5 USDT) y se bloquea."""
    w = await make_world(monkeypatch)
    await warm(w)
    price = Decimal("80100")
    for _ in range(6):
        await w.push(str(price))
        positions = [p for p in await rows(m.Position) if p.status == "OPEN"]
        if not positions:
            break
        stop = positions[0].stop_price
        assert stop is not None
        crash = stop - 400  # gap bajista: la salida es peor que el stop
        w.adapter.set_price("BTCUSDT", crash)
        await w.push(str(crash), low=str(crash - 10))
        price = crash + Decimal(10) + crash * Decimal("0.0015")
        w.adapter.set_price("BTCUSDT", price)
    decisions = await rows(m.RiskDecisionRow, m.RiskDecisionRow.decided_at)
    rejected = [d for d in decisions if not d.approved]
    assert rejected, "se esperaba un bloqueo por pérdida diaria o drawdown"
    names = {c["name"] for d in rejected for c in d.checks if not c["passed"]}
    assert names & {"pérdida_diaria", "drawdown", "bloqueos", "pérdidas_consecutivas"}
