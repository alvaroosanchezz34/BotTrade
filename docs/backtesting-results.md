# Resultados de backtesting

Generado: 2026-10-13 21:51 UTC · datos 2025-10-01 → 2026-09-29 · símbolos BTCUSDT, ETHUSDT

Costes supuestos: taker_fee=0.001, maker_fee=0.001, slippage=0.0002, assumed_spread=0.0001
Capital de referencia: 500 USDT. Hash de datos por intervalo: 1m=12af14647697…, 5m=eaa8706c78fa…

> Estos resultados son **históricos y simulados**. No predicen resultados futuros
> ni autorizan operar con dinero real. Una estrategia «candidata» solo pasa a
> paper trading prolongado (sección 23).

## Resumen

| Estrategia | Intervalo | Ops prueba | Neto prueba | PF prueba | DD máx. (límites reales) | Walk-forward neto | Supera aleatorio | Veredicto |
|---|---|---|---|---|---|---|---|---|
| momentum | 1m | 265 | -112.16 | 0.30 | 5.01 % | -632.51 (1490 ops) | 0% | **DESCARTADA** |
| trend | 1m | 324 | -150.66 | 0.40 | 5.06 % | -983.54 (1964 ops) | 0% | **DESCARTADA** |
| mean_reversion | 1m | 282 | -150.56 | 0.23 | 5.13 % | -435.55 (988 ops) | 40% | **DESCARTADA** |
| momentum | 5m | 197 | -85.70 | 0.34 | 5.12 % | -268.31 (742 ops) | 0% | **DESCARTADA** |
| trend | 5m | 80 | 30.50 | 1.60 | 4.67 % | -108.22 (382 ops) | 100% | **DESCARTADA** |
| mean_reversion | 5m | 45 | -14.43 | 0.45 | 3.14 % | -73.96 (206 ops) | 100% | **DESCARTADA** |

## momentum · 1m

Combinaciones probadas en entrenamiento: **8**. Elegida: `{"lookback": 20, "min_volume_ratio": 2.0, "stop_atr": 3.0}` (máximo beneficio neto en entrenamiento con ≥ 20 operaciones).

| Tramo | Ops | Neto | Comisiones | Win rate | Expectativa | IC95 expectativa | PF | DD máx. | Sharpe | Comprar y mantener |
|---|---|---|---|---|---|---|---|---|---|---|
| Entrenamiento (mejor) | 329 | -149.70 | 116.48 | 26.1 % | -0.455 | [-0.541, -0.369] | 0.27 | 30.01 % | -6.41 | -36.31 % |
| Validación | 361 | -150.03 | 118.66 | 26.0 % | -0.416 | [-0.495, -0.336] | 0.28 | 30.01 % | -12.34 | -21.46 % |
| Prueba | 265 | -112.16 | 98.12 | 25.7 % | -0.423 | [-0.520, -0.327] | 0.30 | 22.76 % | -14.12 | 37.75 % |
| Prueba, límites reales | 48 | -24.23 | 19.72 | 22.9 % | -0.505 | [-0.719, -0.290] | 0.22 | 5.01 % | -7.88 | 37.75 % |
| Prueba, costes ×1,5/×2 | 260 | -150.12 | 119.92 | 21.2 % | -0.577 | [-0.658, -0.496] | 0.15 | 30.09 % | -15.24 | 37.75 % |

Sensibilidad (entrenamiento, todas las combinaciones):

| Parámetros | Ops | Neto | PF | DD máx. |
|---|---|---|---|---|
| `{"lookback": 20, "min_volume_ratio": 2.0, "stop_atr": 1.5}` | 235 | -150.20 | 0.14 | 30.10 % |
| `{"lookback": 20, "min_volume_ratio": 2.0, "stop_atr": 3.0}` | 329 | -149.70 | 0.27 | 30.01 % |
| `{"lookback": 20, "min_volume_ratio": 3.0, "stop_atr": 1.5}` | 224 | -150.61 | 0.13 | 30.18 % |
| `{"lookback": 20, "min_volume_ratio": 3.0, "stop_atr": 3.0}` | 306 | -150.33 | 0.26 | 30.13 % |
| `{"lookback": 60, "min_volume_ratio": 2.0, "stop_atr": 1.5}` | 224 | -150.35 | 0.13 | 30.07 % |
| `{"lookback": 60, "min_volume_ratio": 2.0, "stop_atr": 3.0}` | 294 | -150.03 | 0.24 | 30.01 % |
| `{"lookback": 60, "min_volume_ratio": 3.0, "stop_atr": 1.5}` | 222 | -150.33 | 0.13 | 30.07 % |
| `{"lookback": 60, "min_volume_ratio": 3.0, "stop_atr": 3.0}` | 292 | -150.03 | 0.24 | 30.01 % |

