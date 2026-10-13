import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useCreateLabJob, useLabJobs, useLabOptions } from "../api/queries";
import type { LabOptions, LabParamSpec, LabRequest } from "../api/types";
import { Async, Banner, Card, Empty, Tag, errorText } from "../components/ui";
import { ago, dateTime } from "../services/format";
import { useTimeZone } from "../hooks/useTimeZone";

type Strategy = LabOptions["strategies"][number];

export function validateParam(spec: LabParamSpec, raw: string): string | null {
  if (raw.trim() === "") return "Obligatorio";
  const v = Number(raw);
  if (!Number.isFinite(v)) return "Debe ser un número";
  if (spec.type === "integer" && !Number.isInteger(v)) return "Debe ser un entero";
  if (spec.min !== null && (spec.min_exclusive ? v <= spec.min : v < spec.min))
    return `Debe ser ${spec.min_exclusive ? "mayor que" : "al menos"} ${spec.min}`;
  if (spec.max !== null && v > spec.max) return `Máximo ${spec.max}`;
  return null;
}

function Seg<T extends string | number>({
  label,
  value,
  options,
  onChange,
  disabled = () => false,
}: {
  label: string;
  value: T;
  options: { value: T; text: string }[];
  onChange: (v: T) => void;
  disabled?: (v: T) => boolean;
}) {
  return (
    <div className="field">
      <span className="small muted" id={`seg-${label}`} style={{ fontWeight: 600 }}>
        {label}
      </span>
      <div className="seg" role="radiogroup" aria-labelledby={`seg-${label}`}>
        {options.map((o) => (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={value === o.value}
            aria-selected={value === o.value}
            disabled={disabled(o.value)}
            onClick={() => onChange(o.value)}
          >
            {o.text}
          </button>
        ))}
      </div>
    </div>
  );
}

