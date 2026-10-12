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
| `STATUS.md` | Estado actual y siguiente paso |
