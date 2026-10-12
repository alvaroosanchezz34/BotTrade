from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.domain import OrderStatus, OrderType, Quote, Side
from app.execution.adapter import AmbiguousResponseError, OrderRejectedError, OrderRequest
from app.execution.simulated import Fault, SimulatedExecution
from app.risk.sizing import CostModel
from tests.exec_helpers import NOW, RULES


def make(usdt: str = "1000", btc: str = "0") -> SimulatedExecution:
    ex = SimulatedExecution(
        costs=CostModel(), rules=RULES, balances={"USDT": Decimal(usdt), "BTC": Decimal(btc)}
    )
    ex.set_price("BTCUSDT", Decimal(80000))
    return ex


def req(cid: str = "bt-1", side: Side = Side.BUY, qty: str = "0.005", **kw: object) -> OrderRequest:
    base = dict(
        client_order_id=cid,
        symbol="BTCUSDT",
        side=side,
        type=OrderType.MARKET,
        quantity=Decimal(qty),
    )
    base.update(kw)
    return OrderRequest(**base)  # type: ignore[arg-type]


async def test_market_buy_pays_spread_slippage_and_fee() -> None:
    ex = make()
    r = await ex.submit(req())
    costs = CostModel()
    assert r.status is OrderStatus.FILLED
    fill = r.fills[0]
    assert fill.price == costs.buy_fill(Decimal(80000)) > Decimal(80000)
    assert fill.fee == fill.price * fill.quantity * costs.taker_fee
    bal = await ex.balances()
    assert bal["BTC"] == Decimal("0.005")
    assert bal["USDT"] == 1000 - fill.price * fill.quantity - fill.fee


async def test_uses_bid_ask_when_available() -> None:
    ex = make()
    ex.set_quote(Quote("BTCUSDT", Decimal(79990), Decimal(80010), NOW, "test"))
    buy = await ex.submit(req("bt-b"))
    assert buy.fills[0].price == Decimal(80010) * (1 + CostModel().slippage)
    sell = await ex.submit(req("bt-s", Side.SELL))
    assert sell.fills[0].price == Decimal(79990) * (1 - CostModel().slippage)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"qty": "0.000001"}, "LOT_SIZE"),
        ({"qty": "0.000015"}, "LOT_SIZE"),  # no múltiplo del step
        ({"qty": "0.00005"}, "NOTIONAL"),  # 4 USDT < 5
        ({"qty": "1"}, "saldo"),
    ],
)
async def test_exchange_filters_and_balance_reject(kwargs: dict[str, str], reason: str) -> None:
    ex = make()
    with pytest.raises(OrderRejectedError, match=reason):
        await ex.submit(req(**kwargs))  # type: ignore[arg-type]


async def test_cannot_sell_what_you_do_not_have_no_shorts() -> None:
    ex = make(btc="0")
    with pytest.raises(OrderRejectedError, match="saldo"):
        await ex.submit(req("bt-s", Side.SELL))


async def test_duplicate_client_order_id_is_rejected_not_executed_twice() -> None:
    ex = make()
    await ex.submit(req("bt-dup"))
    with pytest.raises(OrderRejectedError, match="duplicado"):
        await ex.submit(req("bt-dup"))
    assert (await ex.balances())["BTC"] == Decimal("0.005")


async def test_faults_have_the_documented_semantics() -> None:
    ex = make()
    ex.inject(Fault.TIMEOUT_BEFORE)
    with pytest.raises(AmbiguousResponseError):
        await ex.submit(req("bt-a"))
    assert await ex.get_order("BTCUSDT", "bt-a") is None  # nunca existió

    ex.inject(Fault.TIMEOUT_AFTER)
    with pytest.raises(AmbiguousResponseError):
        await ex.submit(req("bt-b"))
    got = await ex.get_order("BTCUSDT", "bt-b")
    assert got is not None and got.status is OrderStatus.FILLED  # sí se ejecutó


async def test_limit_order_requires_price_to_cross_and_pays_maker_fee() -> None:
    ex = make()
    r = await ex.submit(req("bt-l", type=OrderType.LIMIT, limit_price=Decimal("79000.00")))
    assert r.status is OrderStatus.ACKNOWLEDGED
    ex.advance("BTCUSDT", low=Decimal(79000), high=Decimal(80500))  # solo toca
    assert (await ex.get_order("BTCUSDT", "bt-l")).status is OrderStatus.ACKNOWLEDGED  # type: ignore[union-attr]
    ex.advance("BTCUSDT", low=Decimal("78999.99"), high=Decimal(80500))
    done = await ex.get_order("BTCUSDT", "bt-l")
    assert done is not None and done.status is OrderStatus.FILLED
    assert done.fills[0].price == Decimal("79000.00") and done.fills[0].is_maker


async def test_cancel_rules() -> None:
    ex = make()
    await ex.submit(req("bt-l", type=OrderType.LIMIT, limit_price=Decimal("70000.00")))
    assert (await ex.cancel("BTCUSDT", "bt-l")).status is OrderStatus.CANCELED
    filled = await ex.submit(req("bt-m"))
    assert filled.status is OrderStatus.FILLED
    assert (await ex.cancel("BTCUSDT", "bt-m")).status is OrderStatus.FILLED  # no se «des-ejecuta»
    with pytest.raises(OrderRejectedError):
        await ex.cancel("BTCUSDT", "nope")
