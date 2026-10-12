import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

type Handler = (url: URL, init: RequestInit) => { status?: number; body?: unknown } | Promise<{ status?: number; body?: unknown }>;

export interface MockFetch {
  calls: { url: string; method: string; headers: Record<string, string>; body: unknown }[];
  set: (path: string, h: Handler) => void;
}

/** fetch simulado por ruta. Una ruta sin manejador responde 404 (la prueba lo notará). */
export function mockFetch(routes: Record<string, Handler> = {}): MockFetch {
  const table = new Map(Object.entries(routes));
  const m: MockFetch = { calls: [], set: (p, h) => void table.set(p, h) };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const url = new URL(input, "http://localhost");
      m.calls.push({
        url: url.pathname + url.search,
        method: init.method ?? "GET",
        headers: (init.headers ?? {}) as Record<string, string>,
        body: init.body ? JSON.parse(String(init.body)) : undefined,
      });
      const h = table.get(url.pathname);
      if (!h) return new Response(JSON.stringify({ detail: "no mock" }), { status: 404 });
      const r = await h(url, init);
      return new Response(r.status === 204 ? null : JSON.stringify(r.body ?? {}), {
        status: r.status ?? 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return m;
}

export function renderWithProviders(ui: ReactElement, { route = "/" } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}
