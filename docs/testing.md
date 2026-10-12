# Pruebas

## Qué se ejecuta en el entorno de desarrollo

```bash
make check                         # ruff + mypy estricto + pytest (BD PostgreSQL real)
cd frontend && npm test            # vitest (unidad/componentes)
cd frontend && npm run test:e2e    # Playwright: API + engine reales, BD de fixtures, Chromium emulando iPhone 13 y escritorio
```

Resultado de la última ejecución completa: ver `STATUS.md`.

| Capa | Cobertura (resumen) |
|---|---|
| Datos | duplicadas, incompletas, desorden, huecos, velas abiertas, obsolescencia, reconexión y recuperación (WS simulado) |
| Estrategias | señales esperadas, **ausencia de look-ahead**, invalidación, datos insuficientes, desactivadas |
| Riesgo | tamaño, stop, comisiones, exposición, pérdida diaria, drawdown, correlación, capital insuficiente, mínimo incompatible, datos obsoletos |
| Ejecución | rechazos, parciales, respuestas ambiguas, timeouts, **reintentos idempotentes (nunca se reenvía)**, duplicados, cancelaciones, reinicios, reconciliación, posiciones fantasma |
| Engine e2e | datos → señal → riesgo → orden → fill → BD; stops/objetivo/tiempo; coordinación; comandos; reinicio; bloqueos |
| API / seguridad | 401/403/CSRF, sesión caducada, rate limit, CORS, validación, sin fuga de errores, WebSocket (origen/sesión), no-LIVE |
| Frontend | formato, cliente HTTP, WebSocket con reconexión, flujo solicitado→confirmado, estados vacíos/error, sesión |
| E2E navegador | login, 5 secciones, móvil/escritorio sin desborde, gráfico, kill switch con confirmación, tiempo real, offline, PWA, axe en claro/oscuro |

Los tests de red real (`pytest -m network`) están desactivados por defecto; en este entorno la política de salida bloqueó
`data-api.binance.vision`, así que **no se han ejecutado en esta sesión**.

## Lo que NO se ha podido probar aquí (requiere dispositivos o servicios reales)

* Safari en un iPhone real (instalación en pantalla de inicio, comportamiento de la barra inferior con el teclado, caché de iOS).
  Se probó Chromium emulando iPhone 13 (tamaño, táctil, UA), que **no es WebKit**.
* Binance Testnet real y datos de mercado en vivo (geobloqueo/política de red).
* Imágenes Docker y Caddy con HTTPS real.
* Notificaciones push (no implementadas).

## Prueba manual en el iPhone (Safari)

1. Despliega según `docs/deployment.md` y abre la URL en Safari (con Tailscale activo si usas esa opción).
2. Inicia sesión. Comprueba que arriba se ve **PAPER · simulación** y **Sin dinero real**.
3. Compartir → «Añadir a pantalla de inicio»; ábrela desde el icono (pantalla completa).
4. Navega las 5 pestañas; en Mercados abre un activo y pellizca/arrastra el gráfico.
5. Activa el modo avión: debe aparecer «Datos desactualizados» conservando lo último; al volver, recuperarse y mostrar «En vivo».
6. Ajustes → Pausar entradas: debe pasar por «Solicitada, pendiente» y después «Confirmada por el engine». Reanudar.
7. Cierra la app por completo y ábrela: debe mostrar el último estado marcado como desactualizado hasta refrescar.
8. Gira el teléfono y prueba con texto grande (Ajustes de iOS → Tamaño del texto).

## Prueba en Chrome (escritorio)

Abre la URL; comprueba la barra lateral, DevTools → Application → Service Workers (activo) y Cache Storage
(**sin** rutas `/api`); Lighthouse → PWA/Accesibilidad.
