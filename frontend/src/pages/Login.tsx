import { useState, type FormEvent } from "react";
import { useLogin } from "../api/queries";
import { Banner, errorText } from "../components/ui";

export function Login({
  expired,
  networkError,
  onLoggedIn,
}: {
  expired: boolean;
  networkError: boolean;
  onLoggedIn: () => void;
}) {
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await login.mutateAsync({ username, password });
      setPassword("");
      onLoggedIn();
    } catch {
      /* el error se muestra abajo */
    }
  }

  return (
    <div className="login stack">
      <h1 style={{ margin: 0 }}>BotTrade</h1>
      <div className="muted">Panel de supervisión · simulación y testnet. Este sistema no opera con dinero real.</div>
      {expired && <Banner kind="warn">Tu sesión ha caducado o se cerró en otro dispositivo. Entra de nuevo.</Banner>}
      {networkError && <Banner kind="bad">No hay conexión con el servidor.</Banner>}
      <form className="stack" onSubmit={submit}>
        <div className="field">
          <label htmlFor="u">Usuario</label>
          <input id="u" className="input" autoComplete="username" autoCapitalize="none" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="p">Contraseña</label>
          <input id="p" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {login.isError && <Banner kind="bad">{errorText(login.error)}</Banner>}
        <button className="btn primary block" disabled={login.isPending}>
          {login.isPending ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}
