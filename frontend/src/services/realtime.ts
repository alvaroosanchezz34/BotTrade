/**
 * Canal en tiempo real. El WebSocket solo *avisa*: la verdad se pide por REST.
 *
 * - Reconexión con backoff exponencial + jitter.
 * - Al (re)conectar llega un pulso completo: el consumidor refresca todo (recuperación de estado).
 * - Mientras no esté "live", la app cae a sondeo periódico (ver ``pollingInterval``).
 * - Un cierre 1008 (sesión no válida / origen) detiene el canal y avisa de sesión caducada.
 */
import { UNAUTHORIZED_EVENT } from "../api/client";
import type { Pulse } from "../api/types";

export type RtState = "idle" | "connecting" | "live" | "reconnecting" | "stopped";

export interface RtSnapshot {
  state: RtState;
  pulse: Pulse | null;
  lastMessageAt: number | null;
  attempt: number;
}

type Listener = () => void;
type PulseHandler = (pulse: Pulse, previous: Pulse | null, isFirstAfterConnect: boolean) => void;

export interface RealtimeDeps {
  createSocket?: (url: string) => WebSocket;
  setTimer?: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  clearTimer?: (t: ReturnType<typeof setTimeout>) => void;
  random?: () => number;
}

export function backoffMs(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(30_000, 1000 * 2 ** Math.max(0, attempt - 1));
  return Math.round(base + random() * base * 0.25);
}

export class RealtimeClient {
  private snap: RtSnapshot = { state: "idle", pulse: null, lastMessageAt: null, attempt: 0 };
  private listeners = new Set<Listener>();
  private handlers = new Set<PulseHandler>();
  private ws: WebSocket | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private firstAfterConnect = true;
  private stoppedByUser = false;
  private readonly deps: Required<RealtimeDeps>;

  constructor(
    private readonly url: () => string,
    deps: RealtimeDeps = {},
  ) {
    this.deps = {
      createSocket: deps.createSocket ?? ((u) => new WebSocket(u)),
      setTimer: deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms)),
      clearTimer: deps.clearTimer ?? ((t) => clearTimeout(t)),
      random: deps.random ?? Math.random,
    };
  }

  getSnapshot = (): RtSnapshot => this.snap;

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };

  onPulse(h: PulseHandler): () => void {
    this.handlers.add(h);
    return () => this.handlers.delete(h);
  }

  private set(patch: Partial<RtSnapshot>): void {
    this.snap = { ...this.snap, ...patch };
    this.listeners.forEach((l) => l());
  }

  start(): void {
    this.stoppedByUser = false;
    if (this.ws || this.timer) return;
    this.connect();
  }

  stop(): void {
    this.stoppedByUser = true;
    if (this.timer) this.deps.clearTimer(this.timer);
    this.timer = null;
    this.ws?.close(1000);
    this.ws = null;
    this.set({ state: "stopped", pulse: null });
  }

  /** Útil al volver a estar online o al recuperar el foco de la app. */
  retryNow(): void {
    if (this.stoppedByUser || this.snap.state === "live" || this.snap.state === "connecting") return;
    if (this.timer) this.deps.clearTimer(this.timer);
    this.timer = null;
    this.connect();
  }

  private connect(): void {
    this.timer = null;
    this.set({ state: this.snap.attempt === 0 ? "connecting" : "reconnecting" });
    let ws: WebSocket;
    try {
      ws = this.deps.createSocket(this.url());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    this.firstAfterConnect = true;
    ws.onmessage = (ev: MessageEvent) => {
      let msg: { type?: string; data?: Pulse };
      try {
        msg = JSON.parse(String(ev.data));
      } catch {
        return;
      }
      if (msg.type === "auth_expired") {
        window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
        return;
      }
      if (msg.type === "pulse" && msg.data) {
        const previous = this.snap.pulse;
        const first = this.firstAfterConnect;
        this.firstAfterConnect = false;
        this.set({ state: "live", pulse: msg.data, lastMessageAt: Date.now(), attempt: 0 });
        this.handlers.forEach((h) => h(msg.data as Pulse, previous, first));
      }
    };
    ws.onclose = (ev: CloseEvent) => {
      this.ws = null;
      if (this.stoppedByUser) return;
      if (ev.code === 1008) {
        // Sesión no válida u origen rechazado: reintentar no sirve.
        this.set({ state: "stopped" });
        window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
        return;
      }
      this.scheduleReconnect();
    };
    ws.onerror = () => {
      /* el cierre llega después y dispara la reconexión */
    };
  }

  private scheduleReconnect(): void {
    const attempt = this.snap.attempt + 1;
    this.set({ state: "reconnecting", attempt });
    this.timer = this.deps.setTimer(() => this.connect(), backoffMs(attempt, this.deps.random));
  }
}

export function defaultWsUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws`;
}
