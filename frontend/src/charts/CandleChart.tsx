import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Candles } from "../api/types";
import { useTimeZone } from "../hooks/useTimeZone";

function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** Velas con zoom/arrastre táctil (pellizco y deslizamiento los gestiona la librería). */
export function CandleChart({ data }: { data: Candles }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const tz = useTimeZone();

  useEffect(() => {
    if (!el.current) return;
    const text = cssVar("--muted", "#94a3b8");
    const grid = cssVar("--border", "#25344f");
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: text, fontSize: 12 },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: grid, minimumWidth: 56 },
      timeScale: { borderColor: grid, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
      handleScroll: { pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { pinch: true, mouseWheel: true, axisPressedMouseMove: true },
    });
    const s = c.addSeries(CandlestickSeries, {
      upColor: cssVar("--pos", "#22c55e"),
      downColor: cssVar("--neg", "#ef4444"),
      borderVisible: false,
      wickUpColor: cssVar("--pos", "#22c55e"),
      wickDownColor: cssVar("--neg", "#ef4444"),
    });
    chart.current = c;
    series.current = s;
    markers.current = createSeriesMarkers(s, []);
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
      markers.current = null;
    };
  }, []);

  useEffect(() => {
    // Las marcas de tiempo del gráfico van en UTC; se muestran en la zona elegida.
    const fmt = (t: number, opts: Intl.DateTimeFormatOptions) =>
      new Intl.DateTimeFormat("es-ES", { timeZone: tz, hour12: false, ...opts }).format(new Date(t * 1000));
    chart.current?.applyOptions({
      localization: {
        timeFormatter: (t: Time) => fmt(Number(t), { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }),
      },
      timeScale: {
        tickMarkFormatter: (t: Time) => fmt(Number(t), { hour: "2-digit", minute: "2-digit" }),
      },
    });
  }, [tz]);

  useEffect(() => {
    if (!series.current) return;
    series.current.setData(
      data.candles.map((k) => ({
        time: k.t as UTCTimestamp,
        open: Number(k.o),
        high: Number(k.h),
        low: Number(k.l),
        close: Number(k.c),
      })),
    );
    const step = data.interval === "5m" ? 300 : 60;
    const first = data.candles[0]?.t ?? 0;
    const ms = data.trade_markers
      .map((m) => ({
        // Se ancla a la vela que contiene el momento de la operación.
        time: (Math.floor(new Date(m.time).getTime() / 1000 / step) * step) as UTCTimestamp,
        position: m.type === "entry" ? ("belowBar" as const) : ("aboveBar" as const),
        shape: m.type === "entry" ? ("arrowUp" as const) : ("arrowDown" as const),
        color: m.type === "entry" ? cssVar("--accent", "#38bdf8") : cssVar("--warn", "#f59e0b"),
        text: m.type === "entry" ? "E" : "S",
      }))
      .filter((m) => m.time >= first)
      .sort((a, b) => a.time - b.time);
    markers.current?.setMarkers(ms);
    chart.current?.timeScale().fitContent();
  }, [data]);

  return <div ref={el} className="chart" role="img" aria-label={`Gráfico de velas ${data.symbol} ${data.interval}`} />;
}
