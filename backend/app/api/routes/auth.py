from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import CSRF_COOKIE, DB, SESSION_COOKIE, Auth, SettingsDep, require_csrf
from app.api.ratelimit import client_ip, limit, login_limiter
from app.audit.log import audit
from app.auth.service import AuthError, login, revoke_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class MeOut(BaseModel):
    username: str
    role: str


@router.post("/login", response_model=MeOut, dependencies=[Depends(limit(login_limiter, "login"))])
async def do_login(
    body: LoginIn, request: Request, response: Response, db: DB, settings: SettingsDep
) -> MeOut:
    try:
        issued = await login(
            db,
            settings,
            body.username,
            body.password,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AuthError:
        await db.commit()  # persistir contadores de fallo y auditoría
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas") from None

    max_age = settings.session_ttl_minutes * 60
    common = {
        "secure": settings.cookie_secure,
        "samesite": "strict",
        "max_age": max_age,
        "path": "/",
    }
    response.set_cookie(SESSION_COOKIE, issued.token, httponly=True, **common)  # type: ignore[arg-type]
    # Doble envío: el frontend lee esta cookie y la manda en la cabecera X-CSRF-Token.
    response.set_cookie(CSRF_COOKIE, issued.csrf_token, httponly=False, **common)  # type: ignore[arg-type]
    return MeOut(username=issued.user.username, role=issued.user.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def do_logout(
    request: Request, response: Response, db: DB, auth: Annotated[Auth, Depends(require_csrf)]
) -> None:
    await revoke_session(db, auth.session)
    await audit(db, actor=auth.actor, action="auth.logout", ip=client_ip(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/me", response_model=MeOut)
async def me(auth: Auth) -> MeOut:
    return MeOut(username=auth.user.username, role=auth.user.role)
