# Capital y costes (500 USDT)

Calculado con el **mismo dimensionamiento que el bot** (`backend/scripts/cost_analysis.py`).
Los escenarios son **hipotéticos, no predicciones**. No hay objetivo de rentabilidad mensual.

Supuestos: comisión taker 0,10 %/lado (sin descuento BNB), deslizamiento 0,02 %/lado, spread supuesto 0,01 %,
riesgo por operación 1,25 USDT (0,25 %), exposición máxima 250 USDT.

**Coste ida y vuelta = 0,250 % del nocional.** El precio debe moverse a favor al menos eso solo para empatar.

| Stop | Nocional | Limitado por | Riesgo real | Comisiones est. | Comisiones / riesgo |
|---|---|---|---|---|---|
| 0,2 % | 249,66 | exposición | 1,123 | 0,499 | 44 % |
| 0,3 % | 227,26 | riesgo | 1,249 | 0,454 | 36 % |
| 0,5 % | 166,44 | riesgo | 1,247 | 0,332 | 27 % |
| 1,0 % | 100,02 | riesgo | 1,249 | 0,199 | 16 % |
| 2,0 % | 55,21 | riesgo | 1,241 | 0,109 | 9 % |

Cuanto más corto el stop (scalping), mayor la proporción que se llevan los costes: con stop del 0,2 % cerca de la mitad
del riesgo presupuestado son comisiones, y el tope de exposición (250 USDT) impide siquiera usar todo el riesgo.

## Ejemplo hipotético: stop 0,5 %, objetivo 1,0 %, 1 operación al día durante 30 días

Nocional 166,44 USDT → ganadora ≈ +1,248, perdedora ≈ −1,248 (tras costes el pago es 1:1), así que **hace falta acertar
más del 50 %** solo para no perder.

| Acierto | Expectativa/op. | 30 operaciones | % del capital |
|---|---|---|---|
| 30 % | −0,499 | −14,98 | −3,00 % |
| 35 % | −0,374 | −11,23 | −2,25 % |
| 40 % | −0,250 | −7,49 | −1,50 % |
| 45 % | −0,125 | −3,74 | −0,75 % |
| 50 % | 0,000 | 0,00 | 0,00 % |
| 60 % | +0,250 | +7,49 | +1,50 % |

Incluso con un 60 % de acierto —que no hay motivo para esperar— serían ~7 USDT al mes sobre 500: **500 € no dan ingresos
significativos con scalping**. Peor caso de diseño: 5 USDT/día de pérdida diaria máxima; el drawdown de 25 USDT bloquea las
entradas mucho antes de acumular 30 días de pérdidas máximas.

Si una estrategia no tiene ventaja estadística tras costes, el resultado correcto es **descartarla**. Subir el capital no
corrige una estrategia deficiente (los costes escalan con el nocional) y no se recomienda.
