"""Motor de riesgo determinista. Puede rechazar cualquier señal y siempre explica por qué.

No conoce bases de datos ni brokers: recibe una foto del estado y devuelve una
``RiskDecision``. Ningún módulo de IA ni estrategia puede saltárselo: el
``OrderManager`` solo acepta decisiones aprobadas por esta clase.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.core.domain import InstrumentRules, Quote, RiskCheck, RiskDecision, Signal, SignalAction
from app.core.money import ZERO
from app.risk.limits import RiskLimits, RiskState
from app.risk.sizing import CostModel, position_size


@dataclass(frozen=True)
class OpenPositionView:
    symbol: str
    notional: Decimal  # valor de mercado actual
    risk_amount: Decimal  # pérdida estimada si salta su stop


@dataclass(frozen=True)
class PortfolioView:
    equity: Decimal
    cash: Decimal
    positions: tuple[OpenPositionView, ...] = ()

    @property
    def exposure(self) -> Decimal:
        return sum((p.notional for p in self.positions), ZERO)


@dataclass(frozen=True)
class MarketContext:
    """Condiciones externas que el engine aporta en cada decisión."""

    data_ready: bool = True
    data_reason: str = "ok"
    connections_ok: bool = True
    reconciliation_ok: bool = True
    quote: Quote | None = None
    # Correlación reciente entre símbolos, p. ej. {("BTCUSDT","ETHUSDT"): 0.82}
    correlations: Mapping[tuple[str, str], float] = field(default_factory=dict)


class RiskEngine:
    def __init__(self, limits: RiskLimits, costs: CostModel):
        self.limits = limits
        self.costs = costs

    def update_latches(self, state: RiskState, equity: Decimal) -> list[str]:
        """Activa bloqueos persistentes. Devuelve los nuevos para alertar."""
        new: list[str] = []
        if state.drawdown(equity) >= self.limits.max_drawdown:
            reason = (
                f"drawdown {state.drawdown(equity):.2f} ≥ límite {self.limits.max_drawdown:.2f}"
            )
            if not any(r.startswith("drawdown") for r in state.latched_reasons):
                state.latch(reason)
                new.append(reason)
        if state.execution_errors >= self.limits.max_execution_errors:
            reason = f"{state.execution_errors} errores de ejecución"
            if not any("errores de ejecución" in r for r in state.latched_reasons):
                state.latch(reason)
                new.append(reason)
        return new

    def evaluate(
        self,
        signal: Signal,
        *,
        now: datetime,
        state: RiskState,
        portfolio: PortfolioView,
        rules: InstrumentRules,
        market: MarketContext,
        expected_entry: Decimal | None = None,
    ) -> RiskDecision:
        L = self.limits
        checks: list[RiskCheck] = []

        def check(name: str, passed: bool, detail: str) -> None:
            checks.append(RiskCheck(name, passed, detail))

        equity = portfolio.equity
        self.update_latches(state, equity)

        check(
            "kill_switch",
            not state.kill_switch_active,
            "kill switch activo" if state.kill_switch_active else "inactivo",
        )
        check("pausa", not state.paused, "entradas pausadas" if state.paused else "activo")
        check(
            "bloqueos",
            not state.latched_reasons,
            "; ".join(state.latched_reasons) or "sin bloqueos",
        )
        check("datos", market.data_ready, market.data_reason)
        check(
            "conexiones", market.connections_ok, "ok" if market.connections_ok else "conexión caída"
        )
        check(
            "reconciliación",
            market.reconciliation_ok,
            "ok" if market.reconciliation_ok else "discrepancia sin resolver",
        )
        check("solo_largos", signal.action is SignalAction.ENTER_LONG, signal.action.value)
        check(
            "vigencia",
            now <= signal.valid_until,
            f"válida hasta {signal.valid_until.isoformat()}",
        )
        check("instrumento", rules.status == "TRADING", rules.status)

        dl = state.daily_loss(equity)
        check("pérdida_diaria", dl < L.max_daily_loss, f"{dl:.2f} / {L.max_daily_loss:.2f}")
        dd = state.drawdown(equity)
        check("drawdown", dd < L.max_drawdown, f"{dd:.2f} / {L.max_drawdown:.2f}")
        check(
            "pérdidas_consecutivas",
            state.cooldown_until_day is None,
            f"{state.consecutive_losses} seguidas"
            + (" (enfriamiento hasta fin de día)" if state.cooldown_until_day else ""),
        )
        check(
            "operaciones_día",
            state.entries_today < L.max_trades_per_day,
            f"{state.entries_today} / {L.max_trades_per_day}",
        )
        check(
            "posiciones",
            len(portfolio.positions) < L.max_positions,
            f"{len(portfolio.positions)} / {L.max_positions}",
        )
        dup = any(p.symbol == signal.symbol for p in portfolio.positions)
        check(
            "duplicada", not dup, "ya hay posición en el activo" if dup else "sin posición previa"
        )

        if market.quote is not None and market.quote.mid > 0:
            spread_bps = market.quote.spread / market.quote.mid * Decimal(10_000)
            check(
                "spread",
                spread_bps <= L.max_spread_bps,
                f"{spread_bps:.2f} bps / {L.max_spread_bps}",
            )
            reference = market.quote.ask
        else:
            check("spread", True, "sin cotización: se aplica el spread supuesto del modelo")
            reference = signal.reference_price
        entry = expected_entry or self.costs.buy_fill(reference)

        # Riesgo agregado de posiciones correlacionadas.
        corr_risk = ZERO
        for p in portfolio.positions:
            key = tuple(sorted((p.symbol, signal.symbol)))
            c = market.correlations.get((key[0], key[1]))
            if c is not None and c >= L.correlation_threshold:
                corr_risk += p.risk_amount
        corr_cap = L.risk_per_trade * L.max_correlated_risk_multiple
        risk_budget = min(L.risk_per_trade, max(ZERO, corr_cap - corr_risk))
        # Nunca arriesgar más de lo que queda antes de tocar el límite diario.
        risk_budget = min(risk_budget, max(ZERO, L.max_daily_loss - dl))
        check(
            "riesgo_correlacionado",
            risk_budget > 0,
            f"riesgo correlacionado abierto {corr_risk:.2f}, presupuesto {risk_budget:.2f}",
        )

        exposure_room = min(
            L.max_exposure - portfolio.exposure,
            L.max_asset_exposure,
        )
        quantity = notional = risk_amount = costs = ZERO
        if signal.stop_price is None:
            check("tamaño", False, "la señal no trae stop")
        else:
            size = position_size(
                entry_price=entry,
                stop_price=signal.stop_price,
                risk_budget=risk_budget,
                cash=portfolio.cash,
                exposure_room=exposure_room,
                rules=rules,
                costs=self.costs,
            )
            check(
                "tamaño",
                size.ok,
                size.reason if not size.ok else f"{size.quantity} (limitado por {size.binding})",
            )
            if size.ok:
                quantity, notional = size.quantity, size.notional
                risk_amount, costs = size.risk_amount, size.estimated_costs

        approved = all(c.passed for c in checks)
        return RiskDecision(
            signal_id=signal.id,
            approved=approved,
            checks=tuple(checks),
            quantity=quantity if approved else ZERO,
            notional=notional if approved else ZERO,
            risk_amount=risk_amount if approved else ZERO,
            estimated_costs=costs if approved else ZERO,
            decided_at=now,
        )
