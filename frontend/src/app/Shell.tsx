import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useOverview } from "../api/queries";
import { IconBrain, IconChart, IconGear, IconHome, IconList } from "../components/icons";
import { Banner, Tag } from "../components/ui";
import { useOnline } from "../hooks/useOnline";
import { useRealtime } from "../hooks/realtime";
import { ago, dateTime } from "../services/format";
import { useTimeZone } from "../hooks/useTimeZone";

const NAV = [
  { to: "/", label: "Inicio", icon: <IconHome />, end: true },
  { to: "/mercados", label: "Mercados", icon: <IconChart /> },
  { to: "/operaciones", label: "Operaciones", icon: <IconList /> },
  { to: "/estrategias", label: "Estrategias", icon: <IconBrain /> },
  { to: "/configuracion", label: "Ajustes", icon: <IconGear /> },
];

function ModeBadge() {
  const { data } = useOverview();
  const mode = data?.mode;
  if (!mode) return <Tag kind="warn">Modo desconocido</Tag>;
  return (
    <Tag kind="sim" title={data.data_origin}>
      <span className="mode">{mode}</span> · simulación
    </Tag>
  );
}

function LiveBadge() {
  const online = useOnline();
  const rt = useRealtime();
  if (!online) return <Tag kind="bad">Sin conexión</Tag>;
  switch (rt.state) {
    case "live":
      return <Tag kind="ok">● En vivo</Tag>;
    case "connecting":
    case "reconnecting":
      return <Tag kind="warn">Reconectando…</Tag>;
    default:
      return <Tag kind="warn">Sondeo</Tag>;
  }
}

/** Aviso permanente cuando lo que se ve puede no ser lo actual. */
function Freshness() {
  const online = useOnline();
  const q = useOverview();
  const tz = useTimeZone();
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 15_000);
    return () => clearInterval(id);
  }, []);
  const updated = q.dataUpdatedAt ? new Date(q.dataUpdatedAt).toISOString() : null;
  const tooOld = q.dataUpdatedAt > 0 && Date.now() - q.dataUpdatedAt > 90_000;
  if (!online || (q.isError && q.data) || tooOld) {
    return (
      <Banner kind="warn" role="status">
        <b>Datos desactualizados.</b> {online ? "No se pueden refrescar ahora." : "Sin conexión."} Último estado recibido:{" "}
        {updated ? `${dateTime(updated, tz)} (${ago(updated)})` : "ninguno"}. Los controles pueden no reflejar la realidad.
      </Banner>
    );
  }
  if (q.data && !q.data.mode_matches_engine) {
    return <Banner kind="bad">El modo configurado en la API no coincide con el del engine. No te fíes de estas cifras.</Banner>;
  }
  return null;
}

export function Shell() {
  return (
    <div className="shell">
      <header className="topbar">
        <h1>BotTrade</h1>
        <div className="right">
          <ModeBadge />
          <Tag title="Este sistema no puede operar con dinero real">Sin dinero real</Tag>
          <LiveBadge />
        </div>
      </header>
      <nav className="nav" aria-label="Principal">
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end}>
            {n.icon}
            <span>{n.label}</span>
          </NavLink>
        ))}
      </nav>
      <main>
        <div className="stack">
          <Freshness />
          <Outlet />
        </div>
      </main>
    </div>
  );
}
