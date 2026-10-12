# Imagen única para los procesos api, engine y migraciones.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 10001 bottrade
WORKDIR /app

COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install .

COPY backend/alembic.ini ./
COPY backend/migrations ./migrations

USER bottrade
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

# --forwarded-allow-ips: solo el proxy (red interna de Docker) puede fijar la IP del cliente.
CMD ["uvicorn", "app.api.main:build", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
