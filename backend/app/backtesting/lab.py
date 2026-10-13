"""Laboratorio de estrategias: una idea concreta (plantilla + parámetros elegidos por el usuario)
se evalúa con un informe honesto.

Diferencias deliberadas con ``research``:

* **No optimiza**: se evalúa exactamente la configuración que se pide, sin elegir la mejor de una rejilla.
* En lugar de entrenamiento/validación/prueba, se divide el periodo en **tres tercios iguales** para ver
  si el resultado es estable o depende de una racha.
* Se informa de **cuántas variantes ha probado ya el usuario**: probar muchas hasta que una salga bien
  es la forma más habitual de engañarse (comparaciones múltiples).
* Solo plantillas del catálogo con parámetros **validados y acotados**; nunca se ejecuta código del usuario.

Un veredicto «PROMETEDORA» **no** es prueba de rentabilidad: solo significa que supera los filtros
iniciales en los datos usados. Hace falta validarla en datos que no se hayan visto.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from pydantic import ValidationError

from app.backtesting import research as rs
from app.backtesting.engine import BacktestConfig, BacktestResult
from app.backtesting.metrics import buy_and_hold
from app.core.domain import InstrumentRules, interval_delta
from app.risk.limits import RiskLimits
from app.risk.sizing import CostModel
from app.strategies.registry import STRATEGIES, build

ALLOWED_INTERVALS = ("1m", "5m")
# Límites por intervalo (protegen la CPU del servidor y evitan simulaciones sin sentido estadístico).
MAX_MONTHS: dict[str, int] = {"1m": 6, "5m": 12}
MONTH_CHOICES = (1, 3, 6, 12)
MIN_BARS: dict[str, int] = {"1m": 10_000, "5m": 2_000}
MIN_TRADES_FOR_VERDICT = 100
SEEDS = 10
MAX_SYMBOLS = 2

COST_LEVELS: dict[str, CostModel] = {
    "normal": CostModel(),
    # Más cerca del peor caso: comisión 0,15 %/lado (sin descuento y con mercado menos líquido), doble deslizamiento.
    "conservador": CostModel(
        taker_fee=Decimal("0.0015"),
        maker_fee=Decimal("0.0015"),
        slippage=CostModel().slippage * 2,
        assumed_spread=CostModel().assumed_spread * 2,
    ),
}


class LabError(ValueError):
    """Petición no válida para el laboratorio (mensaje apto para el usuario)."""


@dataclass(frozen=True)
class LabSpec:
    strategy_id: str
    symbols: tuple[str, ...]
    interval: str
    start: datetime
    end: datetime
    params: dict[str, Any]
    cost_level: str = "normal"

    def to_json(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbols": list(self.symbols),
            "interval": self.interval,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "params": self.params,
            "cost_level": self.cost_level,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> LabSpec:
        return cls(
            strategy_id=d["strategy_id"],
            symbols=tuple(d["symbols"]),
            interval=d["interval"],
            start=datetime.fromisoformat(d["start"]),
            end=datetime.fromisoformat(d["end"]),
            params=dict(d["params"]),
            cost_level=d.get("cost_level", "normal"),
        )


# ------------------------------------------------------------------ catálogo y validación


def param_specs(strategy_id: str) -> list[dict[str, Any]]:
    """Parámetros editables (numéricos de primer nivel) con sus rangos, para generar el formulario."""
    cls = STRATEGIES[strategy_id]
    schema = cls.Params.model_json_schema()
    out: list[dict[str, Any]] = []
    for name, p in schema["properties"].items():
        if name in ("filter",) or p.get("type") not in ("integer", "number"):
            continue
        lo = p.get("minimum", p.get("exclusiveMinimum"))
        out.append(
            {
                "name": name,
                "type": p["type"],
                "default": p.get("default"),
                "min": lo,
                "min_exclusive": "exclusiveMinimum" in p,
                "max": p.get("maximum", p.get("exclusiveMaximum")),
                "description": p.get("description", ""),
            }
        )
    return out


def validate_params(strategy_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Devuelve los parámetros completos y validados (o ``LabError`` con un motivo legible)."""
    if strategy_id not in STRATEGIES:
        raise LabError(f"Estrategia desconocida: {strategy_id}")
    allowed = {p["name"] for p in param_specs(strategy_id)}
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise LabError(f"Parámetros no editables o desconocidos: {', '.join(unknown)}")
    try:
        strat = build(strategy_id, params)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(x) for x in first["loc"]) or "parámetros"
        raise LabError(f"Valor no válido en «{field}»: {first['msg']}") from exc
    full = strat.params.model_dump()
    return {k: full[k] for k in allowed}


