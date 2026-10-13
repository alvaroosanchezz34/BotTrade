import { Link, useParams } from "react-router-dom";
import { useBacktests, useStrategies, useStrategy } from "../api/queries";
import type { BacktestBrief } from "../api/types";
import { useAuth } from "../app/auth";
import { Async, Banner, Card, Empty, Tag } from "../components/ui";
import { StrategyToggle } from "../features/control";
import { useTimeZone } from "../hooks/useTimeZone";
import { ago, dateTime, duration, money, pct, price, ratio, signedMoney, tone } from "../services/format";
import { fmtReturn } from "./Simulation";

function verdictTag(v: string | null) {
  if (!v) return <Tag>Sin backtest</Tag>;
  return <Tag kind={v.startsWith("CANDIDATA") ? "warn" : "bad"}>{v}</Tag>;
}

function SimulationsList() {
  const q = useBacktests();
  return (
    <Card title="Simulaciones históricas (dinero ficticio)">
      <Async q={q}>
        {(d) =>
          d.items.length === 0 ? (
            <Empty>Aún no hay simulaciones. Se generan con el comando de investigación (ver docs/backtesting.md).</Empty>
          ) : (
            <div className="list">
              {d.items.map((b) => (
                <Link key={b.id} to={`/simulaciones/${b.id}`} className="item" style={{ textDecoration: "none", color: "inherit" }}>
                  <div className="row between">
                    <b>{b.strategy_id} · {b.interval}</b>
                    <b className={`mono ${tone(Number(b.final_capital) - Number(b.initial_capital))}`}>
                      {money(b.initial_capital, 0, "")} → {money(b.final_capital, 2, "")}
                    </b>
                  </div>
                  <div className="muted small">
                    {fmtReturn(b.initial_capital, b.final_capital)} · {b.closed_trades} operaciones · {b.notes.split(":")[0] || "sin veredicto"}
                  </div>
                </Link>
              ))}
            </div>
          )
        }
      </Async>
    </Card>
  );
}

export function StrategiesPage() {
  const q = useStrategies();
  return (
    <Async q={q} label="Cargando estrategias…">
      {(d) => (
        <div className="stack">
          <Banner kind="info">{d.disclaimer}</Banner>
          <SimulationsList />
          {d.strategies.length === 0 ? (
            <Empty>El engine aún no ha registrado estrategias.</Empty>
          ) : (
            d.strategies.map((s) => (
              <Link key={s.id} to={`/estrategias/${s.id}`} style={{ textDecoration: "none", color: "inherit" }}>
                <Card>
                  <div className="row between">
                    <b style={{ fontSize: "1.05rem" }}>{s.name}</b>
                    <Tag kind={s.enabled ? "ok" : "plain"}>{s.enabled ? "Activa" : "Desactivada"}</Tag>
                  </div>
                  <div className="muted small">{s.description}</div>
                  <div className="row wrap" style={{ marginTop: 8 }}>
                    <Tag>v{s.version}</Tag>
                    <Tag>{s.interval}</Tag>
                    {s.symbols.map((x) => <Tag key={x}>{x}</Tag>)}
                    {verdictTag(s.last_backtest_verdict)}
                  </div>
                  <div className="muted small" style={{ marginTop: 6 }}>
                    Última señal: {s.last_signal_at ? ago(s.last_signal_at) : "ninguna"} · Último backtest: {s.last_backtest_at ? ago(s.last_backtest_at) : "ninguno"}
                  </div>
                </Card>
              </Link>
            ))
          )}
        </div>
      )}
    </Async>
  );
}

function BacktestRow({ b }: { b: BacktestBrief }) {
  const tz = useTimeZone();
  const m = b.metrics;
  const f = (k: string) => (m[k] === undefined || m[k] === null ? "—" : String(m[k]));
  return (
    <div className="item">
      <div className="row between">
        <b>{b.segment}</b>
        <span className="muted small">{dateTime(b.created_at, tz)}</span>
      </div>
      <div className="small">
        Neto {f("net_pnl")} · operaciones {f("trades")} · PF {f("profit_factor")} · DD máx. {f("max_drawdown")}
      </div>
      <div className="muted small">{b.notes || "—"}</div>
      <div className="muted small mono">datos {b.data_hash.slice(0, 12)} · v{b.strategy_version}</div>
    </div>
  );
}

