/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// El navegador solo habla con el mismo origen: /api y /ws se reenvían al backend.
// Así la cookie de sesión es de primer nivel (SameSite=Strict) y no hace falta CORS.
const BACKEND = process.env.BOTTRADE_BACKEND ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": { target: BACKEND, changeOrigin: false },
  "/health": { target: BACKEND, changeOrigin: false },
  "/ws": { target: BACKEND.replace("http", "ws"), ws: true, changeOrigin: false },
};

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, proxy },
  preview: { host: "127.0.0.1", port: 4173, proxy },
  build: { target: "es2022", sourcemap: false, chunkSizeWarningLimit: 700 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
