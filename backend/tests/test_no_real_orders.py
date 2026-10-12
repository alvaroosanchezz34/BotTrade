"""Garantías de que la configuración por defecto no puede enviar órdenes reales (sección 13)."""

from pathlib import Path

from app.core.config import TESTNET_HOSTS, TradingMode
from tests.conftest import make_settings

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Hosts de producción de Binance capaces de aceptar órdenes o streams de usuario.
PRODUCTION_HOSTS = [
    "api.binance.com",
    "api1.binance.com",
    "api2.binance.com",
    "api3.binance.com",
    "api4.binance.com",
    "api-gcp.binance.com",
    "stream.binance.com",
    "ws-api.binance.com",
    "fix-oe.binance.com",
]


def test_trading_mode_has_no_live_member() -> None:
    assert {m.value for m in TradingMode} == {"BACKTEST", "PAPER", "TESTNET"}


def test_default_settings_never_use_real_broker() -> None:
    s = make_settings()
    assert s.trading_mode is not TradingMode.TESTNET
    assert s.uses_real_broker is False
    assert s.testnet_api_key is None


def test_no_production_hosts_in_source() -> None:
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {h}" for h in PRODUCTION_HOSTS if h in text]
    assert offenders == [], f"Hosts de producción en el código: {offenders}"


def test_testnet_whitelist_only_contains_testnet_hosts() -> None:
    assert all("testnet" in h for h in TESTNET_HOSTS)
