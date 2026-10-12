"""Validación de velas individuales y de series (duplicados, desorden, huecos, obsolescencia).

Regla central: una serie con huecos sin resolver, datos obsoletos o una vela
inválida reciente **no puede generar señales** hasta recuperarse (sección 7).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.domain import Candle, DataQuality, interval_delta

_ONE_MS = timedelta(milliseconds=1)


@dataclass(frozen=True, slots=True)
class Gap:
    symbol: str
    interval: str
    start: datetime  # open_time de la primera vela que falta
    end: datetime  # open_time de la última vela que falta

    def missing_count(self) -> int:
        return int((self.end - self.start) / interval_delta(self.interval)) + 1


@dataclass(frozen=True, slots=True)
class CandleCheck:
    quality: DataQuality
    reasons: tuple[str, ...] = ()
    gap: Gap | None = None
    conflict: bool = False  # duplicado con valores distintos

    @property
    def accepted(self) -> bool:
        return self.quality is DataQuality.VALID


def check_candle(c: Candle) -> CandleCheck:
    """Comprobaciones que no dependen de velas anteriores."""
    reasons: list[str] = []
    step = interval_delta(c.interval)
    if c.open_time.tzinfo is None or c.close_time.tzinfo is None:
        reasons.append("timestamp sin zona horaria")
    else:
        epoch_s = c.open_time.timestamp()
        if epoch_s % step.total_seconds() != 0:
            reasons.append("open_time no alineado con el intervalo")
        expected_end = c.open_time + step
        if not (expected_end - _ONE_MS <= c.close_time < expected_end):
            reasons.append("close_time incoherente con open_time")
    if min(c.open, c.high, c.low, c.close) <= 0:
        reasons.append("precio no positivo")
    if c.high < max(c.open, c.close, c.low) or c.low > min(c.open, c.close, c.high):
        reasons.append("OHLC incoherente")
    if c.volume < 0 or c.quote_volume < 0 or c.trades < 0:
        reasons.append("volumen negativo")
    if reasons:
        return CandleCheck(DataQuality.INVALID, tuple(reasons))
    if not c.is_closed:
        return CandleCheck(DataQuality.OPEN, ("vela todavía abierta",))
    return CandleCheck(DataQuality.VALID)


def _same_values(a: Candle, b: Candle) -> bool:
    return (a.open, a.high, a.low, a.close, a.volume) == (b.open, b.high, b.low, b.close, b.volume)


@dataclass
class SeriesValidator:
    """Validador con estado para un flujo en vivo de un (símbolo, intervalo)."""

    symbol: str
    interval: str
    last: Candle | None = None
    open_gaps: list[Gap] = field(default_factory=list)
    last_invalid_at: datetime | None = None

    def seed(self, last_stored: Candle | None) -> None:
        """Inicializa con la última vela cerrada persistida (tras un reinicio)."""
        self.last = last_stored

    def feed(self, c: Candle) -> CandleCheck:
        if (c.symbol, c.interval) != (self.symbol, self.interval):
            return CandleCheck(DataQuality.INVALID, ("símbolo/intervalo incorrecto",))
        base = check_candle(c)
        if base.quality is DataQuality.INVALID:
            self.last_invalid_at = c.open_time
            return base
        if base.quality is DataQuality.OPEN:
            return base
        if self.last is not None:
            if c.open_time == self.last.open_time:
                return CandleCheck(
                    DataQuality.DUPLICATE,
                    ("vela repetida",),
                    conflict=not _same_values(c, self.last),
                )
            if c.open_time < self.last.open_time:
                return CandleCheck(DataQuality.OUT_OF_ORDER, ("vela anterior a la última",))
            step = interval_delta(self.interval)
            expected = self.last.open_time + step
            if c.open_time > expected:
                gap = Gap(self.symbol, self.interval, expected, c.open_time - step)
                self.open_gaps.append(gap)
                self.last = c
                return CandleCheck(DataQuality.VALID, ("hueco antes de esta vela",), gap=gap)
        self.last = c
        return CandleCheck(DataQuality.VALID)

    def resolve_gaps_until(self, covered_until: datetime) -> None:
        """Marca como resueltos los huecos cuyo final ya está cubierto por el backfill."""
        self.open_gaps = [g for g in self.open_gaps if g.end > covered_until]

    def readiness(self, now: datetime, stale_after: timedelta) -> tuple[bool, str]:
        """¿Puede esta serie producir señales nuevas ahora mismo?"""
        if self.last is None:
            return False, "sin datos"
        if self.open_gaps:
            return False, f"{len(self.open_gaps)} hueco(s) sin resolver"
        if self.last_invalid_at is not None and self.last_invalid_at >= self.last.open_time:
            return False, "última vela recibida inválida"
        # La última vela cerrada debería terminar hace menos de un intervalo + tolerancia.
        age = now - self.last.close_time
        if age > interval_delta(self.interval) + stale_after:
            return False, f"datos obsoletos ({int(age.total_seconds())} s)"
        return True, "ok"


@dataclass(frozen=True)
class SeriesReport:
    count: int
    duplicates: int
    out_of_order: int
    invalid: int
    open_candles: int
    gaps: tuple[Gap, ...]

    @property
    def clean(self) -> bool:
        return not (self.duplicates or self.out_of_order or self.invalid or self.gaps)


def check_series(
    candles: Sequence[Candle], *, start: datetime | None = None, end: datetime | None = None
) -> SeriesReport:
    """Auditoría de un lote histórico (orden de llegada preservado).

    ``start``/``end`` (open_time inclusivos) permiten detectar huecos en los extremos.
    """
    if not candles and (start is None or end is None):
        return SeriesReport(0, 0, 0, 0, 0, ())
    symbol = candles[0].symbol if candles else ""
    interval = candles[0].interval if candles else ""
    seen: set[datetime] = set()
    dup = ooo = inv = opn = 0
    prev: datetime | None = None
    for c in candles:
        q = check_candle(c).quality
        if q is DataQuality.INVALID:
            inv += 1
            continue
        if q is DataQuality.OPEN:
            opn += 1
            continue
        if c.open_time in seen:
            dup += 1
            continue
        if prev is not None and c.open_time < prev:
            ooo += 1
        seen.add(c.open_time)
        prev = c.open_time if prev is None else max(prev, c.open_time)
    gaps = tuple(find_gaps(sorted(seen), symbol, interval, start=start, end=end))
    return SeriesReport(len(candles), dup, ooo, inv, opn, gaps)


def find_gaps(
    open_times: Iterable[datetime],
    symbol: str,
    interval: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Gap]:
    step = interval_delta(interval)
    times = sorted(set(open_times))
    gaps: list[Gap] = []
    expected = start if start is not None else (times[0] if times else None)
    for t in times:
        if expected is not None and t > expected:
            gaps.append(Gap(symbol, interval, expected, t - step))
        expected = t + step
    if end is not None and expected is not None and expected <= end:
        gaps.append(Gap(symbol, interval, expected, end))
    return gaps
