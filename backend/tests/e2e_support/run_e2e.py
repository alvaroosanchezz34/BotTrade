"""Arnés de pruebas extremo a extremo (SOLO para tests; BD ``bottrade_e2e`` dedicada).

Levanta, en un único proceso:
  * el API real (FastAPI + uvicorn) en 127.0.0.1:8000;
  * un Engine real con exchange simulado y un feed estático (sin red) que procesa los
    comandos del panel y emite latidos;
tras sembrar la BD con operaciones generadas por el flujo real de órdenes (no a mano).

Los datos de ``bottrade_e2e`` son FIXTURES de test: la BD se destruye y recrea en cada
arranque y nunca se usa en desarrollo ni producción.

Uso:  BOTTRADE_DATABASE_URL=postgresql+asyncpg://.../bottrade_e2e python -m tests.e2e_support.run_e2e
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

import numpy as np
import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.api.main import create_app
from app.auth.service import create_user
from app.core.config import Settings
from app.core.domain import Quote, utcnow
from app.execution.order_manager import OrderManager
from app.execution.simulated import Fault, SimulatedExecution
from app.market_data import store
from app.persistence import db
from app.persistence import models as m
from app.risk.engine import RiskEngine
from app.risk.limits import RiskLimits
from app.risk.sizing import CostModel
from app.services.engine import Engine, EngineConfig
from app.services.lab_worker import run_worker
from tests.exec_helpers import RULES, Harness, make_signal
from tests.factories import candle

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PASSWORD = "e2e-password-123"
PORT = int(os.environ.get("E2E_API_PORT", "8000"))


@dataclass
class StaticFeed:
    connected: bool = True
    quotes: dict[str, Quote] = field(default_factory=dict)

    def readiness(self, symbol: str, interval: str) -> tuple[bool, str]:
        return True, "ok"


def alembic_cfg(url: str) -> Config:
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "migrations"))
    cfg.attributes["database_url"] = url
    return cfg


async def recreate_schema(url: str) -> None:
    engine = db.init_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))

    def _up(sync_conn: object) -> None:
        cfg = alembic_cfg(url)
        cfg.attributes["connection"] = sync_conn
        command.upgrade(cfg, "head")

    async with engine.begin() as conn:
        await conn.run_sync(_up)


def minute_floor() -> object:
    now = utcnow()
    return now.replace(second=0, microsecond=0)


async def seed_candles() -> None:
    last_open = minute_floor() - timedelta(minutes=1)  # type: ignore[operator]
    for symbol, base in (("BTCUSDT", 80000), ("ETHUSDT", 3000)):
        cs = []
        for i in range(400):
            t = last_open - timedelta(minutes=399 - i)
            px = Decimal(
                str(round(base + base * 0.002 * ((i % 40) - 20) / 20 + i * base * 1e-6, 2))
            )
            cs.append(candle(t, symbol=symbol, o=px, c=px + Decimal("3"), v="12"))
        # 5m: paseo aleatorio de ~9 días (suficiente para el laboratorio, que exige ≥ 2000 velas).
        rng = np.random.default_rng(7 if symbol == "BTCUSDT" else 8)
        five = []
        px = float(base)
        end5 = last_open.replace(second=0, microsecond=0)
        end5 = end5.replace(minute=end5.minute - end5.minute % 5)
        for i in range(2600):
            t = end5 - timedelta(minutes=5 * (2599 - i))
            o = px
            px = px * float(np.exp(rng.normal(0, 0.0012)))
            hi = max(o, px) * (1 + abs(float(rng.normal(0, 0.0006))))
            lo = min(o, px) * (1 - abs(float(rng.normal(0, 0.0006))))
            five.append(
                candle(t, symbol=symbol, interval="5m", o=str(round(o, 2)), c=str(round(px, 2)),
                       h=str(round(hi, 2)), l=str(round(lo, 2)), v="40")
            )  # fmt: skip
        async with db.session_scope() as s:
            await store.save_candles(s, cs)
            await store.save_candles(s, five)


async def seed_trading() -> None:
    """Operaciones generadas por el flujo REAL: señal → riesgo → orden → fill → posición."""
    h = await Harness.create()
    # 1) Operación cerrada con pérdida (stop).
    sig = make_signal()
    await h.manager.submit_entry(sig, await h.decide(sig))
    async with db.session_scope() as s:
        from sqlalchemy import select

        pos = (await s.execute(select(m.Position))).scalar_one()
    h.adapter.set_price("BTCUSDT", Decimal(79500))
    await h.manager.submit_exit(pos.id, "EXIT_STOP")
    # 2) Una orden rechazada por el exchange simulado.
    h.adapter.inject(Fault.REJECT)
    rej = make_signal("ETHUSDT", "3000", "2985", "3100")
    await h.manager.submit_entry(rej, await h.decide(rej))
    # 3) Una posición abierta en ETH.
    h.adapter.set_price("ETHUSDT", Decimal(3000))
    eth = make_signal("ETHUSDT", "3000", "2985", "3100")
    await h.manager.submit_entry(eth, await h.decide(eth))
    async with db.session_scope() as s:
        run = m.BacktestRun(
            strategy_id="momentum", strategy_version="1.0", symbols=["BTCUSDT"], interval="1m",
            start=utcnow() - timedelta(days=30), end=utcnow() - timedelta(days=1), segment="test",
            params={}, cost_model={**CostModel().as_dict(), "limits": {"reference_capital": "500"}},
            data_hash="e2efixture0000", status="DONE",
            metrics={"net_pnl": -12.4, "trades": 41, "profit_factor": 0.83, "max_drawdown": 22.1},
            notes="DESCARTADA: fixture de test E2E, sin ventaja tras costes",
        )  # fmt: skip
        s.add(run)
        await s.flush()
        t0 = utcnow() - timedelta(days=20)
        for i, pnl in enumerate(["12.5", "-3.0", "-8.25", "4.0", "-1.5", "-6.0"]):
            s.add(
                m.BacktestTrade(
                    run_id=run.id, symbol="BTCUSDT", entry_time=t0 + timedelta(hours=i),
                    exit_time=t0 + timedelta(hours=i, minutes=20), entry_price=Decimal(80000),
                    exit_price=Decimal(80100), quantity=Decimal("0.002"), fees=Decimal("0.4"),
                    gross_pnl=Decimal(pnl) + Decimal("0.4"), net_pnl=Decimal(pnl), exit_reason="stop",
                )
            )  # fmt: skip


async def publish_feed_status() -> None:
    async with db.session_scope() as s:
        await store.set_connection_status(
            s, "e2e-static-feed", "UP", last_message_at=utcnow(), last_valid_data_at=utcnow(),
            detail={"fixture": True},
        )  # fmt: skip


async def tick_candles(stop: asyncio.Event) -> None:
    """Mantiene el mercado «fresco» durante la ejecución de los tests (simula el feed)."""
    await publish_feed_status()
    while not stop.is_set():
        await asyncio.sleep(20)
        last_open = minute_floor() - timedelta(minutes=1)  # type: ignore[operator]
        async with db.session_scope() as s:
            for symbol, base in (("BTCUSDT", 80000), ("ETHUSDT", 3000)):
                await store.save_candles(
                    s, [candle(last_open, symbol=symbol, o=base, c=base + 3, v="9")]
                )
        await publish_feed_status()


async def main() -> int:
    url = os.environ["BOTTRADE_DATABASE_URL"]
    if not url.rstrip("/").endswith("bottrade_e2e"):
        print("Por seguridad este arnés solo trabaja sobre la BD 'bottrade_e2e'", file=sys.stderr)
        return 2
    os.environ.setdefault("BOTTRADE_ENVIRONMENT", "development")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    await recreate_schema(url)
    async with db.session_scope() as s:
        await create_user(s, "e2e-admin", PASSWORD, role="admin")
        await create_user(s, "e2e-viewer", PASSWORD, role="viewer")
    await seed_candles()
    async with db.session_scope() as s:
        await store.save_instruments(s, RULES, "e2e-fixture")
    await seed_trading()

    costs = CostModel()
    adapter = SimulatedExecution(
        costs=costs,
        rules=RULES,
        balances={"USDT": Decimal(500), "BTC": Decimal(0), "ETH": Decimal(0)},
    )
    manager = OrderManager(
        mode="PAPER", adapter=adapter, rules=RULES, risk_engine=RiskEngine(RiskLimits(), costs)
    )
    engine = Engine(
        config=EngineConfig(
            mode="PAPER", symbols=["BTCUSDT", "ETHUSDT"], reference_capital=Decimal(500),
            heartbeat_interval=1.0, command_poll_interval=0.3, reconcile_interval=30.0,
        ),
        manager=manager,
        feed=StaticFeed(),
    )  # fmt: skip
    await engine.start()

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, manage_db=False), host="127.0.0.1", port=PORT, log_level="warning"
        )
    )
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(engine.run()),
        asyncio.create_task(tick_candles(stop)),
        asyncio.create_task(run_worker(stop, poll_seconds=0.5, workers=1)),
        asyncio.create_task(server.serve()),
    ]
    print(f"E2E listo en http://127.0.0.1:{PORT}", flush=True)
    await tasks[-1]
    stop.set()  # detiene también el worker del laboratorio
    engine.stop()
    await asyncio.gather(*tasks[:-1], return_exceptions=True)
    await db.dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
