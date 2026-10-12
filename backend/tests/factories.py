"""Generadores deterministas de datos de mercado para tests."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.domain import Candle, interval_delta
from app.providers.binance.parsing import to_epoch_ms

T0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def floor_minute(dt: datetime, interval: str = "1m") -> datetime:
    step = int(interval_delta(interval).total_seconds())
    ts = int(dt.timestamp()) // step * step
    return datetime.fromtimestamp(ts, UTC)


def price_at(i: int, base: float = 100.0) -> Decimal:
    return Decimal(str(round(base + 5 * math.sin(i / 7.0) + i * 0.01, 2)))


def candle(
    open_time: datetime,
    *,
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    o: str | Decimal | None = None,
    h: str | Decimal | None = None,
    l: str | Decimal | None = None,  # noqa: E741
    c: str | Decimal | None = None,
    v: str = "10",
    closed: bool = True,
    source: str = "test",
) -> Candle:
    op = Decimal(o) if o is not None else Decimal("100")
    cl = Decimal(c) if c is not None else op
    hi = Decimal(h) if h is not None else max(op, cl) + Decimal("1")
    lo = Decimal(l) if l is not None else min(op, cl) - Decimal("1")
    step = interval_delta(interval)
    return Candle(
        symbol=symbol,
        interval=interval,
        open_time=open_time,
        close_time=open_time + step - timedelta(milliseconds=1),
        open=op,
        high=hi,
        low=lo,
        close=cl,
        volume=Decimal(v),
        quote_volume=Decimal(v) * cl,
        trades=5,
        is_closed=closed,
        source=source,
    )


def series(
    start: datetime, n: int, *, symbol: str = "BTCUSDT", interval: str = "1m", base: float = 100.0
) -> list[Candle]:
    step = interval_delta(interval)
    out = []
    for i in range(n):
        o = price_at(i, base)
        c = price_at(i + 1, base)
        out.append(candle(start + i * step, symbol=symbol, interval=interval, o=o, c=c))
    return out


def rest_row(c: Candle) -> list[Any]:
    return [
        to_epoch_ms(c.open_time),
        str(c.open),
        str(c.high),
        str(c.low),
        str(c.close),
        str(c.volume),
        to_epoch_ms(c.close_time),
        str(c.quote_volume),
        c.trades,
        "0",
        "0",
        "0",
    ]


def ws_kline(c: Candle, *, closed: bool | None = None) -> dict[str, Any]:
    data = {
        "e": "kline",
        "E": to_epoch_ms(c.close_time),
        "s": c.symbol,
        "k": {
            "t": to_epoch_ms(c.open_time),
            "T": to_epoch_ms(c.close_time),
            "s": c.symbol,
            "i": c.interval,
            "f": 1,
            "L": 2,
            "o": str(c.open),
            "c": str(c.close),
            "h": str(c.high),
            "l": str(c.low),
            "v": str(c.volume),
            "n": c.trades,
            "x": c.is_closed if closed is None else closed,
            "q": str(c.quote_volume),
            "V": "0",
            "Q": "0",
            "B": "0",
        },
    }
    return {"stream": f"{c.symbol.lower()}@kline_{c.interval}", "data": data}
