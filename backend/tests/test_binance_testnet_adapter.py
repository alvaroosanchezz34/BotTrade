"""El adaptador de testnet contra un servidor simulado (el testnet real está geobloqueado
desde el entorno de desarrollo: ver docstring del módulo)."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.domain import OrderStatus, OrderType, Side
from app.execution.adapter import (
    AmbiguousResponseError,
    OrderRejectedError,
    OrderRequest,
)
from app.providers.binance.testnet import (
    BinanceSetupError,
    BinanceTestnetExecution,
    sign,
)

URL = "https://testnet.binance.vision"
SECRET = (
    "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"  # ejemplo público de la doc
)


def adapter(handler: Callable[[httpx.Request], httpx.Response]) -> BinanceTestnetExecution:
    client = httpx.AsyncClient(base_url=URL, transport=httpx.MockTransport(handler))
    return BinanceTestnetExecution(base_url=URL, api_key="key", api_secret=SECRET, client=client)


def req(**kw: object) -> OrderRequest:
    base = dict(
        client_order_id="bt-abc", symbol="BTCUSDT", side=Side.BUY, type=OrderType.MARKET,
        quantity=Decimal("0.00100"),
    )  # fmt: skip
    base.update(kw)
    return OrderRequest(**base)  # type: ignore[arg-type]


def q(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


FULL = {
    "symbol": "BTCUSDT", "orderId": 28, "clientOrderId": "bt-abc", "transactTime": 1507725176595,
    "executedQty": "0.00100000", "status": "FILLED", "type": "MARKET", "side": "BUY",
    "fills": [
        {"price": "80000.00", "qty": "0.00060000", "commission": "0.00000060",
         "commissionAsset": "BTC", "tradeId": 56},
        {"price": "80001.00", "qty": "0.00040000", "commission": "0.00000040",
         "commissionAsset": "BTC", "tradeId": 57},
    ],
}  # fmt: skip


def test_signature_matches_binance_documentation_vector() -> None:
    query = (
        "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
        "&recvWindow=5000&timestamp=1499827319559"
    )
    assert sign(query, SECRET) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://data-api.binance.vision",
        "http://testnet.binance.vision",  # sin TLS
        "https://testnet.binance.vision.evil.com",
        "https://stream.testnet.binance.vision",  # host de streams, no de órdenes
    ],
)
def test_only_testnet_order_hosts_are_accepted(url: str) -> None:
    with pytest.raises(BinanceSetupError):
        BinanceTestnetExecution(base_url=url, api_key="k", api_secret="s")


def test_credentials_are_required() -> None:
    with pytest.raises(BinanceSetupError):
        BinanceTestnetExecution(base_url=URL, api_key="", api_secret="")


async def test_submit_market_signs_request_and_parses_full_response() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=FULL)

    report = await adapter(handler).submit(req())
    r = seen[0]
    params = q(r)
    assert r.method == "POST" and r.url.path == "/api/v3/order"
    assert r.headers["X-MBX-APIKEY"] == "key"
    assert params["newClientOrderId"] == "bt-abc" and params["newOrderRespType"] == "FULL"
    assert params["quantity"] == "0.001" and params["side"] == "BUY"
    # La firma es válida y el secreto jamás viaja en la petición.
    unsigned = str(r.url).split("?", 1)[1].rsplit("&signature=", 1)[0]
    assert params["signature"] == sign(unsigned, SECRET)
    assert SECRET not in str(r.url) and "recvWindow" in params and "timestamp" in params

    assert report.status is OrderStatus.FILLED and report.filled_qty == Decimal("0.001")
    assert [f.trade_id for f in report.fills] == ["56", "57"]
    assert report.fills[0].fee_asset == "BTC" and report.fills[0].fee == Decimal("0.0000006")


async def test_limit_order_sends_price_and_gtc() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"orderId": 1, "clientOrderId": "bt-abc", "executedQty": "0", "status": "NEW"},
        )

    r = await adapter(handler).submit(req(type=OrderType.LIMIT, limit_price=Decimal("79900.00")))
    assert r.status is OrderStatus.ACKNOWLEDGED and r.fills == ()
    p = q(seen[0])
    assert p["price"] == "79900" and p["timeInForce"] == "GTC" and p["type"] == "LIMIT"


async def test_definitive_rejection_vs_ambiguous_failures() -> None:
    def reject(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": -2010, "msg": "Account has insufficient balance"})

    with pytest.raises(OrderRejectedError, match="-2010"):
        await adapter(reject).submit(req())

    for response in (
        httpx.Response(500, text="boom"),
        httpx.Response(503, text="busy"),
        httpx.Response(429, headers={"retry-after": "1"}, text="slow"),
        httpx.Response(200, json={"code": -1007, "msg": "Timeout waiting for response"}),
        httpx.Response(400, json={"code": -1007, "msg": "Timeout waiting for response"}),
    ):
        with pytest.raises(AmbiguousResponseError):
            await adapter(lambda _r, resp=response: resp).submit(req())  # type: ignore[misc]


async def test_network_errors_are_ambiguous_not_rejections() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=request)

    with pytest.raises(AmbiguousResponseError):
        await adapter(handler).submit(req())


async def test_get_order_unknown_returns_none_and_other_errors_are_ambiguous() -> None:
    def no_such(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": -2013, "msg": "Order does not exist."})

    assert await adapter(no_such).get_order("BTCUSDT", "bt-x") is None

    def bad_key(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": -2015, "msg": "Invalid API-key"})

    with pytest.raises(AmbiguousResponseError):  # nunca se toma por «no existe»
        await adapter(bad_key).get_order("BTCUSDT", "bt-x")


async def test_get_order_fetches_fills_from_my_trades() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/order":
            assert q(request)["origClientOrderId"] == "bt-abc"
            return httpx.Response(
                200,
                json={"orderId": 28, "clientOrderId": "bt-abc", "status": "FILLED",
                      "executedQty": "0.00100000"},
            )  # fmt: skip
        assert request.url.path == "/api/v3/myTrades" and q(request)["orderId"] == "28"
        return httpx.Response(
            200,
            json=[{"id": 56, "price": "80000.00", "qty": "0.00100000", "commission": "0.08",
                   "commissionAsset": "USDT", "time": 1507725176595, "isMaker": False}],
        )  # fmt: skip

    r = await adapter(handler).get_order("BTCUSDT", "bt-abc")
    assert r is not None and r.status is OrderStatus.FILLED and r.fills[0].fee_asset == "USDT"


async def test_executed_without_fill_detail_is_ambiguous_not_filled() -> None:
    """myTrades con retraso: no se puede declarar ejecutada sin fills (no habría posición)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/order":
            return httpx.Response(
                200,
                json={"orderId": 28, "clientOrderId": "bt-abc", "status": "FILLED",
                      "executedQty": "0.001"},
            )  # fmt: skip
        return httpx.Response(200, json=[])

    with pytest.raises(AmbiguousResponseError):
        await adapter(handler).get_order("BTCUSDT", "bt-abc")


