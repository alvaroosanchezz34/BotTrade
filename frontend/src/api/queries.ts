import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePollingInterval } from "../hooks/realtime";
import { loadOverview, saveOverview } from "../services/snapshot";
import { api, withQuery } from "./client";
import type {
  Alert,
  BacktestDetail,
  BacktestListItem,
  Candles,
  Command,
  CommandAccepted,
  LabJobOut,
  LabOptions,
  LabRequest,
  MarketRow,
  Me,
  Order,
  OrderDetail,
  Overview,
  Page,
  Performance,
  Position,
  RiskDetail,
  Signal,
  StrategyDetail,
  StrategyListItem,
  SystemStatus,
  Trade,
} from "./types";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => api<Me>("/api/auth/me"),
    retry: false,
    staleTime: 5 * 60_000,
  });
}

export function useOverview() {
  const refetchInterval = usePollingInterval();
  const stored = loadOverview();
  return useQuery({
    queryKey: ["overview"],
    queryFn: async () => {
      const data = await api<Overview>("/api/dashboard/overview");
      saveOverview(data);
      return data;
    },
    refetchInterval,
    // Al reabrir la app se muestra el último estado conocido, marcado como desactualizado.
    initialData: stored?.data,
    initialDataUpdatedAt: stored?.savedAt,
  });
}

export function useMarkets(symbol?: string, strategy?: string) {
  const refetchInterval = usePollingInterval();
  return useQuery({
    queryKey: ["markets", symbol ?? "", strategy ?? ""],
    queryFn: () => api<{ as_of: string; markets: MarketRow[] }>(withQuery("/api/markets", { symbol, strategy })),
    refetchInterval,
    placeholderData: keepPreviousData,
  });
}

export function useCandles(symbol: string, interval: "1m" | "5m") {
  const refetchInterval = usePollingInterval();
  return useQuery({
    queryKey: ["markets", "candles", symbol, interval],
    queryFn: () => api<Candles>(withQuery(`/api/markets/${symbol}/candles`, { interval, limit: 300 })),
    refetchInterval,
  });
}

function usePaged<T>(key: string, path: string, query: Record<string, string | undefined>, pageSize = 25) {
  const refetchInterval = usePollingInterval();
  return useInfiniteQuery({
    queryKey: [key, query],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api<Page<T>>(withQuery(path, { ...query, limit: pageSize, offset: pageParam })),
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.limit : undefined),
    refetchInterval,
  });
}

export const useOrders = (q: Record<string, string | undefined>) => usePaged<Order>("orders", "/api/orders", q);
export const useTrades = (q: Record<string, string | undefined>) => usePaged<Trade>("trades", "/api/trades", q);
export const usePositions = (q: Record<string, string | undefined>) =>
  usePaged<Position>("positions", "/api/positions", q);
export const useSignals = (q: Record<string, string | undefined>) => usePaged<Signal>("signals", "/api/signals", q);
export const useAlerts = (q: Record<string, string | undefined>) => usePaged<Alert>("alerts", "/api/alerts", q);

export function useOrderDetail(id: string | null) {
  return useQuery({
    queryKey: ["orders", "detail", id],
    queryFn: () => api<OrderDetail>(`/api/orders/${id}`),
    enabled: id !== null,
  });
}

export function usePerformance() {
  const refetchInterval = usePollingInterval();
  return useQuery({
    queryKey: ["performance"],
    queryFn: () => api<Performance>("/api/performance"),
    refetchInterval,
  });
}

export function useStrategies() {
  const refetchInterval = usePollingInterval();
  return useQuery({
    queryKey: ["strategies"],
    queryFn: () => api<{ strategies: StrategyListItem[]; disclaimer: string }>("/api/strategies"),
    refetchInterval,
  });
}

export function useStrategy(id: string) {
  return useQuery({
    queryKey: ["strategies", id],
    queryFn: () => api<StrategyDetail>(`/api/strategies/${id}`),
    refetchInterval: usePollingInterval(),
  });
}

export function useRisk() {
  const refetchInterval = usePollingInterval();
  return useQuery({ queryKey: ["risk"], queryFn: () => api<RiskDetail>("/api/risk"), refetchInterval });
}

