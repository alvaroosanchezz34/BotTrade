import { defineConfig, devices } from "@playwright/test";

// Chromium preinstalado en el entorno de desarrollo (no se descarga nada).
const chromium = process.env.PW_CHROMIUM ?? "/opt/pw-browsers/chromium";
const DB = process.env.E2E_DATABASE_URL ?? "postgresql+asyncpg://bottrade:bottrade_dev@127.0.0.1:5432/bottrade_e2e";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1, // comparten backend y BD de prueba: los tests que cambian estado lo restauran
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  outputDir: "e2e/.results",
  use: {
    baseURL: "http://127.0.0.1:4173",
    launchOptions: { executablePath: chromium, args: ["--no-sandbox"] },
    trace: "retain-on-failure",
    locale: "es-ES",
    timezoneId: "Europe/Madrid",
  },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      // iPhone 13 emulado sobre Chromium (WebKit no está disponible aquí): tamaño, táctil y UA.
      name: "iphone",
      testIgnore: /auth\.setup\.ts/,
      dependencies: ["setup"],
      use: { ...devices["iPhone 13"], browserName: "chromium", defaultBrowserType: "chromium", storageState: "e2e/.auth/admin.json" },
    },
    {
      name: "desktop",
      testIgnore: /auth\.setup\.ts/,
      dependencies: ["setup"],
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 }, storageState: "e2e/.auth/admin.json" },
    },
  ],
  webServer: [
    {
      command: "../backend/.venv/bin/python -m tests.e2e_support.run_e2e",
      cwd: "../backend",
      url: "http://127.0.0.1:8000/health",
      timeout: 90_000,
      reuseExistingServer: false,
      env: { BOTTRADE_DATABASE_URL: DB, BOTTRADE_ENVIRONMENT: "development", BOTTRADE_LOG_JSON: "false", PYTHONPATH: "." },
    },
    {
      command: "npm run build && npm run preview",
      url: "http://127.0.0.1:4173",
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
});