export function StrategyPage() {
  const { id = "" } = useParams();
  const q = useStrategy(id);
  const { isAdmin } = useAuth();
  const tz = useTimeZone();
  return (
    <>
      <Link to="/estrategias">← Estrategias</Link>
      <Async q={q} label="Cargando estrategia…">
        {(s) => (
          <div className="stack">
            <Card>
              <div className="row between">
                <b style={{ fontSize: "1.15rem" }}>{s.name}</b>
                <Tag kind={s.enabled ? "ok" : "plain"}>{s.enabled ? "Activa" : "Desactivada"}</Tag>
              </div>
              <p className="muted small">{s.description}</p>
              <dl className="kv">
                <dt>Activos</dt><dd>{s.symbols.join(", ")}</dd>
                <dt>Intervalo</dt><dd>{s.interval}</dd>
                <dt>Versión</dt><dd>{s.version} · código {s.code_hash ?? "—"}</dd>
                <dt>Posiciones abiertas</dt><dd>{s.open_positions}</dd>
                <dt>Último backtest</dt><dd>{s.backtests[0] ? dateTime(s.backtests[0].created_at, tz) : "ninguno"}</dd>
              </dl>
              <div style={{ marginTop: 12 }}>
                <StrategyToggle id={s.id} enabled={s.enabled} isAdmin={isAdmin} />
              </div>
              <div className="muted small" style={{ marginTop: 8 }}>
                Solo se puede activar o desactivar dentro de la simulación / testnet. Los parámetros son de solo lectura: no se pueden cambiar desde el panel para no eludir el riesgo.
              </div>
            </Card>

            <Card title="Parámetros (solo lectura)">
              <table className="t"><tbody>
                {Object.entries(s.params).map(([k, v]) => (
                  <tr key={k}><th>{k}</th><td className="mono">{typeof v === "object" ? JSON.stringify(v) : String(v)}</td></tr>
                ))}
              </tbody></table>
            </Card>

            <Card title="Resultados en simulación / testnet (operaciones cerradas)">
              <dl className="kv">
                <dt>Operaciones</dt><dd>{s.live_stats.trades}</dd>
                <dt>P&L neto</dt><dd className={`mono ${tone(s.live_stats.net_pnl)}`}>{signedMoney(s.live_stats.net_pnl)}</dd>
                <dt>Comisiones</dt><dd className="mono">{money(s.live_stats.fees)}</dd>
                <dt>Acierto</dt><dd>{s.live_stats.win_rate === null ? "—" : pct(s.live_stats.win_rate * 100, 1, false)}</dd>
                <dt>Expectativa neta/op.</dt><dd className="mono">{signedMoney(s.live_stats.expectancy_net)}</dd>
                <dt>Profit factor</dt><dd>{s.live_stats.profit_factor === null ? "—" : ratio(s.live_stats.profit_factor)}</dd>
                <dt>Duración media</dt><dd>{duration(s.live_stats.avg_duration_seconds)}</dd>
              </dl>
              {s.live_stats.trades < 300 && <div className="muted small">Muestra de {s.live_stats.trades} operaciones: insuficiente para concluir nada (objetivo ≥ 300).</div>}
            </Card>

            <Card title="Resultados fuera de muestra (backtest)">
              {s.out_of_sample.length === 0 ? <Empty>Sin backtests fuera de muestra registrados.</Empty> : <div className="list">{s.out_of_sample.map((b) => <BacktestRow key={b.id} b={b} />)}</div>}
              <div className="muted small">{s.note}</div>
            </Card>

            <Card title="Señales recientes">
              {s.recent_signals.length === 0 ? (
                <Empty>Sin señales todavía.</Empty>
              ) : (
                <div className="list">
                  {s.recent_signals.slice(0, 10).map((g) => (
                    <div key={g.id} className="item">
                      <div className="row between"><b>{g.symbol}</b><Tag kind={g.status === "APPROVED" ? "ok" : g.status === "REJECTED" ? "bad" : "plain"}>{g.status}</Tag></div>
                      <span className="small">{g.rationale}</span>
                      <span className="muted small">ref. {price(g.reference_price)} · {ago(g.created_at)}</span>
                      {g.risk_reasons.length > 0 && <span className="small neg">{g.risk_reasons.join("; ")}</span>}
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card title="Operaciones recientes">
              {s.recent_trades.length === 0 ? (
                <Empty>Sin operaciones cerradas.</Empty>
              ) : (
                <div className="list">
                  {s.recent_trades.slice(0, 10).map((t) => (
                    <div key={t.id} className="item">
                      <div className="row between"><b>{t.symbol}</b><b className={`mono ${tone(t.net_pnl)}`}>{signedMoney(t.net_pnl)}</b></div>
                      <span className="muted small">{t.exit_reason ?? "—"} · {duration(t.duration_seconds)} · {ago(t.closed_at)}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card title="Versiones">
              <div className="list">
                {s.versions.map((v) => (
                  <div key={v.version} className="item"><b>v{v.version}</b><span className="muted small mono">{v.code_hash} · {dateTime(v.created_at, tz)}</span></div>
                ))}
              </div>
            </Card>
          </div>
        )}
      </Async>
    </>
  );
}
