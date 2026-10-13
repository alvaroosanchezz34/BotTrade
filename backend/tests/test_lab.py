from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.backtesting import lab
from app.backtesting.lab import LabError, LabSpec
from tests.exec_helpers import RULES


def random_walk(
    n: int, *, seed: int, start_px: float = 80000.0, drift: float = 0.0
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    idx = pd.DatetimeIndex([t0 + timedelta(minutes=5 * i) for i in range(n)], name="open_time")
    ret = rng.normal(drift, 0.0012, n)
    close = start_px * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[start_px], close[:-1]])
    spread = np.abs(rng.normal(0, 0.0008, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    vol = rng.lognormal(1.0, 0.6, n) + 1
    return pd.DataFrame(
        {
            "close_time": idx + timedelta(minutes=5) - timedelta(milliseconds=1),
            "open": open_, "high": high, "low": low, "close": close,
            "volume": vol, "quote_volume": vol * close, "trades": 50,
        },
        index=idx,
    )  # fmt: skip


def spec_for(frame: pd.DataFrame, strategy: str = "momentum", **params: object) -> LabSpec:
    return LabSpec(
        strategy_id=strategy,
        symbols=("BTCUSDT",),
        interval="5m",
        start=frame.index[2000].to_pydatetime(),
        end=frame.index[-1].to_pydatetime(),
        params=lab.validate_params(strategy, dict(params)),
    )


# ------------------------------------------------------------------ validación


def test_param_specs_expose_bounded_numeric_parameters_only() -> None:
    specs = {p["name"]: p for p in lab.param_specs("momentum")}
    assert "filter" not in specs and "lookback" in specs
    assert specs["lookback"]["min"] == 2 and specs["lookback"]["max"] == 1000
    assert specs["stop_atr"]["min_exclusive"] is True


@pytest.mark.parametrize(
    ("strategy", "params", "fragment"),
    [
        ("momentum", {"lookback": 1}, "lookback"),
        ("momentum", {"lookback": 99999}, "lookback"),
        ("momentum", {"stop_atr": 0}, "stop_atr"),
        ("momentum", {"hack": 1}, "no editables"),
        (
            "momentum",
            {"filter": {"enabled": False}},
            "no editables",
        ),  # no se puede apagar el filtro
        ("trend", {"fast": 300, "slow": 100}, "fast"),  # fast < slow < regime
        ("nope", {}, "desconocida"),
    ],
)
def test_validate_params_rejects_bad_input_with_readable_message(
    strategy: str, params: dict[str, object], fragment: str
) -> None:
    with pytest.raises(LabError, match=fragment):
        lab.validate_params(strategy, params)


def test_validate_params_fills_defaults() -> None:
    full = lab.validate_params("momentum", {"lookback": 30})
    assert full["lookback"] == 30 and full["stop_atr"] == 1.5 and "filter" not in full


def test_plan_period_clamps_and_enforces_limits() -> None:
    first = datetime(2025, 1, 1, tzinfo=UTC)
    last = datetime(2026, 1, 1, tzinfo=UTC)
    start, end = lab.plan_period(first, last, "5m", 3)
    assert end == last and 85 <= (end - start).days <= 95
    s2, _ = lab.plan_period(first, last, "5m", 12)
    assert s2 >= first
    with pytest.raises(LabError, match="máximo"):
        lab.plan_period(first, last, "1m", 12)
    with pytest.raises(LabError, match="Periodo no válido"):
        lab.plan_period(first, last, "5m", 2)
    with pytest.raises(LabError, match="Intervalo"):
        lab.plan_period(first, last, "1h", 3)
    with pytest.raises(LabError, match="insuficientes"):
        lab.plan_period(last - timedelta(days=2), last, "5m", 3)


def test_multiple_testing_note_only_after_many_variants() -> None:
    assert lab.multiple_testing_note(1) is None
    assert lab.multiple_testing_note(4) is None
    note = lab.multiple_testing_note(7)
    assert note and "7 variantes" in note and "casualidad" in note


# ------------------------------------------------------------------ informe


def test_random_walk_has_no_edge_and_report_is_honest_and_serializable() -> None:
    frame = random_walk(6000, seed=11)
    report, res = lab.run_lab(spec_for(frame), {"BTCUSDT": frame}, RULES, workers=1)

    # En un paseo aleatorio no hay ventaja que encontrar: nunca puede salir «prometedora».
    assert report["verdict"] in ("SIN EVIDENCIA DE VENTAJA", "NO CONCLUYENTE")
    names = [c["name"] for c in report["checks"]]
    assert "Muestra suficiente" in names and "Supera a entradas aleatorias" in names
    assert any(not c["passed"] for c in report["checks"])
    assert [t["label"] for t in report["thirds"]] == ["Tercio 1", "Tercio 2", "Tercio 3"]
    assert report["random"]["n"] == lab.SEEDS
    assert report["stress"]["net_pnl"] <= report["main"]["net_pnl"] + 1e-9  # más costes, no mejor
    assert report["variants_tried"] == 1 and report["multiple_testing_note"] is None
    assert any("dinero ficticio" in c for c in report["caveats"])
    json.dumps(report, allow_nan=False)  # PostgreSQL jsonb no admite NaN/Infinity
    assert res.config.limits.reference_capital == 500


def test_same_request_same_result_reproducible() -> None:
    frame = random_walk(5000, seed=3)
    a, _ = lab.run_lab(spec_for(frame), {"BTCUSDT": frame}, RULES, workers=1)
    b, _ = lab.run_lab(spec_for(frame), {"BTCUSDT": frame}, RULES, workers=1)
    assert a["main"] == b["main"] and a["random"] == b["random"] and a["data"] == b["data"]


def test_conservative_costs_never_improve_the_result() -> None:
    frame = random_walk(5000, seed=5)
    normal, _ = lab.run_lab(spec_for(frame), {"BTCUSDT": frame}, RULES, workers=1)
    spec = spec_for(frame)
    cons_spec = LabSpec(**{**spec.__dict__, "cost_level": "conservador"})
    cons, _ = lab.run_lab(cons_spec, {"BTCUSDT": frame}, RULES, workers=1)
    assert (
        cons["main"]["fees"] >= normal["main"]["fees"] * 0.99
        or cons["main"]["trades"] < normal["main"]["trades"]
    )
    assert (
        cons["main"]["net_pnl"] <= normal["main"]["net_pnl"] + 1e-6
        or cons["main"]["trades"] != normal["main"]["trades"]
    )


def test_variants_count_is_reported() -> None:
    frame = random_walk(5000, seed=7)
    report, _ = lab.run_lab(spec_for(frame), {"BTCUSDT": frame}, RULES, variants_tried=9, workers=1)
    assert report["variants_tried"] == 9 and "9 variantes" in report["multiple_testing_note"]


def test_verdict_requires_every_check() -> None:
    good = {
        "trades": 200, "net_pnl": 40.0, "expectancy_ci95": [0.05, 0.4], "profit_factor": 1.5,
        "net_pnl_without_best": 25.0, "largest_win_share": 0.2,
    }  # fmt: skip
    thirds = [{"net_pnl": 10.0}, {"net_pnl": 20.0}, {"net_pnl": 10.0}]
    stress = {"net_pnl": 5.0}
    limited = {"max_drawdown_pct": 3.0}
    verdict, checks = lab.lab_verdict(good, thirds, stress, limited, [-10.0] * 10)
    assert verdict == "PROMETEDORA" and all(c["passed"] for c in checks)

    for tweak, label in (
        ({"net_pnl": -1.0}, "Resultado neto positivo"),
        ({"expectancy_ci95": [-0.1, 0.5]}, "La ventaja no es compatible con cero"),
        ({"profit_factor": 1.05}, "Profit factor"),
        ({"net_pnl_without_best": -3.0}, "No depende de una sola operación"),
    ):
        v, c = lab.lab_verdict({**good, **tweak}, thirds, stress, limited, [-10.0] * 10)
        assert v == "SIN EVIDENCIA DE VENTAJA", label
        assert any(not x["passed"] and label in x["name"] for x in c), label

    v, _ = lab.lab_verdict(
        good,
        [{"net_pnl": 30.0}, {"net_pnl": -5.0}, {"net_pnl": -4.0}],
        stress,
        limited,
        [-10.0] * 10,
    )
    assert v == "SIN EVIDENCIA DE VENTAJA"  # un solo tercio con beneficio: racha, no estabilidad
    v, _ = lab.lab_verdict(good, thirds, {"net_pnl": -2.0}, limited, [-10.0] * 10)
    assert v == "SIN EVIDENCIA DE VENTAJA"
    v, _ = lab.lab_verdict(good, thirds, stress, {"max_drawdown_pct": 7.0}, [-10.0] * 10)
    assert v == "SIN EVIDENCIA DE VENTAJA"
    v, _ = lab.lab_verdict(good, thirds, stress, limited, [100.0] * 10)  # el azar lo supera
    assert v == "SIN EVIDENCIA DE VENTAJA"