Entradas aleatorias en prueba (20 semillas, misma frecuencia y salidas): neto mínimo -20.76, mediana -11.96, máximo -5.15.

Walk-forward (90 días de entrenamiento → 30 de prueba):

* 2025-12-30 → 2026-01-29: 152 ops, neto -72.08, PF 0.28
* 2026-01-29 → 2026-02-28: 251 ops, neto -105.62, PF 0.31
* 2026-02-28 → 2026-03-30: 309 ops, neto -126.60, PF 0.30
* 2026-03-30 → 2026-04-29: 181 ops, neto -73.59, PF 0.29
* 2026-04-29 → 2026-05-29: 59 ops, neto -33.84, PF 0.12
* 2026-05-29 → 2026-06-28: 299 ops, neto -122.78, PF 0.29
* 2026-06-28 → 2026-07-28: 99 ops, neto -39.07, PF 0.31
* 2026-07-28 → 2026-08-27: 63 ops, neto -15.31, PF 0.55
* 2026-08-27 → 2026-09-26: 77 ops, neto -43.62, PF 0.23

**Veredicto: DESCARTADA**

* resultado neto negativo fuera de muestra (-112.16)
* el intervalo de confianza de la expectativa incluye el 0
* profit factor en prueba ≤ 1,15 (0.3)
* drawdown con límites reales ≥ 5 % (5.01 %)
* validación negativa: no es estable entre periodos
* walk-forward negativo (-632.51 en 1490 operaciones)
* no sobrevive a costes 1,5×/2×
* no supera al 90 % de las entradas aleatorias (supera al 0%)

Rechazos del motor de riesgo en prueba: operaciones_día: 16

Motivos de salida en prueba: target: 53, stop: 110, time_stop: 102

## trend · 1m

Combinaciones probadas en entrenamiento: **8**. Elegida: `{"fast": 50, "slow": 200, "regime": 800, "stop_atr": 2.0, "max_hold_bars": 1200}` (máximo beneficio neto en entrenamiento con ≥ 20 operaciones).

| Tramo | Ops | Neto | Comisiones | Win rate | Expectativa | IC95 expectativa | PF | DD máx. | Sharpe | Comprar y mantener |
|---|---|---|---|---|---|---|---|---|---|---|
| Entrenamiento (mejor) | 302 | -149.58 | 143.58 | 10.6 % | -0.495 | [-0.670, -0.321] | 0.37 | 30.02 % | -5.04 | -36.31 % |
| Validación | 232 | -150.58 | 112.10 | 6.5 % | -0.649 | [-0.771, -0.527] | 0.16 | 30.12 % | -10.13 | -21.46 % |
| Prueba | 324 | -150.66 | 158.91 | 7.1 % | -0.465 | [-0.756, -0.174] | 0.40 | 30.13 % | -5.08 | 37.75 % |
| Prueba, límites reales | 42 | -25.28 | 20.48 | 7.1 % | -0.602 | [-0.841, -0.362] | 0.18 | 5.06 % | -6.36 | 37.75 % |
| Prueba, costes ×1,5/×2 | 167 | -150.35 | 107.70 | 4.2 % | -0.900 | [-1.041, -0.760] | 0.09 | 30.07 % | -8.03 | 37.75 % |

Sensibilidad (entrenamiento, todas las combinaciones):

