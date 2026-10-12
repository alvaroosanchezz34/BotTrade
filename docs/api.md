# API

OpenAPI interactiva en `/docs` (desactivada en producción). Todas las rutas `/api/**` exigen
sesión (cookie `bt_session` HttpOnly, `SameSite=Strict`). Las mutaciones exigen además
`X-CSRF-Token` (= cookie `bt_csrf`) y rol `admin`. Los importes viajan como cadenas Decimal.
Las listas se paginan: `?limit=1..200&offset=`. Parámetros inválidos → 422.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health`, `/health/ready` | liveness / readiness por componente (público) |
| POST | `/api/auth/login` · `/logout` · `/change-password` | sesión (login limitado a 10/min por IP) |
| GET | `/api/auth/me` · `/sessions` | usuario y sesiones activas |
| GET | `/api/dashboard/overview` | estado del bot, cuenta, drawdown, conexiones, alertas, riesgo |
| GET | `/api/markets`, `/api/markets/{symbol}/candles` | precios, variación, volatilidad, velas |
| GET | `/api/strategies`, `/api/strategies/{id}` | catálogo, parámetros (solo lectura), resultados |
| GET | `/api/signals` · `/orders` · `/orders/{id}` · `/positions` · `/trades` | filtros por activo, estrategia, estado, fechas |
| GET | `/api/performance` | rendimiento neto de operaciones **cerradas** |
| GET | `/api/backtests`, `/api/backtests/{id}` | resultados reproducibles |
| GET | `/api/risk` | límites, estado, uso, decisiones recientes |
| GET | `/api/alerts` · POST `/api/alerts/{id}/ack` | alertas (reconocer ≠ resolver) |
| GET | `/api/audit`, `/api/events` | auditoría y eventos (solo admin) |
| GET | `/api/system/status` | salud por componente |
| POST | `/api/bot/pause` · `/resume` · `/kill-switch` | **solicitan** (202); ver abajo |
| POST | `/api/strategies/{id}/enable` · `/disable` | idem |
| GET | `/api/commands`, `/api/commands/{id}` | estado de las solicitudes |
| WS | `/ws` | pulsos de estado (cookie + comprobación de `Origin`) |

## Solicitado ≠ confirmado

Los POST de control **no ejecutan**: encolan un comando (`PENDING`) y devuelven
`202 {command_id, engine_alive, ...}`. El engine lo valida y lo marca `APPLIED` o `REJECTED`
(con motivo). Un doble toque devuelve el comando pendiente idéntico (`duplicate_of_pending`).
`kill-switch` exige `{"confirm": "KILL"}`; `liquidate_positions` por defecto es `false`.
Límite de 30 acciones/min. No existe ningún endpoint para cambiar de modo ni activar dinero real
(un test lo comprueba sobre el esquema OpenAPI).

## Sin datos, sin cifras

Si un dato no existe (engine nunca arrancado, sin velas, sin cuenta) el campo es `null`
y la UI lo muestra como «no disponible». Las valoraciones llevan `marks_stale`.

## WebSocket

Mensajes `{"type":"pulse","seq":n,"data":{engine,paused,kill_switch,entries_allowed,equity,
open_positions,critical_alerts,last_order_at,last_signal_at,commands[]}}`. Solo se envía si
cambia algo (o cada 15 s). El cliente refresca por REST; al reconectar recibe el pulso
completo. `{"type":"auth_expired"}` + cierre 1008 si la sesión se revoca o caduca.
