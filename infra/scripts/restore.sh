#!/usr/bin/env sh
# Restaura una copia en una base de datos NUEVA y la verifica. No toca la base en uso.
# Uso: PGHOST=… PGUSER=… PGPASSWORD=… infra/scripts/restore.sh copia.dump nombre_bd_nueva
# Para sustituir la base de producción: parar api y engine, renombrar/borrar la antigua,
# restaurar con este script y arrancar de nuevo (ver docs/operations.md).
set -eu
DUMP="${1:?Uso: restore.sh copia.dump nombre_bd_nueva}"
DB="${2:?Falta el nombre de la base de datos destino}"
[ -f "$DUMP" ] || { echo "No existe $DUMP" >&2; exit 1; }
if psql -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q 1; then
  echo "La base '$DB' ya existe: elige otro nombre (este script no sobrescribe)." >&2; exit 1
fi
createdb "$DB"
pg_restore --no-owner --no-privileges --dbname "$DB" "$DUMP"
echo "Restaurada en '$DB'. Verificación:"
psql -d "$DB" -Atc "SELECT 'alembic_version', version_num FROM alembic_version"
for t in users orders fills positions risk_state audit_log candles alerts; do
  printf '%s: ' "$t"; psql -d "$DB" -Atc "SELECT count(*) FROM $t"
done
