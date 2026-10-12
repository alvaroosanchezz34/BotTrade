"""A. Momentum / ruptura con volumen (hipótesis de investigación, no rentable por defecto)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field

from app.features import indicators as ind
from app.strategies.base import Strategy, StrategyParams


class MomentumParams(StrategyParams):
    lookback: int = Field(
        20, ge=2, le=1000, description="Barras previas cuyo máximo hay que romper"
    )
    roc_period: int = Field(5, ge=1, le=500)
    min_move_atr: float = Field(1.5, gt=0, le=20, description="Subida en roc_period medida en ATR")
    volume_lookback: int = Field(20, ge=2, le=1000)
    min_volume_ratio: float = Field(2.0, gt=0, le=50)
    atr_period: int = Field(14, ge=2, le=500)
    stop_atr: float = Field(1.5, gt=0, le=20)
    reward_risk: float = Field(2.0, gt=0, le=20)
    max_hold_bars: int = Field(30, ge=1, le=10_000)


class MomentumStrategy(Strategy[MomentumParams]):
    id = "momentum"
    version = "1.0.0"
    name = "Momentum con volumen"
    description = (
        "Entra cuando el cierre rompe el máximo de las N barras anteriores con una subida "
        "de al menos k ATR y volumen muy superior a la media. Stop en ATR y objetivo R:R fijo."
    )
    Params = MomentumParams

    @property
    def warmup(self) -> int:
        p = self.params
        return max(p.lookback, p.roc_period, p.volume_lookback, p.atr_period) + 1

    def _evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        close = df["close"]
        a = ind.atr(df, p.atr_period)
        breakout_level = ind.rolling_high(df["high"], p.lookback)
        move_atr = (close - close.shift(p.roc_period)) / a
        vr = ind.volume_ratio(df["volume"], p.volume_lookback)
        enter = (close > breakout_level) & (move_atr >= p.min_move_atr) & (vr >= p.min_volume_ratio)
        stop = close - p.stop_atr * a
        target = close + p.reward_risk * (close - stop)
        out = pd.DataFrame(index=df.index)
        out["enter"] = enter.fillna(False)
        out["exit"] = False
        out["stop"] = stop
        out["target"] = target
        out["score"] = np.minimum(vr / p.min_volume_ratio, 3.0)
        out["reason"] = (
            "Ruptura del máximo de "
            + str(p.lookback)
            + " barras con subida de "
            + move_atr.round(2).astype(str)
            + " ATR y volumen x"
            + vr.round(2).astype(str)
        )
        out["f_atr"] = a
        out["f_breakout_level"] = breakout_level
        out["f_move_atr"] = move_atr
        out["f_volume_ratio"] = vr
        return out
