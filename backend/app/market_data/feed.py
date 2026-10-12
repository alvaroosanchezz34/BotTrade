"""Feed de mercado en tiempo real: WebSocket para el presente, REST para recuperar.

* Suscripción combinada ``/stream?streams=...`` a velas y ``bookTicker``.
* Solo velas con ``x=true`` (cerradas) pasan al pipeline; las abiertas se usan
  para el estado de conexión, nunca para señales.
* Reconexión con backoff exponencial + jitter; reconexión preventiva antes de
  las 24 h que impone Binance y ante ``serverShutdown``.
* Tras cualquier hueco (desconexión, mensaje perdido) se piden las velas que
  faltan por REST antes de declarar la serie sana otra vez.
* Un vigilante marca la serie como obsoleta si no llegan velas cerradas a tiempo.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from app.core.domain import Candle, DataQuality, Quote, Severity, interval_delta, utcnow
from app.market_data import store
from app.market_data.validation import Gap, SeriesValidator
from app.monitoring.alerts import raise_alert, resolve_alert
from app.persistence.db import session_scope
from app.providers.binance.parsing import parse_book_ticker, parse_ws_kline

log = logging.getLogger(__name__)

CandleHandler = Callable[[Candle], Awaitable[None]]
QuoteHandler = Callable[[Quote], Awaitable[None]]


class KlineSource(Protocol):
    async def klines_range(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[Candle]: ...


class WsConnection(Protocol):
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


Connector = Callable[[str], Awaitable[WsConnection]]


async def _default_connect(url: str) -> WsConnection:
    return await ws_connect(url, open_timeout=10, max_size=2**20, ping_interval=20, ping_timeout=20)


class LiveMarketFeed:
    def __init__(
        self,
        *,
        ws_url: str,
        symbols: list[str],
        intervals: list[str],
        rest: KlineSource,
        on_candle: CandleHandler | None = None,
        on_quote: QuoteHandler | None = None,
        stale_after: timedelta = timedelta(seconds=150),
        name: str = "binance-ws",
        connector: Connector = _default_connect,
        max_session: timedelta = timedelta(hours=23),
        recv_timeout: float = 30.0,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        watchdog_interval: float = 10.0,
    ) -> None:
        self.ws_url = ws_url
        self.symbols = [s.upper() for s in symbols]
        self.intervals = intervals
        self.rest = rest
        self.on_candle = on_candle
        self.on_quote = on_quote
        self.stale_after = stale_after
        self.name = name
        self._connect = connector
        self.max_session = max_session
        self.recv_timeout = recv_timeout
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.watchdog_interval = watchdog_interval

        self.validators = {
            (s, i): SeriesValidator(s, i) for s in self.symbols for i in self.intervals
        }
        self.quotes: dict[str, Quote] = {}
        self.connected = False
        self.reconnects = 0
        self.last_message_at: datetime | None = None
        self._stop = asyncio.Event()
        self._degraded_keys: set[str] = set()
        self._conn: WsConnection | None = None
        self._closing: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ URLs

    @property
    def stream_url(self) -> str:
        streams = [f"{s.lower()}@kline_{i}" for s in self.symbols for i in self.intervals]
        streams += [f"{s.lower()}@bookTicker" for s in self.symbols]
        return f"{self.ws_url}/stream?streams={'/'.join(streams)}"

    # ------------------------------------------------------------------ ciclo de vida

    def stop(self) -> None:
        self._stop.set()
        conn = self._conn
        if conn is not None:
            # Cerrar la conexión despierta el recv() pendiente.
            self._closing = asyncio.get_running_loop().create_task(conn.close())

    async def run(self) -> None:
        """Bucle principal. Termina solo con :meth:`stop`."""
        await self._seed_and_catch_up()
        watchdog = asyncio.create_task(self._watchdog(), name="feed-watchdog")
        attempt = 0
        try:
            while not self._stop.is_set():
                try:
                    await self._session()
                    attempt = 0  # sesión cerrada de forma ordenada
                except (OSError, WebSocketException, TimeoutError, json.JSONDecodeError) as exc:
                    attempt += 1
                    log.warning(
                        "websocket desconectado",
                        extra={"error": type(exc).__name__, "attempt": attempt},
                    )
                self.connected = False
                await self._set_status("DOWN", {"reconnects": self.reconnects})
                if self._stop.is_set():
                    break
                delay = min(self.backoff_max, self.backoff_base * 2 ** max(0, attempt - 1))
                delay += random.uniform(0, delay * 0.25)  # noqa: S311
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                if not self._stop.is_set():
                    self.reconnects += 1
                    await self._catch_up_all()
        finally:
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog

    async def _session(self) -> None:
        conn = await self._connect(self.stream_url)
        self._conn = conn
        self.connected = True
        started = utcnow()
        await self._set_status("UP", {"reconnects": self.reconnects})
        try:
            while not self._stop.is_set():
                if utcnow() - started > self.max_session:
                    log.info("reconexión preventiva (límite de sesión)")
                    return
                raw = await asyncio.wait_for(conn.recv(), timeout=self.recv_timeout)
                self.last_message_at = utcnow()
                msg = json.loads(raw)
                if await self._dispatch(msg) == "shutdown":
                    return
        finally:
            self._conn = None
            with contextlib.suppress(Exception):
                await conn.close()

    # ------------------------------------------------------------------ mensajes

    async def _dispatch(self, msg: dict[str, Any]) -> str | None:
        data = msg.get("data", msg)
        event = data.get("e")
        if event == "serverShutdown":
            log.info("serverShutdown recibido: reconectando")
            return "shutdown"
        if event == "kline":
            await self.handle_candle(parse_ws_kline(data))
        elif "b" in data and "a" in data and "s" in data:  # bookTicker no lleva "e"
            q = parse_book_ticker(data)
            self.quotes[q.symbol] = q
            if self.on_quote:
                await self.on_quote(q)
        return None

    async def handle_candle(self, c: Candle) -> DataQuality:
        v = self.validators.get((c.symbol, c.interval))
        if v is None:
            return DataQuality.INVALID
        check = v.feed(c)
        if check.quality is DataQuality.OPEN:
            return check.quality
        if check.quality is not DataQuality.VALID:
            if check.quality is DataQuality.INVALID or check.conflict:
                async with session_scope() as s:
                    await raise_alert(
                        s,
                        severity=Severity.WARNING,
                        code="DATA_INVALID",
                        message=f"Vela rechazada {c.symbol} {c.interval}: {', '.join(check.reasons)}",
                        recommended_action="Revisar el proveedor de datos; las señales de esta serie están bloqueadas.",
                        dedup_key=f"DATA_INVALID:{c.symbol}:{c.interval}",
                        context={"open_time": c.open_time.isoformat(), "conflict": check.conflict},
                    )
            return check.quality

        async with session_scope() as s:
            await store.save_candles(s, [c])
            await store.set_connection_status(
                s,
                self.name,
                "UP",
                last_message_at=self.last_message_at,
                last_valid_data_at=c.close_time,
            )
            if check.gap is not None:
                await store.record_gap(s, check.gap)
        if check.gap is not None:
            await self._fill_gap(check.gap)
        if self.on_candle and not v.open_gaps:
            await self.on_candle(c)
        return check.quality

    # ------------------------------------------------------------------ recuperación

    async def _fill_gap(self, gap: Gap) -> None:
        try:
            candles = await self.rest.klines_range(gap.symbol, gap.interval, gap.start, gap.end)
        except Exception as exc:
            log.warning("no se pudo rellenar el hueco", extra={"gap": str(gap), "error": str(exc)})
            return
        closed = [c for c in candles if c.is_closed and gap.start <= c.open_time <= gap.end]
        async with session_scope() as s:
            await store.save_candles(s, closed)
            missing = await store.missing_ranges(s, gap.symbol, gap.interval, gap.start, gap.end)
            if not missing:
                await store.resolve_gaps(s, gap.symbol, gap.interval, gap.end, "rest-backfill")
        if not missing:
            self.validators[(gap.symbol, gap.interval)].resolve_gaps_until(gap.end)
            log.info("hueco rellenado", extra={"gap": str(gap), "candles": len(closed)})

    async def _seed_and_catch_up(self) -> None:
        for (symbol, interval), v in self.validators.items():
            async with session_scope() as s:
                v.seed(await store.last_closed(s, symbol, interval))
        await self._catch_up_all()

    async def _catch_up_all(self) -> None:
        """Pide por REST lo que falte desde la última vela guardada hasta ahora."""
        now = utcnow()
        for (symbol, interval), v in self.validators.items():
            step = interval_delta(interval)
            latest_closed_open = now - step  # la vela más reciente que puede estar cerrada
            if v.last is None:
                start = latest_closed_open - step * 500  # contexto inicial para indicadores
            else:
                start = v.last.open_time + step
            if start > latest_closed_open:
                continue
            try:
                candles = await self.rest.klines_range(symbol, interval, start, latest_closed_open)
            except Exception as exc:
                log.warning("catch-up REST falló", extra={"symbol": symbol, "error": str(exc)})
                continue
            closed = [c for c in candles if c.is_closed]
            async with session_scope() as s:
                await store.save_candles(s, closed)
                v.seed(await store.last_closed(s, symbol, interval))
                if v.last is not None:
                    await store.resolve_gaps(s, symbol, interval, v.last.open_time, "rest-catch-up")
            if v.last is not None:
                v.resolve_gaps_until(v.last.open_time)

    # ------------------------------------------------------------------ vigilancia

    def readiness(
        self, symbol: str, interval: str, now: datetime | None = None
    ) -> tuple[bool, str]:
        if not self.connected:
            return False, "websocket desconectado"
        return self.validators[(symbol, interval)].readiness(now or utcnow(), self.stale_after)

    async def check_staleness(self, now: datetime | None = None) -> dict[str, str]:
        now = now or utcnow()
        problems: dict[str, str] = {}
        for symbol, interval in self.validators:
            ok, reason = self.readiness(symbol, interval, now)
            key = f"DATA_NOT_READY:{symbol}:{interval}"
            if ok:
                if key in self._degraded_keys:
                    async with session_scope() as s:
                        await resolve_alert(s, key)
                    self._degraded_keys.discard(key)
                continue
            problems[f"{symbol}:{interval}"] = reason
            async with session_scope() as s:
                await raise_alert(
                    s,
                    severity=Severity.WARNING,
                    code="DATA_NOT_READY",
                    message=f"Serie {symbol} {interval} no apta para señales: {reason}",
                    recommended_action="Las entradas nuevas de esta serie están bloqueadas hasta que se recupere.",
                    dedup_key=key,
                )
            self._degraded_keys.add(key)
        if self.connected:
            async with session_scope() as s:
                await store.set_connection_status(
                    s,
                    self.name,
                    "DEGRADED" if problems else "UP",
                    last_message_at=self.last_message_at,
                    detail={"problems": problems, "reconnects": self.reconnects},
                )
        return problems

    async def _watchdog(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.watchdog_interval)
            if self._stop.is_set():
                return
            try:
                await self.check_staleness()
            except Exception:
                log.exception("fallo en el vigilante de datos")

    async def _set_status(self, status: str, detail: dict[str, Any]) -> None:
        try:
            async with session_scope() as s:
                await store.set_connection_status(
                    s, self.name, status, last_message_at=self.last_message_at, detail=detail
                )
        except Exception:
            log.exception("no se pudo guardar el estado de conexión")
