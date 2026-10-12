import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export type TagKind = "sim" | "ok" | "warn" | "bad" | "plain";

export function Tag({ kind = "plain", children, title }: { kind?: TagKind; children: ReactNode; title?: string }) {
  return (
    <span className={`tag ${kind === "plain" ? "" : kind}`} title={title}>
      {children}
    </span>
  );
}

export function Dot({ kind }: { kind: "ok" | "warn" | "bad" | "off" }) {
  return <span className={`dot ${kind === "off" ? "" : kind}`} aria-hidden="true" />;
}

export function Card({
  title,
  right,
  children,
  className = "",
}: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <div className="row between" style={{ marginBottom: 10 }}>
          {title && (
            <h2 className="card-title" style={{ margin: 0 }}>
              {title}
            </h2>
          )}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone,
  tag,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "pos" | "neg" | "flat";
  tag?: ReactNode;
}) {
  return (
    <div className="stat">
      <div className="label">
        <span>{label}</span>
        {tag}
      </div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Banner({
  kind = "info",
  children,
  role,
}: {
  kind?: "info" | "warn" | "bad";
  children: ReactNode;
  role?: "alert" | "status";
}) {
  return (
    <div className={`banner ${kind}`} role={role ?? (kind === "bad" ? "alert" : "status")}>
      {children}
    </div>
  );
}

export function Skeleton({ h = 18, w = "100%" }: { h?: number; w?: number | string }) {
  return <div className="skeleton" style={{ height: h, width: w }} aria-hidden="true" />;
}

export function LoadingBlock({ label = "Cargando…" }: { label?: string }) {
  return (
    <div className="stack" role="status" aria-live="polite" aria-label={label}>
      <Skeleton h={22} w="60%" />
      <Skeleton h={64} />
      <Skeleton h={64} />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.isNetwork) return "No hay conexión con el servidor.";
    if (e.status === 429) return `Demasiadas peticiones. Espera ${e.retryAfter ?? 30} s.`;
    if (e.status === 403) return "No tienes permiso para esta acción.";
    return e.message;
  }
  return "Error inesperado.";
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="banner bad" role="alert">
      <b>No se pudieron cargar los datos.</b> {errorText(error)}
      <div className="muted small">No se muestran valores de ejemplo: mientras falle, estos datos no están disponibles.</div>
      {onRetry && (
        <button className="btn small" style={{ marginTop: 8 }} onClick={onRetry}>
          Reintentar
        </button>
      )}
    </div>
  );
}

interface QueryLike<T> {
  data: T | undefined;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

/** Carga / error / datos. Si hay datos previos y el refresco falla, se conservan y se avisa. */
export function Async<T>({
  q,
  children,
  label,
}: {
  q: QueryLike<T>;
  children: (data: T) => ReactNode;
  label?: string;
}) {
  if (q.data !== undefined) {
    return (
      <>
        {q.isError && (
          <Banner kind="warn">
            <b>Datos desactualizados.</b> No se pudo refrescar ({errorText(q.error)}). Se muestra lo último recibido.
          </Banner>
        )}
        {children(q.data)}
      </>
    );
  }
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  return <LoadingBlock label={label} />;
}

export function ProgressBar({ value, max, label }: { value: number | null; max: number | null; label: string }) {
  if (value === null || max === null || max <= 0) return <div className="bar" aria-hidden="true" />;
  const frac = Math.max(0, Math.min(1, value / max));
  const cls = frac >= 1 ? "max" : frac >= 0.7 ? "hot" : "";
  return (
    <div
      className={`bar ${cls}`}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={Math.min(value, max)}
    >
      <i style={{ width: `${frac * 100}%` }} />
    </div>
  );
}
