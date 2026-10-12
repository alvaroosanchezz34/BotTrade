import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useCandles, useSignals } from "../api/queries";
import { CandleChart } from "../charts/CandleChart";
import { Async, Banner, Card, Empty, Tag } from "../components/ui";
import { ago, price } from "../services/format";

export function MarketDetail() {
  const { symbol = "" } = useParams();
  const [interval, setIntervalState] = useState<"1m" | "5m">("1m");
  const candles = useCandles(symbol, interval);
  const signals = useSignals({ symbol });

  return (
    <>
      <div className="row between">
        <Link to="/mercados">← Mercados</Link>
        <b>{symbol}</b>
      </div>
      <div className="seg" role="tablist" aria-label="Intervalo">
        {(["1m", "5m"] as const).map((i) => (
          <button key={i} role="tab" aria-selected={interval === i} onClick={() => setIntervalState(i)}>
            {i}
          </button>
        ))}
      </div>
      <Card title="Velas cerradas" right={<Tag kind="plain">Histórico persistido</Tag>}>
        <Async q={candles} label="Cargando gráfico…">
          {(d) =>
            d.candles.length === 0 ? (
              <Empty>No hay velas guardadas para este activo e intervalo.</Empty>
            ) : (
              <>
                <CandleChart data={d} />
                <div className="muted small">
                  Última vela cerrada: {price(d.candles.at(-1)?.c)}. Flechas: entradas (E) y salidas (S) de las operaciones.
                </div>
              </>
            )
          }
        </Async>
      </Card>
      <Card title="Últimas señales">
        <Async q={signals}>
          {(d) => {
            const items = d.pages.flatMap((p) => p.items).slice(0, 10);
            if (items.length === 0) return <Empty>Sin señales para este activo.</Empty>;
            return (
              <div className="list">
                {items.map((s) => (
                  <div key={s.id} className="item">
                    <div className="row between">
                      <b>{s.strategy_id}</b>
                      <Tag kind={s.status === "APPROVED" ? "ok" : s.status === "REJECTED" ? "bad" : "plain"}>{s.status}</Tag>
                    </div>
                    <span className="small">{s.rationale}</span>
                    <span className="muted small">
                      ref. {price(s.reference_price)} · stop {price(s.stop_price)} · {ago(s.created_at)}
                    </span>
                    {s.risk_reasons.length > 0 && <Banner kind="warn">Rechazada por riesgo: {s.risk_reasons.join("; ")}</Banner>}
                    {s.score !== null && (
                      <span className="muted small">
                        Puntuación {s.score.toFixed(2)} ({s.score_is_calibrated ? "calibrada" : "heurística, NO es una probabilidad"})
                      </span>
                    )}
                  </div>
                ))}
              </div>
            );
          }}
        </Async>
      </Card>
    </>
  );
}
