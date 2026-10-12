"""Persistencia del estado de riesgo (sección 11: el reinicio no borra límites).

El mismo ``RiskState`` puro que usa el backtest se carga y guarda aquí. La fila lleva
control de versión optimista: dos procesos que modifiquen el estado a la vez no se pisan.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain import utcnow
from app.persistence import models as m
from app.risk.limits import RiskState


def _to_state(row: m.RiskState) -> RiskState:
    return RiskState(
        trading_day=row.trading_day,
        day_start_equity=row.day_start_equity,
        peak_equity=row.peak_equity,
        consecutive_losses=row.consecutive_losses,
        entries_today=row.entries_today,
        paused=row.paused,
        kill_switch_active=row.kill_switch_active,
        latched_reasons=[str(r) for r in row.block_reasons],
        execution_errors=row.execution_errors,
        cooldown_until_day=row.cooldown_until_day,
    )


def _apply(row: m.RiskState, state: RiskState) -> None:
    row.trading_day = state.trading_day
    row.day_start_equity = state.day_start_equity
    row.peak_equity = state.peak_equity
    row.consecutive_losses = state.consecutive_losses
    row.entries_today = state.entries_today
    row.paused = state.paused
    row.kill_switch_active = state.kill_switch_active
    row.block_reasons = list(state.latched_reasons)
    row.entries_blocked = bool(state.latched_reasons) or state.kill_switch_active
    row.execution_errors = state.execution_errors
    row.cooldown_until_day = state.cooldown_until_day


async def load_risk_state(session: AsyncSession, mode: str) -> RiskState | None:
    row = await session.get(m.RiskState, mode)
    return _to_state(row) if row else None


async def load_or_create_risk_state(session: AsyncSession, mode: str, equity: Decimal) -> RiskState:
    """Si no existe, arranca con el equity actual. Nunca sobrescribe uno existente."""
    existing = await load_risk_state(session, mode)
    if existing is not None:
        return existing
    state = RiskState.fresh(utcnow(), equity)
    await save_risk_state(session, mode, state)
    return state


async def save_risk_state(session: AsyncSession, mode: str, state: RiskState) -> None:
    row = await session.get(m.RiskState, mode)
    if row is None:
        row = m.RiskState(mode=mode)
        _apply(row, state)
        session.add(row)
    else:
        _apply(row, state)
    await session.flush()
