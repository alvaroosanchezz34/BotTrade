from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.domain import InstrumentRules, Quote, Signal, SignalAction
from app.risk.engine import MarketContext, OpenPositionView, PortfolioView, RiskEngine
from app.risk.limits import RiskLimits, RiskState
from app.risk.sizing import CostModel, position_size

NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
BTC = InstrumentRules(
    "BTCUSDT",
    "BTC",
    "USDT",
    Decimal("0.01"),
    Decimal("0.00001"),
    Decimal("0.00001"),
    Decimal("9000"),
    Decimal("5"),
)
ETH = InstrumentRules(
    "ETHUSDT",
    "ETH",
    "USDT",
    Decimal("0.01"),
    Decimal("0.0001"),
    Decimal("0.0001"),
    Decimal("9000"),
    Decimal("5"),
)
COSTS = CostModel()
LIMITS = RiskLimits()


def sig(symbol: str = "BTCUSDT", ref: str = "80000", stop: str = "79600", **kw: object) -> Signal:
    base = dict(
        symbol=symbol,
        strategy_id="t",
        strategy_version="1",
        action=SignalAction.ENTER_LONG,
        created_at=NOW,
        reference_price=Decimal(ref),
        rationale="test",
        entry_conditions={},
        invalidation="",
        stop_price=Decimal(stop),
        target_price=None,
        params={},
        valid_until=NOW + timedelta(minutes=1),
    )
    base.update(kw)
    return Signal(**base)  # type: ignore[arg-type]


def evaluate(
    s: Signal,
    *,
    state: RiskState | None = None,
    portfolio: PortfolioView | None = None,
    market: MarketContext | None = None,
    rules: InstrumentRules = BTC,
    limits: RiskLimits = LIMITS,
):
    state = state or RiskState.fresh(NOW, Decimal("500"))
    portfolio = portfolio or PortfolioView(equity=Decimal("500"), cash=Decimal("500"))
    return RiskEngine(limits, COSTS).evaluate(
        s, now=NOW, state=state, portfolio=portfolio, rules=rules, market=market or MarketContext()
    )


def failed(decision) -> set[str]:  # type: ignore[no-untyped-def]
    return {c.name for c in decision.checks if not c.passed}


# ------------------------------------------------------------------ sizing


def test_size_respects_risk_including_costs() -> None:
    entry, stop = Decimal("80000"), Decimal("79600")  # 0,5 % de stop
    r = position_size(
        entry_price=entry,
        stop_price=stop,
        risk_budget=Decimal("1.25"),
        cash=Decimal("500"),
        exposure_room=Decimal("250"),
        rules=BTC,
        costs=COSTS,
    )
    assert r.ok and r.binding == "riesgo"
    assert r.risk_amount <= Decimal("1.25")
    assert r.quantity == r.quantity.quantize(Decimal("0.00001"))
    # Con costes, el nocional es bastante menor que el ingenuo 1,25 / 0,5 % = 250.
    assert r.notional < Decimal("190")
    # Con un stop del 0,5 %, las comisiones ida/vuelta se comen ~28 % del riesgo presupuestado.
    fees = r.notional * COSTS.taker_fee * 2
    assert Decimal("0.25") < fees / r.risk_amount < Decimal("0.30")


def test_size_limited_by_exposure_and_cash() -> None:
    r = position_size(
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        risk_budget=Decimal("100"),
        cash=Decimal("500"),
        exposure_room=Decimal("50"),
        rules=ETH,
        costs=COSTS,
    )
    assert r.ok and r.binding == "exposición" and r.notional <= Decimal("50")
    r2 = position_size(
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        risk_budget=Decimal("100"),
        cash=Decimal("20"),
        exposure_room=Decimal("500"),
        rules=ETH,
        costs=COSTS,
    )
    assert r2.ok and r2.binding == "caja" and r2.notional * (1 + COSTS.taker_fee) <= Decimal("20")


def test_min_order_exceeding_risk_is_rejected() -> None:
    # Stop muy lejano: la orden mínima (5 USDT) arriesgaría más que 0,05 USDT.
    r = position_size(
        entry_price=Decimal("80000"),
        stop_price=Decimal("40000"),
        risk_budget=Decimal("0.05"),
        cash=Decimal("500"),
        exposure_room=Decimal("250"),
        rules=BTC,
        costs=COSTS,
    )
    assert not r.ok and "supera el riesgo" in r.reason


def test_invalid_stop_and_insufficient_capital() -> None:
    assert not position_size(
        entry_price=Decimal("100"),
        stop_price=Decimal("101"),
        risk_budget=Decimal("1"),
        cash=Decimal("500"),
        exposure_room=Decimal("500"),
        rules=ETH,
        costs=COSTS,
    ).ok
    r = position_size(
        entry_price=Decimal("100"),
        stop_price=Decimal("99"),
        risk_budget=Decimal("1"),
        cash=Decimal("1"),
        exposure_room=Decimal("500"),
        rules=ETH,
        costs=COSTS,
    )
    assert not r.ok and r.binding == "caja"


# ------------------------------------------------------------------ motor de riesgo


def test_approves_normal_signal() -> None:
    d = evaluate(sig())
    assert d.approved, d.reasons
    assert d.quantity > 0 and d.risk_amount <= LIMITS.risk_per_trade


def test_rejects_with_explanations() -> None:
    st = RiskState.fresh(NOW, Decimal("500"))
    st.kill_switch_active = True
    st.paused = True
    d = evaluate(
        sig(),
        state=st,
        market=MarketContext(
            data_ready=False, data_reason="obsoletos", reconciliation_ok=False, connections_ok=False
        ),
    )
    assert not d.approved and d.quantity == 0
    assert {"kill_switch", "pausa", "datos", "reconciliación", "conexiones"} <= failed(d)
    assert any("obsoletos" in r for r in d.reasons)


