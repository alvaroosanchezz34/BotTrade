from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.main import create_app
from app.api.ratelimit import control_limiter, login_limiter
from app.core.config import Environment, Settings
from app.persistence import db
from app.persistence.models import Base

TEST_DB_URL = os.environ.get(
    "BOTTRADE_TEST_DATABASE_URL",
    "postgresql+asyncpg://bottrade:bottrade_dev@127.0.0.1:5432/bottrade_test",
)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": Environment.TEST,
        "database_url": TEST_DB_URL,
        "log_json": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return make_settings()


def _alembic_config() -> Config:
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "migrations"))
    cfg.attributes["database_url"] = TEST_DB_URL
    return cfg


@pytest.fixture(scope="session", autouse=True)
async def database() -> AsyncIterator[None]:
    """Esquema limpio por sesión de tests, creado con las migraciones reales."""
    engine = db.init_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))

    def _upgrade(sync_conn: object) -> None:
        cfg = _alembic_config()
        cfg.attributes["connection"] = sync_conn
        command.upgrade(cfg, "head")

    async with engine.begin() as conn:
        await conn.run_sync(_upgrade)
    yield
    await db.dispose_engine()


@pytest.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    yield
    names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    async with db.get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    login_limiter.reset()
    control_limiter.reset()


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with db.get_sessionmaker()() as s:
        yield s
        await s.rollback()


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, manage_db=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


ADMIN_PASSWORD = "correct-horse-battery"


@pytest.fixture
async def admin_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    """Cliente autenticado como admin, con la cabecera CSRF ya puesta."""
    from app.auth.service import create_user

    async with db.session_scope() as s:
        await create_user(s, "admin", ADMIN_PASSWORD)
    r = await client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = client.cookies["bt_csrf"]
    return client