| Parámetros | Ops | Neto | PF | DD máx. |
|---|---|---|---|---|
| `{"fast": 20, "slow": 100, "regime": 400, "stop_atr": 2.0, "max_hold_bars": 300}` | 311 | -150.72 | 0.33 | 30.15 % |
| `{"fast": 20, "slow": 100, "regime": 400, "stop_atr": 2.0, "max_hold_bars": 1200}` | 316 | -150.21 | 0.33 | 30.05 % |
| `{"fast": 20, "slow": 100, "regime": 400, "stop_atr": 4.0, "max_hold_bars": 300}` | 395 | -151.13 | 0.36 | 30.24 % |
| `{"fast": 20, "slow": 100, "regime": 400, "stop_atr": 4.0, "max_hold_bars": 1200}` | 401 | -151.05 | 0.37 | 30.22 % |
| `{"fast": 50, "slow": 200, "regime": 800, "stop_atr": 2.0, "max_hold_bars": 300}` | 294 | -150.08 | 0.36 | 30.12 % |
| `{"fast": 50, "slow": 200, "regime": 800, "stop_atr": 2.0, "max_hold_bars": 1200}` | 302 | -149.58 | 0.37 | 30.02 % |
| `{"fast": 50, "slow": 200, "regime": 800, "stop_atr": 4.0, "max_hold_bars": 300}` | 349 | -150.19 | 0.39 | 30.04 % |
| `{"fast": 50, "slow": 200, "regime": 800, "stop_atr": 4.0, "max_hold_bars": 1200}` | 370 | -150.99 | 0.41 | 30.20 % |

Entradas aleatorias en prueba (20 semillas, misma frecuencia y salidas): neto mínimo -150.55, mediana -150.25, máximo -148.45.

Walk-forward (90 días de entrenamiento → 30 de prueba):

* 2025-12-30 → 2026-01-29: 201 ops, neto -126.85, PF 0.17
* 2026-01-29 → 2026-02-28: 293 ops, neto -136.31, PF 0.26
* 2026-02-28 → 2026-03-30: 251 ops, neto -84.80, PF 0.46
* 2026-03-30 → 2026-04-29: 169 ops, neto -58.89, PF 0.51
* 2026-04-29 → 2026-05-29: 136 ops, neto -83.15, PF 0.15
* 2026-05-29 → 2026-06-28: 195 ops, neto -129.06, PF 0.19
* 2026-06-28 → 2026-07-28: 252 ops, neto -150.84, PF 0.19
* 2026-07-28 → 2026-08-27: 219 ops, neto -79.82, PF 0.46
* 2026-08-27 → 2026-09-26: 248 ops, neto -133.83, PF 0.21

**Veredicto: DESCARTADA**

* resultado neto negativo fuera de muestra (-150.66)
* el intervalo de confianza de la expectativa incluye el 0
* profit factor en prueba ≤ 1,15 (0.4)
* drawdown con límites reales ≥ 5 % (5.06 %)
* validación negativa: no es estable entre periodos
* walk-forward negativo (-983.54 en 1964 operaciones)
* no sobrevive a costes 1,5×/2×
* no supera al 90 % de las entradas aleatorias (supera al 0%)

Rechazos del motor de riesgo en prueba: bloqueos: 1014, drawdown: 1014, tamaño: 614, operaciones_día: 12

Motivos de salida en prueba: stop: 285, signal_exit: 39

## mean_reversion · 1m

Combinaciones probadas en entrenamiento: **8**. Elegida: `{"window": 240, "z_entry": 2.0, "stop_atr": 2.0}` (máximo beneficio neto en entrenamiento con ≥ 20 operaciones).

| Tramo | Ops | Neto | Comisiones | Win rate | Expectativa | IC95 expectativa | PF | DD máx. | Sharpe | Comprar y mantener |
|---|---|---|---|---|---|---|---|---|---|---|
| Entrenamiento (mejor) | 282 | -149.27 | 120.71 | 25.9 % | -0.529 | [-0.628, -0.431] | 0.26 | 29.97 % | -4.81 | -36.31 % |
| Validación | 237 | -149.42 | 115.34 | 21.1 % | -0.630 | [-0.712, -0.549] | 0.15 | 30.07 % | -9.91 | -21.46 % |
| Prueba | 282 | -150.56 | 131.71 | 22.3 % | -0.534 | [-0.624, -0.444] | 0.23 | 30.12 % | -10.89 | 37.75 % |
| Prueba, límites reales | 40 | -25.58 | 18.72 | 20.0 % | -0.640 | [-0.797, -0.483] | 0.07 | 5.13 % | -5.82 | 37.75 % |
| Prueba, costes ×1,5/×2 | 204 | -151.19 | 117.41 | 15.2 % | -0.741 | [-0.833, -0.649] | 0.08 | 30.24 % | -9.18 | 37.75 % |

