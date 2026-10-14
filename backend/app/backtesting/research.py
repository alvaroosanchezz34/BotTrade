"""Protocolo de investigación reproducible para una estrategia.

1. Partición temporal: entrenamiento 60 % · validación 20 % · prueba 20 %.
2. Rejilla pequeña de parámetros evaluada **solo** en entrenamiento.
3. Selección por beneficio neto con un mínimo de operaciones; nunca por la
   curva más bonita. La validación decide si merece la pena mirar la prueba.
4. Prueba fuera de muestra, ejecutada una única vez con la configuración elegida.
5. Walk-forward: reoptimización en ventanas móviles y concatenación de los
   tramos fuera de muestra.
6. Benchmarks: comprar y mantener, y entradas aleatorias con la misma
   frecuencia, stops y salidas (N semillas) para medir si hay ventaja real.
7. Estrés: costes duplicados sobre el tramo de prueba.

Para medir la ventaja estadística, las corridas de investigación usan límites de
pérdida diaria/drawdown holgados (si no, el bloqueo del 5 % detiene la serie y
la muestra queda truncada). El drawdown resultante se compara después con el
presupuesto del 5 %: es uno de los criterios de descarte.
"""

from __future__ import annotations

import itertools
import multiprocessing as mp
import os
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from app.backtesting.engine import BacktestConfig, BacktestResult, run_backtest
from app.backtesting.metrics import buy_and_hold, summarize
from app.core.domain import InstrumentRules
from app.risk.limits import RiskLimits
from app.risk.sizing import CostModel
from app.strategies.registry import build

# Rejillas deliberadamente pequeñas (8 combinaciones): más combinaciones = más
# probabilidad de encontrar un ajuste casual. El número se informa siempre.
GRIDS: dict[str, dict[str, list[Any]]] = {
    "momentum": {
        "lookback": [20, 60],
        "min_volume_ratio": [2.0, 3.0],
        "stop_atr": [1.5, 3.0],
    },
    "trend": {
        "fast,slow,regime": [(20, 100, 400), (50, 200, 800)],
        "stop_atr": [2.0, 4.0],
        "max_hold_bars": [300, 1200],
    },
    "mean_reversion": {
        "window": [60, 240],
        "z_entry": [2.0, 3.0],
        "stop_atr": [2.0, 4.0],
    },
}

RESEARCH_LIMITS = RiskLimits(
    max_daily_loss_pct=Decimal("10"), max_drawdown_pct=Decimal("30"), max_consecutive_losses=100
)
MIN_TRADES = 20


