# Laboratorio de estrategias

Una persona elige una **plantilla** (momentum, tendencia o reversión a la media), un activo, un intervalo, un periodo y
**sus parámetros**; el servidor simula con dinero ficticio y devuelve un informe honesto. Es el primer paso hacia un producto
(`docs/producto.md`): su valor no es prometer ganancias, es **ahorrar a la gente probar con dinero ideas que no funcionan**.

## Qué hace (y qué no)

* **No optimiza**: evalúa exactamente la configuración pedida. No busca «la mejor» de una rejilla.
* **No ejecuta código del usuario**: solo plantillas del catálogo con parámetros numéricos validados y acotados
  (rangos de Pydantic; los parámetros no editables, como el filtro de volatilidad, se rechazan). Subir código propio sería
  ejecución remota de código y no existe.
* **Datos propios del servidor**: velas de 1 m y 5 m de BTCUSDT y ETHUSDT ya descargadas y verificadas.
* Reutiliza el **mismo motor** que el trading simulado y la investigación: sin look-ahead, comisiones, spread, deslizamiento,
  mínimos del exchange y motor de riesgo.

## El informe (9 pruebas)

| Prueba | Pasa si… |
|---|---|
| Muestra suficiente | ≥ 100 operaciones cerradas |
| Resultado neto positivo | neto > 0 tras costes |
| La ventaja no es compatible con cero | IC95 de la ganancia media por operación > 0 |
| Profit factor | > 1,15 |
| Estable entre periodos | beneficio en ≥ 2 de 3 tercios del periodo |
| Sobrevive a costes ×1,5/×2 | neto > 0 con costes estresados |
| Supera a entradas aleatorias | mejor que el 90 % de 10 simulaciones con entradas al azar, mismas salidas y frecuencia |
| No depende de una sola operación | sin su mejor operación el neto sigue > 0 |
| Drawdown dentro del presupuesto | < 5 % con los límites de riesgo reales |

Veredictos: **PROMETEDORA** (todas superadas; *no* es prueba de rentabilidad y se muestra con color de aviso, nunca verde),
**SIN EVIDENCIA DE VENTAJA** (alguna fallada con muestra suficiente) y **NO CONCLUYENTE** (menos de 100 operaciones: con
pocas operaciones no se puede concluir nada, ni a favor ni en contra; p. ej. 0 operaciones).

Además: curva de capital, resultado final (capital inicial → final), comparación con comprar y mantener, resultado por tercios,
datos usados (hash) y avisos.

## Defensa contra el autoengaño

* **Comparaciones múltiples**: el informe indica cuántas variantes de *la misma idea* (estrategia + activos + intervalo, últimos
  90 días) lleva probadas el usuario, y desde la 5.ª avisa de que un buen resultado puede ser casualidad.
* La validación fuera de muestra y el paper trading en vivo siguen siendo obligatorios antes de creerse nada.

## Arquitectura y límites

```
Navegador ──► API (valida, cuotas, encola) ──► tabla lab_jobs ◄── lab-worker (simula, guarda el informe)
```
* La API **no simula**. El proceso `lab-worker` reclama trabajos con `FOR UPDATE SKIP LOCKED` (se pueden lanzar varios) y los
  deja siempre en `DONE` o `FAILED` con motivo. Tras un reinicio, los `RUNNING` huérfanos pasan a `FAILED` (no se reintentan a ciegas).
* Cuotas: 1 simulación activa por usuario, 10 por hora, cola global de 30, 1 m ≤ 6 meses y 5 m ≤ 12 meses, ≤ 2 activos.
* **Privacidad**: cada usuario solo ve sus trabajos e informes. Las simulaciones del laboratorio no salen en los listados generales
  y el detalle de una ajena devuelve 404.
* Rendimiento medido con datos reales (12 meses, 2 activos, 5 m; y 3 meses de 1 m): ≈ 13–15 s por simulación en 4 vCPU.

## API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/lab/options` | plantillas, rangos de parámetros, datos disponibles, límites |
| POST | `/api/lab/jobs` | encola una simulación (202 + `job_id`); valida y aplica cuotas (409/422/429/503) |
| GET | `/api/lab/jobs` · `/api/lab/jobs/{id}` | mis simulaciones · estado e informe |

El resultado final de una simulación termina de verse con `GET /api/backtests/{run_id}` (solo para su dueño).

## Limitaciones

* Solo 2 activos y 2 intervalos; tres plantillas. Ampliar el catálogo = nuevas versiones que hay que validar.
* Los tercios son iguales en tiempo (no hay optimización, así que no hay entrenamiento/validación/prueba).
* No hay registro público, 2FA, facturación ni cuotas por plan: hoy cualquier usuario con cuenta creada por CLI puede usarlo.
* El tiempo máximo de una simulación no está forzado por proceso (los límites de periodo lo acotan indirectamente).
