import { Link } from "react-router-dom";
import { useAckAlert, useOverview, usePerformance } from "../api/queries";
import type { Overview } from "../api/types";
import { useAuth } from "../app/auth";
import { EquityLine } from "../charts/EquityLine";
import { Async, Banner, Card, Dot, ProgressBar, Stat, Tag } from "../components/ui";
import { BotControls } from "../features/control";
import { InstallHint } from "../features/InstallHint";
import { useTimeZone } from "../hooks/useTimeZone";
import { ago, dateTime, money, num, pct, price, signedMoney, tone } from "../services/format";

function engineLabel(o: Overview): { kind: "ok" | "warn" | "bad"; text: string } {
  const e = o.engine;
  if (e.status === "never_started") return { kind: "warn", text: "El engine no ha arrancado nunca" };
  if (e.status === "stopped") return { kind: "bad", text: `Engine parado (sin latido ${e.age_seconds ?? "?"} s)` };
  const r = o.risk.state;
  if (r?.kill_switch_active) return { kind: "bad", text: "Kill switch activo: entradas bloqueadas" };
  if (r?.paused) return { kind: "warn", text: "Pausado: sin nuevas entradas" };
  if (r && !r.entries_allowed) return { kind: "warn", text: "Entradas bloqueadas por el motor de riesgo" };
  if (!r) return { kind: "warn", text: "Engine activo, estado de riesgo no disponible" };
  return { kind: "ok", text: "Operando en simulación" };
}

function connStatus(s: string): "ok" | "warn" | "bad" | "off" {
  return s === "UP" ? "ok" : s === "DEGRADED" ? "warn" : s === "DOWN" ? "bad" : "off";
}

function Limits({ o }: { o: Overview }) {
  const { limits, usage, state } = o.risk;
  const rows: { label: string; value: number | null; max: number | null; fmt: (n: number) => string }[] = [
    { label: "Pérdida diaria", value: num(usage?.daily_loss), max: num(limits.max_daily_loss), fmt: (n) => money(n) },
    { label: "Drawdown", value: num(usage?.drawdown), max: num(limits.max_drawdown), fmt: (n) => money(n) },
    { label: "Posiciones", value: usage?.open_positions ?? null, max: limits.max_positions, fmt: String },
    { label: "Exposición", value: num(usage?.exposure), max: num(limits.max_exposure), fmt: (n) => money(n) },
    { label: "Operaciones hoy", value: state?.entries_today ?? null, max: limits.max_trades_per_day, fmt: String },
  ];
  return (
    <div className="stack">
      {rows.map((r) => (
        <div key={r.label}>
          <div className="row between small">
            <span>{r.label}</span>
            <span className="mono">{r.value === null ? "no disponible" : `${r.fmt(r.value)} / ${r.max === null ? "—" : r.fmt(r.max)}`}</span>
          </div>
          <ProgressBar value={r.value} max={r.max} label={`${r.label}: uso del límite`} />
        </div>
      ))}
      {state && state.latched_reasons.length > 0 && (
        <Banner kind="bad">
          <b>Bloqueos activos:</b> {state.latched_reasons.join("; ")}
        </Banner>
      )}
      <div className="muted small">Límites de diseño en {o.account?.currency ?? "USDT"}, solo spot largo sin apalancamiento. No garantizan que una pérdida real no los supere (gaps, deslizamiento).</div>
    </div>
  );
}

