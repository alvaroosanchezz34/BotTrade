# Plan de implementación

Cada fase termina con: tests ejecutados, resumen de archivos, qué funciona,
limitaciones y comandos de verificación (en `STATUS.md`).

| Fase | Entregable | Criterio de aceptación | Depende de |
|---|---|---|---|
| 0 | Arquitectura, ADR, plan | Docs revisables; entorno y APIs verificados en vivo | — |
| 1 | Config, BD, migraciones, logging, health, API mínima, auth | `alembic upgrade head` limpio; `pytest` verde; test de no-órdenes-reales verde; `/health` y `/health/ready` responden | 0 |
| 2 | Datos de mercado | Descarga histórica idempotente con checksum; WS con reconexión; validador detecta duplicados, huecos, velas abiertas, desorden y datos obsoletos; recuperación de huecos tras desconexión | 1 |
| 3 | Backtesting + 4 estrategias | Motor sin look-ahead (test que lo demuestra); costes (comisión, spread, deslizamiento, latencia, mínimos); métricas; walk-forward; informe reproducible con datos reales descargados | 2 |
| 4 | Riesgo + órdenes | RiskManager rechaza con motivo; sizing con step/minNotional; máquina de estados con `UNKNOWN`; idempotencia por `clientOrderId`; reconciliación; kill switch; estado de riesgo persistente tras reinicio | 3 |
| 5 | PAPER y TESTNET continuos | Engine en bucle con heartbeat; comandos pause/resume/kill desde BD; alertas; e2e datos→señal→riesgo→orden→fill→BD | 4 |
| 6 | PWA móvil | 5 secciones; estados carga/error/vacío/desconectado; acciones "solicitada" vs "confirmada"; instalable en iOS | 5 |
| 7 | Seguridad y despliegue | Tests de seguridad; Docker Compose prod con Caddy; backups y restauración probados | 6 |
| 8 | Validación prolongada | ≥ 8 semanas de paper; informe honesto por estrategia | 7 |

## Riesgos técnicos identificados

1. **Geobloqueo de Binance desde el sandbox de desarrollo** (HTTP 451 en
   `api.binance.com` y en el testnet). Mitigación: endpoints públicos
   `*.binance.vision` para datos; servidor simulado para tests del adaptador de
   testnet; verificación real desde el VPS.
2. **Costes vs. capital.** Con 0,10 % por lado, una operación ida/vuelta cuesta
   0,20 % del nocional más spread y deslizamiento (~0,03 %). Con riesgo de
   1,25 USDT y stop al 0,5 %, el sizing que incluye costes da un nocional de
   ~171 USDT y comisiones de ~0,34 USDT: **~28 % del riesgo presupuestado se va
   en comisiones** (corregido el 2026-10-12: la estimación inicial del 40 %
   ignoraba que el sizing ya descuenta los costes). Cuanto más corto el stop,
   peor la proporción. Se cuantifica en `docs/backtesting.md`.
3. **Testnet poco representativo.** Precios y liquidez distintos de producción;
   resets mensuales.
4. **Ambigüedad intra-vela** con datos de 1m (ADR-005).
5. **Sobreajuste** al probar muchas variantes: se limita el número de
   configuraciones por estrategia y se reporta cuántas se probaron.
