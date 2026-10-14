#!/usr/bin/env bash
# Para la API, el worker y la web arrancados por dev-all.sh (no toca la base de datos ni los datos).
cd "$(dirname "$0")/.."
for n in api worker web; do
  f=".run/$n.pid"
  if [ -f "$f" ]; then
    pid="$(cat "$f")"
    # Se mata el grupo de procesos entero (npm → vite…); si no hay grupo propio, el proceso y sus hijos.
    kill -TERM -- "-$pid" 2>/dev/null || { pkill -TERM -P "$pid" 2>/dev/null || true; kill -TERM "$pid" 2>/dev/null || true; }
    rm -f "$f"
  fi
done
# Red de seguridad por si quedó algún proceso suelto de ejecuciones anteriores.
pkill -f "frontend/node_modules/.bin/vite" 2>/dev/null || true
pkill -f "app.api.main:build" 2>/dev/null || true
pkill -f "app.cli lab-worker" 2>/dev/null || true
echo "parado"
