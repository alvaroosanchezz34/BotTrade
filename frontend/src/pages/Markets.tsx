import { useState } from "react";
import { Link } from "react-router-dom";
import { useMarkets, useStrategies, useSystem } from "../api/queries";
import type { MarketRow } from "../api/types";
import { Async, Card, Empty, Tag } from "../components/ui";
import { useTimeZone } from "../hooks/useTimeZone";
import { ago, dateTime, money, pct, price, ratio, tone } from "../services/format";

export function dataTag(s: MarketRow["data_status"]) {
  if (s === "al_dia") return <Tag kind="ok">Datos al día</Tag>;
  if (s === "obsoleto") return <Tag kind="warn">Datos obsoletos</Tag>;
  return <Tag kind="bad">Sin datos</Tag>;
}

export function Markets() {
  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("");
  const q = useMarkets(symbol || undefined, strategy || undefined);
  const sys = useSystem();
  const strategies = useStrategies();
  const tz = useTimeZone();

  return (
    <>
      <Card title="Filtros">
        <div className="grid3">
          <div className="field">
            <label htmlFor="f-sym">Activo</label>
            <select id="f-sym" className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              <option value="">Todos</option>
              {(sys.data?.symbols ?? []).map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="f-str">Estrategia</label>
            <select id="f-str" className="input" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="">Todas</option>
              {(strategies.data?.strategies ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      </Card>
      <Async q={q} label="Cargando mercados…">
        {(d) =>
          d.markets.length === 0 ? (
            <Empty>No hay activos que coincidan con el filtro.</Empty>
          ) : (
            <div className="stack">
              {d.markets.map((m) => (
                <Link key={m.symbol} to={`/mercados/${m.symbol}`} style={{ textDecoration: "none", color: "inherit" }}>
                  <Card>
                    <div className="row between">
                      <b style={{ fontSize: "1.1rem" }}>{m.symbol}</b>
                      {dataTag(m.data_status)}
                    </div>
                    <div className="row between" style={{ marginTop: 6 }}>
                      <span className="mono" style={{ fontSize: "1.5rem", fontWeight: 700 }}>
                        {price(m.price)}
                      </span>
                      <span className={`mono ${tone(m.change_24h_pct)}`}>
                        {m.change_24h_pct === null ? "24 h: no disponible" : `24 h ${pct(m.change_24h_pct)}`}
                      </span>
                    </div>
                    <dl className="kv" style={{ marginTop: 8 }}>
                      <dt>Precio de</dt>
                      <dd>{m.price_as_of ? `${dateTime(m.price_as_of, tz)} (${ago(m.price_as_of)})` : "no disponible"}</dd>
                      <dt>Volumen 24 h</dt>
                      <dd className="mono">{m.volume_24h === null ? "no disponible" : money(m.volume_24h, 0, "")}</dd>
                      <dt>Volatilidad 1 h</dt>
                      <dd className="mono">{m.volatility_1h_pct === null ? "no disponible" : `${ratio(m.volatility_1h_pct)} %`}</dd>
                      <dt>Estrategias</dt>
                      <dd>{m.strategies.length ? m.strategies.join(", ") : "ninguna activa"}</dd>
                      <dt>Última señal</dt>
                      <dd>{m.latest_signals[0] ? `${m.latest_signals[0].strategy_id} · ${ago(m.latest_signals[0].created_at)}` : "ninguna"}</dd>
                    </dl>
                  </Card>
                </Link>
              ))}
              <div className="muted small">Fuente de precios: última vela cerrada de 1 min. Variación, volumen y volatilidad solo se muestran con historia suficiente.</div>
            </div>
          )
        }
      </Async>
    </>
  );
}
