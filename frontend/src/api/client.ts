/** Cliente HTTP mínimo. Mismo origen, cookie de sesión HttpOnly y CSRF por doble envío. */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public retryAfter?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
  /** 0 = sin respuesta del servidor (red caída, DNS, timeout...). */
  get isNetwork(): boolean {
    return this.status === 0;
  }
}

export const UNAUTHORIZED_EVENT = "bottrade:unauthorized";

function readCookie(name: string): string | undefined {
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return m ? decodeURIComponent(m[1] ?? "") : undefined;
}

type Query = Record<string, string | number | boolean | undefined | null>;

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `${path}?${s}` : path;
}

interface Options {
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
}

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    const token = readCookie("bt_csrf");
    if (token) headers["X-CSRF-Token"] = token;
  }
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "Sin conexión con el servidor");
  }
  if (res.status === 401) {
    // Un 401 en las rutas de acceso es esperado (no hay sesión / credenciales erróneas):
    // solo cuenta como «sesión caducada» si ocurre durante el uso normal de la app.
    if (!path.startsWith("/api/auth/")) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    throw new ApiError(401, path.startsWith("/api/auth/login") ? "Credenciales inválidas" : "Sesión no válida o caducada");
  }
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* cuerpo vacío o no JSON */
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    const msg = typeof detail === "string" ? detail : `Error ${res.status}`;
    const ra = Number(res.headers.get("retry-after"));
    throw new ApiError(res.status, msg, Number.isFinite(ra) && ra > 0 ? ra : undefined);
  }
  return data as T;
}
