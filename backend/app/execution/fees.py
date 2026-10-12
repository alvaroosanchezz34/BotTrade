"""Conversión de comisiones a moneda de cotización.

Binance cobra por defecto la comisión de una compra en la moneda *base* (BTC) y la de una
venta en la moneda de cotización (USDT). La contabilidad (caja, P&L) trabaja en
cotización, así que cada fill se convierte aquí con su propio precio.

Una comisión en otro activo (p. ej. BNB) no se puede convertir sin una cotización: se
estima con la tarifa taker sobre el nocional, que es una cota razonable, y se avisa.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from app.core.domain import InstrumentRules

log = logging.getLogger(__name__)

FALLBACK_FEE_RATE = Decimal("0.001")


def fee_in_quote(
    *, fee: Decimal, fee_asset: str, price: Decimal, quantity: Decimal, rules: InstrumentRules
) -> Decimal:
    if fee == 0 or fee_asset == rules.quote_asset:
        return fee
    if fee_asset == rules.base_asset:
        return fee * price
    log.warning("comisión en un activo no convertible; se estima", extra={"asset": fee_asset})
    return price * quantity * FALLBACK_FEE_RATE


def net_base_quantity(
    *, quantity: Decimal, fee: Decimal, fee_asset: str, rules: InstrumentRules
) -> Decimal:
    """Cantidad realmente recibida en una compra (la comisión en base se descuenta)."""
    return quantity - fee if fee_asset == rules.base_asset else quantity
