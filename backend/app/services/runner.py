"""Ensamblado del proceso ``engine``: feed + adaptador + riesgo + engine, según el modo.

* PAPER: datos públicos reales (``*.binance.vision``) y exchange **simulado** local.
* TESTNET: datos y órdenes del **Binance Spot Testnet**. Valida permisos antes de operar.
* BACKTEST no corre aquí (usa ``research``): este proceso es para modo continuo.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from decimal import Decimal

from app.core.config import Settings, TradingMode
from app.core.domain import InstrumentRules
from app.core.money import bps
from app.core.signals import install_stop_handlers
from app.execution.adapter import ExecutionAdapter
from app.execution.order_manager import OrderManager
from app.execution.simulated import SimulatedExecution
from app.market_data import store
from app.market_data.feed import LiveMarketFeed
from app.persistence import db
from app.persistence.models import RiskLimitsRow
from app.providers.binance.rest import BinanceMarketDataClient
from app.providers.binance.testnet import BinanceSetupError, BinanceTestnetExecution
from app.risk.engine import RiskEngine
from app.risk.limits import RiskLimits
from app.risk.sizing import CostModel
from app.services.engine import Engine, EngineConfig

log = logging.getLogger(__name__)


def cost_model(settings: Settings) -> CostModel:
    return CostModel(
        taker_fee=settings.taker_fee_rate,
        maker_fee=settings.maker_fee_rate,
        slippage=bps(settings.slippage_bps),
    )


async def load_risk_limits(settings: Settings) -> RiskLimits:
    """Límites guardados en BD (auditados) o los de diseño con el capital configurado."""
    async with db.session_scope() as s:
        row = await s.get(RiskLimitsRow, "default")
    if row is not None:
        return RiskLimits.model_validate(row.values)
    return RiskLimits(reference_capital=settings.reference_capital)


async def run_engine(settings: Settings) -> int:
    mode = settings.trading_mode
    if mode is TradingMode.BACKTEST:
        log.error("El engine continuo solo admite PAPER o TESTNET; usa 'research' para BACKTEST")
        return 2

    db.init_engine(settings.database_url.get_secret_value())
    testnet = mode is TradingMode.TESTNET
    rest = BinanceMarketDataClient(
        settings.testnet_rest_url if testnet else settings.market_data_rest_url,
        source="binance-testnet-rest" if testnet else "binance-rest",
    )
    adapter: ExecutionAdapter
    exchange: BinanceTestnetExecution | None = None
    try:
        rules: dict[str, InstrumentRules] = await rest.exchange_info(settings.symbols)
        async with db.session_scope() as s:
            await store.save_instruments(s, rules, "binance-testnet" if testnet else "binance")
        costs = cost_model(settings)
        if testnet:
            assert settings.testnet_api_key and settings.testnet_api_secret  # validado en Settings
            exchange = BinanceTestnetExecution(
                base_url=settings.testnet_rest_url,
                api_key=settings.testnet_api_key.get_secret_value(),
                api_secret=settings.testnet_api_secret.get_secret_value(),
            )
            perms = await exchange.verify_permissions()
            log.info("credenciales del testnet verificadas", extra=perms)
            adapter = exchange
        else:
            quote_assets = {r.quote_asset for r in rules.values()}
            balances = {q: settings.reference_capital for q in quote_assets}
            balances |= {r.base_asset: Decimal(0) for r in rules.values()}
            adapter = SimulatedExecution(costs=costs, rules=rules, balances=balances)

        limits = await load_risk_limits(settings)
        manager = OrderManager(
            mode=mode.value, adapter=adapter, rules=rules, risk_engine=RiskEngine(limits, costs)
        )

        holder: dict[str, Engine] = {}

        async def on_candle(c):  # type: ignore[no-untyped-def]
            await holder["engine"].on_candle(c)

        async def on_quote(q):  # type: ignore[no-untyped-def]
            await holder["engine"].on_quote(q)

        feed = LiveMarketFeed(
            ws_url=settings.testnet_ws_url if testnet else settings.market_data_ws_url,
            symbols=settings.symbols,
            intervals=settings.intervals,
            rest=rest,
            on_candle=on_candle,
            on_quote=on_quote,
            stale_after=timedelta(seconds=settings.stale_data_seconds),
            name="binance-testnet-ws" if testnet else "binance-ws",
        )
        engine = Engine(
            config=EngineConfig(
                mode=mode.value,
                symbols=settings.symbols,
                reference_capital=settings.reference_capital,
                balance_tolerance_pct=Decimal("0.1") if testnet else Decimal(0),
            ),
            manager=manager,
            feed=feed,
        )
        holder["engine"] = engine

        await engine.start()
        loop = asyncio.get_running_loop()

        def shutdown() -> None:
            engine.stop()
            feed.stop()

        install_stop_handlers(loop, shutdown)
        log.info("engine en marcha", extra={"mode": mode.value, "symbols": settings.symbols})
        feed_task = asyncio.create_task(feed.run(), name="feed")
        engine_task = asyncio.create_task(engine.run(), name="engine-loops")
        done, _ = await asyncio.wait({feed_task, engine_task}, return_when=asyncio.FIRST_COMPLETED)
        engine.stop()
        feed.stop()
        for t in (feed_task, engine_task):
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(t, timeout=15)
        for t in done:
            if (exc := t.exception()) is not None:
                raise exc
        return 0
    except BinanceSetupError as exc:
        log.error("configuración de testnet inválida: %s", exc)
        return 1
    finally:
        if exchange is not None:
            await exchange.aclose()
        await rest.aclose()
        await db.dispose_engine()