async def test_expired_partial_market_order_maps_to_expired() -> None:
    body = {**FULL, "status": "EXPIRED", "executedQty": "0.00060000", "fills": FULL["fills"][:1]}
    r = await adapter(lambda _r: httpx.Response(200, json=body)).submit(req())
    assert r.status is OrderStatus.EXPIRED and r.filled_qty == Decimal("0.0006")


async def test_inconsistent_fills_are_rejected_as_untrustworthy() -> None:
    body = {**FULL, "executedQty": "0.005"}
    with pytest.raises(AmbiguousResponseError):
        await adapter(lambda _r: httpx.Response(200, json=body)).submit(req())


async def test_open_orders_and_balances_include_locked_funds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/openOrders":
            return httpx.Response(
                200,
                json=[{"orderId": 1, "clientOrderId": "bt-1", "status": "NEW", "executedQty": "0"}],
            )
        return httpx.Response(
            200,
            json={"canTrade": True, "balances": [
                {"asset": "BTC", "free": "0.5", "locked": "0.1"},
                {"asset": "USDT", "free": "1000", "locked": "0"}]},
        )  # fmt: skip

    a = adapter(handler)
    assert [o.client_order_id for o in await a.open_orders()] == ["bt-1"]
    assert (await a.balances()) == {"BTC": Decimal("0.6"), "USDT": Decimal(1000)}


async def test_cancel_returns_final_state() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "DELETE":
            return httpx.Response(200, json={"orderId": 1, "status": "CANCELED"})
        return httpx.Response(
            200,
            json={"orderId": 1, "clientOrderId": "bt-1", "status": "CANCELED", "executedQty": "0"},
        )

    r = await adapter(handler).cancel("BTCUSDT", "bt-1")
    assert r.status is OrderStatus.CANCELED and calls == ["DELETE", "GET"]


async def test_clock_skew_triggers_one_resync_and_retry() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 2_000_000_000_000})
        if seen.count("/api/v3/order") == 1:
            return httpx.Response(400, json={"code": -1021, "msg": "Timestamp outside recvWindow"})
        return httpx.Response(200, json=FULL)

    r = await adapter(handler).submit(req())
    assert r.status is OrderStatus.FILLED
    assert seen == ["/api/v3/order", "/api/v3/time", "/api/v3/order"]


async def test_verify_permissions_requires_trading_and_valid_keys() -> None:
    def ok(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        return httpx.Response(200, json={"canTrade": True, "accountType": "SPOT", "balances": []})

    assert (await adapter(ok).verify_permissions())["canTrade"] is True

    def no_trade(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        return httpx.Response(200, json={"canTrade": False, "balances": []})

    with pytest.raises(BinanceSetupError, match="trading"):
        await adapter(no_trade).verify_permissions()

    def bad(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        return httpx.Response(401, content=json.dumps({"code": -2015, "msg": "bad"}))

    with pytest.raises(BinanceSetupError, match="no válidas"):
        await adapter(bad).verify_permissions()
