import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AuthGate } from "./auth";
import { mockFetch, renderWithProviders } from "../test/utils";

describe("AuthGate", () => {
  it("sin sesión (401 en /me) muestra el login una sola vez, sin bucle de peticiones", async () => {
    const m = mockFetch({ "/api/auth/me": () => ({ status: 401, body: { detail: "No autenticado" } }) });
    renderWithProviders(
      <AuthGate>
        <div>contenido privado</div>
      </AuthGate>,
    );
    expect(await screen.findByRole("button", { name: "Entrar" })).toBeInTheDocument();
    expect(screen.queryByText("contenido privado")).not.toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 300));
    expect(m.calls.filter((c) => c.url === "/api/auth/me").length).toBeLessThanOrEqual(2);
  });

  it("credenciales erróneas muestran el error sin dar pistas", async () => {
    mockFetch({
      "/api/auth/me": () => ({ status: 401, body: {} }),
      "/api/auth/login": () => ({ status: 401, body: { detail: "Credenciales inválidas" } }),
    });
    const user = userEvent.setup();
    renderWithProviders(
      <AuthGate>
        <div>privado</div>
      </AuthGate>,
    );
    await user.type(await screen.findByLabelText("Usuario"), "admin");
    await user.type(screen.getByLabelText("Contraseña"), "x");
    await user.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByText("Credenciales inválidas")).toBeInTheDocument();
    expect(screen.queryByText("privado")).not.toBeInTheDocument();
  });

  it("con sesión válida muestra el contenido", async () => {
    mockFetch({ "/api/auth/me": () => ({ body: { username: "admin", role: "admin" } }) });
    renderWithProviders(
      <AuthGate>
        <div>contenido privado</div>
      </AuthGate>,
    );
    await waitFor(() => expect(screen.getByText("contenido privado")).toBeInTheDocument());
  });

  it("una sesión caducada durante el uso vuelve al login con aviso", async () => {
    mockFetch({ "/api/auth/me": () => ({ body: { username: "admin", role: "admin" } }) });
    renderWithProviders(
      <AuthGate>
        <div>contenido privado</div>
      </AuthGate>,
    );
    await screen.findByText("contenido privado");
    window.dispatchEvent(new Event("bottrade:unauthorized"));
    expect(await screen.findByText(/sesión ha caducado/)).toBeInTheDocument();
    expect(screen.queryByText("contenido privado")).not.toBeInTheDocument();
  });
});