Sensibilidad (entrenamiento, todas las combinaciones):

| Parámetros | Ops | Neto | PF | DD máx. |
|---|---|---|---|---|
| `{"window": 60, "z_entry": 2.0, "stop_atr": 2.0}` | 239 | -150.09 | 0.09 | 30.06 % |
| `{"window": 60, "z_entry": 2.0, "stop_atr": 4.0}` | 322 | -150.34 | 0.10 | 30.10 % |
| `{"window": 60, "z_entry": 3.0, "stop_atr": 2.0}` | 301 | -149.91 | 0.23 | 30.08 % |
| `{"window": 60, "z_entry": 3.0, "stop_atr": 4.0}` | 367 | -151.08 | 0.21 | 30.25 % |
| `{"window": 240, "z_entry": 2.0, "stop_atr": 2.0}` | 282 | -149.27 | 0.26 | 29.97 % |
| `{"window": 240, "z_entry": 2.0, "stop_atr": 4.0}` | 414 | -150.14 | 0.34 | 30.20 % |
| `{"window": 240, "z_entry": 3.0, "stop_atr": 2.0}` | 322 | -149.93 | 0.35 | 30.02 % |
| `{"window": 240, "z_entry": 3.0, "stop_atr": 4.0}` | 358 | -150.72 | 0.33 | 30.22 % |

Entradas aleatorias en prueba (20 semillas, misma frecuencia y salidas): neto mínimo -153.27, mediana -150.42, máximo -149.98.

Walk-forward (90 días de entrenamiento → 30 de prueba):

* 2025-12-30 → 2026-01-29: 93 ops, neto -42.21, PF 0.28
* 2026-01-29 → 2026-02-28: 134 ops, neto -72.31, PF 0.27
* 2026-02-28 → 2026-03-30: 274 ops, neto -112.99, PF 0.16
* 2026-03-30 → 2026-04-29: 77 ops, neto -35.13, PF 0.26
* 2026-04-29 → 2026-05-29: 91 ops, neto -48.63, PF 0.20
* 2026-05-29 → 2026-06-28: 110 ops, neto -35.37, PF 0.46
* 2026-06-28 → 2026-07-28: 77 ops, neto -38.23, PF 0.24
* 2026-07-28 → 2026-08-27: 57 ops, neto -27.96, PF 0.23
* 2026-08-27 → 2026-09-26: 75 ops, neto -22.73, PF 0.41

**Veredicto: DESCARTADA**

* resultado neto negativo fuera de muestra (-150.56)
* el intervalo de confianza de la expectativa incluye el 0
* profit factor en prueba ≤ 1,15 (0.23)
* drawdown con límites reales ≥ 5 % (5.13 %)
* validación negativa: no es estable entre periodos
* walk-forward negativo (-435.55 en 988 operaciones)
* no sobrevive a costes 1,5×/2×
* no supera al 90 % de las entradas aleatorias (supera al 40%)

Rechazos del motor de riesgo en prueba: bloqueos: 2761, drawdown: 2761, tamaño: 390

Motivos de salida en prueba: target: 22, stop: 204, signal_exit: 40, time_stop: 16

## momentum · 5m

Combinaciones probadas en entrenamiento: **8**. Elegida: `{"lookback": 60, "min_volume_ratio": 3.0, "stop_atr": 3.0}` (máximo beneficio neto en entrenamiento con ≥ 20 operaciones).

| Tramo | Ops | Neto | Comisiones | Win rate | Expectativa | IC95 expectativa | PF | DD máx. | Sharpe | Comprar y mantener |
|---|---|---|---|---|---|---|---|---|---|---|
| Entrenamiento (mejor) | 456 | -148.91 | 108.73 | 30.9 % | -0.327 | [-0.409, -0.244] | 0.43 | 30.00 % | -7.78 | -36.34 % |
| Validación | 163 | -46.32 | 43.22 | 32.5 % | -0.284 | [-0.418, -0.150] | 0.47 | 9.66 % | -8.44 | -21.53 % |
| Prueba | 197 | -85.70 | 56.56 | 26.4 % | -0.435 | [-0.560, -0.310] | 0.34 | 17.29 % | -12.08 | 37.87 % |
| Prueba, límites reales | 66 | -24.73 | 20.60 | 24.2 % | -0.375 | [-0.579, -0.171] | 0.36 | 5.12 % | -8.41 | 37.87 % |
| Prueba, costes ×1,5/×2 | 197 | -107.54 | 72.57 | 22.8 % | -0.546 | [-0.654, -0.438] | 0.22 | 21.59 % | -16.22 | 37.87 % |

