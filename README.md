# BotTrade

Plataforma de investigación y trading algorítmico **exclusivamente en simulación**
(BACKTEST, PAPER y TESTNET de Binance Spot), con panel PWA para iPhone y escritorio.

> ⚠️ Este sistema no envía órdenes reales. No existe ningún modo de dinero real
> en el código (ver `docs/decisions.md`, ADR-006). Ningún resultado de este
> repositorio es una promesa de rentabilidad.

## Estado

Ver [`STATUS.md`](STATUS.md) para la fase actual, qué funciona, limitaciones y
el siguiente paso exacto.

## Requisitos

* Python 3.12 y [uv](https://docs.astral.sh/uv/)
* PostgreSQL 16 (local o con Docker)
* Docker + Docker Compose (opcional para desarrollo, obligatorio en el VPS)

## Arranque rápido (desarrollo local)

```bash
cp .env.example .env                 # edita POSTGRES_PASSWORD y BOTTRADE_DATABASE_URL
make install                          # crea backend/.venv
docker compose up -d db               # o usa un PostgreSQL local
make migrate                          # alembic upgrade head
make admin USER=koke                  # crea el usuario administrador
make api                              # http://127.0.0.1:8000/docs
```

## Frontend (PWA)

```bash
cd frontend && npm ci
npm run dev                    # http://127.0.0.1:5173 (proxy de /api y /ws al backend en :8000)
npm test && npm run build      # unidad + tsc + build de producción
npm run test:e2e               # Playwright: levanta API + engine reales con una BD de fixtures (bottrade_e2e)
```

## Tests

Los tests usan una base de datos PostgreSQL real (`bottrade_test`), que se
recrea desde las migraciones en cada ejecución:

```bash
createdb -h 127.0.0.1 -U bottrade bottrade_test   # una vez
make check                                        # ruff + mypy + pytest
```

Variable para otra BD de test: `BOTTRADE_TEST_DATABASE_URL`.
Los tests que acceden a Internet están marcados `network` y no se ejecutan por
defecto (`cd backend && .venv/bin/pytest -m network`).

## Documentación

| Documento | Contenido |
|---|---|
| `docs/architecture.md` | Procesos, pipeline, contratos, modos |
| `docs/decisions.md` | Decisiones de arquitectura (ADR) |
| `docs/plan.md` | Fases, criterios de aceptación y riesgos |
| `docs/risk-policy.md` | Límites, tamaño de posición, pausa/kill switch/reanudar |
| `docs/strategies.md` · `docs/backtesting.md` | Estrategias y protocolo de validación |
| `docs/api.md` | Contratos REST/WebSocket |
| `docs/security.md` | Modelo de seguridad, acceso desde iPhone sin abrir puertos, rotación |
| `docs/deployment.md` · `docs/operations.md` | VPS, backups, actualizaciones, alertas, TESTNET |
| `docs/testing.md` | Qué se prueba y cómo probar en iPhone/Chrome |
| `docs/capital-and-costs.md` | Costes frente a 500 USDT (hipotético, no predicción) |
| `docs/probar-sin-docker.md` | Probarlo sin Docker (Codespaces con un solo script, o Windows nativo) |
| `docs/laboratorio.md` · `docs/probar-laboratorio.md` | Laboratorio de estrategias y cómo probarlo en local (paso a paso) |
| `docs/simulacion-desde-el-movil.md` | Simulación con dinero ficticio y cómo verla desde el iPhone (paso a paso) |
| `docs/producto.md` | Notas para convertirlo en producto público (regulación, carencias técnicas) |
| `docs/validation-report.md` | Informe de validación (**sin resultados todavía**) |
| `STATUS.md` | Estado actual y siguiente paso |
