import { UNAUTHORIZED_EVENT } from "../api/client";
import type { Pulse } from "../api/types";
import { keysToInvalidate } from "../hooks/realtime";
import { RealtimeClient, backoffMs } from "./realtime";

class FakeSocket {
  static all: FakeSocket[] = [];
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeSocket.all.push(this);
  }
  send(pulse: unknown) {
    this.onmessage?.({ data: JSON.stringify(pulse) } as MessageEvent);
  }
  drop(code = 1006) {
    this.onclose?.({ code } as CloseEvent);
  }
  close() {
    this.closed = true;
  }
}

const pulse = (over: Partial<Pulse> = {}): Pulse => ({
  mode: "PAPER", engine: "running", paused: false, kill_switch: false, entries_allowed: true, equity: "500",
  marks_stale: false, open_positions: 0, critical_alerts: 0, last_order_at: null, last_signal_at: null, commands: [],
  ...over,
});

function make() {
  FakeSocket.all = [];
  const timers: { fn: () => void; ms: number }[] = [];
  const c = new RealtimeClient(() => "ws://x/ws", {
    createSocket: (u) => new FakeSocket(u) as unknown as WebSocket,
    setTimer: (fn, ms) => (timers.push({ fn, ms }), timers.length as unknown as ReturnType<typeof setTimeout>),
    clearTimer: () => {},
    random: () => 0,
  });
  return { c, timers };
}

describe("RealtimeClient", () => {
  it("backoff exponencial acotado", () => {
    expect([1, 2, 3, 4, 10].map((a) => backoffMs(a, () => 0))).toEqual([1000, 2000, 4000, 8000, 30000]);
  });

  it("pasa a «live» con el primer pulso y marca el primero tras conectar", () => {
    const { c } = make();
    const seen: boolean[] = [];
    c.onPulse((_p, _prev, first) => seen.push(first));
    c.start();
    expect(c.getSnapshot().state).toBe("connecting");
    const ws = FakeSocket.all[0]!;
    ws.send({ type: "pulse", data: pulse() });
    ws.send({ type: "pulse", data: pulse({ paused: true }) });
    expect(c.getSnapshot().state).toBe("live");
    expect(seen).toEqual([true, false]);
  });

  it("reconecta tras una caída con backoff y vuelve a pedir el estado completo", () => {
    const { c, timers } = make();
    const seen: boolean[] = [];
    c.onPulse((_p, _prev, first) => seen.push(first));
    c.start();
    FakeSocket.all[0]!.send({ type: "pulse", data: pulse() });
    FakeSocket.all[0]!.drop();
    expect(c.getSnapshot().state).toBe("reconnecting");
    expect(timers.at(-1)!.ms).toBe(1000);
    timers.at(-1)!.fn();
    expect(FakeSocket.all).toHaveLength(2);
    FakeSocket.all[1]!.send({ type: "pulse", data: pulse() });
    expect(seen).toEqual([true, true]); // «primero tras conectar» ⇒ invalidar todo
    expect(c.getSnapshot().attempt).toBe(0);
  });

  it("los fallos repetidos aumentan el retardo", () => {
    const { c, timers } = make();
    c.start();
    for (let i = 0; i < 3; i++) {
      FakeSocket.all.at(-1)!.drop();
      timers.at(-1)!.fn();
    }
    expect(timers.map((t) => t.ms)).toEqual([1000, 2000, 4000]);
  });

  it("un cierre 1008 (sesión/origen) no reintenta y avisa de sesión caducada", () => {
    const { c, timers } = make();
    const handler = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, handler);
    c.start();
    FakeSocket.all[0]!.drop(1008);
    expect(c.getSnapshot().state).toBe("stopped");
    expect(timers).toHaveLength(0);
    expect(handler).toHaveBeenCalled();
    window.removeEventListener(UNAUTHORIZED_EVENT, handler);
  });

  it("auth_expired desde el servidor también avisa", () => {
    const { c } = make();
    const handler = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, handler);
    c.start();
    FakeSocket.all[0]!.send({ type: "auth_expired" });
    expect(handler).toHaveBeenCalled();
    window.removeEventListener(UNAUTHORIZED_EVENT, handler);
  });

  it("ignora mensajes basura y stop() cierra sin reconectar", () => {
    const { c, timers } = make();
    c.start();
    FakeSocket.all[0]!.onmessage?.({ data: "{no json" } as MessageEvent);
    expect(c.getSnapshot().pulse).toBeNull();
    c.stop();
    FakeSocket.all[0]!.drop();
    expect(timers).toHaveLength(0);
    expect(c.getSnapshot().state).toBe("stopped");
  });
});

describe("qué consultas se refrescan con cada pulso", () => {
  it("primer pulso o reconexión ⇒ todo", () => {
    expect(keysToInvalidate(pulse(), null, true)).toEqual([["all"]]);
    expect(keysToInvalidate(pulse(), pulse(), true)).toEqual([["all"]]);
  });
  it("sin cambios ⇒ nada", () => {
    expect(keysToInvalidate(pulse(), pulse(), false)).toEqual([]);
  });
  it("nueva orden ⇒ órdenes, posiciones, operaciones y riesgo", () => {
    const keys = keysToInvalidate(pulse({ last_order_at: "2026-01-01T00:00:00Z" }), pulse(), false).map((k) => k[0]);
    expect(keys).toEqual(expect.arrayContaining(["orders", "positions", "trades", "overview", "risk"]));
  });
  it("cambio de estado de un comando ⇒ comandos y panel", () => {
    const keys = keysToInvalidate(
      pulse({ commands: [{ id: "1", command: "PAUSE", status: "APPLIED", result: "ok" }] }),
      pulse({ commands: [{ id: "1", command: "PAUSE", status: "PENDING", result: null }] }),
      false,
    ).map((k) => k[0]);
    expect(keys).toEqual(expect.arrayContaining(["commands", "overview", "risk"]));
  });
});
