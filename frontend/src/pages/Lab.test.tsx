import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import type { LabJobOut, LabOptions, LabReport } from "../api/types";
import { mockFetch, renderWithProviders } from "../test/utils";
import { Lab, validateParam } from "./Lab";
import { LabJob } from "./LabJob";

const options: LabOptions = {
  strategies: [
    {
      id: "momentum", name: "Momentum con volumen", description: "Ruptura con volumen",
      params: [
        { name: "lookback", type: "integer", default: 20, min: 2, min_exclusive: false, max: 1000, description: "Barras previas" },
        { name: "stop_atr", type: "number", default: 1.5, min: 0, min_exclusive: true, max: 20, description: "Stop en ATR" },
      ],
    },
    { id: "trend", name: "Tendencia", description: "EMA", params: [{ name: "fast", type: "integer", default: 20, min: 2, min_exclusive: false, max: 2000, description: "" }] },
  ],
  symbols: ["BTCUSDT", "ETHUSDT"], intervals: ["1m", "5m"], months: [1, 3, 6, 12], max_months: { "1m": 6, "5m": 12 },
  cost_levels: { normal: { taker_fee: "0.001", slippage: "0.0002", spread: "0.0001" }, conservador: { taker_fee: "0.0015", slippage: "0.0004", spread: "0.0002" } },
  data: { "1m": {}, "5m": { BTCUSDT: { from: "2026-01-01T00:00:00Z", to: "2026-01-10T00:00:00Z", bars: 2600 } } },
  limits: { active_jobs_per_user: 1, jobs_per_hour: 10, min_trades_for_verdict: 100 },
  disclaimer: "Simulación histórica con dinero ficticio. No predice resultados futuros ni es asesoramiento.",
};

describe("validateParam", () => {
  const lookback = options.strategies[0]!.params[0]!;
  const stop = options.strategies[0]!.params[1]!;
  it("valida tipo, rango y límites exclusivos", () => {
    expect(validateParam(lookback, "20")).toBeNull();
    expect(validateParam(lookback, "")).toBe("Obligatorio");
    expect(validateParam(lookback, "abc")).toBe("Debe ser un número");
    expect(validateParam(lookback, "2.5")).toBe("Debe ser un entero");
    expect(validateParam(lookback, "1")).toMatch(/al menos 2/);
    expect(validateParam(lookback, "1001")).toMatch(/Máximo 1000/);
    expect(validateParam(stop, "0")).toMatch(/mayor que 0/);
    expect(validateParam(stop, "0.1")).toBeNull();
  });
});

function renderLab() {
  return renderWithProviders(
    <Routes>
      <Route path="/laboratorio" element={<Lab />} />
      <Route path="/laboratorio/:id" element={<div>página del informe</div>} />
    </Routes>,
    { route: "/laboratorio" },
  );
}

