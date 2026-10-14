#!/usr/bin/env bash
# Monta y arranca BotTrade SIN Docker (Codespaces, Ubuntu/Debian, WSL, Mac con PostgreSQL ya instalado).
# Idempotente: se puede relanzar. Dinero ficticio. Uso:   bash scripts/dev-all.sh
#   Variables opcionales: BT_USER (usuario), BT_PASSWORD (≥ 12 caracteres), BT_MONTHS (meses de datos, def. 6)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
RUN="$ROOT/.run"; mkdir -p "$RUN"
say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
as_pg() { if [ "$(id -u)" -eq 0 ]; then su postgres -c "$1"; else sudo -u postgres bash -c "$1"; fi; }

say "1/7 PostgreSQL"
if ! command -v psql >/dev/null 2>&1; then
  $SUDO apt-get update -y >/dev/null && $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql postgresql-contrib >/dev/null
fi
$SUDO service postgresql start >/dev/null 2>&1 || true
for _ in $(seq 1 30); do as_pg "psql -Atc 'select 1'" >/dev/null 2>&1 && break; sleep 1; done
as_pg "psql -Atc \"select 1 from pg_roles where rolname='bottrade'\"" | grep -q 1 \
  || as_pg "psql -c \"create role bottrade login password 'bottrade_dev'\"" >/dev/null
as_pg "psql -Atc \"select 1 from pg_database where datname='bottrade'\"" | grep -q 1 \
  || as_pg "createdb -O bottrade bottrade"
echo "base de datos lista (solo accesible desde esta máquina)"

say "2/7 Python y dependencias del backend"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null 2>&1 || python3 -m pip install --user -q uv
( cd backend
  [ -x .venv/bin/python ] || uv venv -q -p 3.12 .venv
  uv pip install -q --python .venv/bin/python -e . )
[ -f backend/.env ] || cat > backend/.env <<'ENV'
BOTTRADE_ENVIRONMENT=development
BOTTRADE_TRADING_MODE=PAPER
BOTTRADE_DATABASE_URL=postgresql+asyncpg://bottrade:bottrade_dev@127.0.0.1:5432/bottrade
BOTTRADE_LOG_JSON=false
ENV
PY="$ROOT/backend/.venv/bin/python"

say "3/7 Esquema de la base de datos"
( cd backend && .venv/bin/alembic upgrade head 2>&1 | tail -1 )

say "4/7 Usuario"
BT_USER="${BT_USER:-}"
if [ -z "$BT_USER" ]; then read -rp "Nombre de usuario que quieres crear (Enter para saltar si ya existe): " BT_USER || true; fi
if [ -n "$BT_USER" ]; then
  ( cd backend
    if [ -n "${BT_PASSWORD:-}" ]; then BOTTRADE_ADMIN_PASSWORD="$BT_PASSWORD" .venv/bin/python -m app.cli create-admin "$BT_USER" || true
    else .venv/bin/python -m app.cli create-admin "$BT_USER" || true; fi )
fi

say "5/7 Datos de mercado reales (${BT_MONTHS:-6} meses, velas de 5 min; unos minutos la primera vez)"
START="$(date -u -d "${BT_MONTHS:-6} months ago" +%Y-%m-01 2>/dev/null || date -u -v-"${BT_MONTHS:-6}"m +%Y-%m-01)"
( cd backend && .venv/bin/python -m app.cli download-history --symbols BTCUSDT,ETHUSDT --intervals 5m --start "$START" 2>&1 | tail -4 )

say "6/7 Interfaz (compilando)"
(
  cd frontend
  # Solo se reinstalan las dependencias si cambió package-lock.json (más rápido y evita borrar node_modules en vano).
  LOCK="$(sha256sum package-lock.json 2>/dev/null | cut -d' ' -f1 || shasum -a 256 package-lock.json | cut -d' ' -f1)"
  if [ ! -f node_modules/.bt-lock ] || [ "$(cat node_modules/.bt-lock)" != "$LOCK" ]; then
    npm ci --no-audit --no-fund --loglevel=error || { rm -rf node_modules && npm ci --no-audit --no-fund --loglevel=error; }
    echo "$LOCK" > node_modules/.bt-lock
  fi
  npm run build >/dev/null
)

say "7/7 Arrancando"
bash "$ROOT/scripts/dev-stop.sh" >/dev/null 2>&1 || true
# Cada proceso se lanza desacoplado de esta terminal (sin heredar su salida) y su PID queda guardado.
start() { # nombre carpeta comando...
  local name="$1" dir="$2"; shift 2
  # setsid: cada servicio es su propio grupo de procesos, para poder pararlo entero (npm lanza procesos hijos).
  local launcher="nohup"; command -v setsid >/dev/null 2>&1 && launcher="setsid nohup"
  ( cd "$dir" && exec $launcher "$@" >"$RUN/$name.log" 2>&1 </dev/null ) &
  echo $! >"$RUN/$name.pid"
}
start api backend .venv/bin/uvicorn app.api.main:build --factory --port 8000
start worker backend .venv/bin/python -m app.cli lab-worker
BOTTRADE_ALLOW_ANY_HOST=1 start web frontend npm run preview
for _ in $(seq 1 30); do curl -fs http://127.0.0.1:4173/ >/dev/null 2>&1 && curl -fs http://127.0.0.1:8000/health >/dev/null 2>&1 && break; sleep 1; done

URL="http://127.0.0.1:4173"
[ -n "${CODESPACE_NAME:-}" ] && URL="https://${CODESPACE_NAME}-4173.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
printf '\n\033[1;32m✔ Listo.\033[0m Abre: %s\n   Entra con tu usuario → Estrategias → «Probar una idea».\n   Registros: %s/*.log · Parar todo: bash scripts/dev-stop.sh\n' "$URL" "$RUN"
