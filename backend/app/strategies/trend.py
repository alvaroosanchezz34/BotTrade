"""B. Seguimiento de tendencia con EMAs (hipótesis de investigación)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from app.features import indicators as ind
from app.strategies.base import Strategy, StrategyParams


class TrendParams(StrategyParams):
    fast: int = Field(20, ge=2, le=2000)
    slow: int = Field(100, ge=3, le=5000)
    regime: int = Field(400, ge=4, le=20_000, description="EMA larga que define el régimen")
    atr_period: int = Field(14, ge=2, le=500)
    stop_atr: float = Field(2.5, gt=0, le=20)
    max_hold_bars: int = Field(600, ge=1, le=20_000)

    @model_validator(mode="after")
    def _ordered(self) -> TrendParams:
        if not self.fast < self.slow < self.regime:
            raise ValueError("Se requiere fast < slow < regime")
        return self


class TrendStrategy(Strategy[TrendParams]):
    id = "trend"
    version = "1.0.0"
    name = "Seguimiento de tendencia (EMA)"
    description = (
        "Solo opera a favor del régimen: EMA rápida > lenta > régimen. Entra cuando el cierre "
        "recupera la EMA rápida tras un retroceso. Sale cuando la EMA rápida cruza bajo la lenta."
    )
    Params = TrendParams

    @property
    def warmup(self) -> int:
        return self.params.regime + 1

    def _evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        close = df["close"]
        ef, es, er = ind.ema(close, p.fast), ind.ema(close, p.slow), ind.ema(close, p.regime)
        a = ind.atr(df, p.atr_period)
        uptrend = (ef > es) & (es > er) & (er > er.shift(p.fast))
        reclaim = (close.shift(1) <= ef.shift(1)) & (close > ef)
        out = pd.DataFrame(index=df.index)
        out["enter"] = (uptrend & reclaim).fillna(False)
        out["exit"] = (ef < es).fillna(False)
        out["stop"] = close - p.stop_atr * a
        out["target"] = np.nan
        out["score"] = ((ef - es) / a).clip(lower=0, upper=5)
        out["reason"] = (
            "Recupera EMA"
            + str(p.fast)
            + " en tendencia alcista (EMA"
            + str(p.fast)
            + " > EMA"
            + str(p.slow)
            + " > EMA"
            + str(p.regime)
            + ")"
        )
        out["f_ema_fast"] = ef
        out["f_ema_slow"] = es
        out["f_ema_regime"] = er
        out["f_atr"] = a
        return out
