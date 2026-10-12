"""Motor de backtesting por eventos, multi-activo y con costes (ADR-005).

Reglas de ejecución (conservadoras y documentadas en docs/backtesting.md):

1. Una señal se calcula con la vela cerrada ``t`` y el riesgo se evalúa en ese
   instante. La orden se ejecuta en la apertura de ``t + latency_bars`` (≥ 1).
2. Compra a mercado: apertura × (1 + medio spread + deslizamiento) + comisión.
3. Stop: si la vela abre por debajo del stop (gap), se sale a la apertura; si
   no, al stop × (1 − medio spread − deslizamiento).
4. Si en una vela se tocan stop y objetivo, se asume el **stop**.
5. El objetivo solo se ejecuta si el máximo lo **supera** (no basta con tocarlo).
6. Participación máxima en el volumen de la barra: si la orden es mayor, se
   ejecuta parcialmente; si la parte ejecutable no llega al mínimo, se rechaza.
7. Las posiciones abiertas al final no cuentan como operaciones cerradas: se
   informan aparte y solo se valoran a mercado en la curva de capital.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from app.core.domain import InstrumentRules, Signal, SignalAction, interval_delta
from app.core.money import ZERO, D, floor_to_step
from app.risk.engine import MarketContext, OpenPositionView, PortfolioView, RiskEngine
from app.risk.limits import RiskLimits, RiskState
from app.risk.sizing import CostModel
from app.strategies.base import Strategy


@dataclass(frozen=True)
class BacktestConfig:
    symbols: tuple[str, ...]
    interval: str
    start: datetime  # primera vela en la que se puede operar
    end: datetime  # última vela (open_time) incluida
    limits: RiskLimits = RiskLimits()  # noqa: RUF009 - inmutable
    costs: CostModel = CostModel()  # noqa: RUF009 - inmutable
    latency_bars: int = 1
    max_volume_participation: Decimal = Decimal("0.1")
    corr_window: int = 1440
    seed: int = 0
    # Sustituye las entradas de la estrategia por entradas aleatorias con la misma
    # frecuencia (benchmark de "sin ventaja"). None = estrategia real.
    random_entry_rate: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbols": list(self.symbols),
            "interval": self.interval,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "limits": self.limits.model_dump(mode="json"),
            "costs": self.costs.as_dict(),
            "latency_bars": self.latency_bars,
            "max_volume_participation": str(self.max_volume_participation),
            "corr_window": self.corr_window,
            "seed": self.seed,
            "random_entry_rate": self.random_entry_rate,
        }


@dataclass
class Trade:
    symbol: str
    entry_time: datetime
    entry_price: Decimal
    quantity: Decimal
    entry_fee: Decimal
    stop: float
    target: float
    risk_amount: Decimal
    reason: str
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_fee: Decimal = ZERO
    exit_reason: str | None = None
    bars_held: int = 0
    partial_fill: bool = False

    @property
    def gross_pnl(self) -> Decimal:
        if self.exit_price is None:
            return ZERO
        return (self.exit_price - self.entry_price) * self.quantity

    @property
    def fees(self) -> Decimal:
        return self.entry_fee + self.exit_fee

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees

    @property
    def is_closed(self) -> bool:
        return self.exit_price is not None


@dataclass
class _Pending:
    fill_index: int
    signal: Signal
    quantity: Decimal
    risk_amount: Decimal
    stop: float
    target: float


@dataclass
class BacktestResult:
    config: BacktestConfig
    strategy: dict[str, Any]
    trades: list[Trade]
    open_trades: list[Trade]
    equity: pd.Series  # capital a mercado al cierre de cada barra
    exposure: pd.Series  # nocional abierto / capital
    data_hash: str
    signals: int
    rejections: Counter[str] = field(default_factory=Counter)
    partial_fills: int = 0
    liquidity_rejections: int = 0

    @property
    def closed_trades(self) -> list[Trade]:
        return self.trades


def dataset_hash(frames: dict[str, pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for sym in sorted(frames):
        df = frames[sym]
        h.update(sym.encode())
        h.update(np.ascontiguousarray(df.index.asi8).tobytes())
        h.update(
            np.ascontiguousarray(
                df[["open", "high", "low", "close", "volume"]].to_numpy()
            ).tobytes()
        )
    return h.hexdigest()


def _rolling_corr(frames: dict[str, pd.DataFrame], window: int) -> dict[tuple[str, str], pd.Series]:
    syms = sorted(frames)
    rets = {s: np.log(frames[s]["close"]).diff() for s in syms}
    out: dict[tuple[str, str], pd.Series] = {}
    for i, a in enumerate(syms):
        for b in syms[i + 1 :]:
            joined = pd.concat([rets[a], rets[b]], axis=1, join="inner")
            out[(a, b)] = (
                joined.iloc[:, 0].rolling(window, min_periods=window // 2).corr(joined.iloc[:, 1])
            )
    return out


def _align(master: pd.DatetimeIndex, s: pd.Series) -> pd.Series:
    return s.reindex(master)


def run_backtest(
    strategy: Strategy[Any],
    frames: dict[str, pd.DataFrame],
    rules: dict[str, InstrumentRules],
    cfg: BacktestConfig,
) -> BacktestResult:
    """``frames``: velas por símbolo (índice open_time UTC), con historia previa a ``start``
    para el warmup. Solo se opera en [start, end]."""
    if cfg.latency_bars < 1:
        raise ValueError("latency_bars debe ser ≥ 1: no se puede ejecutar en la vela de la señal")
    step = interval_delta(cfg.interval)
    limits, costs = cfg.limits, cfg.costs
    risk = RiskEngine(limits, costs)
    rng = np.random.default_rng(cfg.seed)

    for s in cfg.symbols:
        idx = frames[s].index
        if not (idx.is_unique and idx.is_monotonic_increasing):
            raise ValueError(f"{s}: índice con duplicados o desordenado; valida los datos antes")
    frames = {s: frames[s].loc[: pd.Timestamp(cfg.end)] for s in cfg.symbols}
    master = pd.DatetimeIndex(sorted(set().union(*(f.index for f in frames.values()))))
    trade_from = int(master.searchsorted(pd.Timestamp(cfg.start)))
    n = len(master)

    # Arrays alineados al índice maestro (NaN = no hay vela de ese símbolo en ese minuto).
    A: dict[str, dict[str, Any]] = {}
    for s, df in frames.items():
        ev = strategy.evaluate(df)
        enter = ev["enter"].to_numpy(dtype=bool)
        if cfg.random_entry_rate is not None:
            eligible = ev["filter_ok"].to_numpy(dtype=bool) & (ev["stop"] < df["close"]).to_numpy()
            enter = eligible & (rng.random(len(df)) < cfg.random_entry_rate)
            enter[: strategy.warmup] = False
        A[s] = {
            "open": _align(master, df["open"]).to_numpy(),
            "high": _align(master, df["high"]).to_numpy(),
            "low": _align(master, df["low"]).to_numpy(),
            "close": _align(master, df["close"]).to_numpy(),
            "volume": _align(master, df["volume"]).to_numpy(),
            "close_time": _align(master, df["close_time"]),
            "enter": _align(master, pd.Series(enter, index=df.index))
            .fillna(False)
            .to_numpy(dtype=bool),
            "exit": _align(master, ev["exit"]).fillna(False).to_numpy(dtype=bool),
            "stop": _align(master, ev["stop"]).to_numpy(),
            "target": _align(master, ev["target"]).to_numpy(),
            "score": _align(master, ev["score"]).to_numpy(),
            "reason": _align(master, ev["reason"]).to_numpy(dtype=object),
        }
    corr = {
        k: v.reindex(master).to_numpy() for k, v in _rolling_corr(frames, cfg.corr_window).items()
    }
    max_hold = strategy.params.max_hold_bars

    cash = limits.reference_capital
    state = RiskState.fresh(master[min(trade_from, n - 1)].to_pydatetime(), cash)
    positions: dict[str, Trade] = {}
    pending: dict[str, _Pending] = {}
    pending_exit: dict[str, tuple[int, str]] = {}
    last_close: dict[str, float] = {}
    closed: list[Trade] = []
    rejections: Counter[str] = Counter()
    equity_curve = np.full(n, np.nan)
    exposure_curve = np.zeros(n)
    signals = partials = liq_rejects = 0
    adverse = costs.adverse_per_side
    fee = costs.taker_fee
    participation = cfg.max_volume_participation

    def mark_equity() -> Decimal:
        value = cash
        for sym, t in positions.items():
            value += t.quantity * D(last_close.get(sym, float(t.entry_price)))
        return value

    def close_trade(sym: str, i: int, price: Decimal, reason: str) -> None:
        nonlocal cash
        t = positions.pop(sym)
        t.exit_time = master[i].to_pydatetime()
        t.exit_price = price
        t.exit_fee = price * t.quantity * fee
        t.exit_reason = reason
        cash += price * t.quantity - t.exit_fee
        closed.append(t)
        state.on_trade_closed(t.net_pnl, limits)
        pending_exit.pop(sym, None)

    for i in range(n):
        ts = master[i]
        trading = i >= trade_from
        if trading:
            state.roll(ts.to_pydatetime(), mark_equity())

        for s in cfg.symbols:
            a = A[s]
            o = a["open"][i]
            if math.isnan(o):
                continue  # sin vela de este símbolo en este minuto
            h, low, c = a["high"][i], a["low"][i], a["close"][i]

            # 1) Salidas programadas al cierre anterior (señal de salida / tiempo).
            if s in pending_exit and s in positions:
                close_trade(s, i, D(o) * (1 - adverse), pending_exit[s][1])

            # 2) Entradas pendientes.
            p = pending.get(s)
            if p is not None and p.fill_index < i:
                # Faltó la vela de ejecución: la señal ha caducado, no se ejecuta tarde.
                del pending[s]
                rejections["caducada"] += 1
                p = None
            if p is not None and p.fill_index == i:
                del pending[s]
                qty = p.quantity
                cap = floor_to_step(D(a["volume"][i]) * participation, rules[s].step_size)
                partial = False
                if qty > cap:
                    qty, partial = cap, True
                fill = D(o) * (1 + adverse)
                if qty < rules[s].min_qty or qty * fill < rules[s].min_notional:
                    liq_rejects += 1
                    rejections["liquidez"] += 1
                else:
                    partials += int(partial)
                    entry_fee = fill * qty * fee
                    cash -= fill * qty + entry_fee
                    positions[s] = Trade(
                        symbol=s,
                        entry_time=ts.to_pydatetime(),
                        entry_price=fill,
                        quantity=qty,
                        entry_fee=entry_fee,
                        stop=p.stop,
                        target=p.target,
                        risk_amount=p.risk_amount * (qty / p.quantity),
                        reason=p.signal.rationale,
                        partial_fill=partial,
                    )
                    state.on_entry()

            # 3) Gestión de la posición dentro de la vela.
            t = positions.get(s)
            if t is not None:
                entered_now = t.entry_time == ts.to_pydatetime()
                stop, target = t.stop, t.target
                has_target = not math.isnan(target)
                if not entered_now and o <= stop:
                    close_trade(s, i, D(o) * (1 - adverse), "stop_gap")
                elif low <= stop:
                    close_trade(s, i, D(stop) * (1 - adverse), "stop")
                elif has_target and h > target:
                    close_trade(s, i, D(target), "target")
                else:
                    t.bars_held += 1

            last_close[s] = c

            # 4) Decisiones al cierre de la vela.
            t = positions.get(s)
            if t is not None and s not in pending_exit:
                if a["exit"][i]:
                    pending_exit[s] = (i + 1, "signal_exit")
                elif t.bars_held >= max_hold:
                    pending_exit[s] = (i + 1, "time_stop")

            if (
                trading
                and a["enter"][i]
                and s not in positions
                and s not in pending
                and i + cfg.latency_bars < n
            ):
                signals += 1
                close_time = a["close_time"].iloc[i].to_pydatetime()
                stop_v, target_v = float(a["stop"][i]), float(a["target"][i])
                sig = Signal(
                    symbol=s,
                    strategy_id=strategy.id,
                    strategy_version=strategy.version,
                    action=SignalAction.ENTER_LONG,
                    created_at=close_time,
                    reference_price=D(c),
                    rationale=str(a["reason"][i]),
                    entry_conditions={},
                    invalidation="",
                    stop_price=D(stop_v),
                    target_price=None if math.isnan(target_v) else D(target_v),
                    params={},
                    valid_until=close_time + step,
                )
                views = [
                    OpenPositionView(
                        sym,
                        tr.quantity * D(last_close.get(sym, float(tr.entry_price))),
                        tr.risk_amount,
                    )
                    for sym, tr in positions.items()
                ] + [
                    OpenPositionView(sym, pp.quantity * pp.signal.reference_price, pp.risk_amount)
                    for sym, pp in pending.items()
                ]
                correlations = {k: float(v[i]) for k, v in corr.items() if not math.isnan(v[i])}
                decision = risk.evaluate(
                    sig,
                    now=close_time,
                    state=state,
                    portfolio=PortfolioView(
                        equity=mark_equity(), cash=cash, positions=tuple(views)
                    ),
                    rules=rules[s],
                    market=MarketContext(correlations=correlations),
                )
                if decision.approved:
                    pending[s] = _Pending(
                        fill_index=i + cfg.latency_bars,
                        signal=sig,
                        quantity=decision.quantity,
                        risk_amount=decision.risk_amount,
                        stop=stop_v,
                        target=target_v,
                    )
                else:
                    for chk in decision.checks:
                        if not chk.passed:
                            rejections[chk.name] += 1

        eq = mark_equity()
        equity_curve[i] = float(eq)
        exposure_curve[i] = (
            float(sum((t.quantity * D(last_close[s]) for s, t in positions.items()), ZERO) / eq)
            if eq > 0
            else 0.0
        )

    open_trades = list(positions.values())
    idx = master[trade_from:]
    return BacktestResult(
        config=cfg,
        strategy=strategy.describe(),
        trades=closed,
        open_trades=open_trades,
        equity=pd.Series(equity_curve[trade_from:], index=idx, name="equity"),
        exposure=pd.Series(exposure_curve[trade_from:], index=idx, name="exposure"),
        data_hash=dataset_hash(frames),
        signals=signals,
        rejections=rejections,
        partial_fills=partials,
        liquidity_rejections=liq_rejects,
    )
