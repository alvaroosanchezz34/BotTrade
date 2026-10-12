import { ApiError, UNAUTHORIZED_EVENT, api, withQuery } from "./client";
import { mockFetch } from "../test/utils";

describe("cliente API", () => {
  beforeEach(() => {
    document.cookie = "bt_csrf=tok123; path=/";
  });

  it("las peticiones POST llevan el CSRF de la cookie y no cachean", async () => {
    const m = mockFetch({ "/api/bot/pause": () => ({ status: 202, body: { ok: true } }) });
    await api("/api/bot/pause", { method: "POST", body: {} });
    expect(m.calls[0]?.headers["X-CSRF-Token"]).toBe("tok123");
    expect((fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0]![1].cache).toBe("no-store");
    expect((fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0]![1].credentials).toBe("same-origin");
  });

  it("las lecturas no envían CSRF", async () => {
    const m = mockFetch({ "/api/risk": () => ({ body: {} }) });
    await api("/api/risk");
    expect(m.calls[0]?.headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("un 401 dispara el evento de sesión caducada", async () => {
    mockFetch({ "/api/risk": () => ({ status: 401, body: { detail: "No autenticado" } }) });
    const h = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, h);
    await expect(api("/api/risk")).rejects.toMatchObject({ status: 401 });
    expect(h).toHaveBeenCalled();
    window.removeEventListener(UNAUTHORIZED_EVENT, h);
  });

  it("convierte fallos de red en ApiError(0)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fallo")));
    const err = (await api("/api/risk").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.isNetwork).toBe(true);
  });

  it("propaga el mensaje del servidor y Retry-After en 429", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Demasiadas peticiones" }), { status: 429, headers: { "retry-after": "12" } })),
    );
    const err = (await api("/api/bot/pause", { method: "POST", body: {} }).catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(429);
    expect(err.retryAfter).toBe(12);
  });

  it("withQuery omite vacíos y codifica", () => {
    expect(withQuery("/api/x", { a: "1 2", b: undefined, c: "", d: 0 })).toBe("/api/x?a=1+2&d=0");
  });
});
