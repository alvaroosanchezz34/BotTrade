import { screen, waitFor } from "@testing-library/react";
import { Home } from "./Home";
import { overview } from "../test/fixtures";
import { mockFetch, renderWithProviders } from "../test/utils";

// Home usa useAuth(): se sustituye por un administrador fijo (el AuthGate real se prueba en e2e).
vi.mock("../app/auth", () => ({
  useAuth: () => ({ me: { username: "admin", role: "admin" }, isAdmin: true, logout: async () => {} }),
}));

function renderHome() {
  return renderWithProviders(<Home />);
}

describe("pantalla de inicio", () => {
  beforeEach(() => localStorage.clear());

  it("muestra las cifras reales del servidor y el origen de los datos", async () => {
    mockFetch({
      "/api/dashboard/overview": () => ({ body: overview() }),
      "/api/performance": () => ({ body: { equity_curve: [], sample_warning: null, overall: {} } }),
    });
    renderHome();
    expect(await screen.findByText("Operando en simulación")).toBeInTheDocument();
    expect(screen.getByText("Capital simulado")).toBeInTheDocument();
    expect(screen.getByText(/498,70 USDT/)).toBeInTheDocument();
    expect(screen.getByText(/Cuenta simulada local/)).toBeInTheDocument();
  });

  it("sin cuenta inicializada NO inventa cifras", async () => {
    mockFetch({
      "/api/dashboard/overview": () => ({
        body: overview({ account: null, drawdown: null, open_positions: null, engine: { status: "never_started", last_beat_at: null, age_seconds: null, state: null }, connections: [], last_data_at: null, risk: { ...overview().risk, state: null, usage: null } }),
      }),
      "/api/performance": () => ({ body: { equity_curve: [], sample_warning: null, overall: {} } }),
    });
    renderHome();
    expect(await screen.findByText(/Cuenta no disponible/)).toBeInTheDocument();
    expect(screen.queryByText("Capital simulado")).not.toBeInTheDocument();
    expect(screen.getByText("El engine no ha arrancado nunca")).toBeInTheDocument();
    expect(screen.getByText(/Sin conexiones registradas/)).toBeInTheDocument();
    expect(screen.getAllByText("no disponible").length).toBeGreaterThan(0);
  });

  it("si el endpoint falla y no hay datos, muestra el error sin valores de ejemplo", async () => {
    mockFetch({ "/api/dashboard/overview": () => ({ status: 500, body: { detail: "Error interno" } }) });
    renderHome();
    expect(await screen.findByText(/No se pudieron cargar los datos/)).toBeInTheDocument();
    expect(screen.queryByText(/USDT/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
  });

  it("alertas críticas con la acción recomendada y estado de kill switch visibles", async () => {
    const o = overview();
    o.critical_alerts = [{ id: "a1", code: "kill_switch", message: "Kill switch activado por admin", recommended_action: "Revisar la causa", last_seen_at: "2026-03-02T11:59:00Z", occurrences: 1 }];
    o.risk.state!.kill_switch_active = true;
    o.risk.state!.entries_allowed = false;
    mockFetch({
      "/api/dashboard/overview": () => ({ body: o }),
      "/api/performance": () => ({ body: { equity_curve: [], sample_warning: null, overall: {} } }),
    });
    renderHome();
    expect(await screen.findByText("Kill switch activo: entradas bloqueadas")).toBeInTheDocument();
    expect(screen.getByText(/Qué hacer: Revisar la causa/)).toBeInTheDocument();
  });

  it("marca los precios de valoración obsoletos", async () => {
    const o = overview();
    o.account!.marks_stale = true;
    mockFetch({
      "/api/dashboard/overview": () => ({ body: o }),
      "/api/performance": () => ({ body: { equity_curve: [], sample_warning: null, overall: {} } }),
    });
    renderHome();
    await waitFor(() => expect(screen.getByText(/Precios de valoración obsoletos/)).toBeInTheDocument());
  });

  it("al reabrir sin conexión muestra el último estado guardado marcado como desactualizado", async () => {
    localStorage.setItem("bottrade.lastOverview.v1", JSON.stringify({ savedAt: Date.now() - 3600_000, data: overview() }));
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    renderHome();
    expect(await screen.findByText("Operando en simulación")).toBeInTheDocument();
    expect(await screen.findByText(/Datos desactualizados/)).toBeInTheDocument();
  });
});
