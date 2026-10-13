"""App factory de FastAPI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import auth, control, dashboard, health, lab, ops, strategies, trading, ws
from app.core.config import Environment, Settings, get_settings
from app.core.logging import configure_logging
from app.persistence import db

log = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def create_app(settings: Settings | None = None, *, manage_db: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if manage_db:
            db.init_engine(settings.database_url.get_secret_value())
        log.info("api arrancada", extra={"mode": settings.trading_mode.value})
        yield
        if manage_db:
            await db.dispose_engine()

    is_prod = settings.environment is Environment.PRODUCTION
    app = FastAPI(
        title="BotTrade API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if is_prod:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Nunca devolver trazas internas al cliente.
        log.exception("error no controlado", extra={"path": request.url.path})
        return JSONResponse({"detail": "Error interno"}, status_code=500)

    app.include_router(health.router)
    app.include_router(auth.router)
    for r in (dashboard, trading, strategies, ops, control, lab, ws):
        app.include_router(r.router)
    return app


def build() -> FastAPI:
    """Entrypoint para uvicorn: ``uvicorn app.api.main:build --factory``."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    return create_app(settings)
