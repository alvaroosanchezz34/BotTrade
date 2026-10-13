import { Link, useParams } from "react-router-dom";
import { useBacktest } from "../api/queries";
import { EquityLine } from "../charts/EquityLine";
import { Async, Banner, Card, Empty, Stat, Tag } from "../components/ui";
import { useTimeZone } from "../hooks/useTimeZone";
import { dateTime, duration, money, num, pct, price, qty, signedMoney, tone } from "../services/format";

/** Resultado final de una simulación histórica con dinero ficticio. */
export function Simulation() {
  const { id = "" } = useParams();
  const q = useBacktest(id);
  const tz = useTimeZone();
  return (
    <>
      <Link to="/estrategias">← Estrategias</Link>
      <Async q={q} label="Cargando simulación…">
        {(d) => {
          const s = d.summary;
          const verdict = d.notes.split(":")[0] ?? "";
          const discarded = verdict.startsWith("DESCARTADA");
          return (
            <div className="stack">
              <Banner kind="info">
                <b>{d.disclaimer}</b> Las operaciones se simularon con comisiones, spread y deslizamiento sobre datos reales
                pasados.
              </Banner>
              <Card>
                <div className="row between">
                  <b style={{ fontSize: "1.1rem" }}>
                    {d.strategy_id} · {d.interval}
                  </b>
                  <Tag kind="sim">Simulado</Tag>
                </div>
                <div className="muted small">
                  {d.symbols.join(", ")} · {dateTime(d.start, tz)} → {dateTime(d.end, tz)} · tramo «{d.segment}» (fuera de muestra)
                </div>
              </Card>

              <Card title="Resultado final">
                <div className="grid2">
                  <Stat label="Capital inicial" value={money(s.initial_capital)} />
                  <Stat
                    label="Capital final"
                    value={money(s.final_capital)}
                    tone={tone(s.net_pnl)}
                    sub={`Rentabilidad ${pct(s.return_pct)}`}
                  />
                  <Stat label="P&L neto" value={signedMoney(s.net_pnl)} tone={tone(s.net_pnl)} sub="tras comisiones" />
                  <Stat label="Operaciones cerradas" value={s.closed_trades} sub={s.win_rate === null ? undefined : `acierto ${pct(s.win_rate * 100, 1, false)}`} />
                  <Stat
                    label="Peor caída (cerradas)"
                    value={money(s.max_drawdown)}
                    sub={s.max_drawdown_pct === null ? undefined : pct(s.max_drawdown_pct, 1, false)}
                  />
                  <Stat label="Mejor / peor operación" value={`${signedMoney(s.best_trade)} / ${signedMoney(s.worst_trade)}`} />
                </div>
              </Card>

              <Card title="Curva de capital (solo operaciones cerradas)">
                <EquityLine points={s.curve} />
                <div className="row between small muted">
                  <span>{money(s.initial_capital)}</span>
                  <span>{money(s.final_capital)}</span>
                </div>
                <div className="muted small">
                  No incluye posiciones abiertas ni el movimiento dentro de cada operación: la caída real entre cierres puede ser mayor.
                </div>
              </Card>

              {d.notes && (
                <Banner kind={discarded ? "bad" : "warn"}>
                  <b>Veredicto del protocolo de validación.</b> {d.notes}
                </Banner>
              )}

              <Card title="Últimas operaciones simuladas">
                {d.trades.length === 0 ? (
                  <Empty>Sin operaciones.</Empty>
                ) : (
                  <div className="list">
                    {[...d.trades]
                      .filter((t) => t.exit_time !== null)
                      .slice(-15)
                      .reverse()
                      .map((t, i) => (
                        <div key={i} className="item">
                          <div className="row between">
                            <b>{t.symbol}</b>
                            <b className={`mono ${tone(t.net_pnl)}`}>{signedMoney(t.net_pnl)}</b>
                          </div>
                          <div className="small mono">
                            {qty(t.quantity)} · {price(t.entry_price)} → {price(t.exit_price)}
                          </div>
                          <div className="muted small">
                            {t.exit_reason ?? "—"} · comisiones {money(t.fees, 4)} ·{" "}
                            {t.exit_time ? duration((new Date(t.exit_time).getTime() - new Date(t.entry_time).getTime()) / 1000) : "—"} ·{" "}
                            {dateTime(t.exit_time, tz)}
                          </div>
                        </div>
                      ))}
                  </div>
                )}
              </Card>
              <div className="muted small mono">
                Reproducible: datos {d.data_hash.slice(0, 16)} · versión {d.strategy_version} · semilla {d.seed}
              </div>
            </div>
          );
        }}
      </Async>
    </>
  );
}

export function fmtReturn(initial: string, final: string): string {
  const i = num(initial);
  const f = num(final);
  return i && f !== null ? pct(((f - i) / i) * 100) : "—";
}
