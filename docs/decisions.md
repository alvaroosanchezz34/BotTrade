# Registro de decisiones (ADR)

Formato corto: contexto → decisión → consecuencias. Las decisiones se pueden
revisar, pero cada cambio se anota aquí con fecha.

## ADR-001 · Primer mercado: Binance Spot (BTCUSDT, ETHUSDT) — 2026-10-12

**Contexto.** Hace falta un mercado cuya API permita probar el flujo completo
(datos + órdenes) sin dinero real. Interactive Brokers exige cuenta, TWS/Gateway
local y no tiene datos históricos gratuitos de calidad para cripto/forex intradía.

**Decisión.** Binance Spot. Datos públicos desde `data-api.binance.vision`,
`data-stream.binance.vision` y archivos mensuales de `data.binance.vision`
(con checksum SHA-256). Órdenes de prueba en Binance Spot Testnet.

**Consecuencias.** Comisión de referencia 0,10 % por lado (taker, sin descuento
BNB). Mínimo nocional 5 USDT. El testnet se resetea ~1 vez al mes sin aviso:
la reconciliación debe tolerar órdenes desaparecidas tras un reset y bloquear
entradas hasta revisión manual.

## ADR-002 · Moneda de cuenta USDT; capital de referencia 500 — 2026-10-12

**Contexto.** El capital de referencia es 500 €, pero los pares cotizan en USDT.

**Decisión.** La simulación usa 500 USDT como capital inicial. No se modela el
tipo EUR/USDT ni el coste de conversión.

**Consecuencias.** Las cifras del panel están en USDT y así se etiquetan. Es una
simplificación documentada en `docs/risk-policy.md`.

## ADR-003 · Monolito modular, dos procesos, sin Redis/Celery — 2026-10-12

**Decisión.** Un único paquete Python con dos entrypoints (`api` y `engine`).
Comunicación por PostgreSQL (tabla `bot_commands`, heartbeat, `LISTEN/NOTIFY`).

**Consecuencias.** Menos piezas que operar en un VPS pequeño. Si en el futuro
hay decenas de símbolos o varios engines, se reevalúa.

## ADR-004 · Decimal para contabilidad, float64 para indicadores — 2026-10-12

**Decisión.** Todo importe que afecta a saldo, cantidad de orden, precio de
ejecución, comisión o P&L es `Decimal` y se guarda como `NUMERIC(38,18)`.
Los indicadores trabajan en `float64`; cuando un valor de indicador se convierte
en precio de orden (p. ej. un stop), pasa por `to_price()` que lo cuantiza al
`tickSize` del instrumento.

## ADR-005 · Ejecución conservadora dentro de la vela — 2026-10-12

**Contexto.** Con velas de 1m no se sabe qué ocurrió primero dentro de la vela.

**Decisión.**
* Una señal calculada con la vela cerrada en `t` solo puede ejecutarse a partir
  de la apertura de la vela `t+1` (más la latencia configurada).
* Entradas a mercado: precio = apertura siguiente + medio spread + deslizamiento.
* Si en una misma vela se tocan stop y objetivo, **se asume el stop**.
* Si la vela abre más allá del stop (gap), la salida es al precio de apertura,
  no al stop.

## ADR-006 · Sin LIVE en el enum de modos — 2026-10-12

**Decisión.** `TradingMode` no contiene `LIVE`. El adaptador de testnet tiene una
lista blanca de hosts cerrada en código. Un test (`test_no_real_orders.py`)
falla si el código de `execution/` o `providers/` contiene hosts de producción
de órdenes, o si la configuración por defecto puede construir un adaptador que
no sea simulado.

## ADR-007 · Autenticación por sesión en cookie — 2026-10-12

**Decisión.** Usuario administrador único creado por CLI. Contraseña con Argon2id.
Sesión opaca guardada (hash) en BD, cookie `HttpOnly`, `Secure` en producción,
`SameSite=Strict`. Peticiones mutantes requieren cabecera `X-CSRF-Token` que
coincide con el token de la sesión. Nada de JWT en `localStorage`.

## ADR-008 · Acceso remoto desde el iPhone por Tailscale (recomendado) — 2026-10-12

**Decisión.** La opción recomendada es no exponer el panel a Internet: VPS y
iPhone en la misma tailnet, Caddy con certificado de Tailscale. Alternativa
documentada: Caddy público con HTTPS + autenticación + rate limiting.
PostgreSQL nunca se publica.