def test_expired_signal_and_short_rejected() -> None:
    assert "vigencia" in failed(evaluate(sig(valid_until=NOW - timedelta(seconds=1))))
    assert "solo_largos" in failed(evaluate(sig(action=SignalAction.EXIT_LONG)))


def test_daily_loss_and_drawdown_limits() -> None:
    st = RiskState.fresh(NOW, Decimal("500"))
    d = evaluate(
        sig(), state=st, portfolio=PortfolioView(equity=Decimal("495"), cash=Decimal("495"))
    )
    assert "pérdida_diaria" in failed(d)

    st2 = RiskState(
        trading_day=NOW.date(), day_start_equity=Decimal("476"), peak_equity=Decimal("500")
    )
    d2 = evaluate(
        sig(), state=st2, portfolio=PortfolioView(equity=Decimal("475"), cash=Decimal("475"))
    )
    assert {"drawdown", "bloqueos"} <= failed(d2)
    # El bloqueo por drawdown es persistente: aunque se recupere el capital, sigue.
    d3 = evaluate(
        sig(), state=st2, portfolio=PortfolioView(equity=Decimal("499"), cash=Decimal("499"))
    )
    assert "bloqueos" in failed(d3)


def test_daily_counters_reset_but_latches_do_not() -> None:
    st = RiskState.fresh(NOW, Decimal("500"))
    st.entries_today = 20
    st.latch("drawdown manual")
    st.roll(NOW + timedelta(days=1), Decimal("490"))
    assert st.entries_today == 0 and st.day_start_equity == Decimal("490")
    assert st.latched_reasons == ["drawdown manual"]


def test_consecutive_losses_cooldown() -> None:
    st = RiskState.fresh(NOW, Decimal("500"))
    for _ in range(5):
        st.on_trade_closed(Decimal("-0.5"), LIMITS)
    assert "pérdidas_consecutivas" in failed(evaluate(sig(), state=st))
    st.roll(NOW + timedelta(days=1), Decimal("497.5"))
    assert evaluate(sig(), state=st).approved


def test_max_positions_duplicates_and_trades_per_day() -> None:
    pv = PortfolioView(
        equity=Decimal("500"),
        cash=Decimal("300"),
        positions=(
            OpenPositionView("BTCUSDT", Decimal("100"), Decimal("1")),
            OpenPositionView("SOLUSDT", Decimal("100"), Decimal("1")),
        ),
    )
    assert {"posiciones", "duplicada"} <= failed(evaluate(sig(), portfolio=pv))
    st = RiskState.fresh(NOW, Decimal("500"))
    st.entries_today = LIMITS.max_trades_per_day
    assert "operaciones_día" in failed(evaluate(sig(), state=st))


def test_correlated_positions_share_risk_budget() -> None:
    pv = PortfolioView(
        equity=Decimal("500"),
        cash=Decimal("400"),
        positions=(OpenPositionView("BTCUSDT", Decimal("100"), Decimal("1.25")),),
    )
    corr = {("BTCUSDT", "ETHUSDT"): 0.85}
    d = evaluate(
        sig("ETHUSDT", "3000", "2985"),
        portfolio=pv,
        rules=ETH,
        market=MarketContext(correlations=corr),
    )
    assert d.approved
    assert d.risk_amount <= Decimal("0.625")  # 1,5 × 1,25 − 1,25
    d_uncorr = evaluate(
        sig("ETHUSDT", "3000", "2985"),
        portfolio=pv,
        rules=ETH,
        market=MarketContext(correlations={("BTCUSDT", "ETHUSDT"): 0.1}),
    )
    assert d_uncorr.risk_amount > d.risk_amount
    full = PortfolioView(
        equity=Decimal("500"),
        cash=Decimal("400"),
        positions=(OpenPositionView("BTCUSDT", Decimal("100"), Decimal("1.875")),),
    )
    assert "riesgo_correlacionado" in failed(
        evaluate(
            sig("ETHUSDT", "3000", "2985"),
            portfolio=full,
            rules=ETH,
            market=MarketContext(correlations=corr),
        )
    )


def test_exposure_cap() -> None:
    pv = PortfolioView(
        equity=Decimal("500"),
        cash=Decimal("260"),
        positions=(OpenPositionView("SOLUSDT", Decimal("249"), Decimal("0.1")),),
    )
    d = evaluate(sig(), portfolio=pv)
    assert not d.approved and "tamaño" in failed(d)


def test_abnormal_spread_rejected() -> None:
    q = Quote("BTCUSDT", Decimal("79900"), Decimal("80100"), NOW, "test")  # 25 bps
    assert "spread" in failed(evaluate(sig(), market=MarketContext(quote=q)))
    ok = Quote("BTCUSDT", Decimal("79999.99"), Decimal("80000.00"), NOW, "test")
    assert evaluate(sig(), market=MarketContext(quote=ok)).approved


def test_execution_errors_latch() -> None:
    st = RiskState.fresh(NOW, Decimal("500"))
    st.execution_errors = 3
    assert "bloqueos" in failed(evaluate(sig(), state=st))


def test_limits_are_validated() -> None:
    with pytest.raises(ValueError):
        RiskLimits(risk_per_trade_pct=Decimal("5"))  # por encima del máximo permitido
    assert LIMITS.risk_per_trade == Decimal("1.25")
    assert LIMITS.max_daily_loss == Decimal("5")
    assert LIMITS.max_drawdown == Decimal("25")
