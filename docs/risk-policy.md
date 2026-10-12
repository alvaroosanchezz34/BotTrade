# Política de riesgo

Los límites son **de diseño**: no garantizan que una pérdida real no los supere (huecos de
precio, deslizamiento, fallos de ejecución). El motor de riesgo (`app/risk/engine.py`) es
determinista, independiente de las estrategias, y **ningún modelo de IA puede saltárselo**:
el `OrderManager` solo acepta una `RiskDecision` aprobada y asociada a la misma señal.

## Valores por defecto (configurables en `RiskLimits`, validados por Pydantic)

| Límite | Valor | Nota |
|---|---|---|
| Capital de referencia | 500 USDT | ADR-002: no se modela EUR/USDT |
| Riesgo por operación | 0,25 % = 1,25 USDT | incluye comisiones y deslizamiento del stop |
| Pérdida diaria máxima | 1 % = 5 USDT | P&L realizado + no realizado − comisiones, día UTC |
| Drawdown máximo | 5 % = 25 USDT | pico de equity → equity actual; bloqueo persistente |
| Posiciones simultáneas | 2 | una por activo |
| Exposición agregada | 50 % = 250 USDT | |
| Operaciones por día | 20 | |
| Pérdidas consecutivas | 5 | enfriamiento hasta fin de día UTC |
| Spread máximo | 10 pb | si hay cotización |
| Errores de ejecución | 3 | bloqueo persistente |
| Correlación | ≥ 0,7 | las posiciones correlacionadas comparten presupuesto (1,5×) |

Siempre: solo spot, sin apalancamiento, sin cortos, sin martingala, sin promediar pérdidas.

## Tamaño de posición

`cantidad = min(riesgo / pérdida_por_unidad, exposición libre / precio, caja / coste, max_qty)`,
redondeada hacia abajo al `stepSize`. La pérdida por unidad incluye distancia al stop,
comisión de entrada y salida y deslizamiento. **Si la orden mínima del exchange (`minQty`,
`minNotional`) implica arriesgar más de lo autorizado, la operación se rechaza.**

## Comprobaciones (cada una se registra con su detalle en `risk_decisions.checks`)

kill switch · pausa · bloqueos persistentes · datos aptos · conexiones · reconciliación ·
solo largos · vigencia de la señal · instrumento operable · pérdida diaria · drawdown ·
pérdidas consecutivas · operaciones/día · posiciones · duplicada · spread · riesgo correlacionado · tamaño.

## Estado persistente

`risk_state` (una fila por modo, con control de versión optimista) sobrevive a reinicios:
contadores del día, pico de equity, pausa, kill switch, errores de ejecución, enfriamiento y
**bloqueos latched**. Los contadores diarios se reinician al cambiar de día UTC; **los
bloqueos no**: solo se levantan con `RESUME` manual y tras comprobaciones.

## Valoración

Caja y equity se **derivan** de las ejecuciones guardadas (no son un contador aparte), así un
reinicio nunca los desincroniza. Las posiciones abiertas se valoran al último cierre de 1 min
sin descontar la comisión de salida futura. Las comisiones en moneda base (Binance cobra las
compras en BTC/ETH) se convierten a USDT con el precio de cada fill.

## Pausa, reanudación y kill switch

| Control | Entradas | Órdenes pendientes | Posiciones abiertas |
|---|---|---|---|
| **Pausa** | bloqueadas | sin cambios | siguen vigiladas (stop/objetivo/tiempo) |
| **Kill switch** (por defecto) | bloqueadas al instante (primero y de forma durable) | se cancelan las de **entrada**; las de salida se conservan | **se conservan** con sus stops |
| Kill switch + `liquidate_positions` | igual | igual | se venden a mercado (`EXIT_EMERGENCY`); opción explícita, desmarcada por defecto |
| **Reanudar** | solo si pasan todas las comprobaciones | — | — |

Reanudar rechaza (y explica) si: conexiones caídas, datos no aptos, pérdida diaria o drawdown
en el límite, enfriamiento activo, discrepancia de reconciliación (se ejecuta una pasada
nueva), órdenes en estado desconocido, o kill switch sin `clear_kill_switch`. Un resultado
limpio de reconciliación **no levanta por sí solo** un bloqueo: hace falta reanudar.
Las solicitudes `RESUME` / `ENABLE_STRATEGY` caducan a los 10 min; pausa y kill switch nunca caducan.

## Limitaciones conocidas

* Los stops son **sintéticos**: los vigila el engine y vende a mercado al detectarlos. Con
  el engine caído no actúan. Hay latencia + deslizamiento entre el cruce y la venta.
* El drawdown tras un bloqueo por drawdown no tiene aún un procedimiento de «reiniciar el pico»
  (hoy: intervención manual en BD, auditada). Es deliberado: obliga a una revisión humana.
* Los límites no son editables desde el panel (solo lectura).
