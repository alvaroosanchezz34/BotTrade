"""Adaptador de ejecución contra **Binance Spot Testnet** (nunca producción, ADR-006).

Verificado contra la documentación oficial (binance-spot-api-docs, 2026-10-12):

* Firma: HMAC-SHA256 hex de la query string completa con el *secret*; cabecera
  ``X-MBX-APIKEY``; parámetros ``timestamp`` y ``recvWindow``.
* ``POST /api/v3/order`` con ``newClientOrderId`` y ``newOrderRespType=FULL`` (incluye fills).
* ``GET /api/v3/order?origClientOrderId=`` para consultar; ``-2013`` = «Order does not exist».
* ``DELETE /api/v3/order?origClientOrderId=``; ``GET /api/v3/openOrders``; ``GET /api/v3/account``;
  ``GET /api/v3/myTrades?orderId=`` para los fills de una orden consultada.
* ``-1007 TIMEOUT`` y cualquier 5XX significan «estado de ejecución desconocido».
* Una orden a mercado ejecutada parcialmente termina en ``EXPIRED`` (no hay liquidez).
* Las compras pagan la comisión en la moneda base por defecto.

**Limitación honesta**: el entorno de desarrollo de este proyecto recibe HTTP 451 desde el
testnet (geobloqueo), así que este adaptador está probado con un servidor simulado
(``httpx.MockTransport``) y **no ha sido ejecutado contra el testnet real**. Ver
``docs/operations.md`` para la verificación manual desde el VPS.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from app.core.config import TESTNET_HOSTS
from app.core.domain import OrderStatus, OrderType, utcnow
from app.core.money import ZERO
from app.execution.adapter import (
    AmbiguousResponseError,
    ExecutionReport,
    FillReport,
    OrderRejectedError,
    OrderRequest,
)
from app.providers.binance.parsing import epoch_to_utc

log = logging.getLogger(__name__)

# Solo estos hosts REST aceptan órdenes en el adaptador (subconjunto de TESTNET_HOSTS).
ORDER_HOSTS = frozenset({"testnet.binance.vision", "api1.testnet.binance.vision"})

STATUS_MAP = {
    "NEW": OrderStatus.ACKNOWLEDGED,
    "PENDING_NEW": OrderStatus.ACKNOWLEDGED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "PENDING_CANCEL": OrderStatus.ACKNOWLEDGED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
    "EXPIRED_IN_MATCH": OrderStatus.EXPIRED,
}
NO_SUCH_ORDER = -2013
AMBIGUOUS_CODES = {-1007, -1006}  # TIMEOUT, UNEXPECTED_RESP


class BinanceSetupError(RuntimeError):
    pass


def sign(query: str, secret: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f") if d != 0 else "0"


class BinanceTestnetExecution:
    name = "binance-testnet"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        api_secret: str,
        client: httpx.AsyncClient | None = None,
        recv_window_ms: int = 5000,
        timeout: float = 10.0,
    ):
        host = (urlparse(base_url).hostname or "").lower()
        if host not in ORDER_HOSTS or host not in TESTNET_HOSTS:
            raise BinanceSetupError(f"Host no permitido para órdenes: {host!r}. Solo testnet.")
        if urlparse(base_url).scheme != "https":
            raise BinanceSetupError("El testnet se usa siempre por HTTPS")
        if not api_key or not api_secret:
            raise BinanceSetupError("Faltan las credenciales del testnet")
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._secret = api_secret
        self._recv = recv_window_ms
        self._client = client or httpx.AsyncClient(base_url=self._base, timeout=timeout)
        self._owns_client = client is None
        self._clock_offset_ms = 0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ transporte

    def _signed_query(self, params: dict[str, Any]) -> str:
        now_ms = int(time.time() * 1000) + self._clock_offset_ms
        q = urlencode({**params, "recvWindow": self._recv, "timestamp": now_ms})
        return f"{q}&signature={sign(q, self._secret)}"

    async def sync_clock(self) -> None:
        """Calcula el desfase con el servidor (evita ``-1021 INVALID_TIMESTAMP``)."""
        resp = await self._client.get("/api/v3/time")
        resp.raise_for_status()
        self._clock_offset_ms = int(resp.json()["serverTime"]) - int(time.time() * 1000)

    async def _request(self, method: str, path: str, params: dict[str, Any]) -> Any:
        """Devuelve el JSON o lanza: ``OrderRejectedError`` (4XX definitivo) /
        ``AmbiguousResponseError`` (red, 5XX, -1007)."""
        for attempt in (0, 1):
            query = self._signed_query(params)
            try:
                resp = await self._client.request(
                    method, f"{path}?{query}", headers={"X-MBX-APIKEY": self._key}
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise AmbiguousResponseError(f"{type(exc).__name__} en {method} {path}") from exc
            if resp.status_code >= 500:
                raise AmbiguousResponseError(f"HTTP {resp.status_code} en {method} {path}")
            if resp.status_code in (418, 429):
                raise AmbiguousResponseError(f"límite de peticiones (HTTP {resp.status_code})")
            body = _json(resp)
            if resp.status_code >= 400 or (
                isinstance(body, dict) and "code" in body and body["code"] < 0
            ):
                code = int(body.get("code", 0)) if isinstance(body, dict) else 0
                msg = str(body.get("msg", "")) if isinstance(body, dict) else resp.text[:120]
                if code == -1021 and attempt == 0:
                    await self.sync_clock()
                    continue
                if code in AMBIGUOUS_CODES:
                    raise AmbiguousResponseError(f"{code}: {msg}")
                raise _ApiError(code, msg)
            return body
        raise AmbiguousResponseError("reloj desincronizado")  # pragma: no cover

    # ------------------------------------------------------------------ ExecutionAdapter

    async def submit(self, request: OrderRequest) -> ExecutionReport:
        params: dict[str, Any] = {
            "symbol": request.symbol,
            "side": request.side.value,
            "type": request.type.value,
            "quantity": _plain(request.quantity),
            "newClientOrderId": request.client_order_id,
            "newOrderRespType": "FULL",
        }
        if request.type is OrderType.LIMIT:
            assert request.limit_price is not None
            params["price"] = _plain(request.limit_price)
            params["timeInForce"] = "GTC"
        try:
            body = await self._request("POST", "/api/v3/order", params)
        except _ApiError as exc:
            raise OrderRejectedError(f"{exc.code}: {exc.msg}") from exc
        return _report_from_order(body, fills=_fills_from_full(body))

    async def get_order(self, symbol: str, client_order_id: str) -> ExecutionReport | None:
        try:
            body = await self._request(
                "GET", "/api/v3/order", {"symbol": symbol, "origClientOrderId": client_order_id}
            )
        except _ApiError as exc:
            if exc.code == NO_SUCH_ORDER:
                return None
            raise AmbiguousResponseError(f"consulta fallida {exc.code}: {exc.msg}") from exc
        fills: tuple[FillReport, ...] = ()
        if Decimal(str(body.get("executedQty", "0"))) > 0:
            fills = await self._fills_for(symbol, int(body["orderId"]))
        return _report_from_order(body, fills=fills)

    async def cancel(self, symbol: str, client_order_id: str) -> ExecutionReport:
        try:
            await self._request(
                "DELETE", "/api/v3/order", {"symbol": symbol, "origClientOrderId": client_order_id}
            )
        except _ApiError as exc:
            raise OrderRejectedError(f"{exc.code}: {exc.msg}") from exc
        report = await self.get_order(symbol, client_order_id)
        if report is None:
            raise AmbiguousResponseError("la orden cancelada no se puede consultar")
        return report

    async def open_orders(self) -> list[ExecutionReport]:
        try:
            body = await self._request("GET", "/api/v3/openOrders", {})
        except _ApiError as exc:
            raise AmbiguousResponseError(f"{exc.code}: {exc.msg}") from exc
        return [_report_from_order(o, fills=(), require_fills=False) for o in body]

    async def balances(self) -> dict[str, Decimal]:
        try:
            body = await self._request("GET", "/api/v3/account", {"omitZeroBalances": "true"})
        except _ApiError as exc:
            raise AmbiguousResponseError(f"{exc.code}: {exc.msg}") from exc
        # «free + locked»: lo bloqueado en órdenes abiertas sigue siendo nuestro.
        return {
            b["asset"]: Decimal(str(b["free"])) + Decimal(str(b["locked"]))
            for b in body["balances"]
        }

    async def verify_permissions(self) -> dict[str, Any]:
        """Validación previa al arranque: credenciales válidas y SIN permiso de retirada."""
        await self.sync_clock()
        try:
            body = await self._request("GET", "/api/v3/account", {"omitZeroBalances": "true"})
        except _ApiError as exc:
            raise BinanceSetupError(f"Credenciales del testnet no válidas: {exc.code}") from exc
        if not body.get("canTrade", False):
            raise BinanceSetupError("La clave del testnet no tiene permiso de trading")
        return {"canTrade": True, "accountType": body.get("accountType"), "uid": body.get("uid")}

    # ------------------------------------------------------------------ fills

    async def _fills_for(self, symbol: str, order_id: int) -> tuple[FillReport, ...]:
        try:
            trades = await self._request(
                "GET", "/api/v3/myTrades", {"symbol": symbol, "orderId": order_id}
            )
        except _ApiError as exc:
            raise AmbiguousResponseError(f"myTrades {exc.code}: {exc.msg}") from exc
        return tuple(
            FillReport(
                trade_id=str(t["id"]),
                price=Decimal(str(t["price"])),
                quantity=Decimal(str(t["qty"])),
                fee=Decimal(str(t["commission"])),
                fee_asset=str(t["commissionAsset"]),
                filled_at=epoch_to_utc(t["time"]),
                is_maker=bool(t.get("isMaker", False)),
            )
            for t in trades
        )


class _ApiError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"code": 0, "msg": resp.text[:120]}


def _fills_from_full(body: dict[str, Any]) -> tuple[FillReport, ...]:
    ts = epoch_to_utc(body["transactTime"]) if "transactTime" in body else utcnow()
    return tuple(
        FillReport(
            trade_id=str(f["tradeId"]),
            price=Decimal(str(f["price"])),
            quantity=Decimal(str(f["qty"])),
            fee=Decimal(str(f["commission"])),
            fee_asset=str(f["commissionAsset"]),
            filled_at=ts,
        )
        for f in body.get("fills", [])
    )


def _report_from_order(
    body: dict[str, Any], *, fills: tuple[FillReport, ...], require_fills: bool = True
) -> ExecutionReport:
    raw = str(body["status"])
    status = STATUS_MAP.get(raw)
    if status is None:
        raise AmbiguousResponseError(f"estado de orden desconocido: {raw}")
    executed = Decimal(str(body.get("executedQty", "0")))
    filled = sum((f.quantity for f in fills), ZERO)
    if require_fills and executed > 0 and not fills:
        # Ejecutada según el estado pero sin detalle (myTrades puede ir con retraso): si se
        # aceptara, la BD marcaría la orden como ejecutada sin crear la posición.
        raise AmbiguousResponseError("orden ejecutada sin detalle de fills todavía")
    if fills and filled != executed:
        # El detalle de fills no cuadra con executedQty: no se puede confiar en el informe.
        raise AmbiguousResponseError(f"fills ({filled}) ≠ executedQty ({executed})")
    return ExecutionReport(
        client_order_id=str(body.get("clientOrderId") or body.get("origClientOrderId")),
        exchange_order_id=str(body["orderId"]),
        status=status,
        filled_qty=executed,
        fills=fills,
    )
