"""Carga de históricos y relleno de huecos (archivo verificado + REST)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.domain import Candle, interval_delta, utcnow
from app.market_data import store
from app.market_data.validation import Gap, check_candle
from app.persistence.db import session_scope
from app.providers.binance.archive import BinanceArchiveClient, parse_archive, plan_files
from app.providers.binance.rest import BinanceMarketDataClient

log = logging.getLogger(__name__)


@dataclass
class HistoryReport:
    symbol: str
    interval: str
    start: datetime
    end: datetime
    inserted: int = 0
    files: list[str] = field(default_factory=list)
    checksums: dict[str, str] = field(default_factory=dict)
    rejected: int = 0
    remaining_gaps: list[Gap] = field(default_factory=list)

    @property
    def missing_candles(self) -> int:
        return sum(g.missing_count() for g in self.remaining_gaps)


async def _persist(candles: list[Candle]) -> tuple[int, int]:
    """Guarda solo velas que superan la validación individual. Devuelve (insertadas, rechazadas)."""
    good = [c for c in candles if check_candle(c).accepted]
    async with session_scope() as s:
        inserted = await store.save_candles(s, good)
    return inserted, len(candles) - len(good)


async def backfill_rest(
    rest: BinanceMarketDataClient, symbol: str, interval: str, start: datetime, end: datetime
) -> int:
    candles = await rest.klines_range(symbol, interval, start, end)
    inserted, _ = await _persist(candles)
    return inserted


async def ensure_history(
    *,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    rest: BinanceMarketDataClient,
    archive: BinanceArchiveClient | None,
) -> HistoryReport:
    """Garantiza velas cerradas con open_time en [start, end]. Idempotente."""
    rep = HistoryReport(symbol, interval, start, end)
    step = interval_delta(interval)
    # La última vela que puede estar cerrada.
    latest_closed = utcnow() - step
    end = min(end, latest_closed)

    if archive is not None:
        today = utcnow().date()
        for f in plan_files(symbol, interval, start.date(), end.date(), today):
            async with session_scope() as s:
                gaps = await store.missing_ranges(
                    s, symbol, interval, *_file_bounds(f.period, f.label, step, start, end)
                )
            if not gaps:
                continue  # ya completo, no descargar
            data, digest = await archive.fetch(f)
            candles = [
                c for c in parse_archive(data, symbol, interval) if start <= c.open_time <= end
            ]
            ins, rej = await _persist(candles)
            rep.inserted += ins
            rep.rejected += rej
            rep.files.append(f.name)
            rep.checksums[f.name] = digest

    async with session_scope() as s:
        gaps = await store.missing_ranges(s, symbol, interval, start, end)
    for g in gaps:
        rep.inserted += await backfill_rest(rest, symbol, interval, g.start, g.end)

    async with session_scope() as s:
        rep.remaining_gaps = await store.missing_ranges(s, symbol, interval, start, end)
        for g in rep.remaining_gaps:
            # Pueden ser paradas reales del exchange: se registran, no se inventan velas.
            await store.record_gap(s, g)
    log.info(
        "histórico asegurado",
        extra={
            "symbol": symbol,
            "interval": interval,
            "inserted": rep.inserted,
            "files": len(rep.files),
            "remaining_missing": rep.missing_candles,
        },
    )
    return rep


def _file_bounds(
    period: str, label: str, step: timedelta, start: datetime, end: datetime
) -> tuple[datetime, datetime]:
    if period == "monthly":
        y, m = map(int, label.split("-"))
        lo = datetime(y, m, 1, tzinfo=UTC)
        hi = datetime(y + m // 12, m % 12 + 1, 1, tzinfo=UTC) - step
    else:
        d = datetime.fromisoformat(label).replace(tzinfo=UTC)
        lo, hi = d, d + timedelta(days=1) - step
    return max(lo, start), min(hi, end)
