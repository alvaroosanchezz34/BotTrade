"""Ayudas de tests de API: clientes por rol y datos sembrados con el flujo real."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from app.api.main import create_app
from app.auth.service import create_user
from app.core.domain import utcnow
from app.market_data import store
from app.persistence import db
from tests.conftest import ADMIN_PASSWORD, make_settings
from tests.exec_helpers import Harness, make_signal
from tests.factories import candle

READ_ENDPOINTS = [
    "/api/dashboard/overview",
    "/api/markets",
    "/api/markets/BTCUSDT/candles",
    "/api/strategies",
    "/api/strategies/momentum",
    "/api/signals",
    "/api/orders",
    "/api/positions",
    "/api/trades",
    "/api/performance",
    "/api/backtests",
    "/api/risk",
    "/api/alerts",
    "/api/audit",
    "/api/events",
    "/api/commands",
    "/api/system/status",
    "/api/auth/me",
    "/api/auth/sessions",
]


@asynccontextmanager
async def client_as(
    username: str, role: str, **settings_kw: object
) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(**settings_kw)
    async with db.session_scope() as s:
        await create_user(s, username, ADMIN_PASSWORD, role=role)
    app = create_app(settings, manage_db=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post("/api/auth/login", json={"username": username, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = c.cookies["bt_csrf"]
        yield c


async def seed_closed_and_open_trade() -> Harness:
    """Una operación cerrada con pérdida y otra abierta, generadas por el flujo real."""
    h = await Harness.create()
    sig = make_signal()
    await h.manager.submit_entry(sig, await h.decide(sig))
    h.adapter.set_price("BTCUSDT", Decimal(79000))
    from sqlalchemy import select

    from app.persistence import models as m

    async with db.session_scope() as s:
        pos = (await s.execute(select(m.Position))).scalar_one()
    await h.manager.submit_exit(pos.id, "EXIT_STOP")
    h.adapter.set_price("BTCUSDT", Decimal(3000))
    eth = make_signal("ETHUSDT", "3000", "2985", "3100")
    h.adapter.set_price("ETHUSDT", Decimal(3000))
    await h.manager.submit_entry(eth, await h.decide(eth))
    return h


async def seed_candles(n: int = 5, *, end: datetime | None = None, symbol: str = "BTCUSDT") -> None:
    end = end or (utcnow().replace(second=0, microsecond=0) - timedelta(minutes=1))
    start = end - timedelta(minutes=n - 1)
    cs = [
        candle(start + timedelta(minutes=i), symbol=symbol, o=str(80000 + i), c=str(80000 + i))
        for i in range(n)
    ]
    async with db.session_scope() as s:
        await store.save_candles(s, cs)


OLD = datetime(2026, 1, 1, tzinfo=UTC)
