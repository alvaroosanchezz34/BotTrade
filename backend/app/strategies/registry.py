"""Catálogo de estrategias disponibles."""

from __future__ import annotations

from typing import Any

from app.strategies.base import Strategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.momentum import MomentumStrategy
from app.strategies.trend import TrendStrategy

STRATEGIES: dict[str, type[Strategy[Any]]] = {
    MomentumStrategy.id: MomentumStrategy,
    TrendStrategy.id: TrendStrategy,
    MeanReversionStrategy.id: MeanReversionStrategy,
}


def build(strategy_id: str, params: dict[str, Any] | None = None) -> Strategy[Any]:
    try:
        cls = STRATEGIES[strategy_id]
    except KeyError as exc:
        raise ValueError(f"Estrategia desconocida: {strategy_id}") from exc
    return cls(params)