Sensibilidad (entrenamiento, todas las combinaciones):

| Parámetros | Ops | Neto | PF | DD máx. |
|---|---|---|---|---|
| `{"lookback": 20, "min_volume_ratio": 2.0, "stop_atr": 1.5}` | 308 | -150.02 | 0.29 | 30.00 % |
| `{"lookback": 20, "min_volume_ratio": 2.0, "stop_atr": 3.0}` | 459 | -149.96 | 0.39 | 30.03 % |
| `{"lookback": 20, "min_volume_ratio": 3.0, "stop_atr": 1.5}` | 293 | -150.97 | 0.28 | 30.19 % |
| `{"lookback": 20, "min_volume_ratio": 3.0, "stop_atr": 3.0}` | 436 | -150.45 | 0.39 | 30.18 % |
| `{"lookback": 60, "min_volume_ratio": 2.0, "stop_atr": 1.5}` | 295 | -149.80 | 0.29 | 30.00 % |
| `{"lookback": 60, "min_volume_ratio": 2.0, "stop_atr": 3.0}` | 450 | -150.46 | 0.40 | 30.16 % |
| `{"lookback": 60, "min_volume_ratio": 3.0, "stop_atr": 1.5}` | 301 | -150.81 | 0.29 | 30.16 % |
| `{"lookback": 60, "min_volume_ratio": 3.0, "stop_atr": 3.0}` | 456 | -148.91 | 0.43 | 30.00 % |

Entradas aleatorias en prueba (20 semillas, misma frecuencia y salidas): neto mínimo -54.25, mediana -39.33, máximo -31.48.

Walk-forward (90 días de entrenamiento → 30 de prueba):

* 2025-12-30 → 2026-01-29: 80 ops, neto -32.88, PF 0.31
* 2026-01-29 → 2026-02-28: 77 ops, neto -29.22, PF 0.39
* 2026-02-28 → 2026-03-30: 97 ops, neto -34.41, PF 0.40
* 2026-03-30 → 2026-04-29: 100 ops, neto -26.39, PF 0.49
* 2026-04-29 → 2026-05-29: 68 ops, neto -28.04, PF 0.30
* 2026-05-29 → 2026-06-28: 70 ops, neto -25.50, PF 0.40
* 2026-06-28 → 2026-07-28: 83 ops, neto -15.61, PF 0.60
* 2026-07-28 → 2026-08-27: 73 ops, neto -23.68, PF 0.43
* 2026-08-27 → 2026-09-26: 94 ops, neto -52.58, PF 0.24

**Veredicto: DESCARTADA**

* resultado neto negativo fuera de muestra (-85.70)
* el intervalo de confianza de la expectativa incluye el 0
* profit factor en prueba ≤ 1,15 (0.34)
* drawdown con límites reales ≥ 5 % (5.12 %)
* validación negativa: no es estable entre periodos
* walk-forward negativo (-268.31 en 742 operaciones)
* no sobrevive a costes 1,5×/2×
* no supera al 90 % de las entradas aleatorias (supera al 0%)

Motivos de salida en prueba: target: 35, time_stop: 55, stop: 107

## trend · 5m

Combinaciones probadas en entrenamiento: **8**. Elegida: `{"fast": 50, "slow": 200, "regime": 800, "stop_atr": 4.0, "max_hold_bars": 1200}` (máximo beneficio neto en entrenamiento con ≥ 20 operaciones).

