/**
 * Política de datos locales (docs/security.md): el service worker NUNCA cachea /api.
 * Para poder enseñar el «último estado conocido» al reabrir la app sin conexión se guarda,
 * en el almacenamiento privado del origen, solo el último resumen del panel de inicio.
 * Caduca a las 24 h, no contiene credenciales y se borra al cerrar sesión.
 */
import type { Overview } from "../api/types";

const KEY = "bottrade.lastOverview.v1";
const MAX_AGE_MS = 24 * 3600 * 1000;

export interface StoredOverview {
  savedAt: number;
  data: Overview;
}

export function saveOverview(data: Overview): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ savedAt: Date.now(), data } satisfies StoredOverview));
  } catch {
    /* sin almacenamiento: la app funciona igual */
  }
}

export function loadOverview(now = Date.now()): StoredOverview | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredOverview;
    if (typeof parsed.savedAt !== "number" || now - parsed.savedAt > MAX_AGE_MS) {
      localStorage.removeItem(KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function clearStoredData(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nada que borrar */
  }
}
