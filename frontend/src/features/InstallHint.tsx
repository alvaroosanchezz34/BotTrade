import { useState } from "react";
import { Banner } from "../components/ui";
import { isIOS, isStandalone } from "../pwa/register";

const KEY = "bottrade.installHint.dismissed";

/** Instrucciones para iPhone (Safari no ofrece «instalar» por sí solo). */
export function InstallInstructions() {
  return (
    <ol className="small" style={{ margin: 0, paddingLeft: 20 }}>
      <li>Abre esta dirección en <b>Safari</b> (no desde otra app).</li>
      <li>Toca el botón <b>Compartir</b> (cuadrado con flecha hacia arriba).</li>
      <li>Elige <b>«Añadir a pantalla de inicio»</b> y confirma.</li>
      <li>Abre BotTrade desde el icono: se ve a pantalla completa. El bot sigue en el servidor aunque cierres la app.</li>
    </ol>
  );
}

export function InstallHint() {
  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem(KEY) === "1";
    } catch {
      return false;
    }
  });
  if (hidden || isStandalone() || !isIOS()) return null;
  return (
    <Banner kind="info">
      <b>Instálala en tu iPhone.</b>
      <InstallInstructions />
      <button
        className="btn small"
        style={{ marginTop: 8 }}
        onClick={() => {
          try {
            localStorage.setItem(KEY, "1");
          } catch {
            /* sin almacenamiento */
          }
          setHidden(true);
        }}
      >
        No volver a mostrar
      </button>
    </Banner>
  );
}
