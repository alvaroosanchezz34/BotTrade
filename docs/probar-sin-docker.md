# Probar BotTrade sin Docker

Dos caminos. **El A es el recomendado**: no instalas nada en tu PC y el resultado se abre también desde el iPhone con HTTPS.

> Estado de verificación: el script del camino A se ha ejecutado de principio a fin en un Linux limpio de desarrollo (instala la base de
> datos, las dependencias, descarga datos reales, compila, arranca y se ha comprobado en el navegador: login → laboratorio → informe en
> ~14 s). **No se ha podido ejecutar dentro de GitHub Codespaces ni en Windows nativo** (no tengo acceso a ellos): puede haber algún ajuste.
> Si algo falla, copia el mensaje de error completo.

---
## Camino A · GitHub Codespaces (un Linux en el navegador)

Necesitas una cuenta de GitHub (la tuya, la del repositorio). El plan gratuito incluye una cuota mensual de uso (unas 60 horas con una
máquina de 2 núcleos); se para solo tras 30 min sin actividad.

1. Abre https://github.com/alvaroosanchezz34/BotTrade
2. Arriba a la izquierda, en el selector de ramas, elige **`claude/peaceful-fermat-qo4tgr`** (importante: el laboratorio está en esa rama).
3. Botón verde **Code** → pestaña **Codespaces** → **Create codespace on claude/peaceful-fermat-qo4tgr**.
   (Si te deja elegir máquina, la de **2 núcleos** basta; con 4 las simulaciones van el doble de rápido pero gastas cuota el doble.)
4. Espera 2–4 minutos: se abre un editor (VS Code) en el navegador.
5. Abajo hay una **terminal** (si no se ve: menú ☰ → Terminal → New Terminal). Escribe:
   ```bash
   bash scripts/dev-all.sh
   ```
   Te pregunta un **nombre de usuario** y luego una **contraseña** (mínimo 12 caracteres; no se ve al escribir). La primera vez tarda ~5–8 min
   (instala la base de datos y las dependencias, descarga 6 meses de datos reales y compila la interfaz). Es normal que salgan muchas líneas.
6. Al final verás **«✔ Listo. Abre: https://…app.github.dev»**. Pulsa ese enlace (o pestaña **Puertos** → fila 4173 → icono del globo).
   La primera vez GitHub muestra «You are about to access a development port…» → **Continue**.
7. Entra con el usuario y contraseña que acabas de crear → **Estrategias → Probar una idea**.

**En el iPhone:** abre esa misma dirección `https://…app.github.dev` en Safari (te pedirá iniciar sesión en GitHub la primera vez, y luego en BotTrade).

**Otro día:** abre el Codespace desde github.com/codespaces y vuelve a ejecutar `bash scripts/dev-all.sh` (más rápido: ~1 min; no recrea nada que ya exista;
cuando pregunte el usuario, pulsa Enter para saltarlo). Para parar sin borrar: `bash scripts/dev-stop.sh`. Cuando no lo uses más, borra el Codespace en
github.com/codespaces para no gastar espacio.

**Si falla:** `tail -40 .run/api.log .run/worker.log .run/web.log` y pega el resultado.

---
## Camino B · Windows nativo (sin Docker ni WSL)

Más pasos y más probabilidad de ajustes. Las simulaciones van **sin paralelismo** en Windows, así que tardan más (1–2 min).
Abre **PowerShell** en la carpeta del proyecto (`C:\Users\Theal\Desktop\BotTrade`) y estate en la rama correcta: `git checkout claude/peaceful-fermat-qo4tgr`.

**1. Instala las herramientas** (acepta lo que pidan; cierra y vuelve a abrir PowerShell al terminar):
```powershell
winget install -e --id Python.Python.3.12
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id PostgreSQL.PostgreSQL.16
```
PostgreSQL te pedirá una **contraseña para el usuario «postgres»** (apúntala) y el puerto: deja **5432**.

**2. Crea la base de datos** (te pedirá la contraseña de «postgres»):
```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "CREATE ROLE bottrade LOGIN PASSWORD 'bottrade_dev'"
& "C:\Program Files\PostgreSQL\16\bin\createdb.exe" -U postgres -O bottrade bottrade
```

**3. Prepara el backend:**
```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
@"
BOTTRADE_ENVIRONMENT=development
BOTTRADE_TRADING_MODE=PAPER
BOTTRADE_DATABASE_URL=postgresql+asyncpg://bottrade:bottrade_dev@127.0.0.1:5432/bottrade
BOTTRADE_LOG_JSON=false
"@ | Set-Content -Encoding ascii .env
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m app.cli create-admin tu_usuario
.\.venv\Scripts\python.exe -m app.cli download-history --symbols BTCUSDT,ETHUSDT --intervals 5m --start 2026-04-01
```

**4. Compila la interfaz:**
```powershell
cd ..\frontend
npm ci
npm run build
```

**5. Arranca (tres ventanas de PowerShell, todas dejadas abiertas):**
```powershell
# Ventana 1 — API
cd C:\Users\Theal\Desktop\BotTrade\backend ; .\.venv\Scripts\uvicorn.exe app.api.main:build --factory --port 8000
# Ventana 2 — simulaciones
cd C:\Users\Theal\Desktop\BotTrade\backend ; .\.venv\Scripts\python.exe -m app.cli lab-worker
# Ventana 3 — web
cd C:\Users\Theal\Desktop\BotTrade\frontend ; npm run preview
```
Abre **http://127.0.0.1:4173**, entra con tu usuario → **Estrategias → Probar una idea**. Para parar, Ctrl+C en cada ventana.

*(Si PowerShell dice que no puede ejecutar scripts, no hace falta: aquí se llama a los programas por su ruta, no se «activa» el entorno.)*

---
## Qué probar (guion de 10 minutos)

Es el mismo que en `docs/probar-laboratorio.md`: simulación por defecto, valores inválidos, iterar variantes hasta ver el aviso de comparaciones
múltiples, un resultado «No concluyente», y comprobar con un segundo usuario que no ve tus simulaciones.

## Qué NO es
Dinero ficticio. Una simulación histórica no predice nada y no existe ningún modo ni botón para operar con dinero real.
