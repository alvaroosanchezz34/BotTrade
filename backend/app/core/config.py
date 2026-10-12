"""Configuración validada del sistema.

Reglas de seguridad aplicadas aquí (fallar cerrado):

* ``TradingMode`` no contiene ningún modo de dinero real.
* Las URLs de datos de mercado solo pueden apuntar a hosts públicos de datos.
* Las URLs del testnet solo pueden apuntar a hosts del testnet.
* Si el entorno contiene variables que parecen credenciales de producción de
  Binance, el arranque falla.
* En ``production`` se exigen cookies seguras, CORS explícito y contraseñas
  de base de datos no triviales.
"""

from __future__ import annotations

import os
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class TradingMode(StrEnum):
    """Modos permitidos. No existe un modo de dinero real (ADR-006)."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    TESTNET = "TESTNET"


# Hosts permitidos. Listas cerradas en código, no configurables.
PUBLIC_MARKET_DATA_HOSTS = frozenset(
    {"data-api.binance.vision", "data-stream.binance.vision", "data.binance.vision"}
)
TESTNET_HOSTS = frozenset(
    {
        "testnet.binance.vision",
        "api1.testnet.binance.vision",
        "stream.testnet.binance.vision",
        "ws-api.testnet.binance.vision",
    }
)
# Variables que indicarían credenciales de producción. Su mera presencia aborta.
FORBIDDEN_ENV_VARS = frozenset(
    {"BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_SECRET_KEY", "BINANCE_PRIVATE_KEY"}
)

_WEAK_DB_PASSWORDS = frozenset({"", "postgres", "password", "bottrade", "bottrade_dev", "changeme"})


class UnsafeConfigurationError(RuntimeError):
    """La configuración no es segura; el proceso no debe arrancar."""


def _host(url: str) -> str:
    host = urlparse(url).hostname
    if not host:
        raise ValueError(f"URL sin host: {url!r}")
    return host.lower()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BOTTRADE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEVELOPMENT
    trading_mode: TradingMode = TradingMode.PAPER

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://bottrade:bottrade_dev@127.0.0.1:5432/bottrade"
    )

    # --- Mercado -----------------------------------------------------------
    symbols: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    intervals: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["1m", "5m"])
    market_data_rest_url: str = "https://data-api.binance.vision"
    market_data_ws_url: str = "wss://data-stream.binance.vision"
    market_data_archive_url: str = "https://data.binance.vision"
    data_dir: str = "data"
    stale_data_seconds: int = Field(default=150, ge=10, le=3600)

    # --- Testnet (solo se usa en modo TESTNET) -----------------------------
    testnet_rest_url: str = "https://testnet.binance.vision"
    testnet_ws_url: str = "wss://stream.testnet.binance.vision"
    testnet_api_key: SecretStr | None = None
    testnet_api_secret: SecretStr | None = None

    # --- Simulación --------------------------------------------------------
    quote_asset: str = "USDT"
    reference_capital: Decimal = Decimal("500")
    taker_fee_rate: Decimal = Decimal("0.001")
    maker_fee_rate: Decimal = Decimal("0.001")
    slippage_bps: Decimal = Decimal("2")
    latency_ms: int = Field(default=500, ge=0, le=60_000)

    # --- API / seguridad ---------------------------------------------------
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    cookie_secure: bool = False
    session_ttl_minutes: int = Field(default=12 * 60, ge=5, le=30 * 24 * 60)
    login_max_attempts: int = Field(default=5, ge=1, le=50)
    login_lockout_minutes: int = Field(default=15, ge=1, le=24 * 60)

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("symbols", "intervals", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @field_validator("symbols")
    @classmethod
    def _upper_symbols(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("Debe haber al menos un símbolo")
        return [s.upper() for s in v]

    @field_validator("intervals")
    @classmethod
    def _check_intervals(cls, v: list[str]) -> list[str]:
        allowed = {"1m", "5m"}
        bad = set(v) - allowed
        if bad:
            raise ValueError(f"Intervalos no soportados en v1: {sorted(bad)}")
        return v

    @field_validator("market_data_rest_url", "market_data_ws_url", "market_data_archive_url")
    @classmethod
    def _public_data_only(cls, v: str) -> str:
        if _host(v) not in PUBLIC_MARKET_DATA_HOSTS:
            raise ValueError(
                f"Host de datos no permitido: {_host(v)}. Solo endpoints públicos de datos."
            )
        return v.rstrip("/")

    @field_validator("testnet_rest_url", "testnet_ws_url")
    @classmethod
    def _testnet_only(cls, v: str) -> str:
        if _host(v) not in TESTNET_HOSTS:
            raise ValueError(f"Host de testnet no permitido: {_host(v)}")
        return v.rstrip("/")

    @model_validator(mode="after")
    def _mode_requirements(self) -> Settings:
        if self.trading_mode is TradingMode.TESTNET:
            if not (self.testnet_api_key and self.testnet_api_secret):
                raise ValueError("Modo TESTNET requiere BOTTRADE_TESTNET_API_KEY/SECRET")
        if self.environment is Environment.PRODUCTION:
            problems: list[str] = []
            if not self.cookie_secure:
                problems.append("BOTTRADE_COOKIE_SECURE debe ser true")
            if any(o == "*" or o.startswith("http://") for o in self.cors_origins):
                problems.append("CORS en producción solo admite orígenes https explícitos")
            pwd = urlparse(self.database_url.get_secret_value()).password or ""
            if pwd in _WEAK_DB_PASSWORDS or len(pwd) < 16:
                problems.append("Contraseña de base de datos débil (mínimo 16 caracteres)")
            if problems:
                raise ValueError("Configuración de producción insegura: " + "; ".join(problems))
        return self

    @property
    def uses_real_broker(self) -> bool:
        """Siempre False en esta versión. Existe para que los tests lo comprueben."""
        return False


def assert_safe_environment(environ: dict[str, str] | None = None) -> None:
    """Aborta si el entorno contiene credenciales de producción de Binance."""
    env = os.environ if environ is None else environ
    found = sorted(k for k in FORBIDDEN_ENV_VARS if env.get(k))
    if found:
        raise UnsafeConfigurationError(
            "Variables de credenciales de producción detectadas: "
            + ", ".join(found)
            + ". Este sistema no opera con dinero real; elimínalas."
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    assert_safe_environment()
    return Settings()
