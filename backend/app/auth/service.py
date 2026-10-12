"""Usuarios, login con bloqueo por fuerza bruta y sesiones en servidor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import audit
from app.auth.security import (
    hash_password,
    needs_rehash,
    new_token,
    token_hash,
    verify_password,
)
from app.core.config import Settings
from app.core.domain import utcnow
from app.persistence.models import Session, User


class AuthError(Exception):
    """Credenciales inválidas o cuenta bloqueada. El mensaje es genérico a propósito."""


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    user: User


async def create_user(
    session: AsyncSession, username: str, password: str, role: str = "admin"
) -> User:
    user = User(username=username.strip().lower(), password_hash=hash_password(password), role=role)
    session.add(user)
    await session.flush()
    await audit(session, actor="system", action="user.create", target=user.username)
    return user


async def login(
    session: AsyncSession,
    settings: Settings,
    username: str,
    password: str,
    *,
    ip: str | None,
    user_agent: str | None,
) -> IssuedSession:
    now = utcnow()
    user = await session.scalar(
        select(User).where(User.username == username.strip().lower()).with_for_update()
    )
    # Verificar siempre (también si no existe) para no filtrar usuarios por tiempo.
    ok = verify_password(user.password_hash if user else None, password)

    if user is None or not user.is_active:
        await audit(session, actor=username[:64], action="auth.login_failed", ip=ip)
        raise AuthError("Credenciales inválidas")
    if user.locked_until and user.locked_until > now:
        await audit(session, actor=user.username, action="auth.login_locked", ip=ip)
        raise AuthError("Credenciales inválidas")
    if not ok:
        user.failed_logins += 1
        if user.failed_logins >= settings.login_max_attempts:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_logins = 0
        await audit(session, actor=user.username, action="auth.login_failed", ip=ip)
        raise AuthError("Credenciales inválidas")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now

    token, csrf = new_token(), new_token()
    session.add(
        Session(
            user_id=user.id,
            token_hash=token_hash(token),
            csrf_hash=token_hash(csrf),
            expires_at=now + timedelta(minutes=settings.session_ttl_minutes),
            ip=ip,
            user_agent=(user_agent or "")[:256],
        )
    )
    await audit(session, actor=user.username, action="auth.login", ip=ip)
    return IssuedSession(token=token, csrf_token=csrf, user=user)


async def resolve_session(session: AsyncSession, token: str | None) -> tuple[Session, User] | None:
    if not token:
        return None
    now = utcnow()
    row = await session.execute(
        select(Session, User)
        .join(User, User.id == Session.user_id)
        .where(
            Session.token_hash == token_hash(token),
            Session.revoked_at.is_(None),
            Session.expires_at > now,
            User.is_active.is_(True),
        )
    )
    found = row.first()
    if found is None:
        return None
    sess, user = found
    sess.last_seen_at = now
    return sess, user


async def revoke_session(session: AsyncSession, sess: Session) -> None:
    sess.revoked_at = utcnow()


async def revoke_all_sessions(session: AsyncSession, user: User) -> None:
    await session.execute(
        update(Session)
        .where(Session.user_id == user.id, Session.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
