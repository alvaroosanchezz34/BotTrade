import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { ApiError, UNAUTHORIZED_EVENT } from "../api/client";
import { useLogout, useMe } from "../api/queries";
import type { Me } from "../api/types";
import { LoadingBlock } from "../components/ui";
import { clearStoredData } from "../services/snapshot";
import { Login } from "../pages/Login";

interface AuthCtx {
  me: Me;
  isAdmin: boolean;
  logout: () => Promise<void>;
}
const Ctx = createContext<AuthCtx | null>(null);

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth fuera de AuthGate");
  return v;
}

/** Muestra el login si no hay sesión; si la sesión caduca durante el uso, vuelve al login. */
export function AuthGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const meQ = useMe();
  const logoutM = useLogout();
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    const onUnauthorized = () => {
      setExpired(true);
      clearStoredData();
      // Se descartan los datos privados en memoria, pero NO la consulta de sesión (evita bucles).
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [qc]);

  if (meQ.isPending) return <LoadingBlock label="Comprobando sesión…" />;
  const unauth = meQ.error instanceof ApiError && meQ.error.status === 401;
  if (expired || unauth || !meQ.data) {
    return (
      <Login
        expired={expired}
        networkError={!unauth && meQ.isError && meQ.error instanceof ApiError && meQ.error.isNetwork}
        onLoggedIn={() => setExpired(false)}
      />
    );
  }
  const me = meQ.data;
  return (
    <Ctx.Provider
      value={{
        me,
        isAdmin: me.role === "admin",
        logout: async () => {
          try {
            await logoutM.mutateAsync();
          } finally {
            clearStoredData();
            qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
            setExpired(false);
            qc.setQueryData(["me"], undefined);
            await qc.invalidateQueries({ queryKey: ["me"] });
          }
        },
      }}
    >
      {children}
    </Ctx.Provider>
  );
}
