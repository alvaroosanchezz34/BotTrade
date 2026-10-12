# Arquitectura de BotTrade

> Describe la arquitectura implementada (Fases 0–7). Ver `STATUS.md` para el estado exacto
> y las limitaciones. El código del engine está en `app/services/`, la ejecución en `app/execution/`.

## 1. Principios

1. **El dinero real no existe en el código.** El enum `TradingMode` solo tiene
   `BACKTEST`, `PAPER` y `TESTNET`. No hay rama de código, flag ni variable de
   entorno que active producción. Añadirla requerirá un cambio explícito de código
   revisado (ver `docs/risk-policy.md`, sección "Dinero real").
2. **Ninguna estrategia toca el broker.** Las estrategias devuelven `Signal`.
   Solo el `OrderManager` habla con un `ExecutionAdapter`, y solo después de una
   `RiskDecision` aprobada.
3. **Fallar cerrado.** Ante ambigüedad (entorno, datos obsoletos, estado de orden
   desconocido, discrepancia de reconciliación) el sistema bloquea nuevas entradas.
4. **Decimal para dinero.** Saldos, precios de ejecución, comisiones, cantidades y
   P&L usan `decimal.Decimal`. Los indicadores (EMA, z-score, volatilidad) usan
   `float64` vía pandas/NumPy porque son estimadores estadísticos, no contabilidad.
   La frontera está en `app/core/money.py`.
5. **Todo en UTC.** Timestamps `timestamptz` en BD, `datetime` con `tzinfo=UTC`
   en dominio. La hora local solo existe en el frontend.
6. **Reproducible.** Cada backtest guarda configuración, versión de estrategia,
   rango de datos y hash del dataset.

## 2. Vista de procesos

```
                ┌──────────────────────── servidor (VPS) ────────────────────────┐
                │                                                                │
 iPhone/PC ──HTTPS──► Caddy (TLS) ──► api  (FastAPI, uvicorn)                     │
  (PWA)         │                      │  REST + WebSocket                       │
                │                      │  lee estado / escribe "commands"        │
                │                      ▼                                         │
                │                 PostgreSQL ◄── LISTEN/NOTIFY ──┐               │
                │                      ▲                         │               │
                │                      │                         │               │
                │                    engine (proceso Python asyncio)             │
                │                      │  datos → señales → riesgo → órdenes     │
                │                      ▼                                         │
                │          Binance público (data-api / data-stream .binance.vision)
                │          Binance Spot Testnet (solo en modo TESTNET)           │
                └────────────────────────────────────────────────────────────────┘
```

* **api**: autenticación, lectura de estado, recepción de comandos de control
  (pausa, reanudar, kill switch, activar estrategia). **No ejecuta trading.**
  Los comandos se insertan en la tabla `bot_commands` y se notifican con
  `NOTIFY`; el engine los consume, los valida y deja el resultado. La UI muestra
  "solicitado" hasta que el engine marca el comando como `APPLIED` o `REJECTED`.
* **engine**: único proceso con permiso para crear órdenes. Mantiene heartbeat
  en `engine_heartbeat`. Si el engine muere, el `api` lo detecta y el panel lo
  muestra como caído.
* **PostgreSQL**: fuente de verdad. Sin Redis/Celery: el volumen (2 símbolos,
  velas de 1m) no lo justifica y `LISTEN/NOTIFY` cubre el aviso en tiempo real.

## 3. Pipeline del engine

```
MarketDataFeed ─► CandleValidator ─► FeatureBuilder ─► Strategy[*] ─► SignalCoordinator
                        │                                                   │
                  (marca STALE/GAP                                          ▼
                   y bloquea señales)                                  RiskManager ──► RiskDecision (persistida)
                                                                            │ aprobada
                                                                            ▼
                                                                      OrderManager ──► ExecutionAdapter
                                                                            │              (Simulated | BinanceTestnet)
                                                                            ▼
                                                                  Positions / Fills / Ledger ──► Reconciler
```

Cada flecha es un contrato tipado (`app/core/types.py`):

