import os
from datetime import timedelta

import httpx
from sqlalchemy import select

from app.core.domain import Severity, utcnow
from app.monitoring.alerts import raise_alert, resolve_alert
from app.persistence import db
from app.persistence.models import Alert, ConnectionStatus, EngineHeartbeat


async def test_liveness(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_readiness_without_engine_is_degraded(client: httpx.AsyncClient) -> None:
    r = await client.get("/health/ready")
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "degraded"
    assert body["mode"] == "PAPER"
    assert body["components"]["database"]["status"] == "ok"
    assert body["components"]["engine"]["status"] == "unknown"
    assert body["components"]["market_data"]["status"] == "unknown"


async def test_readiness_all_ok(client: httpx.AsyncClient) -> None:
    now = utcnow()
    async with db.session_scope() as s:
        s.add(EngineHeartbeat(id="engine", mode="PAPER", started_at=now, last_beat_at=now, pid=1))
        s.add(ConnectionStatus(name="binance-ws", status="UP", last_valid_data_at=now))
    body = (await client.get("/health/ready")).json()
    assert body["status"] == "ok"


async def test_readiness_detects_dead_engine_and_stale_data(client: httpx.AsyncClient) -> None:
    old = utcnow() - timedelta(minutes=10)
    async with db.session_scope() as s:
        s.add(EngineHeartbeat(id="engine", mode="PAPER", started_at=old, last_beat_at=old, pid=1))
        s.add(ConnectionStatus(name="binance-ws", status="UP", last_valid_data_at=old))
    comps = (await client.get("/health/ready")).json()["components"]
    assert comps["engine"]["status"] == "down"
    assert comps["market_data"]["status"] == "degraded"


async def test_alert_dedup_and_resolve() -> None:
    for _ in range(3):
        async with db.session_scope() as s:
            await raise_alert(
                s,
                severity=Severity.WARNING,
                code="DATA_STALE",
                message="Datos obsoletos BTCUSDT",
                recommended_action="Revisar conexión",
                dedup_key="DATA_STALE:BTCUSDT",
                context={"api_key": "leak?"},
            )
    async with db.get_sessionmaker()() as s:
        rows = (await s.scalars(select(Alert))).all()
    assert len(rows) == 1
    assert rows[0].occurrences == 3
    assert rows[0].context == {"api_key": "***"}

    async with db.session_scope() as s:
        assert await resolve_alert(s, "DATA_STALE:BTCUSDT") == 1
        await raise_alert(
            s,
            severity=Severity.WARNING,
            code="DATA_STALE",
            message="otra vez",
            recommended_action="Revisar conexión",
            dedup_key="DATA_STALE:BTCUSDT",
        )
    async with db.get_sessionmaker()() as s:
        assert len((await s.scalars(select(Alert))).all()) == 2


async def test_alembic_models_in_sync() -> None:
    """El esquema de las migraciones coincide con los modelos (sin migraciones olvidadas)."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.persistence.models import Base

    def _diff(sync_conn: object) -> list[object]:
        ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})  # type: ignore[arg-type]
        return list(compare_metadata(ctx, Base.metadata))

    async with db.get_engine().connect() as conn:
        diff = await conn.run_sync(_diff)
    assert diff == []


async def test_readiness_db_down_returns_503(settings: object) -> None:
    """Con la BD inaccesible, readiness responde 503 sin filtrar detalles internos."""
    from app.api.main import create_app
    from tests.conftest import make_settings

    bad = make_settings(database_url="postgresql+asyncpg://nobody:x@127.0.0.1:1/none")
    saved_engine, saved_maker = db._engine, db._sessionmaker
    try:
        db.init_engine(bad.database_url.get_secret_value())
        app = create_app(bad, manage_db=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/health/ready")
        assert r.status_code == 503
        assert "nobody" not in r.text and os.sep + "x@" not in r.text
        await db.get_engine().dispose()
    finally:
        db._engine, db._sessionmaker = saved_engine, saved_maker
