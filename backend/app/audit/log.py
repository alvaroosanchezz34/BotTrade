"""Registro de auditoría: quién cambió qué y cuándo."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import redact
from app.persistence.models import AuditLog, SystemEvent


async def audit(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    target: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            target=target,
            before=redact(before) if before is not None else None,
            after=redact(after) if after is not None else None,
            ip=ip,
        )
    )


async def system_event(
    session: AsyncSession, *, kind: str, source: str, detail: dict[str, Any] | None = None
) -> None:
    session.add(SystemEvent(kind=kind, source=source, detail=redact(detail or {})))
