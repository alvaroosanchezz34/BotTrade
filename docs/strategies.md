# Estrategias

Son **hipótesis de investigación**. Ninguna se considera rentable por defecto y todas
nacen desactivadas. Una estrategia nunca envía órdenes: devuelve una `Signal`
(activo, estrategia, versión, dirección, precio de referencia, justificación, condiciones,
invalidación, stop, objetivo, parámetros, puntuación) que pasa por el motor de riesgo.

La misma función causal y vectorizada alimenta backtest y modo en vivo (se lee la última fila),
y un test demuestra la **ausencia de look-ahead** (recortar el futuro no cambia las señales pasadas).

| id | Idea | Entrada | Salida |
|---|---|---|---|
| `momentum` | Ruptura de máximos con volumen | máximo de N barras superado, subida ≥ x ATR, volumen ≥ k× la media | stop ATR, objetivo R:R, tiempo máx. |
| `trend` | Seguir el régimen con EMA | EMA rápida > lenta > régimen y cierre que recupera la rápida | cruce bajista, stop ATR, tiempo |
| `mean_reversion` | Desviación estadística en rango | z-score ≤ −z_entry y ratio de eficiencia bajo (mercado lateral) | vuelta a la media, stop ATR, tiempo |
| filtro de volatilidad | Evitar operar sin ventaja posible | aplicado a todas: movimiento esperado ≥ k × coste ida/vuelta; no en volatilidad extrema | — |

* La puntuación (`score`) es heurística y se etiqueta **no calibrada**: no es una probabilidad.
* **Coordinación**: como máximo una entrada por activo y vela; si varias coinciden gana la de
  mayor puntuación y el resto se guarda como `SUPERSEDED`. Nunca hay posiciones duplicadas
  ni contradictorias. El sistema funciona con una sola estrategia activa.
* Activar/desactivar solo desde el panel (comando confirmado por el engine). Los parámetros
  son de solo lectura en el panel para no eludir el riesgo; cada cambio de código genera una
  nueva versión (`strategy_versions`, con hash del código) que hay que revalidar.