export function useSystem() {
  const refetchInterval = usePollingInterval();
  return useQuery({ queryKey: ["system"], queryFn: () => api<SystemStatus>("/api/system/status"), refetchInterval });
}

export function useEvents(enabled: boolean) {
  return useQuery({
    queryKey: ["events"],
    queryFn: () => api<{ items: { id: number; at: string; kind: string; source: string; detail: unknown }[] }>("/api/events?limit=30"),
    enabled,
  });
}

export function useAudit(enabled: boolean) {
  return useQuery({
    queryKey: ["audit"],
    queryFn: () =>
      api<{ items: { id: number; at: string; actor: string; action: string; target: string }[] }>("/api/audit?limit=30"),
    enabled,
  });
}

export function useCommand(id: string | null) {
  return useQuery({
    queryKey: ["commands", id],
    queryFn: () => api<Command>(`/api/commands/${id}`),
    enabled: id !== null,
    // Sondeo corto mientras esté pendiente: la acción solo cuenta como hecha cuando el engine la confirma.
    refetchInterval: (q) => (q.state.data?.status === "PENDING" || !q.state.data ? 1000 : false),
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { username: string; password: string }) => api<Me>("/api/auth/login", { method: "POST", body: b }),
    onSuccess: (me) => qc.setQueryData(["me"], me),
  });
}

export function useLogout() {
  return useMutation({ mutationFn: () => api<void>("/api/auth/logout", { method: "POST", body: {} }) });
}

export function useAckAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api(`/api/alerts/${id}/ack`, { method: "POST", body: {} }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });
}

export type ControlRequest =
  | { kind: "pause" }
  | { kind: "resume"; clearKillSwitch: boolean }
  | { kind: "kill"; liquidate: boolean }
  | { kind: "strategy"; id: string; enable: boolean };

export function sendControl(req: ControlRequest): Promise<CommandAccepted> {
  switch (req.kind) {
    case "pause":
      return api("/api/bot/pause", { method: "POST", body: {} });
    case "resume":
      return api("/api/bot/resume", { method: "POST", body: { clear_kill_switch: req.clearKillSwitch } });
    case "kill":
      return api("/api/bot/kill-switch", {
        method: "POST",
        body: { confirm: "KILL", liquidate_positions: req.liquidate },
      });
    case "strategy":
      return api(`/api/strategies/${encodeURIComponent(req.id)}/${req.enable ? "enable" : "disable"}`, {
        method: "POST",
        body: {},
      });
  }
}

export function useBacktests() {
  return useQuery({
    queryKey: ["backtests"],
    queryFn: () => api<Page<BacktestListItem>>("/api/backtests?limit=50"),
  });
}

export function useBacktest(id: string) {
  return useQuery({
    queryKey: ["backtests", id],
    queryFn: () => api<BacktestDetail>(`/api/backtests/${id}`),
  });
}

export function useLabOptions() {
  return useQuery({ queryKey: ["lab", "options"], queryFn: () => api<LabOptions>("/api/lab/options"), staleTime: 60_000 });
}

export function useLabJobs() {
  return useQuery({
    queryKey: ["lab", "jobs"],
    queryFn: () => api<Page<LabJobOut>>("/api/lab/jobs?limit=20"),
    refetchInterval: (q) => (q.state.data?.items.some((j) => j.status === "QUEUED" || j.status === "RUNNING") ? 3000 : false),
  });
}

/** Sondeo corto mientras la simulación está en cola o en ejecución; se detiene al terminar. */
export function useLabJob(id: string) {
  return useQuery({
    queryKey: ["lab", "jobs", id],
    queryFn: () => api<LabJobOut>(`/api/lab/jobs/${id}`),
    refetchInterval: (q) => (q.state.data && (q.state.data.status === "DONE" || q.state.data.status === "FAILED") ? false : 2000),
  });
}

export function useCreateLabJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: LabRequest) =>
      api<{ job_id: string; status: string; variants_tried: number }>("/api/lab/jobs", { method: "POST", body: b }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lab", "jobs"] }),
  });
}
