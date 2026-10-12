#!/usr/bin/env sh
# Copia lógica de PostgreSQL en formato custom (comprimido, restaurable por tabla).
# Uso: PGHOST=… PGUSER=… PGPASSWORD=… PGDATABASE=… infra/scripts/backup.sh /ruta/backups [días_retención]
set -eu
DEST="${1:?Uso: backup.sh /ruta/backups [dias_retencion]}"
KEEP="${2:-14}"
mkdir -p "$DEST"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DEST/bottrade-$STAMP.dump"
pg_dump --format=custom --no-owner --no-privileges --file "$OUT.partial"
mv "$OUT.partial" "$OUT"          # un fichero sin .partial siempre está completo
chmod 600 "$OUT"
# Comprobación de que el volcado es legible antes de dar por buena la copia.
pg_restore --list "$OUT" >/dev/null
find "$DEST" -name 'bottrade-*.dump' -mtime +"$KEEP" -delete
echo "OK $OUT ($(du -h "$OUT" | cut -f1))"
