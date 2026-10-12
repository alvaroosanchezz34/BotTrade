"""Dependencias de FastAPI: BD, usuario actual, CSRF y roles."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import tokens_match
from app.auth.service import resolve_session
from app.core.config import Settings, get_settings
from app.persistence.db import get_sessionmaker
from app.persistence.models import Session, User

SESSION_COOKIE = "bt_session"
CSRF_COOKIE = "bt_csrf"
CSRF_HEADER = "X-CSRF-Token"


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


DB = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: Session

    @property
    def actor(self) -> str:
        return self.user.username


async def current_auth(request: Request, db: DB) -> AuthContext:
    resolved = await resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if resolved is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No autenticado")
    sess, user = resolved
    return AuthContext(user=user, session=sess)


Auth = Annotated[AuthContext, Depends(current_auth)]


async def require_csrf(
    auth: Auth, x_csrf_token: Annotated[str | None, Header(alias=CSRF_HEADER)] = None
) -> AuthContext:
    if not tokens_match(x_csrf_token, auth.session.csrf_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token CSRF inválido")
    return auth


async def require_admin(auth: Annotated[AuthContext, Depends(require_csrf)]) -> AuthContext:
    if auth.user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Permiso insuficiente")
    return auth


AdminAction = Annotated[AuthContext, Depends(require_admin)]
