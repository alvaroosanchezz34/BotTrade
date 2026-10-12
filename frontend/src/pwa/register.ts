/** Registro del service worker (solo en producción y en contextos seguros). */
export function registerServiceWorker(): void {
  if (!("serviceWorker" in navigator) || !import.meta.env.PROD) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      /* sin SW la app funciona igual, solo sin la página sin conexión */
    });
  });
}

export function isStandalone(): boolean {
  const nav = navigator as Navigator & { standalone?: boolean };
  return window.matchMedia?.("(display-mode: standalone)").matches === true || nav.standalone === true;
}

export function isIOS(): boolean {
  return /iPad|iPhone|iPod/.test(navigator.userAgent);
}
