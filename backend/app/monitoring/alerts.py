"""Alertas persistentes con deduplicación.

Una alerta activa por ``dedup_key``: si el problema se repite, se incrementa
``occurrences`` en vez de crear filas nuevas (sección 21: sin alertas repetitivas).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain import Severity, utcnow
from app.core.logging import redact
from app.persistence.models import Alert

log = logging.getLogger(__name__)


async def raise_alert(
    session: AsyncSession,
    *,
    severity: Severity,
    code: str,
    message: str,
    recommended_action: str,
    context: dict[str, Any] | None = None,
    dedup_key: str | None = None,
) -> None:
    key = dedup_key or code
    ctx = redact(context or {})
    stmt = insert(Alert).values(
        severity=severity.value,
        code=code,
        message=message,
        context=ctx,
        recommended_action=recommended_action,
        dedup_key=key,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Alert.dedup_key],
        index_where=text("resolved_at IS NULL"),
        set_={
            "occurrences": Alert.occurrences + 1,
            "last_seen_at": func.now(),
            "message": stmt.excluded.message,
            "context": stmt.excluded.context,
            "severity": stmt.excluded.severity,
        },
    )
    await session.execute(stmt)
    log.log(
        logging.CRITICAL if severity is Severity.CRITICAL else logging.WARNING,
        message,
        extra={"alert_code": code, "dedup_key": key, "context": ctx},
    )


async def resolve_alert(session: AsyncSession, dedup_key: str) -> int:
    result = await session.execute(
        update(Alert)
        .where(Alert.dedup_key == dedup_key, Alert.resolved_at.is_(None))
        .values(resolved_at=utcnow())
    )
    return int(getattr(result, "rowcount", 0) or 0)
