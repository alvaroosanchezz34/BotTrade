"""Exchange simulado para BACKTEST/PAPER y para los tests de fallos.

No envía nada a ninguna red. Modela de forma conservadora:

* Órdenes a mercado: compra al ``ask`` (o precio de referencia + medio spread) más
  deslizamiento; venta al ``bid`` menos deslizamiento. Comisión *taker*.
* Liquidez: si ``available_qty`` es menor que la cantidad, la orden a mercado se ejecuta
  parcialmente y el resto **expira** (como una orden a mercado real sin liquidez).
* Órdenes límite: reposan en el libro y solo se ejecutan si el precio las *atraviesa*
  (tocarlas no basta), con comisión *maker*, por el precio límite.
* Comisiones en la moneda de cotización (simplificación: Binance real cobra las compras
  en la moneda base salvo que se pague con BNB; el adaptador de testnet debe reconciliarlo).
* Rechazos del exchange: saldo insuficiente, filtros LOT_SIZE/NOTIONAL, identificador duplicado.
* Fallos inyectables (``inject``) para probar timeouts y respuestas ambiguas.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain import InstrumentRules, OrderStatus, OrderType, Quote, Side, utcnow
from app.core.money import ZERO, floor_to_step
from app.execution.adapter import (
    AmbiguousResponseError,
    ExecutionReport,
    FillReport,
    OrderRejectedError,
    OrderRequest,
)
from app.risk.sizing import CostModel


class Fault(StrEnum):
    REJECT = "REJECT"  # rechazo explícito del exchange
    TIMEOUT_BEFORE = "TIMEOUT_BEFORE"  # la petición no llega: la orden NO existe
    TIMEOUT_AFTER = "TIMEOUT_AFTER"  # la orden se procesa pero la respuesta se pierde
    QUERY_TIMEOUT = "QUERY_TIMEOUT"  # get_order / cancel fallan con respuesta ambigua


@dataclass
class _Order:
    request: OrderRequest
    exchange_order_id: str
    status: OrderStatus
    fills: list[FillReport] = field(default_factory=list)

    @property
    def filled_qty(self) -> Decimal:
        return sum((f.quantity for f in self.fills), ZERO)

    @property
    def remaining(self) -> Decimal:
        return self.request.quantity - self.filled_qty

    def report(self, reject_reason: str | None = None) -> ExecutionReport:
        return ExecutionReport(
            client_order_id=self.request.client_order_id,
            exchange_order_id=self.exchange_order_id,
            status=self.status,
            filled_qty=self.filled_qty,
            fills=tuple(self.fills),
            reject_reason=reject_reason,
        )


class SimulatedExecution:
    name = "simulated"

    def __init__(
        self,
        *,
        costs: CostModel,
        rules: dict[str, InstrumentRules],
        balances: dict[str, Decimal],
        clock: Callable[[], datetime] = utcnow,
    ):
        self.costs = costs
        self.rules = rules
        self._balances = dict(balances)
        self._clock = clock
        self._orders: dict[str, _Order] = {}
        self._quotes: dict[str, Quote] = {}
        self._prices: dict[str, Decimal] = {}
        self._available: dict[str, Decimal | None] = {}
        self._faults: deque[tuple[Fault, str | None]] = deque()
        self._seq = 0
        self.submit_calls = 0  # para comprobar en tests que no se reenvía dos veces

    async def restore_from_ledger(
        self, session: AsyncSession, mode: str, initial_capital: Decimal
    ) -> None:
        """Reconstruye los saldos del exchange simulado desde el libro de ejecuciones.

        El estado de un exchange simulado es *derivado* del registro persistido: tras un
        reinicio, caja = capital + fills (con comisiones) y activo base = posiciones abiertas
        (más la línea base). Las órdenes vivas no se restauran: la reconciliación las descarta.
        """
        from app.execution.accounting import account_snapshot
        from app.persistence import models as m

        snap = await account_snapshot(
            session, mode, initial_capital=initial_capital, marks={}, rules=self.rules
        )
        quotes = {r.quote_asset for r in self.rules.values()}
        for asset in quotes:
            self._balances[asset] = snap.cash
        for rules in self.rules.values():
            self._balances.setdefault(rules.base_asset, ZERO)
        base_held: dict[str, Decimal] = {}
        positions = (
            await session.execute(
                select(m.Position).where(m.Position.mode == mode, m.Position.status == "OPEN")
            )
        ).scalars()
        for p in positions:
            base = self.rules[p.symbol].base_asset
            base_held[base] = base_held.get(base, ZERO) + p.quantity
        row = await session.get(m.SystemSetting, f"account:{mode}")
        baseline = (row.value.get("baseline") if row else None) or {}
        for rules in self.rules.values():
            b = rules.base_asset
            self._balances[b] = Decimal(str(baseline.get(b, 0))) + base_held.get(b, ZERO)

    # ------------------------------------------------------------ mercado simulado

    def set_quote(self, quote: Quote, *, available_qty: Decimal | None = None) -> None:
        self._quotes[quote.symbol] = quote
        self._available[quote.symbol] = available_qty

    def set_price(
        self, symbol: str, price: Decimal, *, available_qty: Decimal | None = None
    ) -> None:
        """Precio de referencia sin bid/ask: se aplica el spread supuesto del modelo."""
        self._prices[symbol] = price
        self._quotes.pop(symbol, None)
        self._available[symbol] = available_qty

    def inject(self, fault: Fault, *, count: int = 1, symbol: str | None = None) -> None:
        for _ in range(count):
            self._faults.append((fault, symbol))

    def _take_fault(self, kinds: set[Fault], symbol: str) -> Fault | None:
        for i, (fault, sym) in enumerate(self._faults):
            if fault in kinds and sym in (None, symbol):
                del self._faults[i]
                return fault
        return None

    # ------------------------------------------------------------ ExecutionAdapter

    async def submit(self, request: OrderRequest) -> ExecutionReport:
        self.submit_calls += 1
        fault = self._take_fault(
            {Fault.REJECT, Fault.TIMEOUT_BEFORE, Fault.TIMEOUT_AFTER}, request.symbol
        )
        if fault is Fault.TIMEOUT_BEFORE:
            raise AmbiguousResponseError("timeout simulado antes de llegar al exchange")
        if fault is Fault.REJECT:
            raise OrderRejectedError("rechazo simulado del exchange")
        if request.client_order_id in self._orders:
            raise OrderRejectedError("clientOrderId duplicado")
        self._validate(request)

        self._seq += 1
        order = _Order(request, f"SIM-{self._seq}", OrderStatus.ACKNOWLEDGED)
        self._orders[request.client_order_id] = order
        if request.type is OrderType.MARKET:
            self._execute_market(order)
        else:
            # Una orden límite que ya es ejecutable en el precio actual se trata como taker.
            self._try_cross_now(order)
        if fault is Fault.TIMEOUT_AFTER:
            raise AmbiguousResponseError("respuesta perdida tras procesar la orden")
        return order.report()

    async def get_order(self, symbol: str, client_order_id: str) -> ExecutionReport | None:
        if self._take_fault({Fault.QUERY_TIMEOUT}, symbol):
            raise AmbiguousResponseError("timeout simulado consultando la orden")
        order = self._orders.get(client_order_id)
        return order.report() if order else None

    async def cancel(self, symbol: str, client_order_id: str) -> ExecutionReport:
        if self._take_fault({Fault.QUERY_TIMEOUT}, symbol):
            raise AmbiguousResponseError("timeout simulado cancelando la orden")
        order = self._orders.get(client_order_id)
        if order is None:
            raise OrderRejectedError("orden desconocida")
        if order.status in (OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED):
            order.status = OrderStatus.CANCELED
        return order.report()

    async def open_orders(self) -> list[ExecutionReport]:
        return [
            o.report()
            for o in self._orders.values()
            if o.status in (OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED)
        ]

    async def balances(self) -> dict[str, Decimal]:
        return dict(self._balances)

    # ------------------------------------------------------------ avance del mercado

    def advance(
        self, symbol: str, *, low: Decimal, high: Decimal, available_qty: Decimal | None = None
    ) -> None:
        """Una nueva vela: ejecuta órdenes límite atravesadas. Solo con precio estrictamente
        mejor que el límite (conservador). ``available_qty`` limita el volumen de la vela."""
        budget = available_qty
        for order in list(self._orders.values()):
            req = order.request
            if req.symbol != symbol or order.status not in (
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
            ):
                continue
            assert req.limit_price is not None
            crossed = low < req.limit_price if req.side is Side.BUY else high > req.limit_price
            if not crossed:
                continue
            qty = order.remaining if budget is None else min(order.remaining, budget)
            if qty <= 0:
                continue
            if self._fill(order, qty, req.limit_price, maker=True) and budget is not None:
                budget -= qty

    # ------------------------------------------------------------ internos

    def _validate(self, req: OrderRequest) -> None:
        rules = self.rules.get(req.symbol)
        if rules is None or rules.status != "TRADING":
            raise OrderRejectedError("símbolo no operable")
        if req.quantity < rules.min_qty or req.quantity > rules.max_qty:
            raise OrderRejectedError("LOT_SIZE: cantidad fuera de rango")
        if floor_to_step(req.quantity, rules.step_size) != req.quantity:
            raise OrderRejectedError("LOT_SIZE: cantidad no múltiplo de stepSize")
        ref = req.limit_price or self._reference(req.symbol, req.side)
        if req.quantity * ref < rules.min_notional:
            raise OrderRejectedError("NOTIONAL: por debajo del mínimo")
        if req.type is OrderType.LIMIT:
            if req.limit_price is None or req.limit_price <= 0:
                raise OrderRejectedError("orden límite sin precio")
            if floor_to_step(req.limit_price, rules.tick_size) != req.limit_price:
                raise OrderRejectedError("PRICE_FILTER: precio no múltiplo de tickSize")
        if req.side is Side.BUY:
            needed = req.quantity * ref * (1 + self.costs.taker_fee) * Decimal("1.001")
            if self._balances.get(rules.quote_asset, ZERO) < needed:
                raise OrderRejectedError("saldo insuficiente")
        elif self._balances.get(rules.base_asset, ZERO) < req.quantity:
            raise OrderRejectedError("saldo insuficiente")

    def _reference(self, symbol: str, side: Side) -> Decimal:
        quote = self._quotes.get(symbol)
        if quote is not None:
            return quote.ask if side is Side.BUY else quote.bid
        price = self._prices.get(symbol)
        if price is None:
            raise OrderRejectedError("sin precio de mercado para simular")
        return price

    def _market_price(self, symbol: str, side: Side) -> Decimal:
        quote = self._quotes.get(symbol)
        if quote is not None:
            adv = self.costs.slippage
            return quote.ask * (1 + adv) if side is Side.BUY else quote.bid * (1 - adv)
        price = self._prices[symbol]
        return self.costs.buy_fill(price) if side is Side.BUY else self.costs.sell_fill(price)

    def _execute_market(self, order: _Order) -> None:
        req = order.request
        avail = self._available.get(req.symbol)
        qty = req.quantity if avail is None else min(req.quantity, avail)
        qty = floor_to_step(qty, self.rules[req.symbol].step_size)
        if qty > 0:
            self._fill(order, qty, self._market_price(req.symbol, req.side), maker=False)
        if order.remaining > 0:
            order.status = OrderStatus.EXPIRED  # sin liquidez: el resto no se ejecuta

    def _try_cross_now(self, order: _Order) -> None:
        req = order.request
        assert req.limit_price is not None
        ref = self._reference(req.symbol, req.side)
        executable = ref <= req.limit_price if req.side is Side.BUY else ref >= req.limit_price
        if executable:
            self._fill(order, order.remaining, self._market_price(req.symbol, req.side), False)

    def _fill(self, order: _Order, qty: Decimal, price: Decimal, maker: bool) -> bool:
        req = order.request
        rules = self.rules[req.symbol]
        fee_rate = self.costs.maker_fee if maker else self.costs.taker_fee
        fee = qty * price * fee_rate
        quote, base = rules.quote_asset, rules.base_asset
        if req.side is Side.BUY:
            cost = qty * price + fee
            if self._balances.get(quote, ZERO) < cost:
                order.status = OrderStatus.CANCELED  # saldo agotado entre medias
                return False
            self._balances[quote] = self._balances.get(quote, ZERO) - cost
            self._balances[base] = self._balances.get(base, ZERO) + qty
        else:
            if self._balances.get(base, ZERO) < qty:
                order.status = OrderStatus.CANCELED
                return False
            self._balances[base] -= qty
            self._balances[quote] = self._balances.get(quote, ZERO) + qty * price - fee
        order.fills.append(
            FillReport(
                trade_id=f"{order.exchange_order_id}-{len(order.fills) + 1}",
                price=price,
                quantity=qty,
                fee=fee,
                fee_asset=quote,
                filled_at=self._clock(),
                is_maker=maker,
            )
        )
        order.status = OrderStatus.FILLED if order.remaining <= 0 else OrderStatus.PARTIALLY_FILLED
        return True
