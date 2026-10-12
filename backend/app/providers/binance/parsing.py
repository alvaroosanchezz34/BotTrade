"""Conversión de formatos de Binance a contratos de dominio.

Formatos verificados el 2026-10-12:
* REST ``/api/v3/klines``: array de 12 campos, tiempos en **milisegundos**.
* Archivos de ``data.binance.vision`` (spot, desde 2025): CSV sin cabecera con
  los mismos 12 campos y tiempos en **microsegundos**.
* WebSocket ``<symbol>@kline_<interval>``: objeto ``k`` con ``x`` = vela cerrada.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.domain import Candle, InstrumentRules, Quote, utcnow

# Cualquier epoch > 1e14 no puede estar en ms (sería el año 5138): son µs.
_MICROS_THRESHOLD = 10**14


def epoch_to_utc(value: int | str) -> datetime:
    v = int(value)
    if v >= _MICROS_THRESHOLD:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=v)
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=v)


def to_epoch_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("datetime sin zona horaria")
    return int(dt.timestamp() * 1000)


def parse_kline_row(
    symbol: str, interval: str, row: Sequence[Any], *, source: str, now: datetime | None = None
) -> Candle:
    """Fila REST o CSV. ``is_closed`` se decide por la hora de cierre, no se supone."""
    close_time = epoch_to_utc(row[6])
    now = now or utcnow()
    return Candle(
        symbol=symbol,
        interval=interval,
        open_time=epoch_to_utc(row[0]),
        close_time=close_time,
        open=Decimal(str(row[1])),
        high=Decimal(str(row[2])),
        low=Decimal(str(row[3])),
        close=Decimal(str(row[4])),
        volume=Decimal(str(row[5])),
        quote_volume=Decimal(str(row[7])),
        trades=int(row[8]),
        is_closed=close_time < now,
        source=source,
        received_at=now,
    )


def parse_ws_kline(payload: dict[str, Any], *, now: datetime | None = None) -> Candle:
    k = payload["k"]
    return Candle(
        symbol=str(k["s"]),
        interval=str(k["i"]),
        open_time=epoch_to_utc(k["t"]),
        close_time=epoch_to_utc(k["T"]),
        open=Decimal(k["o"]),
        high=Decimal(k["h"]),
        low=Decimal(k["l"]),
        close=Decimal(k["c"]),
        volume=Decimal(k["v"]),
        quote_volume=Decimal(k["q"]),
        trades=int(k["n"]),
        is_closed=bool(k["x"]),
        source="binance-ws",
        received_at=now or utcnow(),
    )


def parse_book_ticker(payload: dict[str, Any], *, now: datetime | None = None) -> Quote:
    return Quote(
        symbol=str(payload["s"]),
        bid=Decimal(payload["b"]),
        ask=Decimal(payload["a"]),
        ts=now or utcnow(),
        source="binance-ws",
    )


def parse_instrument_rules(symbol_info: dict[str, Any]) -> InstrumentRules:
    filters = {f["filterType"]: f for f in symbol_info["filters"]}
    price = filters["PRICE_FILTER"]
    lot = filters["LOT_SIZE"]
    notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
    return InstrumentRules(
        symbol=symbol_info["symbol"],
        base_asset=symbol_info["baseAsset"],
        quote_asset=symbol_info["quoteAsset"],
        tick_size=Decimal(price["tickSize"]),
        step_size=Decimal(lot["stepSize"]),
        min_qty=Decimal(lot["minQty"]),
        max_qty=Decimal(lot["maxQty"]),
        min_notional=Decimal(notional.get("minNotional", "0")),
        status=symbol_info.get("status", "UNKNOWN"),
    )