export function Home() {
  const q = useOverview();
  const perf = usePerformance();
  const ack = useAckAlert();
  const tz = useTimeZone();
  const { isAdmin } = useAuth();

  return (
    <>
      <InstallHint />
      <Async q={q} label="Cargando panel…">
        {(o) => {
          const st = engineLabel(o);
          const a = o.account;
          const stale = a?.marks_stale;
          return (
            <>
              <Card title="Estado del bot">
                <div className="row" style={{ gap: 10 }}>
                  <Dot kind={st.kind} />
                  <b>{st.text}</b>
                </div>
                <div className="muted small" style={{ marginTop: 6 }}>
                  Modo <b>{o.mode}</b> · {o.data_origin}
                </div>
                <div className="muted small">
                  Último latido del engine: {o.engine.last_beat_at ? `${dateTime(o.engine.last_beat_at, tz)} (${ago(o.engine.last_beat_at)})` : "nunca"}
                </div>
                <div className="muted small">
                  Últimos datos válidos de mercado: {o.last_data_at ? `${dateTime(o.last_data_at, tz)} (${ago(o.last_data_at)})` : "no disponible"}
                </div>
              </Card>

              {o.critical_alerts.length > 0 && (
                <Card title={`Alertas críticas (${o.critical_alerts.length})`} className="alert-card">
                  <div className="list">
                    {o.critical_alerts.map((al) => (
                      <div key={al.id} className="item">
                        <b>{al.message}</b>
                        <span className="small">Qué hacer: {al.recommended_action || "—"}</span>
                        <span className="muted small">
                          {ago(al.last_seen_at)} · {al.occurrences} vez/veces
                        </span>
                        {isAdmin && (
                          <button className="btn small" style={{ alignSelf: "flex-start" }} onClick={() => ack.mutate(al.id)} disabled={ack.isPending}>
                            Marcar como vista
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </Card>
              )}

              {a === null ? (
                <Banner kind="warn">
                  <b>Cuenta no disponible.</b> El engine aún no ha inicializado la cuenta de este modo; no se muestran cifras.
                </Banner>
              ) : (
                <>
                  {stale && (
                    <Banner kind="warn">
                      Precios de valoración obsoletos: el P&L no realizado y el equity pueden no ser actuales.
                    </Banner>
                  )}
                  <div className="grid2">
                    <Stat label="Capital simulado" value={money(a.initial_capital)} tag={<Tag kind="sim">Simulado</Tag>} />
                    <Stat label="Equity" value={money(a.equity)} sub={`Rentabilidad ${pct(a.return_pct)}`} tone={tone(num(a.equity)! - num(a.initial_capital)!)} />
                    <Stat label="Disponible" value={money(a.cash)} />
                    <Stat label="P&L realizado" value={signedMoney(a.realized_pnl)} tone={tone(a.realized_pnl)} sub="neto de comisiones" />
                    <Stat label="P&L no realizado" value={signedMoney(a.unrealized_pnl)} tone={tone(a.unrealized_pnl)} sub={stale ? "precios obsoletos" : "a último cierre 1m"} />
                    <Stat label="Comisiones pagadas" value={money(a.fees_total)} />
                    <Stat
                      label="Drawdown"
                      value={o.drawdown?.current == null ? "—" : money(o.drawdown.current)}
                      sub={o.drawdown ? `máx. observado ${money(o.drawdown.max_observed)} · límite ${money(o.drawdown.limit)}` : undefined}
                    />
                    <Stat label="Posiciones abiertas" value={o.open_positions ?? "—"} sub={`${o.closed_trades} ${o.closed_trades === 1 ? "operación cerrada" : "operaciones cerradas"}`} />
                  </div>
                  <div className="muted small">{a.valuation}</div>
                </>
              )}

              <Card title="Curva de capital" right={<Tag kind="sim">Instantáneas reales</Tag>}>
                <Async q={perf}>
                  {(p) => (
                    <>
                      <EquityLine points={p.equity_curve} />
                      {p.sample_warning && o.closed_trades > 0 && <div className="muted small">{p.sample_warning}</div>}
                    </>
                  )}
                </Async>
              </Card>

              <Card title="Límites de riesgo">
                <Limits o={o} />
              </Card>

              <Card
                title="Últimas operaciones"
                right={
                  <Link to="/operaciones" className="small">
                    Ver todo
                  </Link>
                }
              >
                {o.recent_trades.length === 0 ? (
                  <div className="muted">Todavía no hay operaciones cerradas. No se fuerzan operaciones para llegar a una muestra.</div>
                ) : (
                  <div className="list">
                    {o.recent_trades.map((t) => (
                      <div key={t.id} className="item">
                        <div className="row between">
                          <b>{t.symbol}</b>
                          <b className={`mono ${tone(t.net_pnl)}`}>{signedMoney(t.net_pnl)}</b>
                        </div>
                        <div className="muted small">
                          {t.strategy_id} · {price(t.entry_price)} → {price(t.exit_price)} · {t.exit_reason ?? "—"} · {ago(t.closed_at)}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Card>

              <Card title="Estrategias activas" right={<Link to="/estrategias" className="small">Gestionar</Link>}>
                {o.active_strategies.length === 0 ? (
                  <div className="muted">Ninguna estrategia activa: el bot no genera señales.</div>
                ) : (
                  <div className="row wrap">
                    {o.active_strategies.map((s) => (
                      <Link key={s} to={`/estrategias/${s}`} style={{ textDecoration: "none" }}>
                        <Tag kind="ok">{s}</Tag>
                      </Link>
                    ))}
                  </div>
                )}
              </Card>

              <Card title="Conexiones">
                {o.connections.length === 0 ? (
                  <div className="muted">Sin conexiones registradas (el engine no ha publicado estado).</div>
                ) : (
                  <div className="list">
                    {o.connections.map((c) => (
                      <div key={c.name} className="item">
                        <div className="row between">
                          <span className="row">
                            <Dot kind={connStatus(c.status)} /> <b>{c.name}</b>
                          </span>
                          <span className="small">{c.status}</span>
                        </div>
                        <span className="muted small">Último dato válido: {c.last_valid_data_at ? `${dateTime(c.last_valid_data_at, tz)} (${ago(c.last_valid_data_at)})` : "no disponible"}</span>
                      </div>
                    ))}
                  </div>
                )}
              </Card>

              <Card title="Control rápido">
                <BotControls
                  entriesAllowed={!!o.risk.state?.entries_allowed}
                  paused={!!o.risk.state?.paused}
                  killSwitch={!!o.risk.state?.kill_switch_active}
                  isAdmin={isAdmin}
                />
              </Card>
            </>
          );
        }}
      </Async>
    </>
  );
}
