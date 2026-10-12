# Despliegue en un VPS

**No despliegues nada ni contrates infraestructura sin decidirlo tú.** Esta guía describe cómo
hacerlo; el código no despliega nada por sí mismo. El estado de verificación de cada paso está al final.

## Requisitos aproximados (2 símbolos, velas 1 m/5 m)

| Recurso | Mínimo | Cómodo |
|---|---|---|
| CPU | 1 vCPU | 2 vCPU (los backtests de investigación son intensivos) |
| RAM | 2 GB | 4 GB |
| Disco | 10 GB | 20–40 GB (velas 1m ≈ 1,5 M filas/año/símbolo; backups; logs) |
| Red | saliente HTTPS/WSS a Binance; 443 entrante solo si no usas Tailscale | |

Orden de magnitud: PostgreSQL + API + engine + Caddy caben en 1–2 GB de RAM en reposo. Es una estimación
de diseño, **no medida** en un VPS real.

## Pasos

```bash
git clone <repo> && cd BotTrade
cp infra/.env.prod.example infra/.env.prod && chmod 600 infra/.env.prod
#   editar: SITE_ADDRESS, WEB_BIND, POSTGRES_PASSWORD (openssl rand -base64 24), BOTTRADE_DATABASE_URL, CORS
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod up -d --build
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod ps
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod run --rm api python -m app.cli create-admin tu_usuario
```

El servicio `migrate` aplica Alembic antes de arrancar `api` y `engine`. El modo por defecto es `PAPER`
(cuenta simulada, datos públicos reales). Para `TESTNET` ver `docs/operations.md`.

## Actualizaciones controladas

1. `git pull` y revisar el diff y el CHANGELOG/STATUS.
2. **Copia de seguridad** (`infra/scripts/backup.sh`).
3. Opcional pero recomendable: pausa desde el panel y espera a no tener órdenes pendientes.
4. `docker compose … up -d --build` (las migraciones corren primero; el engine arranca **sin entradas** hasta reconciliar).
5. Comprobar Ajustes → Estado del sistema y la ausencia de alertas críticas.
6. Si una estrategia cambió de versión: **revalidar** antes de activarla (`docs/backtesting.md`).

Reinicio controlado: `docker compose … restart engine` (`stop_grace_period` 30 s). Tras cualquier reinicio el engine
reconstruye el libro PAPER desde la BD, reconcilia y bloquea entradas hasta que la reconciliación sea limpia.

## Copias de seguridad y restauración

```bash
# Copia (también el servicio opcional «backup»: --profile backup, diario con retención)
PGHOST=… PGUSER=bottrade PGPASSWORD=… PGDATABASE=bottrade infra/scripts/backup.sh ./backups 14
# Restaurar en una BD NUEVA y verificar (no sobrescribe nada)
infra/scripts/restore.sh backups/bottrade-AAAAMMDDTHHMMSSZ.dump bottrade_restaurada
```
Para sustituir la base de producción: parar `api` y `engine`, renombrar la antigua, restaurar con el nombre
original, arrancar. Guarda copias **fuera** del VPS (p. ej. `rclone`/`scp` a otro sitio); un backup en el mismo disco no protege del disco.

## Monitorización de recursos

`docker stats`, `docker compose logs -f engine`, y los health checks: `/health` (API), `/health/ready` (BD, engine,
datos), `check-engine` (latido, usado por Docker). Los logs son JSON con rotación (20 MB × 5).

## Estado de verificación

| Elemento | Estado |
|---|---|
| `docker-compose.prod.yml` | validado con `docker compose config` (solo `web` publica puertos) |
| Imágenes Docker (`build`) y Caddy | **no construidas ni ejecutadas** (sin daemon de Docker en el entorno de desarrollo) |
| Backup/restore | **probado** con datos reales contra PostgreSQL 16 local |
| Migraciones | probadas en BD vacía (`alembic upgrade head`) |
