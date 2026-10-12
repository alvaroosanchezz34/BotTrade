import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useAudit, useEvents, useOverview, useRisk, useSystem } from "../api/queries";
import { useAuth } from "../app/auth";
import { Async, Banner, Card, Dot, Tag, errorText } from "../components/ui";
import { BotControls } from "../features/control";
import { InstallInstructions } from "../features/InstallHint";
import { useTimeZone } from "../hooks/useTimeZone";
import { ago, browserTimeZone, dateTime, isValidTimeZone, money, setTimeZone } from "../services/format";

const ZONES = [
  "Europe/Madrid", "Europe/London", "Europe/Paris", "Europe/Berlin", "America/New_York", "America/Mexico_City",
  "America/Bogota", "America/Buenos_Aires", "America/Santiago", "UTC",
];

function health(s: string): "ok" | "warn" | "bad" | "off" {
  return s === "ok" ? "ok" : s === "down" ? "bad" : s === "degraded" ? "warn" : "off";
}

function ChangePassword() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api<void>("/api/auth/change-password", { method: "POST", body: { current_password: cur, new_password: next } });
      setCur("");
      setNext("");
      setMsg({ ok: true, text: "Contraseña cambiada. Se cerraron tus otras sesiones." });
    } catch (err) {
      setMsg({ ok: false, text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <div className="field"><label htmlFor="cp1">Contraseña actual</label><input id="cp1" className="input" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required /></div>
      <div className="field"><label htmlFor="cp2">Nueva contraseña (mínimo 12 caracteres)</label><input id="cp2" className="input" type="password" autoComplete="new-password" minLength={12} value={next} onChange={(e) => setNext(e.target.value)} required /></div>
      {msg && <Banner kind={msg.ok ? "info" : "bad"}>{msg.text}</Banner>}
      <button className="btn" disabled={busy}>Cambiar contraseña</button>
    </form>
  );
}

export function Settings() {
  const { me, isAdmin, logout } = useAuth();
  const tz = useTimeZone();
  const sys = useSystem();
  const risk = useRisk();
  const overview = useOverview();
  const [showDiag, setShowDiag] = useState(false);
  const events = useEvents(showDiag && isAdmin);
  const audit = useAudit(showDiag && isAdmin);
  const zones = Array.from(new Set([browserTimeZone(), ...ZONES, tz])).filter(isValidTimeZone);

  return (
    <div className="stack">
      <Card title="Estado del sistema">
        <Async q={sys}>
          {(s) => (
            <div className="stack">
              <div className="row between">
                <b>Modo {s.mode}</b>
                <Tag kind="sim">Dinero real: desactivado</Tag>
              </div>
              <div className="list">
                {Object.entries(s.components).map(([name, c]) => (
                  <div key={name} className="item">
                    <div className="row between"><span className="row"><Dot kind={health(c.status)} /> <b>{name}</b></span><span className="small">{c.status}</span></div>
                    <span className="muted small">{c.detail}</span>
                  </div>
                ))}
              </div>
              <dl className="kv">
                <dt>Entorno</dt><dd>{s.environment}</dd>
                <dt>Activos</dt><dd>{s.symbols.join(", ")}</dd>
                <dt>Intervalos</dt><dd>{s.intervals.join(", ")}</dd>
                <dt>Comprobado</dt><dd>{dateTime(s.checked_at, tz)}</dd>
              </dl>
              <div className="muted small">El modo de operación se fija en el servidor al arrancar; no se puede cambiar desde aquí. No existe ningún control para operar con dinero real.</div>
            </div>
          )}
        </Async>
      </Card>

      <Card title="Acciones de emergencia y control">
        <Async q={overview}>
          {(o) => (
            <BotControls
              entriesAllowed={!!o.risk.state?.entries_allowed}
              paused={!!o.risk.state?.paused}
              killSwitch={!!o.risk.state?.kill_switch_active}
              isAdmin={isAdmin}
            />
          )}
        </Async>
      </Card>

      <Card title="Límites de riesgo (solo lectura)">
        <Async q={risk}>
          {(r) => (
            <div className="stack">
              <dl className="kv">
                <dt>Riesgo por operación</dt><dd className="mono">{money(r.limits.risk_per_trade)}</dd>
                <dt>Pérdida diaria máx.</dt><dd className="mono">{money(r.limits.max_daily_loss)}</dd>
                <dt>Drawdown máx.</dt><dd className="mono">{money(r.limits.max_drawdown)}</dd>
                <dt>Posiciones simultáneas</dt><dd>{r.limits.max_positions}</dd>
                <dt>Exposición máx.</dt><dd className="mono">{money(r.limits.max_exposure)}</dd>
                <dt>Operaciones/día</dt><dd>{r.limits.max_trades_per_day}</dd>
                <dt>Pérdidas consecutivas</dt><dd>{r.limits.max_consecutive_losses}</dd>
                <dt>Spread máx.</dt><dd>{r.limits.max_spread_bps} pb</dd>
                <dt>Apalancamiento</dt><dd>{r.limits.leverage}× (solo spot, largos)</dd>
              </dl>
              {r.state && r.state.latched_reasons.length > 0 && <Banner kind="bad">Bloqueos: {r.state.latched_reasons.join("; ")}</Banner>}
              <div className="muted small">{r.policy} Los límites no se pueden editar desde el panel.</div>
            </div>
          )}
        </Async>
      </Card>

      <Card title="Conexiones">
        <Async q={overview}>
          {(o) =>
            o.connections.length === 0 ? (
              <div className="muted">Sin conexiones registradas.</div>
            ) : (
              <div className="list">
                {o.connections.map((c) => (
                  <div key={c.name} className="item"><div className="row between"><b>{c.name}</b><span className="small">{c.status}</span></div><span className="muted small">Último dato válido {ago(c.last_valid_data_at)}</span></div>
                ))}
              </div>
            )
          }
        </Async>
      </Card>

      <Card title="Zona horaria de visualización">
        <div className="field">
          <label htmlFor="tz">Las horas se guardan en UTC y se muestran en:</label>
          <select id="tz" className="input" value={tz} onChange={(e) => setTimeZone(e.target.value)}>
            {zones.map((z) => <option key={z}>{z}</option>)}
          </select>
        </div>
      </Card>

      <Card title="Notificaciones">
        <div className="muted">
          Las notificaciones push no están disponibles en esta versión. Las alertas importantes se guardan siempre en el servidor y aparecen en Inicio (críticas) y en esta pantalla.
        </div>
      </Card>

      <Card title="Instalar en el iPhone">
        <InstallInstructions />
      </Card>

      <Card title="Seguridad de la cuenta">
        <div className="stack">
          <div>Sesión: <b>{me.username}</b> ({me.role === "admin" ? "administrador" : "solo lectura"})</div>
          <ChangePassword />
          <button className="btn" onClick={() => void logout()}>Cerrar sesión</button>
        </div>
      </Card>

      {isAdmin && (
        <Card title="Logs y diagnóstico">
          <button className="btn" onClick={() => setShowDiag((v) => !v)}>{showDiag ? "Ocultar" : "Mostrar eventos y auditoría"}</button>
          {showDiag && (
            <div className="stack" style={{ marginTop: 12 }}>
              <Async q={events}>{(d) => <div className="list">{d.items.map((e) => <div key={e.id} className="item"><b>{e.kind}</b><span className="muted small">{dateTime(e.at, tz)} · {e.source}</span></div>)}</div>}</Async>
              <Async q={audit}>{(d) => <div className="list">{d.items.map((a) => <div key={a.id} className="item"><b>{a.action}</b><span className="muted small">{a.actor} · {a.target} · {dateTime(a.at, tz)}</span></div>)}</div>}</Async>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
