/* Service worker de BotTrade — política de caché EXPLÍCITA (docs/security.md):
 *
 *  - /api/**, /ws, /health  → SIEMPRE a la red. Nunca se guardan respuestas autenticadas.
 *  - Navegaciones           → red primero; si no hay red, página sin conexión genérica
 *                             (sin datos de usuario).
 *  - /assets/** (hasheados) e iconos → caché (contenido público e inmutable).
 */
const VERSION = "v1";
const STATIC = `bottrade-static-${VERSION}`;
const OFFLINE_URL = "/offline.html";
const PRECACHE = [OFFLINE_URL, "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(STATIC).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== STATIC).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // API, WebSocket y health: red directa, jamás caché.
  if (url.pathname.startsWith("/api/") || url.pathname === "/ws" || url.pathname.startsWith("/health")) return;

  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/")) {
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(STATIC).then((c) => c.put(req, copy));
            }
            return res;
          }),
      ),
    );
  }
});
