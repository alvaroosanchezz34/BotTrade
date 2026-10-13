# Informe de validación (Fase 8, parte histórica)

Ejecutado el 2026-10-13 con **12 meses de datos reales** de BTCUSDT y ETHUSDT (2025-10-01 → 2026-09-29, velas de
1 m y 5 m de `data.binance.vision`, verificadas por SHA-256; 0 huecos, todas las velas `VALID`).
Detalle completo, tablas por tramo y sensibilidad: [`backtesting-results.md`](backtesting-results.md).
Reproducible con el hash de datos que lleva el informe (1m=`12af1464…`, 5m=`eaa8706c…`) y las semillas guardadas.

## Resultado: **ninguna estrategia pasa. Las 6 combinaciones estrategia × intervalo quedan DESCARTADAS.**

| Estrategia | Int. | Ops prueba | Neto prueba (USDT) | PF | Walk-forward neto | Supera aleatorio |
|---|---|---|---|---|---|---|
| momentum | 1m | 265 | −112,16 | 0,30 | −632,51 | 0 % |
| trend | 1m | 324 | −150,66 | 0,40 | −983,54 | 0 % |
| mean_reversion | 1m | 282 | −150,56 | 0,23 | −435,55 | 40 % |
| momentum | 5m | 197 | −85,70 | 0,34 | −268,31 | 0 % |
| trend | 5m | 80 | +30,50 | 1,60 | −108,22 | 100 % |
| mean_reversion | 5m | 45 | −14,43 | 0,45 | −73,96 | 100 % |

## Lectura honesta

1. **Sin ventaja bruta; los costes hacen el resto.** En las pruebas a 1 m el resultado *antes* de comisiones es
   aproximadamente cero (p. ej. momentum 1m: neto −112 con 98 de comisiones ⇒ bruto ≈ −14 en 265 operaciones, ≈ −0,05 por
   operación) y cada operación paga ≈ 0,4–0,5 USDT de comisiones. Es exactamente lo que anticipaba `capital-and-costs.md`.
   Los tres IC95 de la expectativa son negativos en 1 m; el drawdown con límites reales supera el 5 %.
2. **trend 5m es un falso positivo instructivo.** Es la única con beneficio en la prueba (+30,5; PF 1,60), pero:
   entrenamiento (−83) y validación (−35) negativos; walk-forward negativo (−108 en 382 operaciones, solo 1 de 9 tramos
   positivo); IC95 de la expectativa [−0,82; +1,58] incluye el 0; con costes ×1,5/×2 pierde; y el 58 % del beneficio bruto
   viene de **una sola operación**. El tramo de prueba coincide con una subida de +38 % de comprar y mantener
   (entrenamiento −36 %, validación −21 %): una estrategia solo larga gana por régimen, no por ventaja. Justo el caso por el
   que existen estos filtros. **No pasa a paper trading.**
3. **Estabilidad**: el mercado de estos 12 meses es muy poco estacionario (bajista → alcista). Con una sola ventana de
   mercado, cualquier ganancia aislada es débil evidencia.
4. En 1 m las cifras de ≈ −150 son un **tope**: la investigación usa límites holgados y se detiene al llegar a un 30 % de
   drawdown, así que la serie queda truncada (no es la pérdida de un año completo). El veredicto no cambia.
5. Dos de las estrategias (momentum, trend 1m) rinden **peor que entradas aleatorias** con las mismas salidas; las
   entradas aleatorias capturan la deriva alcista del tramo de prueba, comprar rupturas no.

## Limitaciones de esta validación

* Una sola rejilla de 8 combinaciones por estrategia y un único año; se informa el número exacto probado.
* Modelo de ejecución conservador pero sin libro de órdenes real (stop gana en velas ambiguas; liquidez = 10 % del volumen).
* Faltan los 10 días de calentamiento previos al 2025-10-01 (aviso del protocolo): las primeras horas no generan señales. No introduce sesgo.
* Solo spot, largos; BTC y ETH correlacionados: no son apuestas independientes.
* Esto es el paso **histórico**. No sustituye al paper trading ni autoriza dinero real.

## Decisión recomendada

* **No activar ninguna estrategia actual** (siguen desactivadas por defecto) ni gastar en un VPS para operarlas.
* Si se quiere seguir investigando, nuevas hipótesis (nueva versión ⇒ nueva validación completa) con cuidado de no
  sobreajustar a este año: más historia (≥ 3 años, varios regímenes), más activos, y frecuencias menores (costes por
  operación mucho menos relevantes que a 1 m). Preguntar si el objetivo es operar o aprender: la plataforma ya cumple
  la segunda función —detecta y descarta con rigor.
* Paper trading continuo (≥ 8 semanas, 300 operaciones) queda **pendiente de una estrategia que supere estos criterios**.

Criterios aplicados (`judge()`): muestra suficiente, neto fuera de muestra > 0, IC95 de la expectativa > 0, profit factor
> 1,15, drawdown con límites reales < 5 %, validación y walk-forward positivos, supervivencia a costes 1,5×/2×, superar al
90 % de las entradas aleatorias, no depender de una sola operación. Cumplirlos no autorizaría dinero real.
