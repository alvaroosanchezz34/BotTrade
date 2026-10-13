import { Route, Routes } from "react-router-dom";
import { screen } from "@testing-library/react";
import type { BacktestDetail } from "../api/types";
import { mockFetch, renderWithProviders } from "../test/utils";
import { Simulation } from "./Simulation";

const detail = (over: Partial<BacktestDetail["summary"]> = {}, notes = "DESCARTADA: sin ventaja"): BacktestDetail => ({
  id: "r1", created_at: "2026-10-13T20:00:00Z", strategy_id: "trend", strategy_version: "1.0.0", symbols: ["BTCUSDT"],
  interval: "5m", start: "2026-07-18T00:00:00Z", end: "2026-09-29T00:00:00Z", segment: "test", notes, data_hash: "abcdef0123456789abcd",
  seed: 0, disclaimer: "Simulación histórica con dinero ficticio. No predice resultados futuros.",
  summary: {
    initial_capital: "500", final_capital: "480", net_pnl: "-20", return_pct: "-4", closed_trades: 5, win_rate: 0.4,
    max_drawdown: "34", max_drawdown_pct: "6.7", best_trade: "10", worst_trade: "-30",
    curve: [{ at: null, equity: "500" }, { at: "2026-08-01T00:00:00Z", equity: "480" }], ...over,
  },
  trades: [{ symbol: "BTCUSDT", entry_time: "2026-08-01T00:00:00Z", exit_time: "2026-08-01T01:00:00Z", entry_price: "100", exit_price: "99", quantity: "1", fees: "0.2", net_pnl: "-1.2", exit_reason: "stop" }],
});

function renderSim(d: BacktestDetail) {
  mockFetch({ "/api/backtests/r1": () => ({ body: d }) });
  return renderWithProviders(
    <Routes>
      <Route path="/simulaciones/:id" element={<Simulation />} />
    </Routes>,
    { route: "/simulaciones/r1" },
  );
}

describe("pantalla de simulación", () => {
  it("muestra capital inicial→final, la etiqueta de dinero ficticio y el veredicto", async () => {
    renderSim(detail());
    expect(await screen.findByText("Resultado final")).toBeInTheDocument();
    const stat = (label: string) => screen.getByText(label).closest(".stat") as HTMLElement;
    expect(stat("Capital inicial")).toHaveTextContent("500,00 USDT");
    expect(stat("Capital final")).toHaveTextContent("480,00 USDT");
    expect(stat("Capital final")).toHaveTextContent("-4,00 %");
    expect(screen.getByText(/dinero ficticio/)).toBeInTheDocument();
    expect(screen.getByText("Simulado")).toBeInTheDocument();
    expect(screen.getByText(/DESCARTADA: sin ventaja/)).toBeInTheDocument();
    expect(screen.getByText(/no incluye posiciones abiertas/i)).toBeInTheDocument();
  });

  it("una simulación sin operaciones no inventa porcentajes", async () => {
    renderSim(detail({ closed_trades: 0, win_rate: null, best_trade: null, worst_trade: null, net_pnl: "0", final_capital: "500", return_pct: "0", curve: [{ at: null, equity: "500" }] }, ""));
    expect(await screen.findByText("Resultado final")).toBeInTheDocument();
    expect(screen.queryByText(/acierto/)).not.toBeInTheDocument();
    expect(screen.getByText(/Aún no hay suficientes instantáneas/)).toBeInTheDocument();
  });
});