describe("formulario del laboratorio", () => {
  beforeEach(() => {
    document.cookie = "bt_csrf=tok; path=/";
  });

  it("muestra solo plantillas del catálogo, rangos y datos disponibles", async () => {
    mockFetch({ "/api/lab/options": () => ({ body: options }), "/api/lab/jobs": () => ({ body: { items: [], total: 0, limit: 20, offset: 0 } }) });
    renderLab();
    expect(await screen.findByLabelText("Estrategia")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Momentum con volumen" })).toBeInTheDocument();
    expect(screen.getByText(/BTCUSDT 2\.?600 velas/)).toBeInTheDocument();
    expect(screen.queryByLabelText("ETHUSDT")).not.toBeInTheDocument(); // sin datos: no se ofrece
    expect(screen.getByText(/rango 2…1000/)).toBeInTheDocument();
    expect(screen.getByText(/Cuantas más variantes pruebes/)).toBeInTheDocument();
    expect(screen.getByText(/Todavía no has lanzado ninguna/)).toBeInTheDocument();
  });

  it("un parámetro fuera de rango muestra el error y bloquea el envío", async () => {
    const m = mockFetch({ "/api/lab/options": () => ({ body: options }), "/api/lab/jobs": () => ({ body: { items: [], total: 0, limit: 20, offset: 0 } }) });
    const user = userEvent.setup();
    renderLab();
    const input = await screen.findByLabelText("lookback");
    await user.clear(input);
    await user.type(input, "1");
    expect(await screen.findByText(/Debe ser al menos 2/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Simular con dinero ficticio" })).toBeDisabled();
    await user.clear(input);
    await user.type(input, "30");
    expect(screen.getByRole("button", { name: "Simular con dinero ficticio" })).toBeEnabled();
    expect(m.calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("envía la petición validada y navega al informe", async () => {
    const m = mockFetch({
      "/api/lab/options": () => ({ body: options }),
      "/api/lab/jobs": (_u, init) => (init.method === "POST" ? { status: 202, body: { job_id: "j1", status: "QUEUED", variants_tried: 1 } } : { body: { items: [], total: 0, limit: 20, offset: 0 } }),
    });
    const user = userEvent.setup();
    renderLab();
    const input = await screen.findByLabelText("lookback");
    await user.clear(input);
    await user.type(input, "35");
    await user.click(screen.getByRole("radio", { name: "Conservador" }));
    await user.click(screen.getByRole("button", { name: "Simular con dinero ficticio" }));
    expect(await screen.findByText("página del informe")).toBeInTheDocument();
    const post = m.calls.find((c) => c.method === "POST")!;
    expect(post.body).toEqual({
      strategy_id: "momentum", symbols: ["BTCUSDT"], interval: "5m", months: 3, cost_level: "conservador",
      params: { lookback: 35, stop_atr: 1.5 },
    });
    expect(post.headers["X-CSRF-Token"]).toBe("tok");
  });

  it("los errores del servidor (ya hay una en curso) se muestran tal cual", async () => {
    mockFetch({
      "/api/lab/options": () => ({ body: options }),
      "/api/lab/jobs": (_u, init) => (init.method === "POST" ? { status: 409, body: { detail: "Ya tienes una simulación en curso. Espera a que termine." } } : { body: { items: [], total: 0, limit: 20, offset: 0 } }),
    });
    const user = userEvent.setup();
    renderLab();
    await user.click(await screen.findByRole("button", { name: "Simular con dinero ficticio" }));
    expect(await screen.findByText(/Ya tienes una simulación en curso/)).toBeInTheDocument();
  });

  it("1 minuto limita el periodo máximo a 6 meses", async () => {
    mockFetch({ "/api/lab/options": () => ({ body: { ...options, data: { ...options.data, "1m": { BTCUSDT: { from: "x", to: "y", bars: 99999 } } } } }), "/api/lab/jobs": () => ({ body: { items: [], total: 0, limit: 20, offset: 0 } }) });
    const user = userEvent.setup();
    renderLab();
    await user.click(await screen.findByRole("radio", { name: "1m" }));
    expect(screen.getByRole("radio", { name: "12 meses" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "6 meses" })).toBeEnabled();
  });
});

const report = (over: Partial<LabReport> = {}): LabReport => ({
  verdict: "SIN EVIDENCIA DE VENTAJA",
  checks: [
    { name: "Muestra suficiente", passed: true, detail: "250 operaciones cerradas" },
    { name: "Resultado neto positivo", passed: false, detail: "neto -42.00 USDT tras costes" },
  ],
  spec: { strategy_id: "momentum", symbols: ["BTCUSDT"], interval: "5m", start: "2026-01-01T00:00:00Z", end: "2026-01-10T00:00:00Z", params: { lookback: 20 }, cost_level: "normal" },
  effective_params: {},
  main: { trades: 250, net_pnl: -42, fees: 90, win_rate: 0.3, profit_factor: 0.6, expectancy: -0.17, expectancy_ci95: [-0.3, -0.03], max_drawdown_pct: 9, return_pct: -8.4, final_equity: 458 },
  thirds: [
    { label: "Tercio 1", start: "", end: "", trades: 80, net_pnl: -10, profit_factor: 0.8, return_pct: -2 },
    { label: "Tercio 2", start: "", end: "", trades: 90, net_pnl: -20, profit_factor: 0.5, return_pct: -4 },
    { label: "Tercio 3", start: "", end: "", trades: 80, net_pnl: -12, profit_factor: 0.7, return_pct: -2.4 },
  ],
  stress: { trades: 240, net_pnl: -60, fees: 120, win_rate: 0.25, profit_factor: 0.4, expectancy: -0.25, expectancy_ci95: null, max_drawdown_pct: 12, return_pct: -12, final_equity: 440 },
  limited: { trades: 60, net_pnl: -15, fees: 20, win_rate: 0.3, profit_factor: 0.6, expectancy: -0.25, expectancy_ci95: null, max_drawdown_pct: 5.1, return_pct: -3, final_equity: 485 },
  random: { n: 10, net_min: -30, net_median: -12, net_max: 5 },
  buy_hold: { return_pct: 3.2 },
  variants_tried: 1, multiple_testing_note: null, data: { hash: "abcdef0123456789", bars: 2600 },
  caveats: ["Simulación histórica con dinero ficticio: no predice resultados futuros."],
  ...over,
});

const job = (over: Partial<LabJobOut> = {}): LabJobOut => ({
  id: "j1", status: "DONE", progress: "Terminada", created_at: "2026-10-14T10:00:00Z", started_at: "2026-10-14T10:00:01Z",
  finished_at: "2026-10-14T10:00:30Z", spec: report().spec, variants_tried: 1, run_id: null, error: null, verdict: "SIN EVIDENCIA DE VENTAJA",
  report: report(), ...over,
});

function renderJob(j: LabJobOut) {
  mockFetch({ "/api/lab/jobs/j1": () => ({ body: j }) });
  return renderWithProviders(
    <Routes>
      <Route path="/laboratorio/:id" element={<LabJob />} />
      <Route path="/laboratorio" element={<div>formulario</div>} />
    </Routes>,
    { route: "/laboratorio/j1" },
  );
}

describe("informe del laboratorio", () => {
  it("muestra el veredicto con cada prueba (✓/✗ con texto, no solo color)", async () => {
    renderJob(job());
    expect(await screen.findByText("Sin evidencia de ventaja")).toBeInTheDocument();
    expect(screen.getByText("1 de 2 pruebas fallidas")).toBeInTheDocument();
    const failed = screen.getByText("Resultado neto positivo").closest("li")!;
    expect(within(failed).getByText("no superada")).toBeInTheDocument();
    expect(within(failed).getByText(/neto -42/)).toBeInTheDocument();
    expect(screen.getByText(/Estabilidad por periodos/i)).toBeInTheDocument();
    expect(screen.getByText(/Entradas al azar \(10 pruebas\)/)).toBeInTheDocument();
    expect(screen.getByText(/Comprar y mantener/)).toBeInTheDocument();
    expect(screen.getByText(/variante nº 1/)).toBeInTheDocument();
  });

  it("con 0 operaciones el veredicto es «No concluyente», nunca «sin ventaja»", async () => {
    renderJob(job({ verdict: "NO CONCLUYENTE", report: report({ verdict: "NO CONCLUYENTE", main: { ...report().main, trades: 0, net_pnl: 0 } }) }));
    expect(await screen.findByText("No concluyente")).toBeInTheDocument();
    expect(screen.queryByText("Sin evidencia de ventaja")).not.toBeInTheDocument();
    expect(screen.getByText(/no generó ninguna operación/)).toBeInTheDocument();
    expect(screen.getByText(/ni a favor ni en contra/)).toBeInTheDocument();
  });

  it("con pocas operaciones explica que puede ser casualidad", async () => {
    renderJob(job({ verdict: "NO CONCLUYENTE", report: report({ verdict: "NO CONCLUYENTE", main: { ...report().main, trades: 37 } }) }));
    expect(await screen.findByText(/Solo hubo 37 operaciones/)).toBeInTheDocument();
  });

  it("«Prometedora» nunca se presenta como éxito: etiqueta de aviso y advertencia explícita", async () => {
    renderJob(job({ verdict: "PROMETEDORA", report: report({ verdict: "PROMETEDORA", checks: [{ name: "Muestra suficiente", passed: true, detail: "ok" }] }) }));
    expect(await screen.findByText("Prometedora")).toBeInTheDocument();
    expect(screen.getByText("Pasa los filtros").className).toContain("warn");
    expect(screen.getByText(/NO prueba que gane dinero/)).toBeInTheDocument();
  });

  it("avisa de las comparaciones múltiples cuando hay muchas variantes", async () => {
    renderJob(job({ report: report({ variants_tried: 8, multiple_testing_note: "Has probado 8 variantes de esta idea. Con tantas pruebas, que una salga bien por pura casualidad es mucho más probable." }) }));
    expect(await screen.findByText(/Has probado 8 variantes/)).toBeInTheDocument();
  });

  it("mientras simula muestra progreso y que se puede cerrar la pantalla", async () => {
    renderJob(job({ status: "RUNNING", progress: "Simulando (16 pruebas)", report: null, verdict: null }));
    expect(await screen.findByText("Simulando (16 pruebas)")).toBeInTheDocument();
    expect(screen.getByText(/Puedes cerrar la pantalla/)).toBeInTheDocument();
    expect(screen.queryByText(/Veredicto/)).not.toBeInTheDocument();
  });

  it("si falla, muestra el motivo y permite volver a intentarlo", async () => {
    renderJob(job({ status: "FAILED", error: "No hay datos o reglas de instrumento para BTCUSDT", report: null, verdict: null }));
    expect(await screen.findByText(/La simulación ha fallado/)).toBeInTheDocument();
    expect(screen.getByText(/No hay datos o reglas/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("link", { name: "Volver a intentarlo" })).toBeInTheDocument());
  });
});
