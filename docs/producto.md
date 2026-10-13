# Notas para convertirlo en producto público

> No es asesoramiento legal ni financiero. Antes de cobrar o publicar, consulta con un abogado especializado
> en servicios financieros y en cripto (en España/UE: CNMV, MiFID II, MiCA, RGPD).

## Qué producto es (y cuál no)

| Idea | Riesgo regulatorio | Comentario |
|---|---|---|
| **Laboratorio/simulador** de estrategias con dinero ficticio, educativo | Bajo (si no promete rentabilidad ni da recomendaciones personalizadas) | Es lo que la plataforma ya hace bien |
| **Señales / «qué comprar»** a terceros | Medio-alto (puede ser asesoramiento en inversiones) | Requiere análisis legal |
| **Operar o gestionar dinero de otros** automáticamente | **Alto** (gestión de carteras, servicios sobre criptoactivos: autorización) | No hacerlo sin licencia/asesoría |

## Honestidad comercial (diferencial y obligación)

* Los resultados de este proyecto son **negativos**: las 3 estrategias actuales pierden tras costes. No se puede vender
  «rentabilidad». Prometer ganancias o mostrar solo simulaciones favorables es publicidad engañosa y un riesgo legal.
* El valor real y defendible es **el rigor**: costes realistas, partición temporal, walk-forward, comparación con azar,
  descarte explícito y trazabilidad. «Descubre si tu idea tiene ventaja *antes* de arriesgar dinero.»
* Toda pantalla de simulación lleva «dinero ficticio / no predice el futuro» (ya implementado).

## Lo que falta técnicamente para varios usuarios (hoy es de **un solo usuario**)

1. **Multi-inquilino**: cada usuario con su propia cuenta simulada, estrategias, ejecuciones y límites (hoy hay una cuenta por modo).
2. **Registro, verificación de correo, recuperación de contraseña, 2FA** (hoy: un admin creado por CLI).
3. **Backtests bajo demanda** con cola y cuotas (hoy son comandos que tardan ~20 min y usan toda la CPU).
4. **Aislamiento y límites**: rate limiting compartido (hoy en memoria), cuotas por plan, protección de abuso.
5. **Facturación** (p. ej. Stripe), términos de uso, política de privacidad, RGPD (datos y borrado).
6. **Datos de mercado**: revisa las condiciones de uso de Binance para uso comercial/redistribución de datos públicos.
7. **Operación**: monitorización, backups probados fuera del servidor, soporte, entorno de staging.
8. **Nunca** custodiar claves de exchange de terceros sin una revisión de seguridad seria: es el mayor riesgo del negocio.

## Orden razonable

1. Validar que alguien lo quiere (landing + lista de espera + 10 entrevistas), antes de construir el multiusuario.
2. Primer producto: **laboratorio de simulación** de un solo propósito (subir idea → ver backtest honesto → informe).
3. Solo si hay tracción y asesoría legal: ampliar.