| Tramo | Ops | Neto | Comisiones | Win rate | Expectativa | IC95 expectativa | PF | DD máx. | Sharpe | Comprar y mantener |
|---|---|---|---|---|---|---|---|---|---|---|
| Entrenamiento (mejor) | 233 | -83.43 | 49.26 | 19.7 % | -0.358 | [-0.546, -0.170] | 0.48 | 17.82 % | -3.37 | -36.34 % |
| Validación | 70 | -34.98 | 16.97 | 12.9 % | -0.500 | [-0.768, -0.232] | 0.30 | 7.19 % | -5.50 | -21.53 % |
| Prueba | 80 | 30.50 | 19.25 | 20.0 % | 0.381 | [-0.816, 1.578] | 1.60 | 5.81 % | 1.67 | 37.87 % |
| Prueba, límites reales | 62 | 27.03 | 14.50 | 19.4 % | 0.436 | [-1.089, 1.961] | 1.68 | 4.67 % | 1.58 | 37.87 % |
| Prueba, costes ×1,5/×2 | 84 | -6.53 | 25.08 | 17.9 % | -0.078 | [-0.567, 0.411] | 0.88 | 5.59 % | -0.52 | 37.87 % |

Sensibilidad (entrenamiento, todas las combinaciones):

| Parámetros | Ops | Neto | PF | DD máx. |
|---|---|---|---|---|
| `{"fast": 20, "slow": 100, "regime": 400, "stop_atr": 2.0, "max_hold_bars": 300}` | 258 | -148.95 | 0.28 | 30.02 % |
| `{"fast": 20, "slow": 100, "regime": 400, "stop_atr": 2.0, "max_hold_bars": 1200}` | 254 | -149.04 | 0.28 | 30.04 % |
| `{"fast": 20, "slow": 100, "regime": 400, "stop_atr": 4.0, "max_hold_bars": 300}` | 438 | -131.83 | 0.51 | 27.11 % |
| `{"fast": 20, "slow": 100, "regime": 400, "stop_atr": 4.0, "max_hold_bars": 1200}` | 433 | -132.87 | 0.50 | 27.31 % |
| `{"fast": 50, "slow": 200, "regime": 800, "stop_atr": 2.0, "max_hold_bars": 300}` | 261 | -145.29 | 0.35 | 29.82 % |
| `{"fast": 50, "slow": 200, "regime": 800, "stop_atr": 2.0, "max_hold_bars": 1200}` | 262 | -145.23 | 0.33 | 29.81 % |
| `{"fast": 50, "slow": 200, "regime": 800, "stop_atr": 4.0, "max_hold_bars": 300}` | 268 | -97.05 | 0.49 | 20.35 % |
| `{"fast": 50, "slow": 200, "regime": 800, "stop_atr": 4.0, "max_hold_bars": 1200}` | 233 | -83.43 | 0.48 | 17.82 % |

Entradas aleatorias en prueba (20 semillas, misma frecuencia y salidas): neto mínimo -51.99, mediana -11.08, máximo 22.50.

Walk-forward (90 días de entrenamiento → 30 de prueba):

* 2025-12-30 → 2026-01-29: 32 ops, neto 5.06, PF 1.21
* 2026-01-29 → 2026-02-28: 19 ops, neto -12.33, PF 0.09
* 2026-02-28 → 2026-03-30: 39 ops, neto -7.08, PF 0.72
* 2026-03-30 → 2026-04-29: 45 ops, neto -26.30, PF 0.22
* 2026-04-29 → 2026-05-29: 61 ops, neto -35.15, PF 0.10
* 2026-05-29 → 2026-06-28: 53 ops, neto -16.90, PF 0.32
* 2026-06-28 → 2026-07-28: 45 ops, neto -3.46, PF 0.87
* 2026-07-28 → 2026-08-27: 37 ops, neto -0.47, PF 0.98
* 2026-08-27 → 2026-09-26: 51 ops, neto -11.59, PF 0.65

**Veredicto: DESCARTADA**

* el intervalo de confianza de la expectativa incluye el 0
* validación negativa: no es estable entre periodos
* walk-forward negativo (-108.22 en 382 operaciones)
* no sobrevive a costes 1,5×/2×
* depende de una sola operación (58% del beneficio bruto)

Rechazos del motor de riesgo en prueba: tamaño: 32

Motivos de salida en prueba: signal_exit: 37, stop: 43

## mean_reversion · 5m

