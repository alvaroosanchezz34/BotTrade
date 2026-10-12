# Informe de validación (Fase 8)

> **Estado: NO HAY RESULTADOS.** Este informe no contiene métricas de rentabilidad porque
> **no se han podido obtener datos de mercado** en el entorno de desarrollo: la política de red de la
> sesión denegó (HTTP 403 en CONNECT) `data.binance.vision`, `data-api.binance.vision` y `api.binance.com`.
> No se ha ejecutado `download-history`, ni `research`, ni paper trading. Cualquier cifra aquí sería inventada,
> así que no hay ninguna. Lo que sí está verificado es la maquinaria (ver `STATUS.md`).

## Conclusión que se puede afirmar hoy

* Ninguna de las tres estrategias (`momentum`, `trend`, `mean_reversion`) ha demostrado ventaja estadística.
  **Su estado es «sin validar», no «rentable».** El sistema las mantiene desactivadas por defecto.
* El análisis de costes (`docs/capital-and-costs.md`) sí es determinista y no requiere datos: con 0,25 % de coste ida y vuelta,
  un stop del 0,5 % y objetivo del 1 % exigen **> 50 % de acierto solo para empatar**. Es un listón alto para scalping a 1 min.
* El sistema **no está listo para dinero real** y no se ha intentado que lo esté (no existe ese modo).

## Cómo obtener el informe real

```bash
# 1) Datos (≥ 12 meses recomendados; BTCUSDT y ETHUSDT; 1m y 5m)
cd backend && .venv/bin/python -m app.cli download-history --start 2025-10-01 --end 2026-09-30
# 2) Protocolo completo: partición 60/20/20, rejilla de 8, walk-forward, benchmarks, estrés
.venv/bin/python -m app.cli research --start 2025-10-01 --end 2026-09-30 --seeds 20
#    → docs/backtesting-results.md (incluye resultados negativos y motivos de descarte)
```

Después, solo las estrategias que el informe marque como `CANDIDATA A PAPER TRADING` pasan a:

1. **Paper trading continuo ≥ 8 semanas** (`BOTTRADE_TRADING_MODE=PAPER`, estrategia activada desde el panel).
2. **Objetivo de 300 operaciones cerradas.** Si no se alcanzan, se prolonga la observación; **no se fuerzan operaciones**.
3. Comparar paper contra el backtest (expectativa neta, coste real por operación, frecuencia de rechazos, deslizamiento) y registrar cualquier
   cambio de versión (`strategy_versions`); toda versión modificada se revalida desde el paso 1.
4. Informe final con los resultados negativos y los motivos de descarte.

Criterios de la sección 23 que aplica `judge()` (cualquiera que falle ⇒ **DESCARTADA**): muestra suficiente, neto fuera de muestra > 0,
IC95 de la expectativa > 0, profit factor > 1,15, drawdown con límites reales < 5 %, validación y walk-forward positivos, supervivencia a costes
1,5×/2×, superar al 90 % de las entradas aleatorias, no depender de una sola operación. **Cumplirlos no autoriza dinero real:** cualquier fase
posterior exige una decisión separada, revisión de riesgos y cambios explícitos de código y configuración.

## Riesgos de la validación (a tener en cuenta al leerla)

* Con 8 combinaciones por estrategia y 3 estrategias hay riesgo de falso positivo: un solo «candidata» no es evidencia fuerte.
* El modelo de ejecución es conservador pero no exacto (sin libro de órdenes; stop gana en velas ambiguas; liquidez = 10 % del volumen de la vela).
* BTC y ETH están muy correlacionados: dos posiciones simultáneas no son dos apuestas independientes.
* Paper ≠ testnet ≠ real: el testnet tiene liquidez y precios distintos y se resetea; sirve para validar el flujo de órdenes, no la rentabilidad.
