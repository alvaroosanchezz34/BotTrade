"""Arnés de tests de ejecución: exchange simulado + gestor de órdenes + riesgo en BD."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.domain import InstrumentRules, RiskDecision, Signal, SignalAction
from app.execution.accounting import account_snapshot, get_initial_capital
from app.execution.order_manager import OrderManager
from app.execution.simulated import SimulatedExecution
from app.persistence import db
from app.risk.engine import MarketContext, RiskEngine
from app.risk.limits import RiskLimits, RiskState
from app.risk.sizing import CostModel
from app.risk.store import load_or_create_risk_state, load_risk_state

MODE = "PAPER"
NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
CAPITAL = Decimal("500")
BTC = InstrumentRules(
    "BTCUSDT", "BTC", "USDT", Decimal("0.01"), Decimal("0.00001"), Decimal("0.00001"),
    Decimal("9000"), Decimal("5"),
)  # fmt: skip
ETH = InstrumentRules(
    "ETHUSDT", "ETH", "USDT", Decimal("0.01"), Decimal("0.0001"), Decimal("0.0001"),
    Decimal("9000"), Decimal("5"),
)  # fmt: skip
RULES = {"BTCUSDT": BTC, "ETHUSDT": ETH}


def make_signal(
    symbol: str = "BTCUSDT", ref: str = "80000", stop: str = "79600", target: str | None = "81000"
) -> Signal:
    return Signal(
        symbol=symbol,
        strategy_id="test-strategy",
        strategy_version="1",
        action=SignalAction.ENTER_LONG,
        created_at=NOW,
        reference_price=Decimal(ref),
        rationale="prueba",
        entry_conditions={"x": 1},
        invalidation="n/a",
        stop_price=Decimal(stop),
        target_price=Decimal(target) if target else None,
        params={},
        valid_until=NOW + timedelta(minutes=1),
    )


@dataclass
class Harness:
    adapter: SimulatedExecution
    manager: OrderManager
    engine: RiskEngine
    marks: dict[str, Decimal] = field(default_factory=dict)

    @classmethod
    async def create(cls, *, limits: RiskLimits | None = None) -> Harness:
        costs = CostModel()
        adapter = SimulatedExecution(
            costs=costs,
            rules=RULES,
            balances={"USDT": CAPITAL, "BTC": Decimal(0), "ETH": Decimal(0)},
        )
        adapter.set_price("BTCUSDT", Decimal("80000"))
        adapter.set_price("ETHUSDT", Decimal("3000"))
        engine = RiskEngine(limits or RiskLimits(), costs)
        manager = OrderManager(mode=MODE, adapter=adapter, rules=RULES, risk_engine=engine)
        h = cls(adapter, manager, engine, {"BTCUSDT": Decimal("80000"), "ETHUSDT": Decimal("3000")})
        async with db.session_scope() as s:
            await get_initial_capital(s, MODE, CAPITAL)  # cuenta, como hace el engine al arrancar
            await load_or_create_risk_state(s, MODE, CAPITAL)
        return h

    def restart(self) -> OrderManager:
        """Un «reinicio»: gestor nuevo, mismo exchange y misma BD."""
        return OrderManager(mode=MODE, adapter=self.adapter, rules=RULES, risk_engine=self.engine)

    async def snapshot(self):  # type: ignore[no-untyped-def]
        async with db.session_scope() as s:
            return await account_snapshot(
                s, MODE, initial_capital=CAPITAL, marks=self.marks, rules=RULES
            )

    async def risk_state(self) -> RiskState:
        async with db.session_scope() as s:
            state = await load_risk_state(s, MODE)
        assert state is not None
        return state

    async def decide(self, signal: Signal, *, market: MarketContext | None = None) -> RiskDecision:
        snap = await self.snapshot()
        state = await self.risk_state()
        decision = self.engine.evaluate(
            signal,
            now=NOW,
            state=state,
            portfolio=snap.portfolio,
            rules=RULES[signal.symbol],
            market=market or MarketContext(),
        )
        # update_latches puede haber modificado el estado: se persiste como hace el engine real.
        from app.risk.store import save_risk_state

        async with db.session_scope() as s:
            await save_risk_state(s, MODE, state)
        return decision
