# Simulación con dinero ficticio, vista desde el móvil

Hay **dos formas** de ver «cómo sería el resultado final». Ambas usan dinero ficticio; no se envía ninguna orden real.

| | Simulación histórica | Simulación en vivo (PAPER) |
|---|---|---|
| Qué es | Reproduce una estrategia sobre 12 meses **pasados** | El bot opera «de mentira» con precios reales de **hoy**, día a día |
| Resultado | Inmediato | Se va construyendo en semanas |
| Dónde se ve | Estrategias → *Simulaciones históricas* → resultado final | Inicio, Operaciones, Mercados |
| Necesita | Ejecutar `research` una vez | Un ordenador/servidor encendido 24 h |

La **histórica es la más rápida** para ver el resultado final (capital inicial → final, curva, operaciones, veredicto).
La **en vivo** es la prueba que de verdad importa antes de creerse nada (≥ 8 semanas, ≥ 300 operaciones).

## A) Ver las simulaciones históricas (ya generadas)

Las 6 simulaciones de 12 meses (momentum, trend, mean_reversion × 1m/5m) se guardan en la base de datos al ejecutar:

```bash
cd backend
.venv/bin/python -m app.cli download-history --start 2025-10-01 --end 2026-09-30   # ~20 min
.venv/bin/python -m app.cli research --start 2025-10-01 --end 2026-09-29 --seeds 20  # ~20 min
```
Luego abre la app → **Estrategias** → *Simulaciones históricas (dinero ficticio)* → toca una. Verás capital inicial y final,
rentabilidad, mejor/peor operación, caída máxima, curva y las últimas operaciones, con el veredicto del protocolo.

## B) Simulación en vivo, paso a paso (desde casa, gratis)

Necesitas un ordenador de casa **encendido de forma continua** (o un VPS de ~5 €/mes, ver `docs/deployment.md`).

1. **Instala Docker** (Docker Desktop en Windows/Mac; Docker Engine en Linux) y **Tailscale** (tailscale.com, gratis) en ese ordenador **y en el iPhone**. Inicia sesión con la misma cuenta en ambos.
2. En el ordenador, descarga el proyecto: `git clone https://github.com/alvaroosanchezz34/BotTrade && cd BotTrade`.
3. Copia la configuración: `cp infra/.env.prod.example infra/.env.prod`. Edítala:
   * `POSTGRES_PASSWORD` y la misma clave dentro de `BOTTRADE_DATABASE_URL`: genera una con `openssl rand -base64 24`.
   * `BOTTRADE_TRADING_MODE=PAPER` (déjalo así: dinero ficticio).
   * `SITE_ADDRESS`: el nombre de tu ordenador en Tailscale (p. ej. `mi-pc.tu-red.ts.net`; lo ves en la app de Tailscale).
   * `WEB_BIND`: la IP de Tailscale de ese ordenador (empieza por `100.`).
   * `BOTTRADE_CORS_ORIGINS=https://` + tu `SITE_ADDRESS`.
4. Arranca todo: `docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod up -d --build` (la primera vez tarda unos minutos).
5. Crea tu usuario: `docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod run --rm api python -m app.cli create-admin tu_usuario`.
6. En el iPhone, con Tailscale activo, abre en **Safari** `https://<SITE_ADDRESS>`, inicia sesión y **Compartir → Añadir a pantalla de inicio**.
7. En **Estrategias**, entra en la estrategia que quieras simular y pulsa **Activar** (primero «solicitada», luego «confirmada por el engine»).
8. Déjalo correr. Mira **Inicio** (capital ficticio, P&L, drawdown), **Operaciones** y **Mercados**. El bot sigue aunque cierres la app o apagues el móvil.

> Estado de verificación: la configuración de Docker está validada pero las imágenes **no se han construido en el entorno de
> desarrollo**; puede haber algún ajuste en el primer arranque. Si algo falla, copia el error de `docker compose logs` y se corrige.

Si el ordenador se apaga o se queda sin internet, el bot se para: al volver, reconcilia y **no abre entradas** hasta que todo cuadre.

## Qué NO es

* No es una predicción: «500 → 530» en una simulación histórica no significa que vaya a repetirse.
* Con los datos actuales **ninguna estrategia está validada** (`docs/validation-report.md`). Verás sobre todo pérdidas simuladas, y es el resultado correcto.
