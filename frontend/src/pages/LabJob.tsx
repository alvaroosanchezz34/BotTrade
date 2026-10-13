import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useBacktest, useLabJob } from "../api/queries";
import type { LabJobOut, LabReport } from "../api/types";
import { Async, Banner, Card, Empty, Tag } from "../components/ui";
import { duration, pct, signedMoney, tone } from "../services/format";
import { SimulationView } from "./Simulation";

function Elapsed({ since }: { since: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  return <>{duration((Date.now() - new Date(since).getTime()) / 1000)}</>;
}

function Verdict({ r }: { r: LabReport }) {
  const promising = r.verdict === "PROMETEDORA";
  const inconclusive = r.verdict === "NO CONCLUYENTE";
  const failed = r.checks.filter((c) => !c.passed).length;
  const title = promising ? "Prometedora" : inconclusive ? "No concluyente" : "Sin evidencia de ventaja";
  const text = promising
    ? "Supera los filtros iniciales en estos datos. Eso NO prueba que gane dinero: falta validarla en datos que no hayas visto (otro periodo u otro activo) y, después, en simulación en vivo durante semanas."
    : inconclusive
      ? r.main.trades === 0
        ? "La idea no generó ninguna operación en este periodo (los filtros son muy exigentes o el mercado no cumplió las condiciones). Sin operaciones no se puede concluir nada, ni a favor ni en contra: prueba un periodo más largo, otro activo o parámetros menos restrictivos."
        : `Solo hubo ${r.main.trades} operaciones: con tan pocas, el resultado puede ser pura casualidad en cualquier dirección. No se puede concluir nada, ni a favor ni en contra. Prueba un periodo más largo o parámetros que operen más.`
      : "Con estos datos y estos costes, la idea no muestra una ventaja distinguible del azar. Es un resultado útil: te ha ahorrado probarla con dinero.";
  return (
    <Card title="Veredicto">
      <div className="row between">
        {/* «Prometedora» nunca se muestra en verde: no es prueba de rentabilidad. */}
        <b style={{ fontSize: "1.25rem" }}>{title}</b>
        <Tag kind={promising ? "warn" : inconclusive ? "plain" : "bad"}>
          {promising ? "Pasa los filtros" : `${failed} de ${r.checks.length} pruebas fallidas`}
        </Tag>
      </div>
      <p className="small" style={{ marginBottom: 8 }}>
        {text}
      </p>
      <ul className="list" style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {r.checks.map((c) => (
          <li key={c.name} className="item">
            <div className="row" style={{ gap: 8, alignItems: "flex-start" }}>
              <span aria-hidden="true" className={c.passed ? "pos" : "neg"} style={{ fontWeight: 800, width: 18 }}>
                {c.passed ? "✓" : "✗"}
              </span>
              <div>
                <b>{c.name}</b> <span className="sr-only">{c.passed ? "superada" : "no superada"}</span>
                <div className="muted small">{c.detail}</div>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Extra({ r }: { r: LabReport }) {
  const rnd = r.random;
  return (
    <>
      <Card title="Estabilidad por periodos (tres tercios)">
        <table className="t">
          <tbody>
            {r.thirds.map((t) => (
              <tr key={t.label}>
                <th>{t.label}</th>
                <td className={`mono ${tone(t.net_pnl)}`}>
                  {signedMoney(t.net_pnl)} · {t.trades} operaciones{t.profit_factor ? ` · PF ${t.profit_factor.toFixed(2)}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="muted small">Una idea con ventaja real debería funcionar en la mayoría de los tramos, no solo en una racha.</div>
      </Card>
      <Card title="Comparaciones">
        <dl className="kv">
          <dt>Tu idea (neto)</dt>
          <dd className={`mono ${tone(r.main.net_pnl)}`}>{signedMoney(r.main.net_pnl)}</dd>
          <dt>Entradas al azar ({rnd.n} pruebas)</dt>
          <dd className="mono">
            {rnd.net_min === null ? "—" : `${signedMoney(rnd.net_min)} … ${signedMoney(rnd.net_max)} (mediana ${signedMoney(rnd.net_median)})`}
          </dd>
          <dt>Comprar y mantener</dt>
          <dd className="mono">{r.buy_hold.return_pct === null ? "—" : pct(r.buy_hold.return_pct)}</dd>
          <dt>Con costes más duros</dt>
          <dd className={`mono ${tone(r.stress.net_pnl)}`}>{signedMoney(r.stress.net_pnl)}</dd>
          <dt>Con los límites de riesgo reales</dt>
          <dd className="mono">
            {signedMoney(r.limited.net_pnl)} · caída máx. {r.limited.max_drawdown_pct === null ? "—" : pct(r.limited.max_drawdown_pct, 1, false)}
          </dd>
        </dl>
        <div className="muted small">
          «Entradas al azar» usa las mismas salidas y la misma frecuencia: si tu idea no las supera, la entrada no aporta nada.
        </div>
      </Card>
      <Card title="Qué se ha simulado">
        <table className="t">
          <tbody>
            <tr><th>Estrategia</th><td>{r.spec.strategy_id} · velas {r.spec.interval} · {r.spec.symbols.join(", ")}</td></tr>
            <tr><th>Costes</th><td>{r.spec.cost_level}</td></tr>
            {Object.entries(r.spec.params).map(([k, v]) => (
              <tr key={k}><th>{k}</th><td className="mono">{String(v)}</td></tr>
            ))}
            <tr><th>Datos</th><td className="mono">{r.data.bars.toLocaleString("es-ES")} velas · {r.data.hash.slice(0, 12)}{r.data.missing_bars ? ` · ${r.data.missing_bars} faltantes` : ""}</td></tr>
          </tbody>
        </table>
        <ul className="small muted" style={{ paddingLeft: 18 }}>
          {r.caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      </Card>
    </>
  );
}

function Done({ job }: { job: LabJobOut }) {
  const nav = useNavigate();
  const run = useBacktest(job.run_id ?? "");
  const r = job.report;
  if (!r) return <Empty>El informe no está disponible.</Empty>;
  return (
    <div className="stack">
      <Verdict r={r} />
      {r.multiple_testing_note && <Banner kind="warn">{r.multiple_testing_note}</Banner>}
      {job.run_id ? (
        <Async q={run} label="Cargando resultado…">
          {(d) => <SimulationView d={d} showVerdict={false} />}
        </Async>
      ) : null}
      <Extra r={r} />
      <button
        className="btn block"
        onClick={() =>
          nav("/laboratorio", {
            state: {
              request: {
                strategy_id: r.spec.strategy_id,
                symbols: r.spec.symbols,
                interval: r.spec.interval,
                months: 3,
                params: r.spec.params,
                cost_level: r.spec.cost_level,
              },
            },
          })
        }
      >
        Probar otra variante
      </button>
      <div className="muted small">
        Tu variante nº {r.variants_tried} de esta idea. Cambiar parámetros hasta que salga bien se llama sobreajuste: el resultado deja de ser fiable.
      </div>
    </div>
  );
}

export function LabJob() {
  const { id = "" } = useParams();
  const q = useLabJob(id);
  return (
    <>
      <Link to="/laboratorio">← Laboratorio</Link>
      <Async q={q} label="Cargando simulación…">
        {(job) => {
          if (job.status === "FAILED") {
            return (
              <div className="stack">
                <Banner kind="bad">
                  <b>La simulación ha fallado.</b> {job.error}
                </Banner>
                <Link className="btn block" to="/laboratorio">
                  Volver a intentarlo
                </Link>
              </div>
            );
          }
          if (job.status !== "DONE") {
            return (
              <Card title="Simulando…">
                <div role="status" aria-live="polite" className="stack">
                  <b>{job.status === "QUEUED" ? "En cola" : job.progress}</b>
                  <div className="muted small">
                    Transcurrido: <Elapsed since={job.started_at ?? job.created_at} />. Puede tardar de unos segundos a un par de minutos.
                    Puedes cerrar la pantalla: el servidor sigue trabajando y el resultado quedará en «Tus simulaciones».
                  </div>
                  <div className="bar" aria-hidden="true">
                    <i style={{ width: job.status === "QUEUED" ? "10%" : "60%" }} />
                  </div>
                </div>
              </Card>
            );
          }
          return <Done job={job} />;
        }}
      </Async>
    </>
  );
}

