/** Formato de importes y fechas. El Decimal del servidor solo se convierte a número para MOSTRAR. */

const KEY_TZ = "bottrade.tz";

export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function getTimeZone(): string {
  try {
    return localStorage.getItem(KEY_TZ) || browserTimeZone();
  } catch {
    return browserTimeZone();
  }
}

export function setTimeZone(tz: string): void {
  try {
    localStorage.setItem(KEY_TZ, tz);
  } catch {
    /* modo privado: se usa la zona del navegador */
  }
  window.dispatchEvent(new Event("bottrade:tz"));
}

export function isValidTimeZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("es-ES", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

export const NA = "—";

export function num(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export function money(v: string | number | null | undefined, digits = 2, currency = "USDT"): string {
  const n = num(v);
  if (n === null) return NA;
  const s = new Intl.NumberFormat("es-ES", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n);
  return currency ? `${s} ${currency}` : s;
}

export function signedMoney(v: string | number | null | undefined, digits = 2): string {
  const n = num(v);
  if (n === null) return NA;
  const s = new Intl.NumberFormat("es-ES", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    signDisplay: "exceptZero",
  }).format(n);
  return `${s} USDT`;
}

export function price(v: string | number | null | undefined): string {
  const n = num(v);
  if (n === null) return NA;
  const digits = Math.abs(n) >= 1000 ? 2 : Math.abs(n) >= 1 ? 4 : 6;
  return new Intl.NumberFormat("es-ES", {
    minimumFractionDigits: 2,
    maximumFractionDigits: digits,
  }).format(n);
}

export function qty(v: string | number | null | undefined): string {
  const n = num(v);
  if (n === null) return NA;
  return new Intl.NumberFormat("es-ES", { maximumFractionDigits: 8 }).format(n);
}

export function pct(v: string | number | null | undefined, digits = 2, signed = true): string {
  const n = num(v);
  if (n === null) return NA;
  return (
    new Intl.NumberFormat("es-ES", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
      signDisplay: signed ? "exceptZero" : "auto",
    }).format(n) + " %"
  );
}

export function ratio(v: string | number | null | undefined, digits = 2): string {
  const n = num(v);
  return n === null ? NA : n.toFixed(digits);
}

export function dateTime(iso: string | null | undefined, tz = getTimeZone()): string {
  if (!iso) return NA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return NA;
  return new Intl.DateTimeFormat("es-ES", {
    timeZone: tz,
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(d);
}

export function timeOnly(iso: string | null | undefined, tz = getTimeZone()): string {
  if (!iso) return NA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return NA;
  return new Intl.DateTimeFormat("es-ES", {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(d);
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return NA;
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 5) return "ahora";
  if (s < 60) return `hace ${s} s`;
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  return `hace ${Math.floor(s / 86400)} d`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return NA;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} h`;
  return `${(seconds / 86400).toFixed(1)} d`;
}

/** Clase de color para un importe con signo (el color nunca es la única señal: lleva signo). */
export function tone(v: string | number | null | undefined): "pos" | "neg" | "flat" {
  const n = num(v);
  if (n === null || n === 0) return "flat";
  return n > 0 ? "pos" : "neg";
}