def expand_grid(strategy_id: str, base: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    grid = GRIDS[strategy_id]
    keys, values = list(grid), [grid[k] for k in grid]
    out = []
    for combo in itertools.product(*values):
        p: dict[str, Any] = dict(base or {})
        for k, v in zip(keys, combo, strict=True):
            if "," in k:
                p.update(dict(zip(k.split(","), v, strict=True)))
            else:
                p[k] = v
        out.append(p)
    return out


@dataclass(frozen=True)
class Segment:
    name: str
    start: datetime
    end: datetime


def split_periods(start: datetime, end: datetime) -> tuple[Segment, Segment, Segment]:
    span = end - start
    t1 = start + span * 0.6
    t2 = start + span * 0.8
    return (
        Segment("train", start, t1),
        Segment("validation", t1, t2),
        Segment("test", t2, end),
    )


def walk_forward_folds(
    start: datetime, end: datetime, train_days: int = 90, test_days: int = 30
) -> list[tuple[Segment, Segment]]:
    folds = []
    k = 0
    cursor = start
    while cursor + timedelta(days=train_days + test_days) <= end + timedelta(days=1):
        tr = Segment(f"wf{k}-train", cursor, cursor + timedelta(days=train_days))
        te_end = min(end, tr.end + timedelta(days=test_days))
        folds.append((tr, Segment(f"wf{k}-test", tr.end, te_end)))
        cursor += timedelta(days=test_days)
        k += 1
    return folds


# ------------------------------------------------------------------ ejecución paralela

_FRAMES: dict[str, pd.DataFrame] = {}
_RULES: dict[str, InstrumentRules] = {}


@dataclass(frozen=True)
class Job:
    strategy_id: str
    params: dict[str, Any]
    cfg: BacktestConfig
    tag: str


def _run_job(job: Job) -> tuple[Job, dict[str, Any], BacktestResult]:
    strat = build(job.strategy_id, job.params)
    res = run_backtest(strat, _FRAMES, _RULES, job.cfg)
    return job, summarize(res), res


def run_jobs(
    jobs: list[Job], workers: int | None = None
) -> list[tuple[Job, dict[str, Any], BacktestResult]]:
    if not jobs:
        return []
    workers = workers or max(1, min(len(jobs), (os.cpu_count() or 2) - 1))
    # Sin «fork» (Windows) los procesos hijos no heredan _FRAMES: se ejecuta en serie (más lento, correcto).
    if workers == 1 or "fork" not in mp.get_all_start_methods():
        return [_run_job(j) for j in jobs]
    ctx = mp.get_context("fork")  # los workers heredan _FRAMES sin copiarlos por pickle
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        return list(pool.map(_run_job, jobs))


def set_data(frames: dict[str, pd.DataFrame], rules: dict[str, InstrumentRules]) -> None:
    _FRAMES.clear()
    _FRAMES.update(frames)
    _RULES.clear()
    _RULES.update(rules)


# ------------------------------------------------------------------ selección


def objective(m: dict[str, Any]) -> float:
    """Beneficio neto si hay muestra mínima; si no, se descarta."""
    if m["trades"] < MIN_TRADES:
        return float("-inf")
    return float(m["net_pnl"])


def _filter_cost(params: dict[str, Any], costs: CostModel) -> dict[str, Any]:
    p = dict(params)
    flt = dict(p.get("filter", {}))
    flt.setdefault("round_trip_cost", float(costs.round_trip_fraction))
    p["filter"] = flt
    return p


@dataclass
class StrategyResearch:
    strategy_id: str
    interval: str
    grid: list[dict[str, Any]]
    train: list[dict[str, Any]]  # métricas por combinación
    chosen: dict[str, Any]
    chosen_reason: str
    validation: dict[str, Any]
    test: dict[str, Any]
    test_result: BacktestResult
    test_limited: dict[str, Any]  # prueba con los límites reales (5 % DD, 1 % diario)
    stress: dict[str, Any]
    random_test: list[dict[str, Any]]
    walk_forward: list[dict[str, Any]]
    walk_forward_trades: int
    walk_forward_net: float
    buy_hold: dict[str, dict[str, Any]]
    verdict: str
    verdict_reasons: list[str]


def research_strategy(
    strategy_id: str,
    *,
    symbols: tuple[str, ...],
    interval: str,
    start: datetime,
    end: datetime,
    costs: CostModel,
    seeds: Iterable[int] = range(10),
    workers: int | None = None,
) -> StrategyResearch:
    base_cfg = BacktestConfig(
        symbols=symbols,
        interval=interval,
        start=start,
        end=end,
        limits=RESEARCH_LIMITS,
        costs=costs,
    )
    train, val, test = split_periods(start, end)
    grid = [_filter_cost(p, costs) for p in expand_grid(strategy_id)]

    def cfg_for(seg: Segment, **kw: Any) -> BacktestConfig:
        return replace(base_cfg, start=seg.start, end=seg.end, **kw)

    # 1) Rejilla en entrenamiento.
    train_runs = run_jobs(
        [Job(strategy_id, p, cfg_for(train), f"train-{i}") for i, p in enumerate(grid)], workers
    )
    train_metrics = [{"params": j.params, **m} for j, m, _ in train_runs]
    best_i = int(np.argmax([objective(m) for _, m, _ in train_runs]))
    chosen = grid[best_i]
    best_obj = objective(train_runs[best_i][1])
    chosen_reason = (
        "máximo beneficio neto en entrenamiento con ≥ 20 operaciones"
        if best_obj != float("-inf")
        else "ninguna combinación alcanzó 20 operaciones en entrenamiento; se usa la primera"
    )
    if best_obj == float("-inf"):
        chosen = grid[0]

    # 2) Validación, prueba, prueba con límites reales y estrés de costes.
    stress_costs = CostModel(
        taker_fee=costs.taker_fee * Decimal("1.5"),
        maker_fee=costs.maker_fee * Decimal("1.5"),
        slippage=costs.slippage * 2,
        assumed_spread=costs.assumed_spread * 2,
    )
    jobs = [
        Job(strategy_id, chosen, cfg_for(val), "validation"),
        Job(strategy_id, chosen, cfg_for(test), "test"),
        Job(strategy_id, chosen, cfg_for(test, limits=RiskLimits()), "test-limited"),
        Job(strategy_id, chosen, cfg_for(test, costs=stress_costs), "stress"),
    ]
    out = {j.tag: (m, r) for j, m, r in run_jobs(jobs, workers)}
    test_m, test_res = out["test"]

    # 3) Entradas aleatorias con la misma frecuencia que la estrategia en la prueba.
    bars_in_test = max(1, len(test_res.equity))
    rate = max(test_m["signals"], 1) / (bars_in_test * len(symbols))
    rnd = run_jobs(
        [
            Job(strategy_id, chosen, cfg_for(test, random_entry_rate=rate, seed=s), f"random-{s}")
            for s in seeds
        ],
        workers,
    )
    random_metrics = [{"seed": j.cfg.seed, **m} for j, m, _ in rnd]

    # 4) Walk-forward con reoptimización.
    wf_rows: list[dict[str, Any]] = []
    wf_trades, wf_net = 0, 0.0
    folds = walk_forward_folds(start, end)
    fold_jobs = [
        Job(strategy_id, p, cfg_for(tr), f"{tr.name}|{i}")
        for tr, _ in folds
        for i, p in enumerate(grid)
    ]
    fold_runs = run_jobs(fold_jobs, workers)
    by_fold: dict[str, list[tuple[Job, dict[str, Any]]]] = {}
    for j, m, _ in fold_runs:
        by_fold.setdefault(j.tag.split("|")[0], []).append((j, m))
    oos_jobs = []
    for tr, te in folds:
        cands = by_fold[tr.name]
        best = max(cands, key=lambda jm: objective(jm[1]))
        params = best[0].params if objective(best[1]) != float("-inf") else None
        if params is None:
            wf_rows.append(
                {
                    "fold": te.name,
                    "start": te.start.isoformat(),
                    "end": te.end.isoformat(),
                    "skipped": "sin combinación con muestra suficiente en entrenamiento",
                }
            )
            continue
        oos_jobs.append(Job(strategy_id, params, cfg_for(te), te.name))
    for j, m, _ in run_jobs(oos_jobs, workers):
        wf_trades += m["trades"]
        wf_net += m["net_pnl"]
        wf_rows.append(
            {
                "fold": j.tag,
                "start": j.cfg.start.isoformat(),
                "end": j.cfg.end.isoformat(),
                "params": j.params,
                "trades": m["trades"],
                "net_pnl": m["net_pnl"],
                "profit_factor": m["profit_factor"],
                "max_drawdown_pct": m["max_drawdown_pct"],
            }
        )
    wf_rows.sort(key=lambda r: r["start"])

    adverse = float(costs.adverse_per_side)
    fee = float(costs.taker_fee)
    bh = {
        seg.name: buy_and_hold(
            _FRAMES, seg.start, seg.end, float(RESEARCH_LIMITS.reference_capital), fee, adverse
        )
        for seg in (train, val, test)
    }

    verdict, reasons = judge(
        val_m=out["validation"][0],
        test_m=test_m,
        limited=out["test-limited"][0],
        stress=out["stress"][0],
        random_metrics=random_metrics,
        wf_net=wf_net,
        wf_trades=wf_trades,
    )
    return StrategyResearch(
        strategy_id=strategy_id,
        interval=interval,
        grid=grid,
        train=train_metrics,
        chosen=chosen,
        chosen_reason=chosen_reason,
        validation=out["validation"][0],
        test=test_m,
        test_result=test_res,
        test_limited=out["test-limited"][0],
        stress=out["stress"][0],
        random_test=random_metrics,
        walk_forward=wf_rows,
        walk_forward_trades=wf_trades,
        walk_forward_net=wf_net,
        buy_hold=bh,
        verdict=verdict,
        verdict_reasons=reasons,
    )


def judge(
    *,
    val_m: dict[str, Any],
    test_m: dict[str, Any],
    limited: dict[str, Any],
    stress: dict[str, Any],
    random_metrics: list[dict[str, Any]],
    wf_net: float,
    wf_trades: int,
) -> tuple[str, list[str]]:
    """Criterios de la sección 23. Cumplirlos NO autoriza dinero real."""
    fails: list[str] = []
    if test_m["trades"] < MIN_TRADES:
        fails.append(f"muestra insuficiente en prueba ({test_m['trades']} operaciones)")
    if test_m["net_pnl"] <= 0:
        fails.append(f"resultado neto negativo fuera de muestra ({test_m['net_pnl']:.2f})")
    ci = test_m.get("expectancy_ci95")
    if ci and ci[0] <= 0:
        fails.append("el intervalo de confianza de la expectativa incluye el 0")
    pf = test_m.get("profit_factor")
    if pf is None or pf <= 1.15:
        fails.append(f"profit factor en prueba ≤ 1,15 ({pf if pf is None else round(pf, 2)})")
    if limited["max_drawdown_pct"] >= 5:
        fails.append(f"drawdown con límites reales ≥ 5 % ({limited['max_drawdown_pct']:.2f} %)")
    if val_m["net_pnl"] <= 0:
        fails.append("validación negativa: no es estable entre periodos")
    if wf_net <= 0:
        fails.append(f"walk-forward negativo ({wf_net:.2f} en {wf_trades} operaciones)")
    if stress["net_pnl"] <= 0:
        fails.append("no sobrevive a costes 1,5×/2×")
    if random_metrics:
        rnd = sorted(r["net_pnl"] for r in random_metrics)
        beaten = sum(test_m["net_pnl"] > r for r in rnd) / len(rnd)
        if beaten < 0.9:
            fails.append(f"no supera al 90 % de las entradas aleatorias (supera al {beaten:.0%})")
    best_share = test_m.get("largest_win_share")
    if (
        test_m.get("net_pnl_without_best", 0) is not None
        and test_m["net_pnl"] > 0
        and (test_m.get("net_pnl_without_best", 0) <= 0)
    ):
        fails.append(f"depende de una sola operación ({best_share:.0%} del beneficio bruto)")
    if fails:
        return "DESCARTADA", fails
    return "CANDIDATA A PAPER TRADING (no a dinero real)", []
