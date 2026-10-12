#!/bin/sh
# Bucle diario dentro del contenedor «backup».
set -eu
export PGHOST=db PGUSER=bottrade PGDATABASE=bottrade
apk add --no-cache coreutils >/dev/null 2>&1 || true
while true; do
  sh -c "$(cat <<'INNER'
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"; OUT="/backups/bottrade-$STAMP.dump"
pg_dump --format=custom --no-owner --no-privileges --file "$OUT.partial" && mv "$OUT.partial" "$OUT" && chmod 600 "$OUT" && pg_restore --list "$OUT" >/dev/null
find /backups -name 'bottrade-*.dump' -mtime +"${RETENTION_DAYS:-14}" -delete
echo "OK $OUT"
INNER
)" || echo "FALLO en la copia de seguridad"
  sleep 86400
done
