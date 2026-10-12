# Seguridad

## Modelo

El sistema controla procesos de trading simulados pero se trata como sensible. El navegador
es solo supervisión y control remoto: **nunca** es el motor y **nunca** recibe claves de proveedores.

| Capa | Medida | Verificado por |
|---|---|---|
| Dinero real | `TradingMode` sin `LIVE`; hosts de órdenes en lista blanca cerrada; el arranque aborta si hay variables `BINANCE_API_KEY…` | `test_no_real_orders`, `test_config`, tests del adaptador de testnet |
| Autenticación | Argon2id; sesión opaca guardada solo como SHA-256; cookie `HttpOnly`, `Secure` en producción, `SameSite=Strict` | `test_auth_api` |
| CSRF | doble envío (`bt_csrf` ↔ `X-CSRF-Token`) ligado a la sesión | `test_api_control_security` |
| Fuerza bruta | bloqueo de cuenta (5 fallos → 15 min) + límite de 10 logins/min por IP; mensaje genérico; tiempo igualado si el usuario no existe | `test_auth_api` |
| Autorización | rol `admin` para mutaciones, auditoría y eventos; `viewer` solo lectura | `test_api_control_security` |
| Validación | Pydantic estricto (`extra=forbid`), consultas con patrones y rangos | `test_api_read` (parametrizados) |
| Límites de frecuencia | 30 acciones de control/min | `test_control_rate_limit` |
| CORS | solo orígenes configurados, `GET/POST`; en producción solo `https` explícito | `test_cors_*`, `test_config` |
| WebSocket | cookie + `Origin` + revalidación de sesión en cada ciclo | `test_ws_*` |
| Errores | nunca trazas al cliente (`{"detail":"Error interno"}`) | `test_internal_errors_*` |
| Cabeceras | `nosniff`, `X-Frame-Options: DENY`, `no-store`, HSTS y CSP (Caddy) en producción | e2e PWA |
| Producción | `Settings` rechaza: cookies no seguras, CORS no https, contraseña de BD débil; `/docs` oculto | `test_production_*` |
| Secretos | solo variables de entorno; `.env*` ignorados por Git; logs con redacción de campos sensibles | `test_core` |
| Auditoría | quién, qué y cuándo en `audit_log` (login, control, cambios) | tests de API |
| Contenedores | PostgreSQL y API **sin puertos publicados**; red `internal` sin salida; `cap_drop: ALL`, `read_only`, `no-new-privileges` | `docker compose config` |

## Política de datos locales y service worker (PWA)

* El service worker **nunca** cachea `/api/**`, `/ws` ni `/health` (red directa). Solo guarda
  recursos públicos inmutables (`/assets`, iconos) y una página `offline.html` genérica **sin datos
  del usuario**. Un test e2e inspecciona `caches` y falla si aparece cualquier ruta de la API.
* Para enseñar el «último estado conocido» al reabrir sin conexión, se guarda en `localStorage`
  (almacenamiento privado del origen, no un caché compartido) **solo el último resumen del panel**.
  Caduca a las 24 h, no contiene credenciales y se borra al cerrar sesión o expirar la sesión.
  Siempre se muestra marcado como «datos desactualizados».
* Las notificaciones push no están implementadas; las alertas se guardan en el servidor.

## Acceso desde el iPhone sin abrir puertos (recomendado: Tailscale)

1. Instala Tailscale en el VPS y en el iPhone (misma tailnet).
2. En el VPS: `tailscale cert maquina.tu-tailnet.ts.net` (o deja que Caddy use el certificado de Tailscale).
3. En `infra/.env.prod`: `SITE_ADDRESS=maquina.tu-tailnet.ts.net` y `WEB_BIND=<IP de Tailscale 100.x.y.z>`.
4. Firewall del VPS: **cierra** 80/443 al exterior; solo SSH (idealmente también por Tailscale).
5. En Safari del iPhone abre `https://maquina.tu-tailnet.ts.net` y añádela a la pantalla de inicio.

Alternativa pública: `SITE_ADDRESS=bot.tudominio.com`, `WEB_BIND=0.0.0.0`; Caddy obtiene el certificado
de Let's Encrypt. Es válida (hay HTTPS, sesión, CSRF y límites) pero amplía la superficie: no es lo recomendado.

## Rotación de credenciales

* **Contraseña de usuario**: Ajustes → Seguridad (revoca las demás sesiones).
* **Contraseña de PostgreSQL**: `ALTER USER bottrade PASSWORD '…'`, actualizar `POSTGRES_PASSWORD` y
  `BOTTRADE_DATABASE_URL` en `infra/.env.prod`, `docker compose … up -d`.
* **Claves del testnet**: regenerarlas en testnet.binance.vision, actualizar el `.env.prod`, reiniciar `engine`.
  No uses nunca claves de tu cuenta real; si el entorno las contiene (`BINANCE_API_KEY…`) el sistema no arranca.
* Tras sospecha de filtración: `UPDATE sessions SET revoked_at = now()` y cambia la contraseña.

## Dependencias

Versiones fijadas por rango compatible en `pyproject.toml` y `package-lock.json`. Revisar con
`pip-audit` / `npm audit` antes de cada despliegue (hoy `npm audit`: 0 vulnerabilidades conocidas al instalar).
