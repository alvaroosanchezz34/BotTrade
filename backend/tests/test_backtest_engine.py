"""Casos límite del motor de backtest con una estrategia guionizada."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import pytest

from app.backtesting.engine import BacktestConfig, run_backtest
from app.backtesting.metrics import summarize, trade_stats
from app.core.domain import InstrumentRules
from app.risk.limits import RiskLimits
from app.risk.sizing import CostModel
from app.strategies.base import Strategy, StrategyParams

T0 = datetime(2026, 2, 1, tzinfo=UTC)
RULES = {
    s: InstrumentRules(
        s,
        s[:3],
        "USDT",
        Decimal("0.01"),
        Decimal("0.001"),
        Decimal("0.001"),
        Decimal("9000"),
        Decimal("5"),
    )
    for s in ("AAAUSDT", "BBBUSDT")
}
COSTS = CostModel(
    taker_fee=Decimal("0.001"), slippage=Decimal("0.0002"), assumed_spread=Decimal("0.0002")
)
ADV = COSTS.adverse_per_side  # 0,0003


class ScriptParams(StrategyParams):
    max_hold_bars: int = 1000


class Scripted(Strategy[ScriptParams]):
    """Entradas/salidas/stops fijados por el test."""

    id = "scripted"
    version = "0"
    name = "scripted"
    description = ""
    Params = ScriptParams

    def __init__(self, plan: dict[str, dict[int, dict[str, Any]]], **params: Any):
        super().__init__({"filter": {"enabled": False}, **params})
        self.plan = plan
        self.symbol_by_len: dict[int, str] = {}

    @property
    def warmup(self) -> int:
        return 0

    def _evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        sym = df.attrs["symbol"]
        out = pd.DataFrame(index=df.index)
        out["enter"] = False
        out["exit"] = False
        out["stop"] = df["close"] * 0.9
        out["target"] = np.nan
        out["score"] = 1.0
        out["reason"] = "guion"
        for i, spec in self.plan.get(sym, {}).items():
            for k, v in spec.items():
                out.iloc[i, out.columns.get_loc(k)] = v
        return out


def bars(
    symbol: str, rows: list[tuple[float, float, float, float]], volume: float = 1000.0
) -> pd.DataFrame:
    idx = pd.date_range(T0, periods=len(rows), freq="1min", tz="UTC")
    o, h, l, c = (np.array(x, dtype=float) for x in zip(*rows, strict=True))  # noqa: E741
    df = pd.DataFrame(
        {
            "close_time": idx + timedelta(seconds=59, milliseconds=999),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": volume,
            "quote_volume": volume * c,
            "trades": 1,
        },
        index=idx,
    )
    df.attrs["symbol"] = symbol
    return df


def flat(n: int, p: float = 100.0) -> list[tuple[float, float, float, float]]:
    return [(p, p + 0.5, p - 0.5, p)] * n


def run(frames: dict[str, pd.DataFrame], strat: Strategy[Any], **cfg: Any):  # type: ignore[no-untyped-def]
    first = next(iter(frames.values()))
    c = BacktestConfig(
        symbols=tuple(frames),
        interval="1m",
        start=first.index[0].to_pydatetime(),
        end=first.index[-1].to_pydatetime(),
        costs=COSTS,
        limits=cfg.pop("limits", RiskLimits(max_daily_loss_pct=Decimal("10"))),
        **cfg,
    )
    return run_backtest(strat, frames, RULES, c)


def test_entry_fills_next_open_with_costs_and_target_exit() -> None:
    rows = flat(10)
    rows[3] = (101.0, 101.5, 100.8, 101.2)  # apertura de la barra siguiente a la señal
    rows[5] = (101.0, 104.0, 100.9, 103.5)  # supera el objetivo 103
    res = run(
        {"AAAUSDT": bars("AAAUSDT", rows)},
        Scripted({"AAAUSDT": {2: {"enter": True, "stop": 99.0, "target": 103.0}}}),
    )
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.entry_time == (T0 + timedelta(minutes=3))
    assert t.entry_price == Decimal("101.0") * (1 + ADV)
    assert t.exit_reason == "target" and t.exit_price == Decimal("103.0")
    assert t.entry_fee == t.entry_price * t.quantity * Decimal("0.001")
    assert t.net_pnl == (t.exit_price - t.entry_price) * t.quantity - t.entry_fee - t.exit_fee
    assert t.risk_amount <= Decimal("1.25")


def test_stop_and_target_same_candle_assumes_stop() -> None:
    rows = flat(10)
    rows[5] = (100.0, 105.0, 95.0, 100.0)
    res = run(
        {"AAAUSDT": bars("AAAUSDT", rows)},
        Scripted({"AAAUSDT": {2: {"enter": True, "stop": 98.0, "target": 103.0}}}),
    )
    t = res.trades[0]
    assert t.exit_reason == "stop"
    assert t.exit_price == Decimal("98.0") * (1 - ADV)


def test_gap_below_stop_exits_at_open() -> None:
    rows = flat(10)
    rows[5] = (90.0, 91.0, 89.0, 90.5)
    res = run(
        {"AAAUSDT": bars("AAAUSDT", rows)},
        Scripted({"AAAUSDT": {2: {"enter": True, "stop": 98.0}}}),
    )
    t = res.trades[0]
    assert t.exit_reason == "stop_gap"
    assert t.exit_price == Decimal("90.0") * (1 - ADV)
    # La pérdida real supera el riesgo presupuestado: el gap no se puede limitar.
    assert -t.net_pnl > t.risk_amount


def test_target_requires_trade_through() -> None:
    rows = flat(10)
    rows[5] = (100.0, 103.0, 99.9, 102.0)  # toca 103 pero no lo supera
    res = run(
        {"AAAUSDT": bars("AAAUSDT", rows)},
        Scripted({"AAAUSDT": {2: {"enter": True, "stop": 98.0, "target": 103.0}}}),
    )
    assert res.trades == [] and len(res.open_trades) == 1


def test_signal_exit_and_time_stop_at_next_open() -> None:
    rows = flat(20)
    rows[7] = (100.2, 100.5, 99.8, 100.1)
    res = run(
        {"AAAUSDT": bars("AAAUSDT", rows)},
        Scripted({"AAAUSDT": {2: {"enter": True, "stop": 95.0}, 6: {"exit": True}}}),
    )
    t = res.trades[0]
    assert t.exit_reason == "signal_exit"
    assert t.exit_time == T0 + timedelta(minutes=7)
    assert t.exit_price == Decimal("100.2") * (1 - ADV)

    res2 = run(
        {"AAAUSDT": bars("AAAUSDT", flat(20))},
        Scripted({"AAAUSDT": {2: {"enter": True, "stop": 95.0}}}, max_hold_bars=4),
    )
    t2 = res2.trades[0]
    assert t2.exit_reason == "time_stop" and t2.bars_held == 4


def test_open_position_at_end_not_counted_as_trade() -> None:
    res = run(
        {"AAAUSDT": bars("AAAUSDT", flat(10))},
        Scripted({"AAAUSDT": {5: {"enter": True, "stop": 95.0}}}),
    )
    assert res.trades == [] and len(res.open_trades) == 1
    s = summarize(res)
    assert s["trades"] == 0 and s["open_positions_at_end"] == 1
    # La curva sí refleja la posición (comisión de entrada pagada).
    assert res.equity.iloc[-1] < 500


def test_partial_fill_and_liquidity_rejection() -> None:
    # Volumen 2 → cap 0,2 unidades (10 %): la orden de ~0,6 unidades se ejecuta parcialmente.
    res = run(
        {"AAAUSDT": bars("AAAUSDT", flat(10), volume=2.0)},
        Scripted({"AAAUSDT": {2: {"enter": True, "stop": 98.0, "target": 150.0}}}),
    )
    t = res.open_trades[0]
    assert t.partial_fill and t.quantity == Decimal("0.2")
    assert res.partial_fills == 1
    # Volumen tan bajo que lo ejecutable no llega al mínimo nocional (5 USDT).
    res2 = run(
        {"AAAUSDT": bars("AAAUSDT", flat(10), volume=0.3)},
        Scripted({"AAAUSDT": {2: {"enter": True, "stop": 98.0}}}),
    )
    assert res2.open_trades == [] and res2.rejections["liquidez"] == 1


def test_missing_fill_candle_expires_signal() -> None:
    df = bars("AAAUSDT", flat(10))
    other = bars("BBBUSDT", flat(10))
    df = df.drop(df.index[3])  # falta justo la vela de ejecución
    res = run(
        {"AAAUSDT": df, "BBBUSDT": other}, Scripted({"AAAUSDT": {2: {"enter": True, "stop": 95.0}}})
    )
    assert res.trades == [] and res.open_trades == []
    assert res.rejections["caducada"] == 1


def test_duplicate_index_rejected() -> None:
    df = bars("AAAUSDT", flat(5))
    dup = pd.concat([df, df.iloc[[2]]]).sort_index()
    with pytest.raises(ValueError, match="duplicados"):
        run({"AAAUSDT": dup}, Scripted({}))


def test_max_positions_and_duplicate_entries() -> None:
    plan = {
        "AAAUSDT": {2: {"enter": True, "stop": 95.0}, 4: {"enter": True, "stop": 95.0}},
        "BBBUSDT": {2: {"enter": True, "stop": 95.0}},
    }
    lim = RiskLimits(max_positions=1, max_daily_loss_pct=Decimal("10"))
    res = run(
        {"AAAUSDT": bars("AAAUSDT", flat(10)), "BBBUSDT": bars("BBBUSDT", flat(10))},
        Scripted(plan),
        limits=lim,
    )
    assert len(res.open_trades) == 1
    assert res.rejections["posiciones"] >= 1
    # La segunda señal sobre AAA no se evalúa: ya hay posición (no se piramida).
    assert res.signals == 2


def test_daily_loss_limit_blocks_new_entries() -> None:
    rows = flat(30)
    for k in (5, 15):
        rows[k] = (100.0, 100.2, 80.0, 81.0)  # cada operación salta el stop con pérdida
    plan = {
        "AAAUSDT": {
            2: {"enter": True, "stop": 99.0},
            12: {"enter": True, "stop": 99.0},
            22: {"enter": True, "stop": 99.0},
        }
    }
    lim = RiskLimits(max_daily_loss_pct=Decimal("0.4"))  # 2 USDT
    res = run({"AAAUSDT": bars("AAAUSDT", rows)}, Scripted(plan), limits=lim)
    assert len(res.trades) == 2 and res.open_trades == []
    # La 2.ª operación ya se dimensiona con el riesgo restante del día (0,75 en vez de 1,25)
    # y la 3.ª se rechaza porque la orden mínima superaría lo que queda del límite diario.
    assert res.trades[1].risk_amount < res.trades[0].risk_amount
    assert res.rejections["tamaño"] + res.rejections["pérdida_diaria"] == 1
    assert -sum(t.net_pnl for t in res.trades) <= Decimal("2")


def test_deterministic_and_seeded_random_benchmark() -> None:
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, 3000)))
    rows = [(c, c * 1.002, c * 0.998, c) for c in close]
    frames = {"AAAUSDT": bars("AAAUSDT", rows)}
    strat = Scripted({}, max_hold_bars=20)
    a = run(frames, strat, random_entry_rate=0.01, seed=42)
    b = run(frames, strat, random_entry_rate=0.01, seed=42)
    c = run(frames, strat, random_entry_rate=0.01, seed=43)
    assert a.data_hash == b.data_hash
    assert [t.net_pnl for t in a.trades] == [t.net_pnl for t in b.trades]
    assert [t.entry_time for t in a.trades] != [t.entry_time for t in c.trades]
    assert len(a.trades) > 5


def test_trade_stats_flags_dependence_on_single_trade() -> None:
    from app.backtesting.engine import Trade

    def tr(p: str) -> Trade:
        return Trade(
            "X",
            T0,
            Decimal("100"),
            Decimal("1"),
            Decimal("0"),
            0.0,
            float("nan"),
            Decimal("1"),
            "",
            exit_time=T0,
            exit_price=Decimal("100") + Decimal(p),
            exit_reason="x",
        )

    st = trade_stats([tr("50"), tr("-1"), tr("-1"), tr("1")])
    assert st["largest_win_share"] > 0.9
    assert st["net_pnl_without_best"] < 0
    assert st["profit_factor"] == pytest.approx(51 / 2)