Combinaciones probadas en entrenamiento: **8**. Elegida: `{"window": 240, "z_entry": 3.0, "stop_atr": 4.0}` (máximo beneficio neto en entrenamiento con ≥ 20 operaciones).

| Tramo | Ops | Neto | Comisiones | Win rate | Expectativa | IC95 expectativa | PF | DD máx. | Sharpe | Comprar y mantener |
|---|---|---|---|---|---|---|---|---|---|---|
| Entrenamiento (mejor) | 172 | -59.43 | 30.80 | 37.8 % | -0.346 | [-0.484, -0.207] | 0.44 | 12.80 % | -4.30 | -36.34 % |
| Validación | 57 | -22.29 | 11.89 | 33.3 % | -0.391 | [-0.627, -0.155] | 0.39 | 4.54 % | -5.60 | -21.53 % |
| Prueba | 45 | -14.43 | 10.95 | 44.4 % | -0.321 | [-0.582, -0.059] | 0.45 | 3.14 % | -4.67 | 37.87 % |
| Prueba, límites reales | 45 | -14.43 | 10.95 | 44.4 % | -0.321 | [-0.582, -0.059] | 0.45 | 3.14 % | -4.67 | 37.87 % |
| Prueba, costes ×1,5/×2 | 45 | -18.94 | 14.01 | 37.8 % | -0.421 | [-0.653, -0.189] | 0.29 | 3.98 % | -6.06 | 37.87 % |

Sensibilidad (entrenamiento, todas las combinaciones):

| Parámetros | Ops | Neto | PF | DD máx. |
|---|---|---|---|---|
| `{"window": 60, "z_entry": 2.0, "stop_atr": 2.0}` | 407 | -150.03 | 0.38 | 30.18 % |
| `{"window": 60, "z_entry": 2.0, "stop_atr": 4.0}` | 611 | -149.79 | 0.40 | 30.08 % |
| `{"window": 60, "z_entry": 3.0, "stop_atr": 2.0}` | 437 | -151.64 | 0.46 | 30.33 % |
| `{"window": 60, "z_entry": 3.0, "stop_atr": 4.0}` | 523 | -130.83 | 0.45 | 26.58 % |
| `{"window": 240, "z_entry": 2.0, "stop_atr": 2.0}` | 445 | -151.28 | 0.51 | 30.26 % |
| `{"window": 240, "z_entry": 2.0, "stop_atr": 4.0}` | 533 | -124.89 | 0.56 | 25.52 % |
| `{"window": 240, "z_entry": 3.0, "stop_atr": 2.0}` | 224 | -99.03 | 0.44 | 20.79 % |
| `{"window": 240, "z_entry": 3.0, "stop_atr": 4.0}` | 172 | -59.43 | 0.44 | 12.80 % |

Entradas aleatorias en prueba (20 semillas, misma frecuencia y salidas): neto mínimo -58.81, mediana -35.56, máximo -22.61.

Walk-forward (90 días de entrenamiento → 30 de prueba):

* 2025-12-30 → 2026-01-29: 29 ops, neto -7.08, PF 0.53
* 2026-01-29 → 2026-02-28: 39 ops, neto -17.20, PF 0.33
* 2026-02-28 → 2026-03-30: 25 ops, neto -15.62, PF 0.25
* 2026-03-30 → 2026-04-29: 15 ops, neto 0.71, PF 1.11
* 2026-04-29 → 2026-05-29: 23 ops, neto -12.41, PF 0.27
* 2026-05-29 → 2026-06-28: 23 ops, neto -8.19, PF 0.42
* 2026-06-28 → 2026-07-28: 18 ops, neto -6.19, PF 0.40
* 2026-07-28 → 2026-08-27: 15 ops, neto -2.37, PF 0.71
* 2026-08-27 → 2026-09-26: 19 ops, neto -5.60, PF 0.48

**Veredicto: DESCARTADA**

* resultado neto negativo fuera de muestra (-14.43)
* el intervalo de confianza de la expectativa incluye el 0
* profit factor en prueba ≤ 1,15 (0.45)
* validación negativa: no es estable entre periodos
* walk-forward negativo (-73.96 en 206 operaciones)
* no sobrevive a costes 1,5×/2×

Motivos de salida en prueba: stop: 23, target: 11, time_stop: 6, signal_exit: 5
