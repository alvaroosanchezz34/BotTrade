.PHONY: help install migrate test lint typecheck check api admin

PY := backend/.venv/bin/python
BIN := backend/.venv/bin

help:
	@echo "install    Crea el entorno virtual e instala dependencias"
	@echo "migrate    Aplica migraciones (alembic upgrade head)"
	@echo "test       Ejecuta los tests (requiere PostgreSQL con BD bottrade_test)"
	@echo "lint       Ruff (lint + formato)"
	@echo "typecheck  mypy estricto"
	@echo "check      lint + typecheck + test"
	@echo "api        Arranca la API en http://127.0.0.1:8000"
	@echo "admin      Crea un usuario administrador: make admin USER=koke"

install:
	cd backend && uv venv -p 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'

migrate:
	cd backend && .venv/bin/alembic upgrade head

test:
	cd backend && .venv/bin/pytest

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .

typecheck:
	cd backend && .venv/bin/mypy app

check: lint typecheck test

api:
	cd backend && .venv/bin/uvicorn app.api.main:build --factory --reload --port 8000

admin:
	cd backend && .venv/bin/python -m app.cli create-admin $(USER)
