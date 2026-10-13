"""Descarga de históricos de ``data.binance.vision`` con verificación SHA-256.

Cada fichero publicado trae un ``.CHECKSUM``. Si no coincide, el fichero se
descarta: no se usan datos históricos no verificables (sección 10).
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import logging
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from app.core.domain import Candle
from app.providers.binance.parsing import parse_kline_row

log = logging.getLogger(__name__)


class ChecksumMismatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveFile:
    symbol: str
    interval: str
    period: str  # "monthly" | "daily"
    label: str  # "2026-08" | "2026-10-10"

    @property
    def name(self) -> str:
        return f"{self.symbol}-{self.interval}-{self.label}.zip"

    @property
    def path(self) -> str:
        return f"/data/spot/{self.period}/klines/{self.symbol}/{self.interval}/{self.name}"


def plan_files(
    symbol: str, interval: str, start: date, end: date, today: date
) -> list[ArchiveFile]:
    """Meses completos como ficheros mensuales; el mes en curso, día a día hasta ayer."""
    files: list[ArchiveFile] = []
    month = date(start.year, start.month, 1)
    current_month = date(today.year, today.month, 1)
    while month <= end:
        nxt = date(month.year + (month.month // 12), month.month % 12 + 1, 1)
        if month < current_month:
            files.append(ArchiveFile(symbol, interval, "monthly", month.strftime("%Y-%m")))
        else:
            day = max(month, start)
            last = min(end, today - timedelta(days=1), nxt - timedelta(days=1))
            while day <= last:
                files.append(ArchiveFile(symbol, interval, "daily", day.isoformat()))
                day += timedelta(days=1)
        month = nxt
    return files


class BinanceArchiveClient:
    def __init__(
        self,
        base_url: str,
        cache_dir: Path,
        *,
        client: httpx.AsyncClient | None = None,
        max_retries: int = 5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=60.0)
        self._owns = client is None
        self.cache_dir = cache_dir
        self.max_retries = max_retries
        self._sleep = sleep

    async def _get(self, path: str) -> httpx.Response:
        """GET idempotente con reintentos ante cortes de red y 5XX (backoff exponencial).

        Un 4XX (p. ej. 404: el fichero aún no existe) no se reintenta. Los checksums
        siguen siendo la defensa frente a datos corruptos: no se relajan.
        """
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._client.get(path)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise
                log.warning(
                    "descarga fallida, reintentando",
                    extra={"path": path, "error": type(exc).__name__},
                )
            else:
                if resp.status_code < 500 or attempt == self.max_retries:
                    return resp
                log.warning(
                    "error 5XX, reintentando", extra={"path": path, "status": resp.status_code}
                )
            await self._sleep(min(30.0, 1.0 * 2**attempt))
        raise AssertionError("inalcanzable")  # pragma: no cover

    async def aclose(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def fetch(self, f: ArchiveFile) -> tuple[bytes, str]:
        """Devuelve (zip, sha256) verificado. Usa la caché local si el hash coincide."""
        expected = await self._expected_checksum(f)
        cached = self.cache_dir / f.symbol / f.interval / f.name
        if cached.exists():
            data = cached.read_bytes()
            if hashlib.sha256(data).hexdigest() == expected:
                return data, expected
            log.warning("caché corrupta, se vuelve a descargar", extra={"file": f.name})
        resp = await self._get(f.path)
        resp.raise_for_status()
        data = resp.content
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise ChecksumMismatchError(f"{f.name}: esperado {expected}, obtenido {actual}")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data, actual

    async def _expected_checksum(self, f: ArchiveFile) -> str:
        resp = await self._get(f.path + ".CHECKSUM")
        resp.raise_for_status()
        return resp.text.split()[0].strip().lower()


def parse_archive(zip_bytes: bytes, symbol: str, interval: str) -> list[Candle]:
    now = datetime.now(UTC)
    out: list[Candle] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            with zf.open(name) as fh:
                reader = csv.reader(io.TextIOWrapper(fh, encoding="utf-8"))
                for row in reader:
                    if not row or not row[0].isdigit():  # cabecera en algunos ficheros
                        continue
                    out.append(
                        parse_kline_row(symbol, interval, row, source="binance-archive", now=now)
                    )
    return out
