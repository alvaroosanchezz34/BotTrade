import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { ApiError } from "../api/client";
import { LoadingBlock } from "../components/ui";
import { RealtimeProvider } from "../hooks/realtime";
import { Home } from "../pages/Home";
import { Markets } from "../pages/Markets";
import { Operations } from "../pages/Operations";
import { Settings } from "../pages/Settings";
import { StrategiesPage, StrategyPage } from "../pages/Strategies";
import { AuthGate } from "./auth";
import { Shell } from "./Shell";

// El gráfico (lightweight-charts) solo se descarga al abrir un activo: mejor en redes móviles.
const MarketDetail = lazy(() => import("../pages/MarketDetail").then((m) => ({ default: m.MarketDetail })));

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        gcTime: 10 * 60_000,
        refetchOnWindowFocus: true,
        // Reintentar solo fallos de red/5XX; un 4XX (p. ej. 401/403/422) no mejora al repetirlo.
        retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
      },
    },
  });
}

export function App({ client = makeQueryClient() }: { client?: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <AuthGate>
          <RealtimeProvider>
            <Routes>
              <Route element={<Shell />}>
                <Route index element={<Home />} />
                <Route path="mercados" element={<Markets />} />
                <Route
                  path="mercados/:symbol"
                  element={
                    <Suspense fallback={<LoadingBlock />}>
                      <MarketDetail />
                    </Suspense>
                  }
                />
                <Route path="operaciones" element={<Operations />} />
                <Route path="estrategias" element={<StrategiesPage />} />
                <Route path="estrategias/:id" element={<StrategyPage />} />
                <Route path="configuracion" element={<Settings />} />
                <Route path="*" element={<div className="empty">Página no encontrada.</div>} />
              </Route>
            </Routes>
          </RealtimeProvider>
        </AuthGate>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
