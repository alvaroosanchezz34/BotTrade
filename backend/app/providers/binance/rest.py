"""Cliente REST de datos públicos de mercado (solo lectura, sin firma).

* Respeta ``X-MBX-USED-WEIGHT-1M``: si se supera el 80 % del límite, espera al
  siguiente minuto antes de seguir.
* 429/418: espera ``Retry-After`` y no reintenta en bucle (un 418 es un ban).
* Reintenta errores de red y 5XX con backoff exponencial: son GET idempotentes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from app.core.domain import Candle, InstrumentRules, Quote, interval_delta, utcnow
from app.providers.binance.parsing import (
    epoch_to_utc,
    parse_instrument_rules,
    parse_kline_row,
    to_epoch_ms,
)

log = logging.getLogger(__name__)

KLINES_MAX_LIMIT = 1000
DEFAULT_WEIGHT_LIMIT = 6000  # REQUEST_WEIGHT/min verificado en exchangeInfo 2026-10-12
WEIGHT_SAFETY = 0.8


class BinanceHTTPError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class RateLimitedError(BinanceHTTPError):
    def __init__(self, status: int, retry_after: float):
        super().__init__(status, f"rate limit, Retry-After={retry_after}s")
        self.retry_after = retry_after


Sleep = Callable[[float], Awaitable[None]]


class BinanceMarketDataClient:
    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        weight_limit: int = DEFAULT_WEIGHT_LIMIT,
        max_retries: int = 4,
        sleep: Sleep = asyncio.sleep,
        source: str = "binance-rest",
    ):
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._owns_client = client is None
        self.weight_limit = weight_limit
        self.max_retries = max_retries
        self._sleep = sleep
        self.used_weight = 0
        self.source = source

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        attempt = 0
        while True:
            if self.used_weight >= self.weight_limit * WEIGHT_SAFETY:
                wait = 61 - utcnow().second
                log.warning(
                    "peso de API alto, esperando", extra={"used": self.used_weight, "wait": wait}
                )
                await self._sleep(wait)
                self.used_weight = 0
            try:
                resp = await self._client.get(path, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise BinanceHTTPError(0, f"error de red: {type(exc).__name__}") from exc
                await self._sleep(self._backoff(attempt))
                continue

            used = resp.headers.get("x-mbx-used-weight-1m")
            if used is not None and used.isdigit():
                self.used_weight = int(used)

            if resp.status_code in (418, 429):
                retry_after = float(resp.headers.get("retry-after", "60"))
                if resp.status_code == 418 or attempt >= 1:
                    # Ban (418) o segundo 429 seguido: no insistir.
                    raise RateLimitedError(resp.status_code, retry_after)
                attempt += 1
                await self._sleep(retry_after)
                continue
            if resp.status_code >= 500:
                attempt += 1
                if attempt > self.max_retries:
                    raise BinanceHTTPError(resp.status_code, "error del servidor")
                await self._sleep(self._backoff(attempt))
                continue
            if resp.status_code >= 400:
                raise BinanceHTTPError(resp.status_code, resp.text[:200])
            return resp.json()

    @staticmethod
    def _backoff(attempt: int) -> float:
        return float(min(30.0, 0.5 * 2**attempt) + random.uniform(0, 0.25))  # noqa: S311

    async def ping(self) -> None:
        await self._get("/api/v3/ping")

    async def server_time(self) -> datetime:
        data = await self._get("/api/v3/time")
        return epoch_to_utc(data["serverTime"])

    async def exchange_info(self, symbols: list[str]) -> dict[str, InstrumentRules]:
        data = await self._get(
            "/api/v3/exchangeInfo", {"symbols": json.dumps(symbols, separators=(",", ":"))}
        )
        return {s["symbol"]: parse_instrument_rules(s) for s in data["symbols"]}

    async def book_ticker(self, symbol: str) -> Quote:
        d = await self._get("/api/v3/ticker/bookTicker", {"symbol": symbol})
        return Quote(
            symbol=symbol,
            bid=Decimal(d["bidPrice"]),
            ask=Decimal(d["askPrice"]),
            ts=utcnow(),
            source=self.source,
        )

    async def klines(
        self,
        symbol: str,
        interval: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = KLINES_MAX_LIMIT,
    ) -> list[Candle]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start is not None:
            params["startTime"] = to_epoch_ms(start)
        if end is not None:
            params["endTime"] = to_epoch_ms(end)
        rows = await self._get("/api/v3/klines", params)
        now = utcnow()
        return [parse_kline_row(symbol, interval, r, source=self.source, now=now) for r in rows]

    async def klines_range(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """Todas las velas con open_time en [start, end], paginando de 1000 en 1000."""
        out: list[Candle] = []
        cursor = start
        step = interval_delta(interval)
        while cursor <= end:
            page = await self.klines(symbol, interval, start=cursor, end=end)
            if not page:
                break
            out.extend(page)
            nxt = page[-1].open_time + step
            if nxt <= cursor:  # protección ante una respuesta anómala
                break
            cursor = nxt
            if len(page) < KLINES_MAX_LIMIT:
                break
        return out
