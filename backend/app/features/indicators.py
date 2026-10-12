"""Indicadores causales sobre velas cerradas.

Garantía: el valor en la fila ``t`` solo depende de filas ``<= t``. Ninguna función
usa ``shift(-k)``, ``center=True`` ni estadísticas de toda la serie. Los tests
de look-ahead lo verifican truncando la serie y comparando.

Trabajan en ``float64`` (ADR-004): son estimadores, no contabilidad.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rolling_std(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).std(ddof=0)


def zscore(s: pd.Series, n: int) -> pd.Series:
    sd = rolling_std(s, n)
    return (s - sma(s, n)) / sd.replace(0.0, np.nan)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    """ATR de Wilder (media exponencial con alpha = 1/n)."""
    return true_range(df).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def log_returns(close: pd.Series) -> pd.Series:
    return np.log(close / close.shift(1))


def realized_vol(close: pd.Series, n: int) -> pd.Series:
    """Desviación típica de rendimientos logarítmicos por barra (sin anualizar)."""
    return log_returns(close).rolling(n, min_periods=n).std(ddof=0)


def rate_of_change(close: pd.Series, n: int) -> pd.Series:
    return close / close.shift(n) - 1.0


def volume_ratio(volume: pd.Series, n: int) -> pd.Series:
    """Volumen de la barra respecto a la media de las ``n`` barras anteriores (excluida)."""
    return volume / volume.shift(1).rolling(n, min_periods=n).mean()


def rolling_high(s: pd.Series, n: int) -> pd.Series:
    """Máximo de las ``n`` barras **anteriores** (excluye la actual): base de rupturas."""
    return s.shift(1).rolling(n, min_periods=n).max()


def efficiency_ratio(close: pd.Series, n: int) -> pd.Series:
    """Kaufman: |cambio neto| / suma de |cambios|. ~1 tendencia limpia, ~0 rango/ruido."""
    change = (close - close.shift(n)).abs()
    noise = close.diff().abs().rolling(n, min_periods=n).sum()
    return change / noise.replace(0.0, np.nan)


def rolling_percentile_rank(s: pd.Series, n: int) -> pd.Series:
    """Percentil (0-1) del valor actual dentro de la ventana de ``n`` barras que lo incluye."""
    return s.rolling(n, min_periods=n).rank(pct=True)
