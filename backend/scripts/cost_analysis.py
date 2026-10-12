"""Análisis de costes frente al capital (sección 24). Usa el mismo dimensionamiento que el bot.

Genera tablas deterministas. Los escenarios son HIPOTÉTICOS: no son predicciones.
Uso: python scripts/cost_analysis.py
"""

from decimal import Decimal as D

from app.core.domain import InstrumentRules
from app.risk.limits import RiskLimits
from app.risk.sizing import CostModel, position_size

BTC = InstrumentRules(
    "BTCUSDT", "BTC", "USDT", D("0.01"), D("0.00001"), D("0.00001"), D("9000"), D("5")
)
limits, costs = RiskLimits(), CostModel()
price = D("80000")
entry = costs.buy_fill(price)

print(
    f"Capital {limits.reference_capital} USDT · riesgo/op {limits.risk_per_trade} · "
    f"exposición máx {limits.max_exposure} · comisión {costs.taker_fee * 100}%/lado · "
    f"deslizamiento {costs.slippage * 100}%/lado · spread supuesto {costs.assumed_spread * 100}%"
)
print(f"Coste ida y vuelta (fracción del nocional): {costs.round_trip_fraction * 100:.3f} %\n")
print(
    "| Stop | Nocional | Limitado por | Riesgo real | Coste est. (comis.) | Coste / riesgo | Mov. bruto para empatar |"
)
print("|---|---|---|---|---|---|---|")
for d in ("0.002", "0.003", "0.005", "0.01", "0.02"):
    stop = price * (1 - D(d))
    r = position_size(
        entry_price=entry,
        stop_price=stop,
        risk_budget=limits.risk_per_trade,
        cash=limits.reference_capital,
        exposure_room=limits.max_exposure,
        rules=BTC,
        costs=costs,
    )
    if not r.ok:
        print(f"| {float(D(d)) * 100:.1f} % | rechazada: {r.reason} | | | | | |")
        continue
    be = costs.round_trip_fraction * 100
    print(
        f"| {float(D(d)) * 100:.1f} % | {r.notional:.2f} | {r.binding} | {r.risk_amount:.3f} | "
        f"{r.estimated_costs:.3f} | {float(r.estimated_costs / r.risk_amount) * 100:.0f} % | {be:.2f} % |"
    )

print("\nEscenarios HIPOTÉTICOS (stop 0,5 %, objetivo 1,0 %, 1 operación/día, 30 días):")
stop_d, tgt_d = D("0.005"), D("0.01")
r = position_size(
    entry_price=entry,
    stop_price=price * (1 - stop_d),
    risk_budget=limits.risk_per_trade,
    cash=limits.reference_capital,
    exposure_room=limits.max_exposure,
    rules=BTC,
    costs=costs,
)
n = r.notional
win_net = n * tgt_d - n * costs.round_trip_fraction
loss_net = -(n * stop_d + n * costs.round_trip_fraction)
print(
    f"Nocional {n:.2f}: ganadora ≈ {win_net:+.3f} USDT, perdedora ≈ {loss_net:+.3f} USDT, "
    f"payoff {abs(win_net / loss_net):.2f}:1"
)
be_wr = -loss_net / (win_net - loss_net)
print(f"Tasa de acierto mínima para empatar tras costes: {be_wr * 100:.1f} %\n")
print("| Acierto | Expectativa/op. | 30 operaciones | % del capital |")
print("|---|---|---|---|")
for wr in (D("0.30"), D("0.35"), D("0.40"), D("0.45"), D("0.50"), D("0.60")):
    e = wr * win_net + (1 - wr) * loss_net
    print(
        f"| {wr * 100:.0f} % | {e:+.3f} | {e * 30:+.2f} | {e * 30 / limits.reference_capital * 100:+.2f} % |"
    )
print(
    "\nPeor caso de diseño: pérdida diaria máx. 5 USDT (1 %) → 30 días ≈ -150 USDT si se alcanzara cada día; "
    "el drawdown máx. de 25 USDT (5 %) bloquea las entradas mucho antes."
)
