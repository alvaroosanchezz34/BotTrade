import AxeBuilder from "@axe-core/playwright";
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";

export const PASSWORD = "e2e-password-123";

export async function apiLogin(page: Page, user = "e2e-admin"): Promise<void> {
  const r = await page.request.post("/api/auth/login", { data: { username: user, password: PASSWORD } });
  expect(r.ok()).toBeTruthy();
}

export async function csrfHeaders(request: APIRequestContext): Promise<Record<string, string>> {
  const state = await request.storageState();
  const csrf = state.cookies.find((c) => c.name === "bt_csrf")?.value ?? "";
  return { "X-CSRF-Token": csrf };
}

export async function shot(page: Page, name: string, project: string): Promise<void> {
  mkdirSync("e2e/screenshots", { recursive: true });
  await page.screenshot({ path: `e2e/screenshots/${project}-${name}.png`, fullPage: true });
}

export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "la página no debe desbordar horizontalmente").toBeLessThanOrEqual(1);
}

/** Accesibilidad automática (axe): ninguna violación grave o crítica. */
export async function expectAccessible(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(
    bad.map((v) => `${v.id}: ${v.help} → ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`),
  ).toEqual([]);
}

/** Espera a que el canal en vivo esté activo (insignia «En vivo»). */
export async function waitLive(page: Page): Promise<void> {
  await expect(page.getByText("● En vivo")).toBeVisible({ timeout: 15_000 });
}

/** Asegura un estado de partida conocido (sin pausa/kill) antes de un test que lo cambia. */
export async function ensureRunning(page: Page): Promise<void> {
  const h = await csrfHeaders(page.request);
  const risk = await (await page.request.get("/api/risk")).json();
  if (risk.state?.kill_switch_active || risk.state?.paused) {
    await page.request.post("/api/bot/resume", { data: { clear_kill_switch: true }, headers: h });
    await expect
      .poll(async () => (await (await page.request.get("/api/risk")).json()).state.entries_allowed, { timeout: 15_000 })
      .toBe(true);
  }
}