function LabForm({ opts, initial }: { opts: LabOptions; initial?: LabRequest }) {
  const nav = useNavigate();
  const create = useCreateLabJob();
  const [strategyId, setStrategyId] = useState(initial?.strategy_id ?? opts.strategies[0]?.id ?? "");
  const strategy: Strategy | undefined = opts.strategies.find((s) => s.id === strategyId);
  const [symbols, setSymbols] = useState<string[]>(initial?.symbols ?? ["BTCUSDT"]);
  const [interval, setIntervalState] = useState<"1m" | "5m">(initial?.interval ?? "5m");
  const [months, setMonths] = useState(initial?.months ?? 3);
  const [cost, setCost] = useState<"normal" | "conservador">(initial?.cost_level ?? "normal");
  const defaults = useMemo(
    () => Object.fromEntries((strategy?.params ?? []).map((p) => [p.name, String(initial?.strategy_id === strategyId ? (initial.params[p.name] ?? p.default ?? "") : (p.default ?? ""))])),
    [strategy, initial, strategyId],
  );
  const [values, setValues] = useState<Record<string, string>>(defaults);
  useEffect(() => setValues(defaults), [defaults]);

  const available = opts.data[interval] ?? {};
  const symbolsWithData = opts.symbols.filter((s) => available[s]);
  const errors: Record<string, string | null> = Object.fromEntries(
    (strategy?.params ?? []).map((p) => [p.name, validateParam(p, values[p.name] ?? "")]),
  );
  const hasErrors = Object.values(errors).some(Boolean);
  const monthOk = (m: number) => m <= (opts.max_months[interval] ?? 0);
  useEffect(() => {
    if (!monthOk(months)) setMonths(Math.max(...opts.months.filter(monthOk)));
  }, [interval]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!strategy || hasErrors || symbols.length === 0) return;
    const params = Object.fromEntries(strategy.params.map((p) => [p.name, Number(values[p.name])]));
    try {
      const r = await create.mutateAsync({ strategy_id: strategy.id, symbols, interval, months, params, cost_level: cost });
      nav(`/laboratorio/${r.job_id}`);
    } catch {
      /* el error se muestra debajo */
    }
  }

  return (
    <form className="stack" onSubmit={submit} aria-label="Nueva simulación">
      <Card title="1 · Elige una plantilla">
        <div className="stack">
          <div className="field">
            <label htmlFor="lab-strategy">Estrategia</label>
            <select id="lab-strategy" className="input" value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>
              {opts.strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div className="muted small">{strategy?.description}</div>
        </div>
      </Card>

      <Card title="2 · Datos y periodo">
        <div className="stack">
          <Seg
            label="Velas"
            value={interval}
            onChange={setIntervalState}
            options={opts.intervals.map((i) => ({ value: i, text: i }))}
          />
          <Seg
            label="Periodo (hasta el último dato disponible)"
            value={months}
            onChange={setMonths}
            options={opts.months.map((m) => ({ value: m, text: `${m} ${m === 1 ? "mes" : "meses"}` }))}
            disabled={(m) => !monthOk(m)}
          />
          <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="small muted" style={{ fontWeight: 600 }}>
              Activos (máx. 2)
            </legend>
            <div className="row wrap">
              {symbolsWithData.length === 0 && <span className="muted">No hay datos para estas velas.</span>}
              {symbolsWithData.map((s) => (
                <label key={s} className="check" style={{ minHeight: 44, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={symbols.includes(s)}
                    onChange={(e) => setSymbols(e.target.checked ? [...symbols, s].slice(0, 2) : symbols.filter((x) => x !== s))}
                  />
                  <span>{s}</span>
                </label>
              ))}
            </div>
          </fieldset>
          {symbolsWithData.length > 0 && (
            <div className="muted small">
              Datos disponibles: {symbolsWithData.map((s) => `${s} ${(available[s]?.bars ?? 0).toLocaleString("es-ES")} velas`).join(" · ")}
            </div>
          )}
          <Seg
            label="Nivel de costes"
            value={cost}
            onChange={setCost}
            options={[
              { value: "normal", text: "Normal" },
              { value: "conservador", text: "Conservador" },
            ]}
          />
          <div className="muted small">
            Normal: comisión {Number(opts.cost_levels.normal?.taker_fee ?? 0) * 100}% por lado. Conservador: {Number(opts.cost_levels.conservador?.taker_fee ?? 0) * 100}% y doble deslizamiento.
          </div>
        </div>
      </Card>

      <Card title="3 · Parámetros de tu idea">
        <div className="stack">
          {(strategy?.params ?? []).map((p) => (
            <div className="field" key={p.name}>
              <label htmlFor={`p-${p.name}`}>{p.name}</label>
              <input
                id={`p-${p.name}`}
                className="input"
                inputMode="decimal"
                value={values[p.name] ?? ""}
                aria-invalid={!!errors[p.name]}
                aria-describedby={`h-${p.name}`}
                onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}
              />
              <span id={`h-${p.name}`} className={`small ${errors[p.name] ? "neg" : "muted"}`}>
                {errors[p.name] ?? `${p.description || "—"} · por defecto ${p.default ?? "—"} · rango ${p.min ?? "−∞"}…${p.max ?? "∞"}`}
              </span>
            </div>
          ))}
          <button type="button" className="btn small" onClick={() => setValues(defaults)}>
            Restaurar valores por defecto
          </button>
        </div>
      </Card>

      <Banner kind="info">
        <b>Cómo leer el resultado.</b> No optimizamos nada: se evalúa exactamente tu configuración. Verás el resultado en tres tramos
        de tiempo, frente al azar y con costes más duros. <b>Cuantas más variantes pruebes, más probable es que alguna salga bien por
        casualidad</b>: te avisaremos de cuántas llevas.
      </Banner>
      {create.isError && <Banner kind="bad">{errorText(create.error)}</Banner>}
      <button className="btn primary block" disabled={create.isPending || hasErrors || symbols.length === 0}>
        {create.isPending ? "Enviando…" : "Simular con dinero ficticio"}
      </button>
      <div className="muted small">{opts.disclaimer} Máximo {opts.limits.jobs_per_hour} simulaciones por hora y una a la vez.</div>
    </form>
  );
}

export function Lab() {
  const opts = useLabOptions();
  const jobs = useLabJobs();
  const tz = useTimeZone();
  const loc = useLocation();
  const initial = (loc.state as { request?: LabRequest } | null)?.request;
  return (
    <>
      <Link to="/estrategias">← Estrategias</Link>
      <h2 style={{ margin: "4px 0" }}>Laboratorio</h2>
      <div className="muted">Prueba una idea con dinero ficticio y recibe un informe honesto, incluido cuando la conclusión es «no hay ventaja».</div>
      <Async q={opts} label="Cargando el laboratorio…">
        {(o) => <LabForm key={loc.key} opts={o} initial={initial} />}
      </Async>
      <Card title="Tus simulaciones">
        <Async q={jobs}>
          {(d) =>
            d.items.length === 0 ? (
              <Empty>Todavía no has lanzado ninguna.</Empty>
            ) : (
              <div className="list">
                {d.items.map((j) => (
                  <Link key={j.id} to={`/laboratorio/${j.id}`} className="item" style={{ textDecoration: "none", color: "inherit" }}>
                    <div className="row between">
                      <b>
                        {j.spec.strategy_id} · {j.spec.interval} · {j.spec.symbols.join(", ")}
                      </b>
                      <Tag kind={j.status === "FAILED" ? "bad" : j.status === "DONE" ? (j.verdict === "PROMETEDORA" ? "warn" : "plain") : "sim"}>
                        {j.status === "DONE" ? (j.verdict ?? "Terminada") : j.status === "FAILED" ? "Fallida" : j.status === "QUEUED" ? "En cola" : "Simulando"}
                      </Tag>
                    </div>
                    <span className="muted small">
                      {dateTime(j.created_at, tz)} ({ago(j.created_at)}) · variante nº {j.variants_tried ?? 1}
                    </span>
                  </Link>
                ))}
              </div>
            )
          }
        </Async>
      </Card>
    </>
  );
}
