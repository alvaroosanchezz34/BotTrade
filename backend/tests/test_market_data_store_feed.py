"""Persistencia y feed en vivo contra un servidor WebSocket local que imita a Binance."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from websockets.asyncio.server import ServerConnection, serve

from app.core.domain import Candle, utcnow
from app.market_data import store
from app.market_data.feed import LiveMarketFeed
from app.persistence import db
from app.persistence.models import Alert, ConnectionStatus, DataGap
from tests.factories import T0, candle, floor_minute, series, ws_kline

MIN = timedelta(minutes=1)


# ------------------------------------------------------------------ store


async def test_save_is_idempotent_and_skips_open_candles() -> None:
    data = series(T0, 5)
    async with db.session_scope() as s:
        assert await store.save_candles(s, [*data, candle(T0 + 5 * MIN, closed=False)]) == 5
    async with db.session_scope() as s:
        assert await store.save_candles(s, data) == 0
        last = await store.last_closed(s, "BTCUSDT", "1m")
    assert last is not None and last.open_time == data[-1].open_time
    assert last.close == data[-1].close


async def test_missing_ranges_sql() -> None:
    data = series(T0, 10)
    keep = [c for i, c in enumerate(data) if i not in (2, 3, 7)]
    async with db.session_scope() as s:
        await store.save_candles(s, keep)
        gaps = await store.missing_ranges(s, "BTCUSDT", "1m", T0 - MIN, T0 + 11 * MIN)
    assert [(g.start, g.end) for g in gaps] == [
        (T0 - MIN, T0 - MIN),
        (T0 + 2 * MIN, T0 + 3 * MIN),
        (T0 + 7 * MIN, T0 + 7 * MIN),
        (T0 + 10 * MIN, T0 + 11 * MIN),
    ]


async def test_load_frame_types() -> None:
    async with db.session_scope() as s:
        await store.save_candles(s, series(T0, 3))
        df = await store.load_frame(s, "BTCUSDT", "1m", T0, T0 + 2 * MIN)
    assert len(df) == 3
    assert str(df["close"].dtype) == "float64"
    assert str(df.index.tz) == "UTC"


# ------------------------------------------------------------------ feed en vivo


class FakeRest:
    """REST que sirve una 'verdad' de velas cerradas hasta ahora."""

    def __init__(self, truth: dict[tuple[str, str], list[Candle]]):
        self.truth = truth
        self.calls: list[tuple[str, Any, Any]] = []
        self.fail = False

    async def klines_range(self, symbol: str, interval: str, start: Any, end: Any) -> list[Candle]:
        self.calls.append((symbol, start, end))
        if self.fail:
            raise OSError("rest caído")
        return [c for c in self.truth.get((symbol, interval), []) if start <= c.open_time <= end]


class FakeBinanceWS:
    """Servidor WS local. ``script`` es una lista de sesiones; cada sesión, una lista de mensajes."""

    def __init__(self, sessions: list[list[dict[str, Any] | str]]):
        self.sessions = sessions
        self.connections = 0
        self.paths: list[str] = []

    async def handler(self, ws: ServerConnection) -> None:
        idx = self.connections
        self.connections += 1
        self.paths.append(ws.request.path if ws.request else "")
        script = self.sessions[idx] if idx < len(self.sessions) else []
        for msg in script:
            if msg == "CLOSE":
                await ws.close()
                return
            if msg == "WAIT":
                await asyncio.sleep(0.2)
                continue
            await ws.send(json.dumps(msg))
            await asyncio.sleep(0.01)
        await ws.wait_closed()  # se queda abierta y en silencio hasta que el cliente cierre


async def _run_feed(feed: LiveMarketFeed, until: Any, timeout: float = 8.0) -> None:
    task = asyncio.create_task(feed.run())
    try:
        async with asyncio.timeout(timeout):
            while not await until():
                await asyncio.sleep(0.05)
    finally:
        feed.stop()
        await asyncio.wait_for(task, 5)


def _times(candles: list[Candle]) -> set[Any]:
    return {c.open_time for c in candles}


def _recent_series(n: int) -> list[Candle]:
    """n velas cerradas que terminan en la última vela cerrada real."""
    last_open = floor_minute(utcnow()) - MIN
    return series(last_open - (n - 1) * MIN, n)


@pytest.fixture
async def ws_server():  # type: ignore[no-untyped-def]
    servers: list[Any] = []

    async def start(fake: FakeBinanceWS) -> str:
        srv = await serve(fake.handler, "127.0.0.1", 0)
        servers.append(srv)
        port = next(iter(srv.sockets)).getsockname()[1]
        return f"ws://127.0.0.1:{port}"

    yield start
    for srv in servers:
        srv.close()
        await srv.wait_closed()


def _feed(url: str, rest: FakeRest, received: list[Candle], **kw: Any) -> LiveMarketFeed:
    async def on_candle(c: Candle) -> None:
        received.append(c)

    return LiveMarketFeed(
        ws_url=url,
        symbols=["BTCUSDT"],
        intervals=["1m"],
        rest=rest,  # type: ignore[arg-type]
        on_candle=on_candle,
        backoff_base=0.05,
        backoff_max=0.1,
        watchdog_interval=3600,
        **kw,
    )


async def test_feed_catches_up_then_streams_closed_candles_only(ws_server: Any) -> None:
    truth = _recent_series(30)
    history, live = truth[:-2], truth[-2:]
    rest = FakeRest({("BTCUSDT", "1m"): history})
    fake = FakeBinanceWS([[ws_kline(live[0], closed=False), ws_kline(live[0]), ws_kline(live[1])]])
    url = await ws_server(fake)
    received: list[Candle] = []
    feed = _feed(url, rest, received)

    async def done() -> bool:
        return len(received) >= 2

    await _run_feed(feed, done)
    assert [c.open_time for c in received] == [c.open_time for c in live]
    assert "btcusdt@kline_1m" in fake.paths[0] and "btcusdt@bookTicker" in fake.paths[0]
    async with db.session_scope() as s:
        gaps = await store.missing_ranges(
            s, "BTCUSDT", "1m", truth[0].open_time, truth[-1].open_time
        )
        status = await s.get(ConnectionStatus, "binance-ws")
    assert gaps == []
    assert status is not None and status.last_valid_data_at == live[1].close_time


async def test_gap_in_stream_is_backfilled_via_rest(ws_server: Any) -> None:
    truth = _recent_series(20)
    rest = FakeRest({("BTCUSDT", "1m"): truth[:15]})
    # El WS salta de la vela 15 a la 18: faltan 16 y 17.
    fake = FakeBinanceWS([["WAIT", ws_kline(truth[15]), ws_kline(truth[18])]])
    url = await ws_server(fake)
    received: list[Candle] = []
    feed = _feed(url, rest, received)

    async def gap_filled() -> bool:
        if feed.connected:
            rest.truth[("BTCUSDT", "1m")] = truth  # REST ya conoce las velas perdidas
        return truth[18].open_time in _times(received)

    await _run_feed(feed, gap_filled)
    async with db.session_scope() as s:
        missing = await store.missing_ranges(
            s, "BTCUSDT", "1m", truth[0].open_time, truth[18].open_time
        )
        gap_rows = (await s.scalars(select(DataGap))).all()
    assert missing == []
    assert len(gap_rows) == 1 and gap_rows[0].resolved_at is not None
    assert (gap_rows[0].gap_start, gap_rows[0].gap_end) == (
        truth[16].open_time,
        truth[17].open_time,
    )
    assert feed.validators[("BTCUSDT", "1m")].open_gaps == []


async def test_unfillable_gap_blocks_signals(ws_server: Any) -> None:
    truth = _recent_series(10)
    rest = FakeRest({("BTCUSDT", "1m"): truth[:5]})
    fake = FakeBinanceWS([["WAIT", ws_kline(truth[5]), ws_kline(truth[9])]])
    url = await ws_server(fake)
    received: list[Candle] = []
    feed = _feed(url, rest, received)

    async def got_last() -> bool:
        if feed.connected:
            rest.fail = True  # tras el arranque, REST cae: el hueco no puede rellenarse
        last = feed.validators[("BTCUSDT", "1m")].last
        return last is not None and last.open_time == truth[9].open_time

    task = asyncio.create_task(feed.run())
    try:
        async with asyncio.timeout(5):
            while not await got_last():
                await asyncio.sleep(0.05)
        ok, reason = feed.readiness("BTCUSDT", "1m")
        assert not ok and "hueco" in reason
        assert truth[9].open_time not in _times(
            received
        )  # la vela posterior al hueco no llega a estrategias
        problems = await feed.check_staleness()
        assert "BTCUSDT:1m" in problems
    finally:
        feed.stop()
        await asyncio.wait_for(task, 5)
    async with db.session_scope() as s:
        alerts = (await s.scalars(select(Alert))).all()
    assert any(a.code == "DATA_NOT_READY" for a in alerts)


async def test_reconnect_after_disconnect_and_server_shutdown(ws_server: Any) -> None:
    truth = _recent_series(12)
    rest = FakeRest({("BTCUSDT", "1m"): truth[:9]})
    fake = FakeBinanceWS(
        [
            ["WAIT", ws_kline(truth[9]), "CLOSE"],
            [{"stream": "x", "data": {"e": "serverShutdown", "E": 1}}],
            [ws_kline(truth[11])],
        ]
    )
    url = await ws_server(fake)
    received: list[Candle] = []
    feed = _feed(url, rest, received)

    async def done() -> bool:
        if fake.connections >= 1:
            # Durante la caída se cierra la vela 10: solo puede llegar por el catch-up REST.
            rest.truth[("BTCUSDT", "1m")] = truth[:11]
        return fake.connections >= 3 and truth[11].open_time in _times(received)

    await _run_feed(feed, done)
    assert feed.reconnects >= 2
    async with db.session_scope() as s:
        missing = await store.missing_ranges(
            s, "BTCUSDT", "1m", truth[0].open_time, truth[11].open_time
        )
    assert missing == []


async def test_invalid_and_conflicting_candles_raise_alerts(ws_server: Any) -> None:
    truth = _recent_series(5)
    rest = FakeRest({("BTCUSDT", "1m"): truth})
    bad = candle(truth[-1].open_time + MIN, o="100", h="90", l="80", c="100")
    conflict = candle(truth[-1].open_time, o="1", c="1", h="2", l="1")
    fake = FakeBinanceWS([[ws_kline(bad), ws_kline(conflict)]])
    url = await ws_server(fake)
    feed = _feed(url, rest, [])

    async def alerted() -> bool:
        async with db.session_scope() as s:
            a = (await s.scalars(select(Alert))).all()
        return any(x.code == "DATA_INVALID" and x.occurrences >= 2 for x in a)

    await _run_feed(feed, alerted)
    ok, _ = feed.validators[("BTCUSDT", "1m")].readiness(utcnow(), timedelta(seconds=150))
    assert not ok


async def test_staleness_detected_without_new_candles(ws_server: Any) -> None:
    truth = _recent_series(5)
    rest = FakeRest({("BTCUSDT", "1m"): truth})
    fake = FakeBinanceWS([["WAIT"]])
    url = await ws_server(fake)
    feed = _feed(url, rest, [])
    task = asyncio.create_task(feed.run())
    try:
        async with asyncio.timeout(5):
            while not feed.connected:
                await asyncio.sleep(0.05)
        assert feed.readiness("BTCUSDT", "1m")[0]
        later = utcnow() + timedelta(minutes=10)
        problems = await feed.check_staleness(now=later)
        assert "obsoletos" in problems["BTCUSDT:1m"]
    finally:
        feed.stop()
        await asyncio.wait_for(task, 5)
