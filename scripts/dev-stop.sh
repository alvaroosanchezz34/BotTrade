#!/usr/bin/env bash
# Para la API, el worker y la web arrancados por dev-all.sh (no toca la base de datos ni los datos).
cd "$(dirname "$0")/.."
for n in api worker web; do
  f=".run/$n.pid"
  if [ -f "$f" ]; then pkill -P "$(cat "$f")" 2>/dev/null || true; kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; fi
done
echo "parado"
