from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import (
    Environment,
    TradingMode,
    UnsafeConfigurationError,
    assert_safe_environment,
)
from tests.conftest import make_settings


def test_defaults_are_simulation_and_paper() -> None:
    s = make_settings()
    assert s.trading_mode is TradingMode.PAPER
    assert s.uses_real_broker is False
    assert s.reference_capital == Decimal("500")
    assert s.symbols == ["BTCUSDT", "ETHUSDT"]


def test_csv_env_parsing() -> None:
    s = make_settings(symbols="btcusdt, ethusdt", intervals="1m")
    assert s.symbols == ["BTCUSDT", "ETHUSDT"]
    assert s.intervals == ["1m"]


@pytest.mark.parametrize(
    "field,url",
    [
        ("market_data_rest_url", "https://api.binance.com"),
        ("market_data_ws_url", "wss://stream.binance.com:9443"),
        ("testnet_rest_url", "https://api.binance.com"),
        ("testnet_rest_url", "https://evil.example.com"),
        ("testnet_ws_url", "wss://stream.binance.com:9443"),
    ],
)
def test_non_whitelisted_hosts_rejected(field: str, url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: url})


def test_testnet_requires_credentials() -> None:
    with pytest.raises(ValidationError, match="TESTNET requiere"):
        make_settings(trading_mode="TESTNET")
    s = make_settings(trading_mode="TESTNET", testnet_api_key="k", testnet_api_secret="s")
    assert s.trading_mode is TradingMode.TESTNET


def test_unknown_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(trading_mode="LIVE")


def test_unsupported_interval_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(intervals="1s")


def test_production_rejects_insecure_config() -> None:
    with pytest.raises(ValidationError, match="insegura"):
        make_settings(environment=Environment.PRODUCTION)


def test_production_accepts_secure_config() -> None:
    s = make_settings(
        environment=Environment.PRODUCTION,
        cookie_secure=True,
        cors_origins="https://bot.example.ts.net",
        database_url="postgresql+asyncpg://bottrade:Xk9-very-long-random-pass@db:5432/bottrade",
    )
    assert s.environment is Environment.PRODUCTION


def test_production_env_vars_abort() -> None:
    with pytest.raises(UnsafeConfigurationError):
        assert_safe_environment({"BINANCE_API_KEY": "abc"})
    assert_safe_environment({"BINANCE_API_KEY": ""})  # vacía: no cuenta
    assert_safe_environment({})
