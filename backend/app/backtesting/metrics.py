"""Métricas de rendimiento. Solo operaciones cerradas cuentan como operaciones.

Supuestos del Sharpe: rendimientos **diarios** de la curva de capital valorada a
mercado (UTC), tasa libre de riesgo 0, anualización con √365 (cripto cotiza
todos los días). Con pocas semanas de datos es una estimación muy ruidosa.
"""

from __future__ import annotations

import math
from collections import defaultdict
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from app.backtesting.engine import BacktestResult, Trade
from app.core.money import ZERO


def _f(x: Decimal | float) -> float:
    return float(x)


def trade_stats(trades: list[Trade]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "trades": 0,
            "win_rate": None,
            "avg_win": None,
            "avg_loss": None,
            "expectancy": None,
            "expectancy_ci95": None,
            "profit_factor": None,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "fees": 0.0,
            "avg_bars_held": None,
            "largest_win_share": None,
            "exit_reasons": {},
        }
    nets = np.array([_f(t.net_pnl) for t in trades])
    wins, losses = nets[nets > 0], nets[nets <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    se = nets.std(ddof=1) / math.sqrt(n) if n > 1 else float("nan")
    reasons: dict[str, int] = defaultdict(int)
    for t in trades:
        reasons[t.exit_reason or "?"] += 1
    total_net = nets.sum()
    return {
        "trades": n,
        "win_rate": float(len(wins) / n),
        "avg_win": float(wins.mean()) if len(wins) else None,
        "avg_loss": float(losses.mean()) if len(losses) else None,
        "expectancy": float(nets.mean()),
        # Intervalo de confianza aproximado (normal) de la expectativa por operación.
        "expectancy_ci95": [float(nets.mean() - 1.96 * se), float(nets.mean() + 1.96 * se)]
        if n > 1
        else None,
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else None,
        "gross_pnl": _f(sum((t.gross_pnl for t in trades), ZERO)),
        "net_pnl": float(total_net),
        "fees": _f(sum((t.fees for t in trades), ZERO)),
        "avg_bars_held": float(np.mean([t.bars_held for t in trades])),
        # ¿Depende el resultado de una sola operación excepcional?
        "largest_win_share": float(wins.max() / gross_win) if gross_win > 0 else None,
        "net_pnl_without_best": float(total_net - (wins.max() if len(wins) else 0.0)),
        "exit_reasons": dict(reasons),
    }


def equity_stats(equity: pd.Series, initial: float) -> dict[str, Any]:
    eq = equity.dropna()
    if eq.empty:
        return {
            "final_equity": initial,
            "return_pct": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": None,
        }
    peak = np.maximum.accumulate(np.concatenate([[initial], eq.to_numpy()]))[1:]
    dd = peak - eq.to_numpy()
    i_max = int(np.argmax(dd))
    daily = eq.resample("1D").last().dropna()
    daily = pd.concat([pd.Series([initial], index=[daily.index[0] - pd.Timedelta(days=1)]), daily])
    rets = daily.pct_change().dropna()
    sharpe = (
        float(rets.mean() / rets.std(ddof=1) * math.sqrt(365))
        if len(rets) > 2 and rets.std(ddof=1) > 0
        else None
    )
    return {
        "final_equity": float(eq.iloc[-1]),
        "return_pct": float((eq.iloc[-1] / initial - 1) * 100),
        "max_drawdown": float(dd.max()),
        "max_drawdown_pct": float(dd[i_max] / peak[i_max] * 100) if peak[i_max] > 0 else 0.0,
        "sharpe": sharpe,
        "days": int(len(daily) - 1),
    }


def summarize(res: BacktestResult) -> dict[str, Any]:
    initial = float(res.config.limits.reference_capital)
    by_symbol = {
        s: trade_stats([t for t in res.trades if t.symbol == s]) for s in res.config.symbols
    }
    by_month: dict[str, dict[str, Any]] = {}
    months = sorted({t.exit_time.strftime("%Y-%m") for t in res.trades if t.exit_time})
    for m in months:
        ts = [t for t in res.trades if t.exit_time and t.exit_time.strftime("%Y-%m") == m]
        st = trade_stats(ts)
        by_month[m] = {
            "trades": st["trades"],
            "net_pnl": st["net_pnl"],
            "profit_factor": st["profit_factor"],
        }
    open_unrealized = 0.0
    if res.open_trades and not res.equity.empty:
        open_unrealized = float(res.equity.iloc[-1]) - initial - trade_stats(res.trades)["net_pnl"]
    return {
        **trade_stats(res.trades),
        **equity_stats(res.equity, initial),
        "exposure_avg_pct": float(res.exposure.mean() * 100) if len(res.exposure) else 0.0,
        "time_in_market_pct": float((res.exposure > 0).mean() * 100) if len(res.exposure) else 0.0,
        "signals": res.signals,
        "risk_rejections": dict(res.rejections),
        "partial_fills": res.partial_fills,
        "open_positions_at_end": len(res.open_trades),
        "open_unrealized_pnl": open_unrealized,
        "by_symbol": by_symbol,
        "by_month": by_month,
        "data_hash": res.data_hash,
    }


def buy_and_hold(
    frames: dict[str, pd.DataFrame],
    start: Any,
    end: Any,
    capital: float,
    fee: float,
    adverse: float,
) -> dict[str, Any]:
    """Benchmark: capital repartido a partes iguales, compra al inicio y venta al final."""
    per = capital / len(frames)
    curve = None
    for df in frames.values():
        sl = df.loc[start:end, "close"]
        if sl.empty:
            continue
        entry = sl.iloc[0] * (1 + adverse)
        qty = per * (1 - fee) / entry
        value = qty * sl * (1 - adverse) * (1 - fee)
        curve = value if curve is None else curve.add(value, fill_value=np.nan).ffill()
    if curve is None:
        return {"return_pct": None}
    st = equity_stats(curve, capital)
    return {
        "return_pct": st["return_pct"],
        "max_drawdown_pct": st["max_drawdown_pct"],
        "sharpe": st["sharpe"],
    }
