# Probar el laboratorio (y la app) en tu ordenador

> **¿Docker no te arranca?** Usa `docs/probar-sin-docker.md` (GitHub Codespaces: sin instalar nada, o Windows nativo).

Todo con **dinero ficticio**, en tu máquina, sin servidores ni dominios. Tiempo: ~20–30 min la primera vez
(la mayoría es esperar a que se construya e instale).

> Estado de verificación: el código y los tests (backend, frontend y navegador) están verificados. Las **imágenes de Docker no se
> han podido construir en el entorno de desarrollo**: puede haber algún ajuste en el primer arranque. Si algo falla, copia el
> error (paso 9) y se corrige.

## Lo que necesitas
* **Docker Desktop** (Windows, Mac) o Docker Engine (Linux), instalado **y abierto**. Descarga: docker.com/products/docker-desktop.
  En Windows te pedirá activar WSL 2: acepta y reinicia si lo pide.
* **Git** (git-scm.com). 
* Unos 3 GB libres de disco y conexión a Internet.

## Pasos

**1. Descarga el código** (en una terminal: PowerShell en Windows, Terminal en Mac):
```bash
git clone https://github.com/alvaroosanchezz34/BotTrade.git
cd BotTrade
git checkout claude/peaceful-fermat-qo4tgr
```

**2. Arranca todo** (la primera vez tarda varios minutos: construye las imágenes):
```bash
docker compose -f infra/docker-compose.local.yml up -d --build
docker compose -f infra/docker-compose.local.yml ps
```
Deben aparecer `db`, `api`, `lab-worker` y `web` en estado *running* (y `migrate` terminado: *exited 0*).

**3. Crea tu usuario** (te pedirá una contraseña de al menos 12 caracteres):
```bash
docker compose -f infra/docker-compose.local.yml run --rm api python -m app.cli create-admin tu_usuario
```

**4. Descarga datos de mercado reales** (el laboratorio simula sobre ellos). Primero 5 minutos, que es lo rápido (~5 min):
```bash
docker compose -f infra/docker-compose.local.yml run --rm data python -m app.cli download-history --symbols BTCUSDT,ETHUSDT --intervals 5m --start 2026-04-01
```
*(Opcional, para velas de 1 minuto, unos minutos más:* `… --intervals 1m --start 2026-07-01` *)*

**5. Abre la app:** http://localhost:8080 → inicia sesión → pestaña **Estrategias** → **Probar una idea**.

## Qué probar (guion de 10 minutos)

1. **Simulación por defecto**: deja «Tendencia», BTCUSDT, 5m, 6 meses → *Simular con dinero ficticio*. Verás «En cola/Simulando» y,
   en ~15–30 s, el **informe**: veredicto, las 9 pruebas con ✓/✗, el **resultado final** (capital inicial → final), la curva, los tres tercios y la comparación con el azar.
2. **Valida el rigor**: ¿sale «Sin evidencia de ventaja»? Es lo esperable. Mira por qué falla cada prueba.
3. **Parámetros inválidos**: pon `fast` = 1 → el campo se marca en rojo y el botón se bloquea.
4. **Iterar**: pulsa *Probar otra variante*, cambia un número y simula. Repítelo 5 veces: en la 5.ª variante aparece el **aviso de comparaciones múltiples**.
5. **No concluyente**: elige 1 mes con parámetros muy exigentes → «No concluyente» (pocas o ninguna operación), *no* «sin ventaja».
6. **Privacidad**: crea otro usuario (`create-admin otro`) y comprueba que **no ve** tus simulaciones.
7. **Tus simulaciones**: vuelve a *Laboratorio*; verás el historial con el veredicto de cada una.

## Verlo desde el móvil (misma Wi-Fi)
1. Averigua la IP de tu ordenador (Windows: `ipconfig` → «Dirección IPv4»; Mac: Ajustes → Wi-Fi → Detalles).
2. Reinicia la web abierta a la red local:
   * Mac/Linux: `WEB_BIND=0.0.0.0 docker compose -f infra/docker-compose.local.yml up -d`
   * Windows (PowerShell): `$env:WEB_BIND="0.0.0.0"; docker compose -f infra/docker-compose.local.yml up -d`
3. En **Safari del iPhone**: `http://TU_IP:8080`. Inicia sesión. (Puede que el firewall del PC pregunte: permite en red privada.)
4. Esto va **sin HTTPS**: úsalo solo en tu casa. Para HTTPS y acceso desde fuera, mira `docs/deployment.md` (Tailscale).
   *(«Añadir a pantalla de inicio» funciona, pero la instalación como app completa y el modo sin conexión requieren HTTPS.)*

Para volver a cerrarlo a tu ordenador: `docker compose -f infra/docker-compose.local.yml up -d` (sin `WEB_BIND`).

## Parar y limpiar
```bash
docker compose -f infra/docker-compose.local.yml down        # para todo, conserva usuarios y datos
docker compose -f infra/docker-compose.local.yml down -v     # BORRA todo (usuarios, datos, informes)
```

## 9. Si algo falla
```bash
docker compose -f infra/docker-compose.local.yml logs --tail 60 api lab-worker web
docker compose -f infra/docker-compose.local.yml ps -a
```
Los fallos habituales: Docker Desktop no está abierto; el puerto 8080 ya está en uso (usa `WEB_PORT=8081`);
la descarga de datos falla por red (se puede relanzar: es idempotente y reanuda).

## Qué NO es esto
Una simulación histórica no predice nada. No opera con dinero real y no existe ningún botón para hacerlo.
