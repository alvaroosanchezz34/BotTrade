import { expect, test as setup } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { PASSWORD } from "./helpers";

// Una sola sesión por rol, reutilizada por todos los tests (el login real está limitado a 10/min).
setup("sesiones de prueba", async ({ playwright, baseURL }) => {
  mkdirSync("e2e/.auth", { recursive: true });
  for (const [user, file] of [
    ["e2e-admin", "admin"],
    ["e2e-viewer", "viewer"],
  ] as const) {
    const ctx = await playwright.request.newContext({ baseURL });
    const r = await ctx.post("/api/auth/login", { data: { username: user, password: PASSWORD } });
    expect(r.ok(), `login de ${user}`).toBeTruthy();
    await ctx.storageState({ path: `e2e/.auth/${file}.json` });
    await ctx.dispose();
  }
});
