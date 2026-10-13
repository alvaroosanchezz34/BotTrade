# Estado del proyecto

_Última actualización: 2026-10-13. Rama `claude/peaceful-fermat-qo4tgr`._

**El sistema opera exclusivamente en BACKTEST, PAPER y TESTNET. No existe modo de dinero real.**
**Ninguna estrategia ha demostrado ventaja estadística: las 6 combinaciones probadas con 12 meses de datos reales quedaron DESCARTADAS** (ver `docs/validation-report.md`).

## Fases

| Fase | Estado | Verificado con |
|---|---|---|
| 0 Diseño | ✅ | `docs/architecture.md`, `decisions.md`, `plan.md`; APIs de Binance contrastadas con la documentación oficial |
| 1 Fundamentos | ✅ | config validada, BD + Alembic, auth, health, auditoría, logs con redacción |
| 2 Datos de mercado | ✅ (sin datos reales en esta sesión) | validador, feed WS con reconexión/recuperación, archivos con SHA-256; tests con WS/REST simulados |
| 3 Backtesting | ✅ | motor sin look-ahead, costes, métricas, walk-forward; **ejecutado con 12 meses reales de BTC/ETH** |
| 4 Riesgo y órdenes | ✅ | máquina de estados, idempotencia, `UNKNOWN`, reconciliación, kill switch; tests de fallos |
| 5 PAPER / TESTNET | ✅ PAPER (simulado); ⚠️ TESTNET **no probado contra el testnet real** | engine e2e, reinicios, comandos; adaptador probado con servidor simulado |
| 6 Aplicación móvil | ✅ | PWA React/TS; 45 tests unitarios; 38 e2e (iPhone emulado + escritorio) contra API y engine reales; axe sin violaciones graves |
| 7 Seguridad y despliegue | ✅ código y docs; ⚠️ imágenes Docker **sin construir** | tests de seguridad; compose validado; backup/restore probado |
| 8 Validación prolongada | ⚠️ parte histórica **hecha** (todas descartadas); paper ≥ 8 semanas **no aplica** hasta que haya una candidata | `docs/validation-report.md` |

## Resultados de pruebas (última ejecución en este entorno)

* Backend: `make check` → ruff ✔, mypy estricto ✔ (68 ficheros), **289 tests pasan**.
* Frontend: `tsc` ✔, **47 tests pasan** (vitest).
* E2E (Playwright, Chromium): **20 + 20 pasan** (proyecto iPhone 13 emulado y escritorio).
* No ejecutados: tests con red real (`-m network`), Safari/iPhone real, Binance Testnet real, construcción de imágenes Docker.

## Novedades

* Pantalla **Simulaciones históricas** (Estrategias → resultado final con capital inicial→final, curva y veredicto) y `GET /api/backtests` con capital final.
* Guía de simulación desde el móvil (`docs/simulacion-desde-el-movil.md`) y notas de producto (`docs/producto.md`).

## Limitaciones conocidas (honestas)

1. **Paper en vivo no ejecutado**: no tiene sentido sin una estrategia candidata. La validación histórica usó solo 12 meses y una rejilla de 8 combinaciones por estrategia.
2. **Testnet**: geobloqueo (HTTP 451) y bloqueo de red; el adaptador está verificado contra la documentación oficial y un servidor simulado, no contra el servicio.
3. **Docker/Caddy**: configuración validada, imágenes no construidas aquí.
4. **iPhone real**: probado con Chromium emulando iPhone 13, no con WebKit/Safari.
5. Stops **sintéticos** (los vigila el engine; no hay stop en el exchange). Con el engine caído no actúan.
6. Límites de riesgo y parámetros de estrategias **solo lectura** en el panel; no hay procedimiento asistido para «reiniciar el pico de drawdown» tras un bloqueo (intervención manual auditada).
7. Notificaciones push **no implementadas** (alertas guardadas en servidor y visibles en el panel).
8. Comisiones: se asume tarifa taker 0,10 % sin descuento BNB; spread supuesto 0,01 % cuando no hay bid/ask.
9. El libro PAPER se reconstruye desde las ejecuciones; las órdenes límite vivas no sobreviven a un reinicio (se descartan al reconciliar).
10. Una sola cuenta de administrador por CLI; sin recuperación de contraseña por correo.

## Siguiente paso exacto

1. Leer `docs/validation-report.md` (resultado: nada pasa; trend 5m es un falso positivo por régimen alcista).
2. Decidir: (a) detenerse aquí con una plataforma de investigación que ha descartado correctamente sus hipótesis, o
   (b) investigar nuevas hipótesis (más años y activos, frecuencias menores) con la misma disciplina: nueva versión ⇒ revalidar.
3. Solo si algo supera los criterios: desplegar (`docs/deployment.md`), probar en el iPhone (`docs/testing.md`) y paper ≥ 8 semanas.
4. Dinero real: **no existe** y requeriría una decisión separada con cambios explícitos de código.

## Comandos de verificación

```bash
cp .env.example .env && make install && docker compose up -d db && make migrate     # entorno local
make check                                                                         # backend completo
cd frontend && npm ci && npm test && npm run build && npm run test:e2e              # frontend
python backend/scripts/cost_analysis.py                                            # análisis de costes
```
(PostgreSQL local con las BD `bottrade_test` y `bottrade_e2e` para los tests; ver `README.md`.)
