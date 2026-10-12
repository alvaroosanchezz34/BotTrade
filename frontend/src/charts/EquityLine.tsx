import { num } from "../services/format";

/** Curva de capital ligera en SVG (sin dependencias). Solo dibuja datos reales recibidos. */
export function EquityLine({ points }: { points: { at: string; equity: string }[] }) {
  const ys = points.map((p) => num(p.equity)).filter((v): v is number => v !== null);
  if (ys.length < 2) return <div className="muted small">Aún no hay suficientes instantáneas para dibujar la curva.</div>;
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const span = max - min || 1;
  const W = 300;
  const H = 70;
  const d = ys.map((y, i) => `${(i / (ys.length - 1)) * W},${H - 4 - ((y - min) / span) * (H - 8)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Curva de capital">
      <polyline points={d} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
