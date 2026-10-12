# Operación

## Procesos

| Proceso | Comando | Qué hace |
|---|---|---|
| `api` | `uvicorn app.api.main:build --factory` | lectura de estado, **solicita** comandos; no opera |
| `engine` | `python -m app.cli engine` | datos → señales → riesgo → órdenes; único que habla con el exchange simulado/testnet |
| `migrate` | `alembic upgrade head` | esquema |

El bot sigue funcionando aunque cierres la app del móvil o apagues tu ordenador: todo corre en el servidor.

## Arranque del engine (orden fijo)

1. Crea cuenta (capital inicial, línea base de saldos), estrategias (desactivadas) y versiones.
2. Detecta reinicios no limpios (`engine_unexpected_restart`, alerta).
3. PAPER: reconstruye el libro simulado desde las ejecuciones guardadas. TESTNET: verifica credenciales y permiso de trading.
4. **Reconcilia** (órdenes no terminales contra el proveedor, huérfanas, duplicados, saldos). Sin pasada limpia, `reconciliation_ok=false` y **no hay entradas**.
5. Latido cada 5 s; comandos cada 1 s; reconciliación cada 60 s; instantánea de balance cada 5 min.

## Modo TESTNET (requiere acción tuya)

1. Crea claves en https://testnet.binance.vision (no son las de tu cuenta real; es otro registro).
2. Pon `BOTTRADE_TRADING_MODE=TESTNET`, `BOTTRADE_TESTNET_API_KEY` y `…_SECRET` en `infra/.env.prod`. Reinicia `engine`.
3. Si el entorno contiene `BINANCE_API_KEY`/`…SECRET` el proceso se niega a arrancar.
4. **Verificación pendiente**: el entorno de desarrollo recibe HTTP 451 (geobloqueo) del testnet, así que el adaptador
   solo está probado con un servidor simulado. Desde el VPS ejecuta primero con una cantidad mínima y revisa:
   `GET /api/orders` (estados), `GET /api/risk`, y que la reconciliación no marque discrepancias. Las comisiones de las
   compras llegan en la moneda base; la posición usa la cantidad neta. El testnet se resetea ~mensualmente sin aviso:
   tras un reset aparecerán `order_vanished`/`ghost_position`; es el comportamiento esperado (bloquea y avisa).

## Alertas (`/api/alerts`; críticas en Inicio)

Cada una lleva gravedad, contexto y **acción recomendada**; se deduplican por clave (`occurrences`).

| Código | Causa | Qué hacer |
|---|---|---|
| `DATA_NOT_READY` / `DATA_INVALID` | WS caído, hueco, datos obsoletos o vela inválida | esperar a la recuperación; las entradas de esa serie están bloqueadas |
| `order_unknown` | respuesta ambigua (timeout/5XX) | se consulta sola; si persiste, revisar proveedor |
| `reconciliation_*` | desajuste BD ↔ proveedor | investigar, corregir causa y **Reanudar** |
| `risk_latch` | drawdown / errores de ejecución | revisar causa; Reanudar exige comprobaciones |
| `kill_switch` | activado por un usuario | revisar causa; Reanudar con confirmación |
| `engine_unexpected_restart` | reinicio sin parada limpia | revisar logs |
| `engine_loop_error` | excepción en un bucle del engine | revisar logs |
| `oversold_position`, `order_state_conflict`, `exit_not_possible` | inconsistencias de ejecución | reconciliar manualmente |

## Procedimientos

* **Parar con orden**: Pausar → comprobar que no hay órdenes pendientes → `docker compose stop engine`.
* **Emergencia**: Kill switch (por defecto conserva posiciones con stops; marca «liquidar» solo si quieres vender a mercado).
* **Tras un kill switch/bloqueo**: revisar alertas y Operaciones → Reanudar. Si se rechaza, el motivo aparece en la UI.
* **Estado desconocido de una orden**: no la reenvíes; deja que el engine la consulte o usa Reanudar (hace una pasada nueva).

## Comandos útiles

```bash
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod logs -f engine
docker compose … exec engine python -m app.cli check-engine
docker compose … run --rm api python -m app.cli create-admin otro_usuario
```
