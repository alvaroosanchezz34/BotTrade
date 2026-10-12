# Backtesting y protocolo de validación

## Motor (`app/backtesting/engine.py`)

Por eventos, multi-activo, con el **mismo** `RiskEngine` y `RiskState` que el modo en vivo.

1. La señal se calcula con la vela cerrada `t`; se ejecuta en la apertura de `t+1` (o más con latencia).
2. Compra a mercado: apertura × (1 + medio spread + deslizamiento) + comisión (0,10 % por lado).
3. Stop: si la vela abre bajo el stop (gap), se sale a la **apertura**; si no, al stop con deslizamiento.
4. Si en una vela se tocan stop y objetivo, **se asume el stop** (supuesto conservador).
5. El objetivo solo cuenta si el máximo lo *supera*.
6. Participación máxima del 10 % del volumen de la barra: ejecución parcial o rechazo si no llega al mínimo.
7. Posiciones abiertas al final: se informan aparte; no cuentan como cerradas.
8. Reproducible: configuración, semilla y hash SHA-256 del dataset se guardan con cada ejecución.

Casos límite con tests: datos faltantes, velas duplicadas, gaps, ejecuciones parciales,
stop y objetivo en la misma vela, fin de periodo con posiciones abiertas.

## Datos

`download-history` baja velas de `data.binance.vision` (archivos mensuales, verificados por
**SHA-256**) y completa con REST. El validador detecta duplicados, huecos, desorden, velas
abiertas y datos obsoletos. Sin supervivencia que corregir en BTC/ETH spot (par único y vigente).

## Protocolo (`research`)

1. Partición temporal 60/20/20 (entrenamiento / validación / prueba).
2. Rejilla de **8 combinaciones** por estrategia, solo en entrenamiento (el número se informa).
3. Selección por beneficio neto con mínimo de operaciones; la validación decide si se mira la prueba.
4. Prueba fuera de muestra, una sola vez. Walk-forward con reoptimización móvil.
5. Benchmarks: comprar y mantener y N semillas de entradas aleatorias con mismas salidas.
6. Estrés: costes duplicados.

**Criterios de descarte** (`judge`): muestra insuficiente · neto fuera de muestra ≤ 0 ·
IC95 de la expectativa que incluye 0 · profit factor ≤ 1,15 · drawdown con límites reales ≥ 5 % ·
validación negativa · walk-forward negativo · no sobrevive a costes 1,5×/2× · no supera al 90 %
de las entradas aleatorias · depende de una sola operación.
Superarlos solo da «CANDIDATA A PAPER TRADING (no a dinero real)».

Sharpe: media/desviación de retornos por barra del equity, anualizado con √(barras/año),
sin tasa libre de riesgo; es indicativo con pocas operaciones.

## Comandos

```bash
cd backend
.venv/bin/python -m app.cli download-history --start 2025-01-01 --end 2026-09-30
.venv/bin/python -m app.cli research --start 2025-01-01 --end 2026-09-30 --seeds 10
```
Salida: `docs/backtesting-results.md` (informe) y `reports/research-*.json` (datos).
