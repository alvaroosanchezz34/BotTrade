import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { useOrderDetail, useOrders, usePerformance, usePositions, useStrategies, useSystem, useTrades } from "../api/queries";
import type { Order, PerfStats, Position, Trade } from "../api/types";
import { Async, Banner, Card, Empty, Stat, Tag } from "../components/ui";
import { useTimeZone } from "../hooks/useTimeZone";
import { ago, dateTime, duration, money, pct, price, qty, ratio, signedMoney, tone } from "../services/format";

type Tab = "open" | "history" | "pending" | "rejected";
const TABS: { id: Tab; label: string }[] = [
  { id: "open", label: "Abiertas" },
  { id: "history", label: "Historial" },
  { id: "pending", label: "Pendientes" },
  { id: "rejected", label: "Rechazadas" },
];

function startOfDay(d: string): string | undefined {
  return d ? new Date(`${d}T00:00:00`).toISOString() : undefined;
}
function endOfDay(d: string): string | undefined {
  return d ? new Date(`${d}T23:59:59.999`).toISOString() : undefined;
}

function Perf({ s, warning }: { s: PerfStats; warning: string | null }) {
  return (
    <Card title="Resumen (operaciones cerradas, neto de costes)">
      <div className="grid2">
        <Stat label="P&L neto" value={signedMoney(s.net_pnl)} tone={tone(s.net_pnl)} sub={`${s.trades} operaciones`} />
        <Stat label="Acierto" value={s.win_rate === null ? "—" : pct(s.win_rate * 100, 1, false)} />
        <Stat label="Expectativa/op." value={signedMoney(s.expectancy_net)} tone={tone(s.expectancy_net)} />
        <Stat label="Profit factor" value={s.profit_factor === null ? "—" : ratio(s.profit_factor)} />
        <Stat label="Comisiones" value={money(s.fees)} />
        <Stat label="Duración media" value={duration(s.avg_duration_seconds)} />
      </div>
      {warning && <div className="muted small" style={{ marginTop: 8 }}>{warning}</div>}
    </Card>
  );
}

function OrderDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useOrderDetail(id);
  const tz = useTimeZone();
  return (
    <Dialog.Root open={id !== null} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog" aria-describedby={undefined}>
          <Dialog.Title asChild>
            <h2>Detalle de la orden</h2>
          </Dialog.Title>
          <Async q={q}>
            {(d) => (
              <div className="stack">
                <dl className="kv">
                  <dt>Activo</dt><dd>{d.order.symbol} · {d.order.side} {d.order.type}</dd>
                  <dt>Estado</dt><dd>{d.order.status}</dd>
                  <dt>Cantidad</dt><dd className="mono">{qty(d.order.filled_qty)} / {qty(d.order.quantity)}</dd>
                  <dt>Precio medio</dt><dd className="mono">{price(d.order.avg_fill_price)}</dd>
                  <dt>Comisiones</dt><dd className="mono">{money(d.order.fees, 4)}</dd>
                  <dt>Estrategia</dt><dd>{d.order.strategy_id ?? "—"}</dd>
                  <dt>Motivo</dt><dd>{d.order.purpose}</dd>
                </dl>
                {d.order.last_error && <Banner kind="bad">{d.order.last_error}</Banner>}
                {d.signal && (
                  <Card title="Señal que la originó">
                    <div className="small">{d.signal.rationale}</div>
                    <div className="muted small">Invalidación: {d.signal.invalidation}</div>
                    <div className="muted small">
                      Riesgo: {d.signal.risk_approved ? "aprobada" : "rechazada"} · cantidad autorizada {qty(d.signal.risk_quantity)}
                    </div>
                  </Card>
                )}
                <Card title="Historial de estados">
                  <div className="list">
                    {d.events.map((e, i) => (
                      <div key={i} className="item">
                        <b>{e.to}</b>
                        <span className="muted small">{dateTime(e.at, tz)}</span>
                      </div>
                    ))}
                  </div>
                </Card>
                {d.fills.length > 0 && (
                  <Card title="Ejecuciones">
                    {d.fills.map((f) => (
                      <div key={f.trade_id} className="small mono">
                        {qty(f.quantity)} @ {price(f.price)} · comisión {f.fee} {f.fee_asset}
                      </div>
                    ))}
                  </Card>
                )}
              </div>
            )}
          </Async>
          <Dialog.Close asChild>
            <button className="btn block" style={{ marginTop: 12 }}>Cerrar</button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function PositionItem({ p }: { p: Position }) {
  return (
    <div className="item">
      <div className="row between">
        <b>{p.symbol}</b>
        <b className={`mono ${tone(p.unrealized_pnl)}`}>{p.unrealized_pnl === null ? "valor no disp." : signedMoney(p.unrealized_pnl)}</b>
      </div>
      <div className="small mono">
        {qty(p.quantity)} @ {price(p.avg_entry_price)} · ahora {price(p.mark_price)}
      </div>
      <div className="muted small">
        Stop {price(p.stop_price)} · Objetivo {price(p.target_price)} · {p.strategy_id} · {ago(p.opened_at)}
      </div>
      <div className="muted small">Entrada: {p.entry_reason}</div>
    </div>
  );
}

function TradeItem({ t }: { t: Trade }) {
  const tz = useTimeZone();
  return (
    <div className="item">
      <div className="row between">
        <b>{t.symbol}</b>
        <b className={`mono ${tone(t.net_pnl)}`}>{signedMoney(t.net_pnl)}</b>
      </div>
      <div className="small mono">
        {qty(t.quantity)} · {price(t.entry_price)} → {price(t.exit_price)}
      </div>
      <div className="muted small">
        Comisiones {money(t.fees, 4)} · {duration(t.duration_seconds)} · {t.strategy_id} · {dateTime(t.closed_at, tz)}
      </div>
      <div className="muted small">Entrada: {t.entry_reason} · Salida: {t.exit_reason ?? "—"}</div>
    </div>
  );
}

function OrderItem({ o, onOpen }: { o: Order; onOpen: (id: string) => void }) {
  return (
    <button className="item" onClick={() => onOpen(o.id)} aria-label={`Orden ${o.symbol} ${o.status}, ver detalle`}>
      <div className="row between">
        <b>{o.symbol} · {o.side}</b>
        <Tag kind={o.status === "REJECTED" ? "bad" : o.status === "UNKNOWN" ? "warn" : "plain"}>{o.status}</Tag>
      </div>
      <div className="small mono">{qty(o.quantity)} {o.type}{o.limit_price ? ` @ ${price(o.limit_price)}` : ""}</div>
      <div className="muted small">{o.purpose} · {o.strategy_id ?? "—"} · {ago(o.created_at)}</div>
      {o.last_error && <div className="small neg">{o.last_error}</div>}
    </button>
  );
}

function More({ hasNext, loading, onMore }: { hasNext: boolean; loading: boolean; onMore: () => void }) {
  if (!hasNext) return null;
  return (
    <button className="btn block" onClick={onMore} disabled={loading}>
      {loading ? "Cargando…" : "Cargar más"}
    </button>
  );
}

export function Operations() {
  const [tab, setTab] = useState<Tab>("open");
  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [openOrder, setOpenOrder] = useState<string | null>(null);
  const sys = useSystem();
  const strategies = useStrategies();
  const perf = usePerformance();

  const common = { symbol: symbol || undefined, strategy: strategy || undefined };
  const dates = { from: startOfDay(from), to: endOfDay(to) };
  const positions = usePositions({ ...common, status: "OPEN" });
  const trades = useTrades({ ...common, ...dates });
  const pending = useOrders({ ...common, ...dates, pending: "true" });
  const rejected = useOrders({ ...common, ...dates, status: "REJECTED" });

  return (
    <>
      <Card title="Filtros">
        <div className="grid3">
          <div className="field">
            <label htmlFor="o-sym">Activo</label>
            <select id="o-sym" className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              <option value="">Todos</option>
              {(sys.data?.symbols ?? []).map((s) => <option key={s}>{s}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="o-str">Estrategia</label>
            <select id="o-str" className="input" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="">Todas</option>
              {(strategies.data?.strategies ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="o-from">Desde</label>
            <input id="o-from" className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="o-to">Hasta</label>
            <input id="o-to" className="input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
        </div>
      </Card>

      <div className="seg" role="tablist" aria-label="Operaciones">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "open" && (
        <Card title="Posiciones abiertas">
          <Async q={positions}>
            {(d) => {
              const items = d.pages.flatMap((p) => p.items);
              return items.length === 0 ? <Empty>No hay posiciones abiertas.</Empty> : <div className="list">{items.map((p) => <PositionItem key={p.id} p={p} />)}</div>;
            }}
          </Async>
          <div className="muted small" style={{ marginTop: 8 }}>
            Valoradas al último cierre de 1 min, sin la comisión de salida. Los stops los vigila el engine en el servidor.
          </div>
        </Card>
      )}

      {tab === "history" && (
        <>
          <Async q={perf}>{(p) => <Perf s={p.overall} warning={p.sample_warning} />}</Async>
          <Card title="Operaciones cerradas">
            <Async q={trades}>
              {(d) => {
                const items = d.pages.flatMap((p) => p.items);
                return (
                  <>
                    {items.length === 0 ? (
                      <Empty>No hay operaciones cerradas con estos filtros.</Empty>
                    ) : (
                      <div className="list">{items.map((t) => <TradeItem key={t.id} t={t} />)}</div>
                    )}
                    <More hasNext={!!trades.hasNextPage} loading={trades.isFetchingNextPage} onMore={() => void trades.fetchNextPage()} />
                  </>
                );
              }}
            </Async>
          </Card>
        </>
      )}

      {tab === "pending" && (
        <Card title="Órdenes pendientes">
          <Async q={pending}>
            {(d) => {
              const items = d.pages.flatMap((p) => p.items);
              return items.length === 0 ? (
                <Empty>No hay órdenes pendientes.</Empty>
              ) : (
                <div className="list">{items.map((o) => <OrderItem key={o.id} o={o} onOpen={setOpenOrder} />)}</div>
              );
            }}
          </Async>
        </Card>
      )}

      {tab === "rejected" && (
        <Card title="Órdenes rechazadas">
          <Async q={rejected}>
            {(d) => {
              const items = d.pages.flatMap((p) => p.items);
              return (
                <>
                  {items.length === 0 ? (
                    <Empty>Sin órdenes rechazadas.</Empty>
                  ) : (
                    <div className="list">{items.map((o) => <OrderItem key={o.id} o={o} onOpen={setOpenOrder} />)}</div>
                  )}
                  <More hasNext={!!rejected.hasNextPage} loading={rejected.isFetchingNextPage} onMore={() => void rejected.fetchNextPage()} />
                </>
              );
            }}
          </Async>
          <div className="muted small" style={{ marginTop: 8 }}>
            Aquí aparecen las rechazadas por el exchange simulado/testnet. Las señales que rechaza el motor de riesgo se ven en cada activo.
          </div>
        </Card>
      )}

      <OrderDialog id={openOrder} onClose={() => setOpenOrder(null)} />
    </>
  );
}
