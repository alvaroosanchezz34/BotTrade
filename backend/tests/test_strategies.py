"""Estrategias: ausencia de look-ahead, datos insuficientes y señales esperadas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.core.domain import SignalAction
from app.strategies.base import FilterParams
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.momentum import MomentumStrategy
from app.strategies.registry import STRATEGIES, build
from app.strategies.trend import TrendStrategy

START = datetime(2026, 1, 1, tzinfo=UTC)
NO_FILTER = {"filter": {"enabled": False}}


def frame(
    close: np.ndarray, volume: np.ndarray | None = None, spread: float = 0.002
) -> pd.DataFrame:
    n = len(close)
    idx = pd.date_range(START, periods=n, freq="1min", tz="UTC")
    open_ = np.concatenate([[close[0]], close[:-1]])
    hi = np.maximum(open_, close) * (1 + spread / 2)
    lo = np.minimum(open_, close) * (1 - spread / 2)
    vol = volume if volume is not None else np.full(n, 10.0)
    return pd.DataFrame(
        {
            "close_time": idx + timedelta(minutes=1) - timedelta(milliseconds=1),
            "open": open_,
            "high": hi,
            "low": lo,
            "close": close,
            "volume": vol,
            "quote_volume": vol * close,
            "trades": 1,
        },
        index=idx,
    )


def random_walk(n: int = 3000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    vol = rng.lognormal(2, 0.8, n)
    return frame(close, vol)


@pytest.mark.parametrize("sid", sorted(STRATEGIES))
@pytest.mark.parametrize("filter_on", [True, False])
def test_no_lookahead(sid: str, filter_on: bool) -> None:
    """La decisión en t no cambia si se borran los datos posteriores a t."""
    df = random_walk()
    params = {"filter": {"enabled": filter_on, "vol_window": 200}}
    if sid == "trend":
        params |= {"fast": 10, "slow": 30, "regime": 120}
    s = build(sid, params)
    full = s.evaluate(df)
    for cut in (s.warmup + 5, 900, 1777, 2500):
        part = s.evaluate(df.iloc[:cut])
        cols = ["enter", "exit", "stop", "target", "score", "filter_ok"]
        pd.testing.assert_frame_equal(
            full.iloc[:cut][cols], part[cols], check_exact=False, rtol=1e-12
        )


@pytest.mark.parametrize("sid", sorted(STRATEGIES))
def test_insufficient_data_produces_no_entries(sid: str) -> None:
    s = build(sid, NO_FILTER)
    df = random_walk(n=max(10, s.warmup - 1))
    assert not s.evaluate(df)["enter"].any()


def test_momentum_detects_volume_breakout() -> None:
    n = 120
    close = np.full(n, 100.0) + np.sin(np.arange(n)) * 0.05
    close[100:105] = [100.4, 100.9, 101.5, 102.2, 103.0]
    vol = np.full(n, 10.0)
    vol[104] = 60.0
    df = frame(close, vol)
    s = MomentumStrategy(NO_FILTER)
    ev = s.evaluate(df)
    assert ev["enter"].iloc[104]
    assert ev["enter"].sum() == 1
    row = ev.iloc[104]
    assert row["stop"] < df["close"].iloc[104] < row["target"]
    sig = s.signal_at("BTCUSDT", "1m", df, ev, 104)
    assert sig is not None and sig.action is SignalAction.ENTER_LONG
    assert sig.created_at == df["close_time"].iloc[104].to_pydatetime()
    assert sig.score_is_calibrated is False
    assert sig.valid_until > sig.created_at
    assert "f_volume_ratio" in sig.entry_conditions
    assert s.signal_at("BTCUSDT", "1m", df, ev, 103) is None


def test_mean_reversion_only_in_range_regime() -> None:
    rng = np.random.default_rng(1)
    n = 300
    flat = 100 + rng.normal(0, 0.05, n)
    flat[250] = 98.5  # caída brusca en mercado lateral
    ev = MeanReversionStrategy(NO_FILTER).evaluate(frame(flat))
    assert ev["enter"].iloc[250]
    assert ev["target"].iloc[250] > flat[250]

    # Misma caída al final de una tendencia bajista limpia: no debe comprar.
    trend = np.linspace(110, 100, n)
    trend[250] = trend[249] - 1.5
    ev2 = MeanReversionStrategy(NO_FILTER).evaluate(frame(trend))
    assert not ev2["enter"].iloc[250]


def test_trend_enters_on_reclaim_and_exits_on_cross() -> None:
    n = 600
    up = np.linspace(100, 130, n)
    up[400:405] -= np.array([0.3, 0.6, 0.9, 0.6, 0.0]) * 3  # retroceso y recuperación
    s = TrendStrategy({"fast": 10, "slow": 30, "regime": 120, **NO_FILTER})
    ev = s.evaluate(frame(up))
    assert ev["enter"].iloc[400:410].any()
    assert not ev["exit"].iloc[200:395].any()
    down = np.concatenate([up, np.linspace(130, 110, 200)])
    ev2 = s.evaluate(frame(down))
    assert ev2["exit"].iloc[-50:].all()


def test_volatility_filter_blocks_when_costs_not_covered() -> None:
    df = random_walk()
    quiet = df.copy()
    quiet[["open", "high", "low", "close"]] = 100.0  # sin movimiento: el ATR no cubre costes
    s = MomentumStrategy({"filter": FilterParams(min_atr_to_cost=1.0, vol_window=100).model_dump()})
    ev = s.evaluate(quiet)
    assert not ev["filter_ok"].iloc[200:].any()
    assert (ev["filter_reason"].iloc[200:] != "").all()


def test_params_validated() -> None:
    with pytest.raises(ValueError):
        TrendStrategy({"fast": 50, "slow": 20, "regime": 100})
    with pytest.raises(ValueError):
        MomentumStrategy({"lookback": 0})
    with pytest.raises(ValueError):
        build("unknown")
    assert len(MomentumStrategy.code_hash()) == 16