def plan_period(
    first: datetime, last: datetime, interval: str, months: int
) -> tuple[datetime, datetime]:
    """Periodo ``[start, end]`` terminado en el último dato disponible. Falla si no hay base suficiente."""
    if interval not in ALLOWED_INTERVALS:
        raise LabError(f"Intervalo no soportado: {interval}")
    if months not in MONTH_CHOICES:
        raise LabError(f"Periodo no válido: elige entre {', '.join(map(str, MONTH_CHOICES))} meses")
    if months > MAX_MONTHS[interval]:
        raise LabError(
            f"Con velas de {interval} el periodo máximo es {MAX_MONTHS[interval]} meses "
            "(protege el servidor y evita simulaciones sin sentido estadístico)"
        )
    end = last
    start = max(first, end - timedelta(days=round(months * 30.4)))
    bars = int((end - start) / interval_delta(interval)) + 1
    if bars < MIN_BARS[interval]:
        raise LabError(
            f"Datos insuficientes: {bars} velas de {interval} en el periodo (mínimo {MIN_BARS[interval]})"
        )
    return start, end


# ------------------------------------------------------------------ informe


def _clean(o: Any) -> Any:
    """JSON válido para PostgreSQL: sin NaN/Infinity (jsonb los rechaza)."""
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else o
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_clean(v) for v in o]
    if isinstance(o, np.generic):
        return _clean(o.item())
    return o


_KEEP = (
    "trades", "win_rate", "avg_win", "avg_loss", "expectancy", "expectancy_ci95", "profit_factor",
    "gross_pnl", "net_pnl", "fees", "avg_bars_held", "largest_win_share", "net_pnl_without_best",
    "exit_reasons", "final_equity", "return_pct", "max_drawdown", "max_drawdown_pct", "sharpe",
    "exposure_avg_pct", "time_in_market_pct", "signals", "risk_rejections", "open_positions_at_end",
    "by_month", "by_symbol",
)  # fmt: skip


def _slim(m: dict[str, Any]) -> dict[str, Any]:
    return {k: m.get(k) for k in _KEEP}


def lab_verdict(
    main: dict[str, Any],
    thirds: list[dict[str, Any]],
    stress: dict[str, Any],
    limited: dict[str, Any],
    random_nets: list[float],
) -> tuple[str, list[dict[str, Any]]]:
    """Comprobaciones y veredicto: «PROMETEDORA» exige pasarlas todas; «NO CONCLUYENTE» si hay pocas operaciones."""
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    n = main["trades"]
    check(
        "Muestra suficiente",
        n >= MIN_TRADES_FOR_VERDICT,
        f"{n} operaciones cerradas (mínimo {MIN_TRADES_FOR_VERDICT} para sacar conclusiones)",
    )
    check(
        "Resultado neto positivo",
        main["net_pnl"] > 0,
        f"neto {main['net_pnl']:.2f} USDT tras costes",
    )
    ci = main.get("expectancy_ci95")
    check(
        "La ventaja no es compatible con cero",
        bool(ci and ci[0] > 0),
        "IC95 de la ganancia media por operación: "
        + (f"[{ci[0]:.3f}; {ci[1]:.3f}] USDT" if ci else "no calculable"),
    )
    pf = main.get("profit_factor")
    check(
        "Profit factor > 1,15",
        bool(pf and pf > 1.15),
        f"profit factor {pf:.2f}" if pf else "no calculable",
    )
    pos = sum(1 for t in thirds if t["net_pnl"] > 0)
    check(
        "Estable entre periodos",
        pos >= 2,
        f"{pos} de {len(thirds)} tercios con beneficio ("
        + ", ".join(f"{t['net_pnl']:+.1f}" for t in thirds)
        + ")",
    )
    check(
        "Sobrevive a costes ×1,5/×2",
        stress["net_pnl"] > 0,
        f"neto con costes estresados: {stress['net_pnl']:.2f} USDT",
    )
    if random_nets:
        beaten = sum(main["net_pnl"] > r for r in random_nets) / len(random_nets)
        check(
            "Supera a entradas aleatorias",
            beaten >= 0.9,
            f"mejor que el {beaten:.0%} de {len(random_nets)} simulaciones con entradas al azar y las mismas salidas",
        )
    best_wo = main.get("net_pnl_without_best")
    share = main.get("largest_win_share")
    check(
        "No depende de una sola operación",
        not (main["net_pnl"] > 0 and best_wo is not None and best_wo <= 0),
        "sin su mejor operación el neto sería "
        + (f"{best_wo:.2f} USDT" if best_wo is not None else "—")
        + (f" (la mejor aporta el {share:.0%} del beneficio bruto)" if share else ""),
    )
    dd = limited.get("max_drawdown_pct") or 0.0
    check(
        "Drawdown dentro del presupuesto (5 %)",
        dd < 5.0,
        f"con los límites de riesgo reales el drawdown máximo fue {dd:.2f} %",
    )
    # Con muestra insuficiente no se puede concluir NADA, ni a favor ni en contra: no es «sin ventaja».
    if n < MIN_TRADES_FOR_VERDICT:
        return "NO CONCLUYENTE", checks
    ok = all(c["passed"] for c in checks)
    return ("PROMETEDORA" if ok else "SIN EVIDENCIA DE VENTAJA"), checks


