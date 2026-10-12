/**
 * Controles remotos. Regla de oro (sección 19): una acción SOLICITADA no es una acción CONFIRMADA.
 *
 *   idle → sending → requested (el servidor la encoló) → confirmed | rejected | unconfirmed
 *
 * Solo «confirmed» (el engine respondió APPLIED) se presenta como hecho. Los botones quedan
 * bloqueados mientras haya una solicitud en curso (evita acciones duplicadas por doble toque).
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ConfirmDialog, type Confirmation } from "../components/ConfirmDialog";
import { Banner, errorText } from "../components/ui";
import { sendControl, useCommand, type ControlRequest } from "../api/queries";

export type ControlPhase =
  | { phase: "idle" }
  | { phase: "sending" }
  | { phase: "requested"; id: string; engineAlive: boolean; detail: string }
  | { phase: "confirmed"; result: string }
  | { phase: "rejected"; reason: string }
  | { phase: "unconfirmed"; detail: string }
  | { phase: "error"; message: string };

export const CONFIRM_TIMEOUT_MS = 45_000;

export function useControl() {
  const qc = useQueryClient();
  const [state, setState] = useState<ControlPhase>({ phase: "idle" });
  const requestedId = state.phase === "requested" ? state.id : null;
  const cmd = useCommand(requestedId);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (state.phase !== "requested") return;
    const c = cmd.data;
    if (c?.status === "APPLIED") {
      setState({ phase: "confirmed", result: c.result ?? "Aplicado" });
      void qc.invalidateQueries();
    } else if (c?.status === "REJECTED") {
      setState({ phase: "rejected", reason: c.result ?? "Rechazado por el engine" });
      void qc.invalidateQueries();
    }
  }, [cmd.data, state, qc]);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (state.phase !== "requested") return;
    timer.current = setTimeout(() => {
      setState((s) =>
        s.phase === "requested"
          ? {
              phase: "unconfirmed",
              detail: s.engineAlive
                ? "El engine no ha confirmado la acción todavía. Comprueba el estado antes de repetirla."
                : "El engine no responde: la acción NO está confirmada.",
            }
          : s,
      );
    }, CONFIRM_TIMEOUT_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [state]);

  async function run(req: ControlRequest) {
    if (state.phase === "sending" || state.phase === "requested") return; // anti doble toque
    setState({ phase: "sending" });
    try {
      const r = await sendControl(req);
      setState({ phase: "requested", id: r.command_id, engineAlive: r.engine_alive, detail: r.detail });
    } catch (e) {
      setState({ phase: "error", message: errorText(e) });
    }
  }

  return { state, run, reset: () => setState({ phase: "idle" }), busy: state.phase === "sending" || state.phase === "requested" };
}

export function ControlFeedback({ state, onClose }: { state: ControlPhase; onClose: () => void }) {
  if (state.phase === "idle") return null;
  const close = (
    <button className="btn small" onClick={onClose} style={{ marginTop: 8 }}>
      Cerrar
    </button>
  );
  switch (state.phase) {
    case "sending":
      return <Banner kind="info">Enviando solicitud…</Banner>;
    case "requested":
      return (
        <Banner kind="warn" role="status">
          <b>Solicitada, pendiente de confirmación.</b> {state.detail}
        </Banner>
      );
    case "confirmed":
      return (
        <Banner kind="info" role="status">
          <b>Confirmada por el engine.</b> {state.result}
          <div>{close}</div>
        </Banner>
      );
    case "rejected":
      return (
        <Banner kind="bad">
          <b>Rechazada.</b> {state.reason}
          <div>{close}</div>
        </Banner>
      );
    case "unconfirmed":
      return (
        <Banner kind="bad">
          <b>Sin confirmar.</b> {state.detail}
          <div>{close}</div>
        </Banner>
      );
    case "error":
      return (
        <Banner kind="bad">
          <b>No se pudo enviar.</b> {state.message}
          <div>{close}</div>
        </Banner>
      );
  }
}

interface Props {
  entriesAllowed: boolean;
  paused: boolean;
  killSwitch: boolean;
  isAdmin: boolean;
}

/** Pausa / reanudar / kill switch con confirmación y estado «solicitado → confirmado». */
export function BotControls({ paused, killSwitch, isAdmin }: Props) {
  const ctl = useControl();
  const [dialog, setDialog] = useState<null | "kill" | "resume" | "pause">(null);

  if (!isAdmin) return <Banner kind="info">Tu cuenta es de solo lectura: no puedes pausar ni reanudar.</Banner>;

  const killChecks: Confirmation[] = [
    {
      id: "understand",
      required: true,
      label:
        "Entiendo que se bloquean de inmediato las nuevas entradas y se cancelan las órdenes de entrada pendientes. Las posiciones abiertas se conservan.",
    },
    { id: "liquidate", danger: true, label: "Además, liquidar las posiciones abiertas a mercado (puede ejecutarse con pérdida)." },
  ];

  return (
    <div className="stack">
      <div className="grid3">
        <button className="btn block" disabled={ctl.busy || paused || killSwitch} onClick={() => setDialog("pause")}>
          Pausar entradas
        </button>
        <button className="btn block primary" disabled={ctl.busy || (!paused && !killSwitch)} onClick={() => setDialog("resume")}>
          Reanudar
        </button>
        <button className="btn block danger" disabled={ctl.busy || killSwitch} onClick={() => setDialog("kill")}>
          Kill switch
        </button>
      </div>
      <div className="muted small">
        Pausar bloquea nuevas entradas y sigue vigilando las posiciones abiertas. Reanudar solo se acepta si el motor
        verifica conexiones, datos, reconciliación y límites. Todo afecta únicamente a la simulación / testnet.
      </div>
      <ControlFeedback state={ctl.state} onClose={ctl.reset} />

      <ConfirmDialog
        open={dialog === "pause"}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Pausar nuevas entradas"
        description="No se abrirán posiciones nuevas. Las abiertas siguen gestionándose con sus stops y objetivos."
        confirmLabel="Solicitar pausa"
        onConfirm={() => {
          setDialog(null);
          void ctl.run({ kind: "pause" });
        }}
      />
      <ConfirmDialog
        open={dialog === "resume"}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Reanudar entradas"
        description="El engine comprobará conexiones, frescura de datos, reconciliación, órdenes pendientes y límites. Si algo falla, rechazará la reanudación y te dirá por qué."
        confirmLabel="Solicitar reanudación"
        checks={killSwitch ? [{ id: "clear", required: true, label: "Confirmo que he revisado la causa del kill switch y quiero levantarlo." }] : []}
        onConfirm={(c) => {
          setDialog(null);
          void ctl.run({ kind: "resume", clearKillSwitch: !!c.clear });
        }}
      />
      <ConfirmDialog
        open={dialog === "kill"}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Activar el kill switch"
        tone="danger"
        description="Acción de emergencia. Solo afecta a la simulación / testnet."
        confirmLabel="Activar kill switch"
        checks={killChecks}
        onConfirm={(c) => {
          setDialog(null);
          void ctl.run({ kind: "kill", liquidate: !!c.liquidate });
        }}
      />
    </div>
  );
}

/** Activar / desactivar una estrategia (misma semántica solicitado → confirmado). */
export function StrategyToggle({ id, enabled, isAdmin }: { id: string; enabled: boolean; isAdmin: boolean }) {
  const ctl = useControl();
  const [open, setOpen] = useState(false);
  if (!isAdmin) return null;
  return (
    <div className="stack">
      <button className={`btn ${enabled ? "" : "primary"} block`} disabled={ctl.busy} onClick={() => setOpen(true)}>
        {enabled ? "Desactivar estrategia" : "Activar estrategia"}
      </button>
      <ControlFeedback state={ctl.state} onClose={ctl.reset} />
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={enabled ? "Desactivar estrategia" : "Activar estrategia"}
        description={
          enabled
            ? "Dejará de generar señales nuevas. Las posiciones abiertas se siguen gestionando."
            : "Empezará a generar señales que pasarán siempre por el motor de riesgo. Solo en simulación / testnet."
        }
        confirmLabel={enabled ? "Solicitar desactivación" : "Solicitar activación"}
        onConfirm={() => {
          setOpen(false);
          void ctl.run({ kind: "strategy", id, enable: !enabled });
        }}
      />
    </div>
  );
}
