"""Pruebas contra los endpoints públicos reales. Ejecutar con ``pytest -m network``."""

import asyncio
import json
from datetime import timedelta

import pytest
from websockets.asyncio.client import connect

from app.core.domain import utcnow
from app.market_data.validation import check_series
from app.providers.binance.parsing import parse_ws_kline
from app.providers.binance.rest import BinanceMarketDataClient

pytestmark = pytest.mark.network

REST = "https://data-api.binance.vision"
WS = "wss://data-stream.binance.vision"


async def test_real_klines_are_clean_and_closed() -> None:
    client = BinanceMarketDataClient(REST)
    try:
        end = utcnow() - timedelta(minutes=2)
        candles = await client.klines_range("BTCUSDT", "1m", end - timedelta(minutes=1500), end)
        rules = await client.exchange_info(["BTCUSDT", "ETHUSDT"])
    finally:
        await client.aclose()
    assert len(candles) >= 1499
    rep = check_series(candles)
    assert rep.duplicates == 0 and rep.invalid == 0 and rep.open_candles == 0
    assert rules["BTCUSDT"].min_notional > 0 and rules["ETHUSDT"].step_size > 0


async def test_real_websocket_delivers_klines() -> None:
    url = f"{WS}/stream?streams=btcusdt@kline_1m/btcusdt@bookTicker"
    seen_kline = seen_book = False
    async with connect(url, open_timeout=10) as ws:
        async with asyncio.timeout(20):
            while not (seen_kline and seen_book):
                msg = json.loads(await ws.recv())
                data = msg["data"]
                if data.get("e") == "kline":
                    c = parse_ws_kline(data)
                    assert c.symbol == "BTCUSDT"
                    seen_kline = True
                elif "b" in data:
                    seen_book = True