| Contrato | Productor | Consumidor |
|---|---|---|
| `Candle` (con `source`, `is_closed`, `validation`) | proveedor de datos | validador, estrategias |
| `Signal` | estrategia | coordinador, riesgo |
| `RiskDecision` | `RiskManager` | `OrderManager`, auditoría |
| `OrderIntent` / `Order` | `OrderManager` | `ExecutionAdapter` |
| `ExecutionReport` | `ExecutionAdapter` | `OrderManager` |

## 4. Modos de trading

| Modo | Datos | Ejecución | Credenciales |
|---|---|---|---|
| `BACKTEST` | históricos (BD / data.binance.vision) | `SimulatedExecution` con modelo de costes | ninguna |
| `PAPER` | tiempo real públicos (`data-api` / `data-stream.binance.vision`) | `SimulatedExecution` | ninguna |
| `TESTNET` | tiempo real del propio testnet | `BinanceTestnetExecution` | claves de testnet |

* El adaptador de testnet valida que el host pertenezca a la lista blanca
  `{testnet.binance.vision, api1.testnet.binance.vision}` y rechaza cualquier
  otro host en construcción, no en ejecución.
* En `BACKTEST` y `PAPER` el adaptador de testnet **no se instancia** y las
  variables de credenciales se ignoran; si existen variables `BINANCE_*` que no
  sean de testnet, el arranque falla.
* En `TESTNET` los precios del testnet difieren del mercado real, por lo que
  las señales se calculan sobre los datos del propio testnet (si no, las órdenes
  límite no tendrían sentido). Los resultados del testnet **no** son evidencia de
  rentabilidad: sirven para validar el flujo de órdenes.

## 5. Decisiones del entorno verificadas en la Fase 0 (2026-10-12)

* Documentación oficial de Binance (CHANGELOG actualizado el 2026-09-18).
  Datos clave comprobados contra `GET /api/v3/exchangeInfo` en vivo:
  * Límites: `REQUEST_WEIGHT` 6000/min, `ORDERS` 100/10 s y 200 000/día.
  * `GET /api/v3/klines`: peso 2, máximo 1000 velas, identificadas por open time.
  * BTCUSDT: `tickSize` 0.01, `stepSize` 0.00001, `minNotional` 5 USDT.
  * ETHUSDT: `tickSize` 0.01, `stepSize` 0.0001, `minNotional` 5 USDT.
  * Un 5XX o el error `-1007 TIMEOUT` significa **estado de ejecución desconocido**:
    se modela como `UNKNOWN` y se consulta por `origClientOrderId`.
  * WebSocket: conexión de 24 h máximo, ping cada 20 s, 5 mensajes entrantes/s,
    campo `k.x` indica vela cerrada.
* Desde el sandbox de desarrollo (nube en EE. UU.) `api.binance.com` y
  `testnet.binance.vision` devuelven **HTTP 451 (ubicación restringida)**.
  `data-api.binance.vision`, `data-stream.binance.vision` y
  `data.binance.vision` sí responden. Consecuencia: el adaptador de testnet se
  prueba con un servidor simulado en los tests; la verificación contra el testnet
  real queda pendiente de ejecutarse desde el VPS (ver `STATUS.md`).

## 6. Estructura del repositorio

```
backend/
  app/
    api/            FastAPI: app factory, rutas, dependencias
    auth/           hash de contraseñas, sesiones, CSRF
    backtesting/    motor, modelo de costes, métricas, walk-forward
    core/           config, logging, tipos de dominio, dinero, reloj
    execution/      máquina de estados, OrderManager, adaptador simulado, reconciliación
    features/       indicadores
    market_data/    validación de velas, almacén, feed
    monitoring/     alertas, health
    persistence/    SQLAlchemy: motor, modelos, repositorios
    providers/      binance: REST, WebSocket, filtros, adaptador testnet
    risk/           RiskManager, dimensionamiento, estado persistido
    strategies/     interfaz y estrategias
  migrations/       Alembic
  tests/
frontend/           React + TS + Vite (Fase 6)
infra/              docker, caddy, scripts
docs/
```

Los módulos solo se crean cuando tienen código real. No hay ficheros vacíos.
