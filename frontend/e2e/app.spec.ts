import { expect, test } from "@playwright/test";
import {
  PASSWORD,
  csrfHeaders,
  ensureRunning,
  expectAccessible,
  expectNoHorizontalScroll,
  shot,
  waitLive,
} from "./helpers";

test.describe("acceso", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("login: credenciales erróneas se rechazan y las correctas entran", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Entrar" })).toBeVisible();
    await expect(page.getByText(/no opera con dinero real/)).toBeVisible();
    await page.getByLabel("Usuario").fill("e2e-admin");
    await page.getByLabel("Contraseña").fill("mala-contraseña-123");
    await page.getByRole("button", { name: "Entrar" }).click();
    await expect(page.getByText("Credenciales inválidas")).toBeVisible();
    await page.getByLabel("Contraseña").fill(PASSWORD);
    await page.getByRole("button", { name: "Entrar" }).click();
    await expect(page.getByRole("heading", { name: "Estado del bot" })).toBeVisible();
  });

  test("sin sesión no se ve ningún dato del bot", async ({ page }) => {
    await page.goto("/operaciones");
    await expect(page.getByRole("button", { name: "Entrar" })).toBeVisible();
    await expect(page.getByText("Capital simulado")).toHaveCount(0);
  });
});

test.describe("panel (administrador)", () => {
  test("la sesión caducada durante el uso devuelve al login con aviso", async ({ page, context }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Estado del bot" })).toBeVisible();
    await context.clearCookies();
    await page.getByRole("link", { name: /Mercados/ }).click();
    await expect(page.getByText(/sesión ha caducado/)).toBeVisible();
    await expect(page.getByText("Capital simulado")).toHaveCount(0);
  });

  test("inicio: modo visible, sin dinero real, cifras reales y sin desbordes", async ({ page }, info) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Estado del bot" })).toBeVisible();
    await expect(page.locator(".topbar").getByText("PAPER")).toBeVisible();
    await expect(page.locator(".topbar").getByText("Sin dinero real")).toBeVisible();
    await expect(page.getByText("Operando en simulación")).toBeVisible();
    for (const label of ["Capital simulado", "Equity", "Disponible", "P&L realizado", "P&L no realizado", "Drawdown", "Posiciones abiertas"]) {
      await expect(page.locator(".stat .label", { hasText: label }).first()).toBeVisible();
    }
    // La operación sembrada se cerró con pérdida: el P&L realizado es negativo y lleva signo.
    await expect(page.locator(".stat", { hasText: "P&L realizado" }).locator(".value")).toContainText("-");
    await expect(page.getByText("Simulado").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Conexiones" })).toBeVisible();
    await expect(page.getByText("e2e-static-feed")).toBeVisible();
    await expectNoHorizontalScroll(page);
    await shot(page, "inicio", info.project.name);
  });

  test("navegación: barra inferior en móvil, lateral en escritorio", async ({ page }, info) => {
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Principal" });
    await expect(nav.getByRole("link")).toHaveCount(5);
    const box = (await nav.boundingBox())!;
    const vp = page.viewportSize()!;
    if (info.project.name === "iphone") {
      expect(box.y + box.height).toBeGreaterThan(vp.height - 2); // pegada abajo
      expect(box.width).toBeGreaterThan(vp.width - 2);
      for (const l of await nav.getByRole("link").all()) {
        expect((await l.boundingBox())!.height).toBeGreaterThanOrEqual(44); // objetivo táctil
      }
    } else {
      expect(box.x).toBeLessThan(2);
      expect(box.width).toBeLessThan(260);
    }
    for (const name of ["Inicio", "Mercados", "Operaciones", "Estrategias", "Ajustes"]) {
      await expect(nav.getByRole("link", { name })).toBeVisible();
    }
  });

  test("mercados: precios reales guardados, filtros y gráfico táctil", async ({ page }, info) => {
    await page.goto("/mercados");
    const card = (sym: string) => page.locator(".card", { has: page.locator("b", { hasText: sym }) });
    await expect(card("BTCUSDT")).toBeVisible();
    await expect(card("BTCUSDT").getByText("Datos al día")).toBeVisible();
    await expect(card("ETHUSDT")).toBeVisible();
    await page.getByLabel("Activo").selectOption("ETHUSDT");
    await expect(card("BTCUSDT")).toHaveCount(0);
    await page.getByLabel("Activo").selectOption("");
    await expectNoHorizontalScroll(page);
    await shot(page, "mercados", info.project.name);
    await page.getByRole("link", { name: /BTCUSDT/ }).first().click();
    await expect(page.getByRole("img", { name: /Gráfico de velas BTCUSDT 1m/ })).toBeVisible();
    await expect(page.locator(".chart canvas").first()).toBeVisible();
    const chart = await page.locator(".chart").boundingBox();
    expect(chart!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    await page.getByRole("tab", { name: "5m" }).click();
    await expect(page.getByRole("img", { name: /BTCUSDT 5m/ })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await shot(page, "mercado-detalle", info.project.name);
  });

  test("operaciones: abiertas, historial neto, pendientes y rechazadas con detalle", async ({ page }, info) => {
    await page.goto("/operaciones");
    await expect(page.getByRole("heading", { name: "Posiciones abiertas" })).toBeVisible();
    await expect(page.locator(".item", { hasText: "ETHUSDT" }).first()).toBeVisible();
    await expect(page.getByText(/Stop .* Objetivo/).first()).toBeVisible();
    await shot(page, "operaciones-abiertas", info.project.name);

    await page.getByRole("tab", { name: "Historial" }).click();
    await expect(page.getByRole("heading", { name: /Operaciones cerradas/ })).toBeVisible();
    const trade = page.locator(".item", { hasText: "BTCUSDT" }).first();
    await expect(trade).toContainText("Comisiones");
    await expect(trade).toContainText("Entrada:");
    await expect(page.getByText(/muestra insuficiente|operaciones cerradas: muestra/)).toBeVisible();
    await shot(page, "operaciones-historial", info.project.name);

    await page.getByRole("tab", { name: "Rechazadas" }).click();
    const rejected = page.getByRole("button", { name: /Orden ETHUSDT REJECTED/ }).first();
    await expect(rejected).toBeVisible();
    await expect(rejected).toContainText("rechazo simulado");
    await rejected.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("heading", { name: "Detalle de la orden" })).toBeVisible();
    await expect(dialog.getByText("Señal que la originó")).toBeVisible();
    await expect(dialog.getByText("Historial de estados")).toBeVisible();
    await shot(page, "orden-detalle", info.project.name);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);

    await page.getByRole("tab", { name: "Pendientes" }).click();
    await expect(page.getByText("No hay órdenes pendientes.")).toBeVisible();
    await expectNoHorizontalScroll(page);
  });

  test("estrategias: resultados fuera de muestra honestos y parámetros de solo lectura", async ({ page }, info) => {
    await page.goto("/estrategias");
    await expect(page.getByText(/ninguna se considera rentable/)).toBeVisible();
    await expect(page.getByText("DESCARTADA").first()).toBeVisible();
    await shot(page, "estrategias", info.project.name);
    await page.getByRole("link", { name: /Momentum/ }).click();
    await expect(page.getByRole("heading", { name: "Parámetros (solo lectura)" })).toBeVisible();
    await expect(page.getByText(/fixture de test E2E/)).toBeVisible();
    await expect(page.getByText(/insuficiente para concluir nada/)).toBeVisible();
    await expect(page.locator("input[type=number], input[type=text]")).toHaveCount(0); // sin edición
    await expectNoHorizontalScroll(page);
  });

  test("laboratorio: validar, simular en el servidor y leer un informe honesto", async ({ page }, info) => {
    await page.goto("/estrategias");
    await page.getByRole("link", { name: "Probar una idea" }).click();
    await expect(page.getByRole("heading", { name: "Laboratorio" }).first()).toBeVisible();
    await expect(page.getByText(/Cuantas más variantes pruebes/)).toBeVisible();

    // Validación en el cliente: un valor fuera de rango bloquea el envío.
    const lookback = page.getByLabel("lookback", { exact: true });
    await lookback.fill("1");
    await expect(page.getByText(/Debe ser al menos 2/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Simular con dinero ficticio" })).toBeDisabled();
    await lookback.fill("30");
    await expectNoHorizontalScroll(page);
    await shot(page, "laboratorio-formulario", info.project.name);

    await page.getByRole("button", { name: "Simular con dinero ficticio" }).click();
    await expect(page).toHaveURL(/\/laboratorio\/[0-9a-f-]{36}$/);
    // El servidor simula en segundo plano (worker): se pasa de «en cola/simulando» al informe.
    await expect(page.getByRole("heading", { name: "Veredicto" })).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText(/Sin evidencia de ventaja|Prometedora|No concluyente/).first()).toBeVisible();
    await expect(page.getByText("Muestra suficiente")).toBeVisible();
    await expect(page.getByText("Supera a entradas aleatorias")).toBeVisible();
    await expect(page.getByText(/Simulación histórica con dinero ficticio/).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Resultado final" })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Estabilidad por periodos/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Comparaciones" })).toBeVisible();
    await expect(page.getByText(/Tu variante nº 1/)).toBeVisible();
    await expectNoHorizontalScroll(page);
    await shot(page, "laboratorio-informe", info.project.name);

    // Iterar: «Probar otra variante» conserva los parámetros y el contador de variantes sube.
    await page.getByRole("button", { name: "Probar otra variante" }).click();
    await expect(page.getByLabel("lookback", { exact: true })).toHaveValue("30");
    await page.getByLabel("lookback", { exact: true }).fill("40");
    await page.getByRole("button", { name: "Simular con dinero ficticio" }).click();
    await expect(page.getByText(/Tu variante nº 2/)).toBeVisible({ timeout: 120_000 });

    // El historial personal lista ambas y la simulación del laboratorio NO sale en los listados generales.
    await page.goto("/laboratorio");
    await expect(page.getByRole("heading", { name: "Tus simulaciones" })).toBeVisible();
    await expect(page.getByText(/variante nº 2/)).toBeVisible();
    await page.goto("/estrategias");
    await expect(page.getByText(/momentum · 5m/)).toHaveCount(0);
  });

  test("simulación histórica: resultado final con dinero ficticio, curva y veredicto", async ({ page }, info) => {
    await page.goto("/estrategias");
    const card = page.getByRole("heading", { name: /Simulaciones históricas/ });
    await expect(card).toBeVisible();
    // 500 + 12,5 − 3 − 8,25 + 4 − 1,5 − 6 = 497,75
    await expect(page.getByText(/500 → 497,75/)).toBeVisible();
    await shot(page, "simulaciones-lista", info.project.name);
    await page.getByRole("link", { name: /momentum · 1m/ }).click();
    await expect(page.getByText(/Simulación histórica con dinero ficticio/).first()).toBeVisible();
    const final = page.locator(".stat", { hasText: "Capital final" });
    await expect(final).toContainText("497,75 USDT");
    await expect(final).toContainText("-0,45 %");
    await expect(page.locator(".stat", { hasText: "Operaciones cerradas" })).toContainText("6");
    await expect(page.getByRole("img", { name: "Curva de capital" })).toBeVisible();
    await expect(page.getByText(/DESCARTADA: fixture/)).toBeVisible();
    await expectNoHorizontalScroll(page);
    await shot(page, "simulacion-resultado", info.project.name);
  });

  test("activar y desactivar una estrategia: solicitado → confirmado por el engine", async ({ page }) => {
    await page.goto("/estrategias/momentum");
    await page.getByRole("button", { name: "Activar estrategia" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Solicitar activación" }).click();
    await expect(page.getByText(/Confirmada por el engine/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Activa", { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: "Cerrar" }).click();
    await page.getByRole("button", { name: "Desactivar estrategia" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Solicitar desactivación" }).click();
    await expect(page.getByText(/Confirmada por el engine/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Desactivada", { exact: true }).first()).toBeVisible();
  });

  test("kill switch: confirmación obligatoria, estado confirmado por el engine y reanudación con comprobaciones", async ({ page }, info) => {
    await ensureRunning(page);
    await page.goto("/configuracion");
    await page.getByRole("button", { name: "Kill switch" }).click();
    const dialog = page.getByRole("dialog");
    const confirm = dialog.getByRole("button", { name: "Activar kill switch" });
    await expect(confirm).toBeDisabled();
    await shot(page, "kill-switch-dialogo", info.project.name);
    await dialog.getByRole("checkbox", { name: /Entiendo que se bloquean/ }).check();
    await expect(dialog.getByRole("checkbox", { name: /liquidar/ })).not.toBeChecked();
    await confirm.click();
    await expect(page.getByText(/Confirmada por el engine/)).toBeVisible({ timeout: 20_000 });

    await page.goto("/");
    await expect(page.getByText("Kill switch activo: entradas bloqueadas")).toBeVisible();
    await expect(page.getByText("Kill switch activado por e2e-admin")).toBeVisible();
    await shot(page, "inicio-kill-switch", info.project.name);

    await page.getByRole("button", { name: "Reanudar" }).click();
    const d2 = page.getByRole("dialog");
    await expect(d2.getByRole("button", { name: "Solicitar reanudación" })).toBeDisabled();
    await d2.getByRole("checkbox").check();
    await d2.getByRole("button", { name: "Solicitar reanudación" }).click();
    await expect(page.getByText(/Confirmada por el engine/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Operando en simulación")).toBeVisible({ timeout: 15_000 });
  });

  test("tiempo real: un cambio hecho desde otra sesión aparece sin recargar", async ({ page }) => {
    await ensureRunning(page);
    await page.goto("/");
    await waitLive(page);
    await expect(page.getByText("Operando en simulación")).toBeVisible();
    // «Otro dispositivo» pausa el bot por la API.
    const other = await page.context().browser()!.newContext({ baseURL: "http://127.0.0.1:4173" });
    const login = await other.request.post("/api/auth/login", { data: { username: "e2e-admin", password: PASSWORD } });
    expect(login.ok()).toBeTruthy();
    const r = await other.request.post("/api/bot/pause", { data: {}, headers: await csrfHeaders(other.request) });
    expect(r.status()).toBe(202);
    await expect(page.getByText("Pausado: sin nuevas entradas")).toBeVisible({ timeout: 20_000 });
    await other.close();
    await ensureRunning(page);
    await expect(page.getByText("Operando en simulación")).toBeVisible({ timeout: 20_000 });
  });

  test("sin conexión: avisa de datos desactualizados, conserva lo último y se recupera", async ({ page, context }, info) => {
    await page.goto("/");
    await expect(page.getByText("Operando en simulación")).toBeVisible();
    await context.setOffline(true);
    await expect(page.getByText("Sin conexión").first()).toBeVisible();
    await expect(page.getByText(/Datos desactualizados/)).toBeVisible();
    await expect(page.getByText("Capital simulado")).toBeVisible(); // último estado conocido
    await shot(page, "sin-conexion", info.project.name);
    await context.setOffline(false);
    await expect(page.getByText(/Datos desactualizados/)).toHaveCount(0, { timeout: 30_000 });
    await expect(page.getByText("● En vivo")).toBeVisible({ timeout: 30_000 });
  });

  test("ajustes: estado, límites de riesgo solo lectura, zona horaria, instalación y sin dinero real", async ({ page }, info) => {
    await page.goto("/configuracion");
    await expect(page.getByRole("heading", { name: "Estado del sistema" })).toBeVisible();
    await expect(page.getByText("Dinero real: desactivado")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Límites de riesgo (solo lectura)" })).toBeVisible();
    await expect(page.getByText(/1× \(solo spot, largos\)/)).toBeVisible();
    await expect(page.getByText(/notificaciones push no están disponibles/i)).toBeVisible();
    await expect(page.getByText(/Añadir a pantalla de inicio/)).toBeVisible();
    await expect(page.getByRole("button", { name: /dinero real|live|producción/i })).toHaveCount(0);
    await page.getByLabel(/Las horas se guardan en UTC/).selectOption("America/New_York");
    await expect(page.getByLabel(/Las horas se guardan en UTC/)).toHaveValue("America/New_York");
    await page.getByRole("button", { name: "Mostrar eventos y auditoría" }).click();
    await expect(page.getByText("engine_started").first()).toBeVisible();
    await page.getByLabel(/Las horas se guardan en UTC/).selectOption("Europe/Madrid");
    await expectNoHorizontalScroll(page);
    await shot(page, "ajustes", info.project.name);
  });

  for (const scheme of ["light", "dark"] as const) {
    test(`accesibilidad (axe) en modo ${scheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      for (const path of ["/", "/mercados", "/operaciones", "/estrategias", "/configuracion"]) {
        await page.goto(path);
        await expect(page.getByRole("navigation", { name: "Principal" })).toBeVisible();
        await page.waitForLoadState("networkidle");
        await expectAccessible(page);
      }
    });
  }
});

test.describe("solo lectura", () => {
  test.use({ storageState: "e2e/.auth/viewer.json" });

  test("un usuario viewer no puede controlar el bot (UI ni API)", async ({ page }) => {
    await page.goto("/configuracion");
    await expect(page.getByText(/solo lectura: no puedes pausar/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Kill switch" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Mostrar eventos y auditoría" })).toHaveCount(0);
    const r = await page.request.post("/api/bot/pause", { data: {}, headers: await csrfHeaders(page.request) });
    expect(r.status()).toBe(403);
    await page.goto("/estrategias/momentum");
    await expect(page.getByRole("button", { name: /Activar estrategia/ })).toHaveCount(0);
  });
});

test.describe("PWA", () => {
  test("manifest instalable, service worker activo y /api nunca en caché", async ({ page }) => {
    const manifest = await (await page.request.get("/manifest.webmanifest")).json();
    expect(manifest.display).toBe("standalone");
    expect(manifest.icons.length).toBeGreaterThanOrEqual(3);
    for (const icon of manifest.icons) expect((await page.request.get(icon.src)).ok()).toBeTruthy();
    expect((await page.request.get("/icons/apple-touch-icon.png")).ok()).toBeTruthy();

    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Estado del bot" })).toBeVisible();
    const swReady = await page.evaluate(async () => {
      const reg = await navigator.serviceWorker.ready;
      return !!reg.active;
    });
    expect(swReady).toBe(true);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Estado del bot" })).toBeVisible();
    const cached = await page.evaluate(async () => {
      const urls: string[] = [];
      for (const name of await caches.keys()) {
        for (const req of await (await caches.open(name)).keys()) urls.push(new URL(req.url).pathname);
      }
      return urls;
    });
    expect(cached.some((u) => u.startsWith("/api/") || u === "/ws")).toBe(false);
    expect(cached).toContain("/offline.html");
    // Cabeceras de seguridad del backend a través del proxy.
    const h = (await page.request.get("/api/auth/me")).headers();
    expect(h["cache-control"]).toBe("no-store");
    expect(h["x-content-type-options"]).toBe("nosniff");
  });

  test("sin red, abrir la app muestra la página offline genérica (sin datos del usuario)", async ({ page, context }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Estado del bot" })).toBeVisible();
    await page.evaluate(() => navigator.serviceWorker.ready);
    await context.setOffline(true);
    await page.goto("/operaciones");
    await expect(page.getByRole("heading", { name: "Sin conexión" })).toBeVisible();
    await expect(page.getByText("Capital simulado")).toHaveCount(0);
    await context.setOffline(false);
  });
});