def multiple_testing_note(variants: int) -> str | None:
    if variants < 5:
        return None
    return (
        f"Has probado {variants} variantes de esta idea. Con tantas pruebas, que una salga bien por pura casualidad "
        "es mucho más probable: un buen resultado aquí es débil evidencia. Valídalo con datos que no hayas visto antes."
    )


def run_lab(
    spec: LabSpec,
    frames: dict[str, pd.DataFrame],
    rules: dict[str, InstrumentRules],
    *,
    variants_tried: int = 1,
    workers: int | None = None,
) -> tuple[dict[str, Any], BacktestResult]:
    """Ejecuta todas las pruebas y devuelve (informe serializable, resultado principal con operaciones)."""
    costs = COST_LEVELS[spec.cost_level]
    params = rs._filter_cost(spec.params, costs)  # el filtro de volatilidad usa el coste real
    strat_ok = build(spec.strategy_id, params)  # validación final (lanza si algo no encaja)
    del strat_ok

    rs.set_data(frames, rules)
    base = BacktestConfig(
        symbols=spec.symbols,
        interval=spec.interval,
        start=spec.start,
        end=spec.end,
        limits=rs.RESEARCH_LIMITS,
        costs=costs,
    )
    stress_costs = CostModel(
        taker_fee=costs.taker_fee * Decimal("1.5"),
        maker_fee=costs.maker_fee * Decimal("1.5"),
        slippage=costs.slippage * 2,
        assumed_spread=costs.assumed_spread * 2,
    )
    span = spec.end - spec.start
    third = [(spec.start + span * i / 3, spec.start + span * (i + 1) / 3) for i in range(3)]
    jobs = [
        rs.Job(spec.strategy_id, params, base, "main"),
        rs.Job(spec.strategy_id, params, replace(base, limits=RiskLimits()), "limited"),
        rs.Job(spec.strategy_id, params, replace(base, costs=stress_costs), "stress"),
    ] + [
        rs.Job(spec.strategy_id, params, replace(base, start=a, end=b), f"third-{i}")
        for i, (a, b) in enumerate(third)
    ]
    out = {j.tag: (m, r) for j, m, r in rs.run_jobs(jobs, workers)}
    main_m, main_res = out["main"]

    bars = max(1, len(main_res.equity))
    rate = max(main_m["signals"], 1) / (bars * len(spec.symbols))
    rnd = rs.run_jobs(
        [
            rs.Job(
                spec.strategy_id, params, replace(base, random_entry_rate=rate, seed=s), f"rnd-{s}"
            )
            for s in range(SEEDS)
        ],
        workers,
    )
    random_nets = [m["net_pnl"] for _, m, _ in rnd]

    thirds = []
    for i, (a, b) in enumerate(third):
        m = out[f"third-{i}"][0]
        thirds.append(
            {
                "label": f"Tercio {i + 1}",
                "start": a.isoformat(),
                "end": b.isoformat(),
                "trades": m["trades"],
                "net_pnl": m["net_pnl"],
                "profit_factor": m["profit_factor"],
                "return_pct": m["return_pct"],
            }
        )
    bh = buy_and_hold(
        frames,
        spec.start,
        spec.end,
        float(rs.RESEARCH_LIMITS.reference_capital),
        float(costs.taker_fee),
        float(costs.adverse_per_side),
    )
    verdict, checks = lab_verdict(main_m, thirds, out["stress"][0], out["limited"][0], random_nets)
    r_sorted = sorted(random_nets)
    report = {
        "verdict": verdict,
        "checks": checks,
        "spec": spec.to_json(),
        "effective_params": params,
        "costs": costs.as_dict(),
        "main": _slim(main_m),
        "thirds": thirds,
        "stress": _slim(out["stress"][0]),
        "limited": _slim(out["limited"][0]),
        "random": {
            "n": len(random_nets),
            "net_min": r_sorted[0] if r_sorted else None,
            "net_median": float(np.median(r_sorted)) if r_sorted else None,
            "net_max": r_sorted[-1] if r_sorted else None,
        },
        "buy_hold": bh,
        "variants_tried": variants_tried,
        "multiple_testing_note": multiple_testing_note(variants_tried),
        "data": {"hash": main_res.data_hash, "bars": bars},
        "caveats": [
            "Simulación histórica con dinero ficticio: no predice resultados futuros.",
            "Ejecución conservadora sin libro de órdenes real (el stop gana en velas ambiguas).",
            f"Solo spot, largos. Capital de referencia {rs.RESEARCH_LIMITS.reference_capital} USDT.",
        ],
    }
    return _clean(report), main_res
