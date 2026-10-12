"""Interfaz común de estrategias.

Una estrategia es una función **causal y vectorizada** de velas cerradas a
columnas de decisión. La misma función alimenta el backtest y el modo en vivo
(se lee la última fila), así que no hay dos implementaciones que puedan divergir.

Columnas que devuelve :meth:`Strategy.evaluate`:

* ``enter`` (bool): proponer entrada larga al abrir la barra siguiente.
* ``exit`` (bool): proponer salida al abrir la barra siguiente.
* ``stop`` (float): stop propuesto si se entra en esta barra.
* ``target`` (float | NaN): objetivo propuesto (NaN = sin objetivo fijo).
* ``score`` (float): puntuación heurística. **No es una probabilidad.**
* ``filter_ok`` (bool) y ``filter_reason`` (str): resultado del filtro de volatilidad.

Las estrategias no conocen saldos ni órdenes: solo proponen. El riesgo y la
ejecución deciden (sección 4).
"""

from __future__ import annotations

import hashlib
import inspect
import math
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any, ClassVar, Generic, TypeVar

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.core.domain import Signal, SignalAction, interval_delta
from app.core.money import D
from app.features import indicators as ind


class FilterParams(BaseModel):
    """Filtro de volatilidad (estrategia D), aplicable a cualquier estrategia."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    atr_period: int = Field(14, ge=2, le=500)
    # El movimiento esperable en el horizonte de la operación (ATR × √barras de mantenimiento)
    # debe cubrir los costes ida/vuelta × este factor. Si no, operar es pagar comisiones.
    min_atr_to_cost: float = Field(2.0, ge=0, le=50)
    round_trip_cost: float = Field(0.0024, ge=0, le=0.05)
    # Evitar los periodos de volatilidad extrema (percentil sobre la ventana).
    vol_window: int = Field(1440, ge=50, le=50_000)
    max_vol_percentile: float = Field(0.95, gt=0, le=1)


class StrategyParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_hold_bars: int = Field(60, ge=1, le=10_000)
    filter: FilterParams = FilterParams()


P = TypeVar("P", bound=StrategyParams)


def volatility_gate(
    df: pd.DataFrame, f: FilterParams, horizon_bars: int = 1
) -> tuple[pd.Series, pd.Series]:
    """Devuelve (permitido, motivo) por barra.

    ``horizon_bars``: barras que se espera mantener la posición. El movimiento
    esperable escala aproximadamente con √horizonte (paseo aleatorio).
    """
    idx = df.index
    if not f.enabled:
        return pd.Series(True, index=idx), pd.Series("", index=idx)
    atr_pct = ind.atr(df, f.atr_period) / df["close"]
    expected_move = atr_pct * float(np.sqrt(max(1, horizon_bars)))
    vol_rank = ind.rolling_percentile_rank(ind.realized_vol(df["close"], 30), f.vol_window)
    cost_ok = expected_move >= f.min_atr_to_cost * f.round_trip_cost
    vol_ok = vol_rank <= f.max_vol_percentile
    ok = (cost_ok & vol_ok).fillna(False)
    reason = pd.Series("", index=idx, dtype="object")
    reason[~cost_ok.fillna(False)] = "movimiento típico insuficiente para cubrir costes"
    reason[~vol_ok.fillna(False)] = "volatilidad extrema"
    return ok.astype(bool), reason


class Strategy(ABC, Generic[P]):
    id: ClassVar[str]
    version: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    Params: ClassVar[type[StrategyParams]]

    def __init__(self, params: P | dict[str, Any] | None = None):
        if params is None:
            params = self.Params()  # type: ignore[assignment]
        elif isinstance(params, dict):
            params = self.Params.model_validate(params)  # type: ignore[assignment]
        self.params: P = params  # type: ignore[assignment]

    # ------------------------------------------------------------------ contrato

    @property
    @abstractmethod
    def warmup(self) -> int:
        """Barras necesarias antes de que la primera señal sea válida."""

    @abstractmethod
    def _evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Columnas enter/exit/stop/target/score/reason sin aplicar el filtro."""

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self._evaluate(df)
        ok, why = volatility_gate(df, self.params.filter, self.params.max_hold_bars)
        out["filter_ok"] = ok
        out["filter_reason"] = why
        out["enter"] = out["enter"].fillna(False).astype(bool) & ok
        out["exit"] = out["exit"].fillna(False).astype(bool)
        # Sin historia suficiente no se propone nada (comportamiento con datos insuficientes).
        if self.warmup > 0:
            out.iloc[: self.warmup, out.columns.get_loc("enter")] = False
        # Un stop por encima del precio en una entrada larga es un error de la estrategia.
        bad_stop = out["enter"] & ~(out["stop"] < df["close"])
        out.loc[bad_stop, "enter"] = False
        return out

    # ------------------------------------------------------------------ identidad

    @classmethod
    def code_hash(cls) -> str:
        src = (
            inspect.getsource(cls)
            + inspect.getsource(Strategy)
            + inspect.getsource(volatility_gate)
        )
        return hashlib.sha256(src.encode()).hexdigest()[:16]

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "params": self.params.model_dump(),
            "code_hash": self.code_hash(),
        }

    # ------------------------------------------------------------------ señales en vivo

    def signal_at(
        self, symbol: str, interval: str, df: pd.DataFrame, ev: pd.DataFrame, i: int = -1
    ) -> Signal | None:
        """Construye la ``Signal`` de la fila ``i`` (por defecto la última vela cerrada)."""
        row = ev.iloc[i]
        if not bool(row["enter"]):
            return None
        candle = df.iloc[i]
        close_time = candle["close_time"]
        created_at = (
            close_time.to_pydatetime() if hasattr(close_time, "to_pydatetime") else close_time
        )
        target = row["target"]
        score = float(row["score"]) if not _isnan(row["score"]) else None
        return Signal(
            symbol=symbol,
            strategy_id=self.id,
            strategy_version=self.version,
            action=SignalAction.ENTER_LONG,
            created_at=created_at,
            reference_price=D(float(candle["close"])),
            rationale=str(row["reason"]),
            entry_conditions={k: _jsonable(row[k]) for k in ev.columns if k.startswith("f_")},
            invalidation=(
                "Caduca si no se ejecuta en la barra siguiente o si el precio "
                f"cae por debajo del stop {float(row['stop']):.2f}"
            ),
            stop_price=D(float(row["stop"])),
            target_price=None if _isnan(target) else D(float(target)),
            params=self.params.model_dump(),
            valid_until=created_at + interval_delta(interval) + timedelta(milliseconds=1),
            score=score,
            score_is_calibrated=False,
        )


def _isnan(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _jsonable(v: Any) -> Any:
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v
