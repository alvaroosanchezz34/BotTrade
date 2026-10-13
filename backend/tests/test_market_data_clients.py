import hashlib
import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.providers.binance.archive import (
    ArchiveFile,
    BinanceArchiveClient,
    ChecksumMismatchError,
    parse_archive,
    plan_files,
)
from app.providers.binance.parsing import epoch_to_utc
from app.providers.binance.rest import BinanceMarketDataClient, RateLimitedError
from tests.factories import T0, rest_row, series


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def __call__(self, s: float) -> None:
        self.sleeps.append(s)


def kline_server(candles: list[Any]) -> httpx.MockTransport:
    """Imita /api/v3/klines con startTime/endTime/limit."""

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.params
        start = (
            epoch_to_utc(int(p["startTime"]))
            if "startTime" in p
            else datetime.min.replace(tzinfo=UTC)
        )
        end = (
            epoch_to_utc(int(p["endTime"])) if "endTime" in p else datetime.max.replace(tzinfo=UTC)
        )
        limit = int(p.get("limit", 500))
        rows = [rest_row(c) for c in candles if start <= c.open_time <= end][:limit]
        return httpx.Response(200, json=rows, headers={"x-mbx-used-weight-1m": "10"})

    return httpx.MockTransport(handler)


def make_client(
    transport: httpx.MockTransport, rec: Recorder, **kw: Any
) -> BinanceMarketDataClient:
    http = httpx.AsyncClient(transport=transport, base_url="https://data-api.binance.vision")
    return BinanceMarketDataClient("https://data-api.binance.vision", client=http, sleep=rec, **kw)


async def test_klines_range_paginates() -> None:
    data = series(T0, 2500)
    rec = Recorder()
    client = make_client(kline_server(data), rec)
    out = await client.klines_range("BTCUSDT", "1m", T0, T0 + timedelta(minutes=2499))
    assert len(out) == 2500
    assert [c.open_time for c in out] == [c.open_time for c in data]
    assert client.used_weight == 10


async def test_429_waits_retry_after_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, json={})

    rec = Recorder()
    await make_client(httpx.MockTransport(handler), rec).ping()
    assert rec.sleeps == [7.0]


async def test_repeated_429_and_418_are_not_spammed() -> None:
    rec = Recorder()
    c429 = make_client(
        httpx.MockTransport(lambda r: httpx.Response(429, headers={"retry-after": "3"})), rec
    )
    with pytest.raises(RateLimitedError) as e:
        await c429.ping()
    assert e.value.retry_after == 3.0 and rec.sleeps == [3.0]

    rec2 = Recorder()
    c418 = make_client(
        httpx.MockTransport(lambda r: httpx.Response(418, headers={"retry-after": "120"})), rec2
    )
    with pytest.raises(RateLimitedError):
        await c418.ping()
    assert rec2.sleeps == []  # ban: no reintentar


async def test_5xx_and_network_errors_retry_with_backoff() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        if calls["n"] == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={})

    rec = Recorder()
    await make_client(httpx.MockTransport(handler), rec).ping()
    assert len(rec.sleeps) == 2 and all(s > 0 for s in rec.sleeps)


async def test_high_weight_throttles() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, headers={"x-mbx-used-weight-1m": "5000"})

    rec = Recorder()
    client = make_client(httpx.MockTransport(handler), rec)
    await client.ping()
    await client.ping()  # 5000 ≥ 80 % de 6000 → espera al siguiente minuto
    assert len(rec.sleeps) == 1 and 1 <= rec.sleeps[0] <= 61


# ------------------------------------------------------------------ archivo


def test_plan_files_monthly_then_daily() -> None:
    files = plan_files(
        "BTCUSDT", "1m", date(2026, 8, 15), date(2026, 10, 12), today=date(2026, 10, 12)
    )
    labels = [f.label for f in files]
    assert labels[:2] == ["2026-08", "2026-09"]
    assert labels[2:] == [f"2026-10-{d:02d}" for d in range(1, 12)]
    assert files[0].path == "/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-08.zip"


def _zip_csv(rows: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.csv", "\n".join(",".join(r) for r in rows))
    return buf.getvalue()


def _archive_row(c: Any) -> list[str]:
    r = [str(x) for x in rest_row(c)]
    r[0] = str(int(r[0]) * 1000)  # microsegundos como en los ficheros reales
    r[6] = str(int(r[6]) * 1000 + 999)
    return r


async def test_archive_checksum_verified_and_cached(tmp_path: Path) -> None:
    payload = _zip_csv([_archive_row(c) for c in series(T0, 3)])
    good = hashlib.sha256(payload).hexdigest()
    served = {"checksum": good, "zip_hits": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text=f"{served['checksum']}  file.zip")
        served["zip_hits"] += 1
        return httpx.Response(200, content=payload)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://data.binance.vision"
    )
    client = BinanceArchiveClient("https://data.binance.vision", tmp_path, client=http)
    f = ArchiveFile("BTCUSDT", "1m", "daily", "2026-01-05")
    data, digest = await client.fetch(f)
    assert digest == good
    await client.fetch(f)
    assert served["zip_hits"] == 1  # segunda vez desde caché

    served["checksum"] = "0" * 64
    (tmp_path / "BTCUSDT" / "1m" / f.name).unlink()
    with pytest.raises(ChecksumMismatchError):
        await client.fetch(f)

    candles = parse_archive(data, "BTCUSDT", "1m")
    assert [c.open_time for c in candles] == [c.open_time for c in series(T0, 3)]
    assert candles[0].close_time.microsecond == 999999


async def test_archive_retries_dropped_connections_but_never_skips_checksum(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import hashlib

    import httpx

    from app.providers.binance.archive import (
        ArchiveFile,
        BinanceArchiveClient,
        ChecksumMismatchError,
    )

    payload = b"zipbytes"
    good = hashlib.sha256(payload).hexdigest()
    f = ArchiveFile("BTCUSDT", "5m", "monthly", "2026-01")
    calls = {"n": 0}

    def make(checksum: str):  # type: ignore[no-untyped-def]
        calls["n"] = 0

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] <= 2:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            if request.url.path.endswith(".CHECKSUM"):
                return httpx.Response(200, text=f"{checksum}  file.zip")
            return httpx.Response(200, content=payload)

        client = httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(handler))

        async def no_sleep(_: float) -> None: ...

        return BinanceArchiveClient("https://x", tmp_path, client=client, sleep=no_sleep)

    data, digest = await make(good).fetch(f)
    assert data == payload and digest == good and calls["n"] >= 4  # 2 cortes reintentados

    with pytest.raises(
        ChecksumMismatchError
    ):  # tras reintentar, un hash distinto sigue siendo fatal
        await make("0" * 64).fetch(ArchiveFile("BTCUSDT", "5m", "monthly", "2026-02"))
