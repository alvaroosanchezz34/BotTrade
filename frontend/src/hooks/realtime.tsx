import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import type { Pulse } from "../api/types";
import { RealtimeClient, defaultWsUrl, type RtSnapshot } from "../services/realtime";

const Ctx = createContext<RealtimeClient | null>(null);

/** Qué consultas se refrescan cuando cambia cada campo del pulso. */
export function keysToInvalidate(p: Pulse, prev: Pulse | null, first: boolean): string[][] {
  if (first || !prev) return [["all"]];
  const keys: string[][] = [];
  if (p.last_order_at !== prev.last_order_at) {
    keys.push(["orders"], ["positions"], ["trades"], ["overview"], ["performance"], ["risk"]);
  }
  if (p.last_signal_at !== prev.last_signal_at) keys.push(["signals"], ["markets"]);
  if (p.paused !== prev.paused || p.kill_switch !== prev.kill_switch || p.entries_allowed !== prev.entries_allowed) {
    keys.push(["overview"], ["risk"]);
  }
  if (p.critical_alerts !== prev.critical_alerts) keys.push(["alerts"], ["overview"]);
  if (p.engine !== prev.engine) keys.push(["overview"], ["system"]);
  if (p.equity !== prev.equity) keys.push(["overview"]);
  if (JSON.stringify(p.commands) !== JSON.stringify(prev.commands)) keys.push(["commands"], ["overview"], ["risk"]);
  return keys;
}

export function RealtimeProvider({ children, client }: { children: ReactNode; client?: RealtimeClient }) {
  const qc = useQueryClient();
  const rt = useMemo(() => client ?? new RealtimeClient(defaultWsUrl), [client]);

  useEffect(() => {
    const off = rt.onPulse((p, prev, first) => {
      for (const key of keysToInvalidate(p, prev, first)) {
        if (key[0] === "all") void qc.invalidateQueries();
        else void qc.invalidateQueries({ queryKey: key });
      }
    });
    rt.start();
    const onOnline = () => rt.retryNow();
    const onVisible = () => document.visibilityState === "visible" && rt.retryNow();
    window.addEventListener("online", onOnline);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      off();
      window.removeEventListener("online", onOnline);
      document.removeEventListener("visibilitychange", onVisible);
      rt.stop();
    };
  }, [rt, qc]);

  return <Ctx.Provider value={rt}>{children}</Ctx.Provider>;
}

const EMPTY: RtSnapshot = { state: "idle", pulse: null, lastMessageAt: null, attempt: 0 };

export function useRealtime(): RtSnapshot {
  const rt = useContext(Ctx);
  return useSyncExternalStore(
    rt ? rt.subscribe : () => () => {},
    rt ? rt.getSnapshot : () => EMPTY,
  );
}

/** Intervalo de sondeo: lento si el canal en vivo funciona; más rápido si hay que suplirlo. */
export function usePollingInterval(): number {
  const { state } = useRealtime();
  return state === "live" ? 60_000 : 15_000;
}
