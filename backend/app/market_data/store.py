"""Persistencia de velas, huecos, instrumentos y estado de conexiones."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain import Candle, InstrumentRules, interval_delta, utcnow
from app.market_data.validation import Gap
from app.persistence.models import CandleRow, ConnectionStatus, DataGap, Instrument

_CHUNK = 2000  # 14 columnas × 2000 < límite de parámetros de asyncpg


def _row(c: Candle, quality: str) -> dict[str, Any]:
    return {
        "symbol": c.symbol,
        "interval": c.interval,
        "open_time": c.open_time,
        "close_time": c.close_time,
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "volume": c.volume,
        "quote_volume": c.quote_volume,
        "trades": c.trades,
        "source": c.source,
        "quality": quality,
    }


async def save_candles(session: AsyncSession, candles: Sequence[Candle]) -> int:
    """Inserta velas **cerradas**; las existentes no se tocan (idempotente).

    Devuelve cuántas filas nuevas se insertaron.
    """
    rows = [_row(c, "VALID") for c in candles if c.is_closed]
    inserted = 0
    for i in range(0, len(rows), _CHUNK):
        stmt = (
            insert(CandleRow)
            .values(rows[i : i + _CHUNK])
            .on_conflict_do_nothing(index_elements=["symbol", "interval", "open_time"])
            .returning(CandleRow.open_time)
        )
        inserted += len((await session.execute(stmt)).all())
    return inserted


def _to_candle(r: CandleRow) -> Candle:
    return Candle(
        symbol=r.symbol,
        interval=r.interval,
        open_time=r.open_time,
        close_time=r.close_time,
        open=r.open,
        high=r.high,
        low=r.low,
        close=r.close,
        volume=r.volume,
        quote_volume=r.quote_volume,
        trades=r.trades,
        is_closed=True,
        source=r.source,
        received_at=r.ingested_at,
    )


async def last_closed(session: AsyncSession, symbol: str, interval: str) -> Candle | None:
    r = await session.scalar(
        select(CandleRow)
        .where(CandleRow.symbol == symbol, CandleRow.interval == interval)
        .order_by(CandleRow.open_time.desc())
        .limit(1)
    )
    return _to_candle(r) if r else None


async def recent_candles(
    session: AsyncSession, symbol: str, interval: str, limit: int
) -> list[Candle]:
    rows = (
        await session.scalars(
            select(CandleRow)
            .where(CandleRow.symbol == symbol, CandleRow.interval == interval)
            .order_by(CandleRow.open_time.desc())
            .limit(limit)
        )
    ).all()
    return [_to_candle(r) for r in reversed(rows)]


async def load_frame(
    session: AsyncSession, symbol: str, interval: str, start: datetime, end: datetime
) -> pd.DataFrame:
    """Velas con open_time en [start, end] como DataFrame float64 indexado por open_time (UTC).

    Uso: indicadores y backtest. Los precios se reconvierten a Decimal al simular
    ejecuciones (ADR-004).
    """
    result = await session.execute(
        select(
            CandleRow.open_time,
            CandleRow.close_time,
            CandleRow.open,
            CandleRow.high,
            CandleRow.low,
            CandleRow.close,
            CandleRow.volume,
            CandleRow.quote_volume,
            CandleRow.trades,
        )
        .where(
            CandleRow.symbol == symbol,
            CandleRow.interval == interval,
            CandleRow.open_time >= start,
            CandleRow.open_time <= end,
        )
        .order_by(CandleRow.open_time)
    )
    cols = [
        "open_time",
        "close_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trades",
    ]
    df = pd.DataFrame(result.all(), columns=cols)
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = df[c].astype("float64")
    df["trades"] = df["trades"].astype("int64")
    for c in ("open_time", "close_time"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df.set_index("open_time")


async def missing_ranges(
    session: AsyncSession, symbol: str, interval: str, start: datetime, end: datetime
) -> list[Gap]:
    """Huecos en [start, end] calculados en PostgreSQL con generate_series."""
    step = interval_delta(interval)
    q = text(
        """
        WITH expected AS (
          SELECT g AS t FROM generate_series(CAST(:start AS timestamptz),
                                             CAST(:end AS timestamptz),
                                             CAST(:step AS interval)) g
        ), missing AS (
          SELECT e.t FROM expected e
          LEFT JOIN candles c ON c.symbol = :symbol AND c.interval = :interval AND c.open_time = e.t
          WHERE c.open_time IS NULL
        ), grouped AS (
          SELECT t, t - (row_number() OVER (ORDER BY t)) * CAST(:step AS interval) AS grp
          FROM missing
        )
        SELECT min(t) AS gap_start, max(t) AS gap_end FROM grouped GROUP BY grp ORDER BY 1
        """
    )
    rows = await session.execute(
        q,
        {"start": start, "end": end, "step": step, "symbol": symbol, "interval": interval},
    )
    return [Gap(symbol, interval, r.gap_start, r.gap_end) for r in rows]


async def record_gap(session: AsyncSession, gap: Gap) -> None:
    await session.execute(
        insert(DataGap)
        .values(symbol=gap.symbol, interval=gap.interval, gap_start=gap.start, gap_end=gap.end)
        .on_conflict_do_nothing(constraint="uq_data_gaps_key")
    )


async def resolve_gaps(
    session: AsyncSession, symbol: str, interval: str, until: datetime, resolution: str
) -> None:
    await session.execute(
        update(DataGap)
        .where(
            DataGap.symbol == symbol,
            DataGap.interval == interval,
            DataGap.gap_end <= until,
            DataGap.resolved_at.is_(None),
        )
        .values(resolved_at=utcnow(), resolution=resolution)
    )


async def set_connection_status(
    session: AsyncSession,
    name: str,
    status: str,
    *,
    last_message_at: datetime | None = None,
    last_valid_data_at: datetime | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    values: dict[str, Any] = {"name": name, "status": status, "detail": detail or {}}
    if last_message_at is not None:
        values["last_message_at"] = last_message_at
    if last_valid_data_at is not None:
        values["last_valid_data_at"] = last_valid_data_at
    stmt = insert(ConnectionStatus).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["name"],
        set_={k: getattr(stmt.excluded, k) for k in values if k != "name"}
        | {"updated_at": utcnow()},
    )
    await session.execute(stmt)


async def save_instruments(
    session: AsyncSession, rules: dict[str, InstrumentRules], provider: str
) -> None:
    for r in rules.values():
        values = {
            "symbol": r.symbol,
            "provider": provider,
            "base_asset": r.base_asset,
            "quote_asset": r.quote_asset,
            "tick_size": r.tick_size,
            "step_size": r.step_size,
            "min_qty": r.min_qty,
            "max_qty": r.max_qty,
            "min_notional": r.min_notional,
            "status": r.status,
        }
        stmt = insert(Instrument).values(**values)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["symbol"],
                set_={k: v for k, v in values.items() if k != "symbol"} | {"updated_at": utcnow()},
            )
        )


async def load_instruments(session: AsyncSession) -> dict[str, InstrumentRules]:
    rows = (await session.scalars(select(Instrument))).all()
    return {
        r.symbol: InstrumentRules(
            symbol=r.symbol,
            base_asset=r.base_asset,
            quote_asset=r.quote_asset,
            tick_size=r.tick_size,
            step_size=r.step_size,
            min_qty=r.min_qty,
            max_qty=r.max_qty,
            min_notional=r.min_notional,
            status=r.status,
        )
        for r in rows
    }
