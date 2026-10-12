"""Reconciliación entre el estado interno (BD) y el del proveedor (sección 12).

Una pasada:
1. Sincroniza cada orden no terminal consultando al proveedor por ``client_order_id``
   (esto también es la recuperación tras un reinicio).
2. Busca órdenes vivas en el proveedor que la BD no conoce (huérfanas/duplicadas).
3. Busca duplicados internos (dos entradas vivas o dos posiciones abiertas por símbolo).
4. Compara el saldo del activo base con ``línea base + posiciones abiertas`` para detectar
   posiciones fantasma (la BD cree que existe, el proveedor no) o saldo sin registrar.

Toda discrepancia es CRÍTICA: se genera alerta y se bloquean las entradas (``latch``)
hasta que una persona la revise y reanude (``controls.resume``). Un resultado limpio
posterior NO levanta el bloqueo por sí solo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select

from app.core.domain import InstrumentRules, OrderStatus, Severity, utcnow
from app.core.money import ZERO
from app.execution.adapter import AmbiguousResponseError
from app.execution.order_manager import NON_TERMINAL, OrderManager
from app.monitoring.alerts import raise_alert, resolve_alert
from app.persistence import db
from app.persistence import models as m
from app.risk.store import load_risk_state, save_risk_state

KINDS = (
    "order_unknown",
    "orphan_order",
    "duplicate_entry",
    "duplicate_position",
    "ghost_position",
    "untracked_balance",
    "provider_unreachable",
)


@dataclass(frozen=True)
class Discrepancy:
    kind: str
    key: str  # símbolo o client_order_id
    detail: str

    @property
    def dedup_key(self) -> str:
        return f"reconcile:{self.kind}:{self.key}"


@dataclass(frozen=True)
class ReconciliationReport:
    checked_at: datetime
    discrepancies: tuple[Discrepancy, ...] = field(default_factory=tuple)
    orders_synced: int = 0

    @property
    def ok(self) -> bool:
        return not self.discrepancies


async def reconcile(
    manager: OrderManager,
    *,
    baseline: Mapping[str, Decimal],
    balance_tolerance_pct: Decimal = Decimal(0),
) -> ReconciliationReport:
    """``baseline``: saldo inicial por activo base (en testnet la cuenta viene con fondos)."""
    mode = manager.mode
    found: list[Discrepancy] = []

    # 1) órdenes no terminales
    async with db.session_scope() as s:
        live_ids = list(
            (
                await s.execute(
                    select(m.OrderRow.id).where(
                        m.OrderRow.mode == mode, m.OrderRow.status.in_(NON_TERMINAL)
                    )
                )
            ).scalars()
        )
    synced = 0
    for oid in live_ids:
        order = await manager.sync_order(oid)
        synced += 1
        if order.status == OrderStatus.UNKNOWN.value:
            found.append(
                Discrepancy(
                    "order_unknown", order.client_order_id, "no se pudo consultar al proveedor"
                )
            )

    # 2) huérfanas en el proveedor
    try:
        provider_open = await manager.adapter.open_orders()
        balances = await manager.adapter.balances()
    except AmbiguousResponseError as exc:
        found.append(Discrepancy("provider_unreachable", manager.adapter.name, str(exc)))
        provider_open, balances = [], None

    async with db.session_scope() as s:
        known = set(
            (
                await s.execute(select(m.OrderRow.client_order_id).where(m.OrderRow.mode == mode))
            ).scalars()
        )
        for r in provider_open:
            if r.client_order_id.startswith("bt-") and r.client_order_id not in known:
                found.append(
                    Discrepancy(
                        "orphan_order", r.client_order_id, "orden viva en el proveedor sin registro"
                    )
                )

        # 3) duplicados internos
        dup_entries = (
            await s.execute(
                select(m.OrderRow.symbol)
                .where(
                    m.OrderRow.mode == mode,
                    m.OrderRow.purpose == "ENTRY",
                    m.OrderRow.status.in_(NON_TERMINAL),
                )
                .group_by(m.OrderRow.symbol)
                .having(func.count() > 1)
            )
        ).scalars()
        found += [
            Discrepancy("duplicate_entry", sym, "varias entradas vivas") for sym in dup_entries
        ]
        open_positions = (
            (
                await s.execute(
                    select(m.Position).where(m.Position.mode == mode, m.Position.status == "OPEN")
                )
            )
            .scalars()
            .all()
        )
        seen: set[str] = set()
        for p in open_positions:
            if p.symbol in seen:
                found.append(
                    Discrepancy("duplicate_position", p.symbol, "varias posiciones abiertas")
                )
            seen.add(p.symbol)

    # 4) saldos
    if balances is not None:
        found += _compare_balances(
            manager.rules, open_positions, balances, baseline, balance_tolerance_pct
        )

    report = ReconciliationReport(utcnow(), tuple(found), synced)
    await _publish(manager, report)
    return report


def _compare_balances(
    rules: Mapping[str, InstrumentRules],
    positions: Sequence[m.Position],
    balances: Mapping[str, Decimal],
    baseline: Mapping[str, Decimal],
    tolerance_pct: Decimal,
) -> list[Discrepancy]:
    out: list[Discrepancy] = []
    expected: dict[str, Decimal] = {}
    asset_rules: dict[str, InstrumentRules] = {}
    for r in rules.values():
        asset_rules.setdefault(r.base_asset, r)
        expected.setdefault(r.base_asset, baseline.get(r.base_asset, ZERO))
    for p in positions:
        base = rules[p.symbol].base_asset
        expected[base] += p.quantity
    for asset, exp in expected.items():
        actual = balances.get(asset, ZERO)
        r = asset_rules[asset]
        tol = max(r.step_size, exp * tolerance_pct / 100)
        diff = actual - exp
        if abs(diff) <= tol:
            continue
        sym = next(s for s, rr in rules.items() if rr.base_asset == asset)
        if diff < 0:
            out.append(
                Discrepancy(
                    "ghost_position",
                    sym,
                    f"BD espera {exp} {asset}, el proveedor tiene {actual}",
                )
            )
        else:
            out.append(
                Discrepancy(
                    "untracked_balance",
                    sym,
                    f"el proveedor tiene {actual} {asset}, la BD solo justifica {exp}",
                )
            )
    return out


async def _publish(manager: OrderManager, report: ReconciliationReport) -> None:
    async with db.session_scope() as s:
        state = await load_risk_state(s, manager.mode)
        active = {d.dedup_key for d in report.discrepancies}
        for d in report.discrepancies:
            await raise_alert(
                s,
                severity=Severity.CRITICAL,
                code=f"reconciliation_{d.kind}",
                message=f"Discrepancia de reconciliación ({d.kind}) en {d.key}: {d.detail}",
                recommended_action=(
                    "Entradas bloqueadas. Revisa el proveedor y la BD; reanuda manualmente "
                    "cuando coincidan."
                ),
                context={"kind": d.kind, "key": d.key},
                dedup_key=d.dedup_key,
            )
            if state is not None and d.kind != "provider_unreachable":
                state.latch(f"reconciliación: {d.kind} {d.key}")
        # Resuelve las alertas de discrepancias que ya no existen.
        for kind in KINDS:
            rows = (
                await s.execute(
                    select(m.Alert.dedup_key).where(
                        m.Alert.dedup_key.like(f"reconcile:{kind}:%"),
                        m.Alert.resolved_at.is_(None),
                    )
                )
            ).scalars()
            for key in list(rows):
                if key not in active:
                    await resolve_alert(s, key)
        if state is not None:
            await save_risk_state(s, manager.mode, state)
