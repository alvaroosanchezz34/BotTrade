"""C. Reversión a la media en régimen lateral (hipótesis de investigación)."""

from __future__ import annotations

import pandas as pd
from pydantic import Field

from app.features import indicators as ind
from app.strategies.base import Strategy, StrategyParams


class MeanReversionParams(StrategyParams):
    window: int = Field(60, ge=5, le=5000)
    z_entry: float = Field(2.5, gt=0, le=10)
    er_window: int = Field(60, ge=5, le=5000)
    max_efficiency: float = Field(0.3, gt=0, le=1, description="Solo en rango: tendencia débil")
    atr_period: int = Field(14, ge=2, le=500)
    stop_atr: float = Field(2.0, gt=0, le=20)
    max_hold_bars: int = Field(120, ge=1, le=10_000)


class MeanReversionStrategy(Strategy[MeanReversionParams]):
    id = "mean_reversion"
    version = "1.0.0"
    name = "Reversión a la media"
    description = (
        "Compra cuando el precio se desvía z desviaciones típicas por debajo de su media móvil "
        "y el mercado está en rango (ratio de eficiencia bajo). Objetivo: volver a la media."
    )
    Params = MeanReversionParams

    @property
    def warmup(self) -> int:
        p = self.params
        return max(p.window, p.er_window, p.atr_period) + 1

    def _evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        close = df["close"]
        mean = ind.sma(close, p.window)
        z = ind.zscore(close, p.window)
        eff = ind.efficiency_ratio(close, p.er_window)
        a = ind.atr(df, p.atr_period)
        out = pd.DataFrame(index=df.index)
        out["enter"] = ((z <= -p.z_entry) & (eff <= p.max_efficiency)).fillna(False)
        out["exit"] = (z >= 0).fillna(False)
        out["stop"] = close - p.stop_atr * a
        out["target"] = mean
        out["score"] = (-z).clip(lower=0, upper=6)
        out["reason"] = (
            "Desviación z=" + z.round(2).astype(str) + " con eficiencia " + eff.round(2).astype(str)
        )
        out["f_zscore"] = z
        out["f_mean"] = mean
        out["f_efficiency"] = eff
        out["f_atr"] = a
        return out
